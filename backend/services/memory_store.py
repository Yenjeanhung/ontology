"""mem0 长期记忆适配层（doc/智能体/智能体会话_功能设计.md P3）。

设计要点（与设计文档一致）：
- 延迟导入 mem0ai：未安装或 MEM0_ENABLED=false 时整体降级为 no-op，不影响主链路；
- 存储复用后端既有选型：Milvus（独立 collection，与知识库隔离）或本地 Chroma；
- LLM / Embedder 跟随系统「生效配置」（config_service 载入后的 settings）；
- disable_graph=True：关闭 graph_store（本地小模型抽三元组质量差且拖慢写入）；
- 写入走后台任务：每轮 add() 含 2 次 LLM 调用（抽取 + 更新判决），不能阻塞 SSE；
- 记忆按 (user_id, agent_id) 复合隔离：mem0 的 user_id 维度编码为
  "user:{uid}:agent:{aid}"——不同用户与同一智能体的记忆互不可见；
  公共知识不进记忆（走 KB/RAG 检索），mem0 只承载个人记忆。
"""

import asyncio
import logging

from config import settings

logger = logging.getLogger(__name__)

_memory = None            # mem0 Memory 单例（懒加载）
_init_failed = False      # 初始化失败只报一次，避免每轮刷日志


# mem0 自定义抽取/更新 prompt（中文，按本地小模型调优；字段名以所选 mem0 版本为准）
_FACT_EXTRACTION_PROMPT = """请从对话中抽取关于用户与业务领域的**长期事实**（跨会话仍然成立的偏好、约定、背景、结论）。

规则：
- 只抽取明确陈述的事实，不要推测或泛化；
- 单条事实一句话，主谓宾完整，不含指代词（「它/上面说的」要还原成具体对象）；
- 临时性问题、寒暄、会话内才成立的上下文一律丢弃；
- 没有可抽取的事实时输出空列表。

对话：
{messages}
"""

_UPDATE_MEMORY_PROMPT = """你负责维护一份长期记忆库。给出现有记忆与新事实，请输出对记忆库的更新操作序列。

操作类型：
- ADD：全新事实，加入记忆库；
- UPDATE：与某条现有记忆同主体但内容有变化，输出合并后的完整新表述；
- DELETE：与某条现有记忆直接矛盾（以新事实为准），标记删除；
- NONE：重复或无信息量，忽略。

输出 JSON 列表，每项含 id / text / event。现有记忆：
{existing_memories}

新抽取事实：
{extracted_facts}
"""


def _active_embedding_dims() -> int:
    return (settings.OPENAI_EMBEDDING_DIMENSION
            if settings.EMBEDDING_PROVIDER == "openai" else settings.EMBEDDING_DIMENSION)


def _build_config() -> dict:
    """按后端生效配置拼 mem0 config dict（字段名以所选 mem0 版本为准）。"""
    cfg: dict = {
        "history_db_path": settings.MEM0_HISTORY_DB_PATH,
        "disable_graph": True,
        "custom_fact_extraction_prompt": _FACT_EXTRACTION_PROMPT,
        "custom_update_memory_prompt": _UPDATE_MEMORY_PROMPT,
    }
    # LLM：mem0 仅直接支持 openai 兼容协议；anthropic 协议时长期记忆自动停用
    if (settings.LLM_PROVIDER or "openai").lower() == "openai" and settings.OPENAI_API_KEY:
        llm_cfg = {
            "model": settings.LLM_MODEL,
            "api_key": settings.OPENAI_API_KEY,
            "temperature": 0.1,
        }
        if settings.OPENAI_BASE_URL:
            llm_cfg["openai_base_url"] = settings.OPENAI_BASE_URL
        cfg["llm"] = {"provider": "openai", "config": llm_cfg}
    else:
        cfg["llm"] = None  # 标记不可用，_get_memory 会拒绝初始化

    # Embedder：跟随系统选型（openai 在线 / huggingface 本地）
    if settings.EMBEDDING_PROVIDER == "openai":
        emb_cfg = {
            "model": settings.OPENAI_EMBEDDING_MODEL,
            "api_key": settings.OPENAI_API_KEY,
            "embedding_dims": settings.OPENAI_EMBEDDING_DIMENSION,
        }
        if settings.OPENAI_BASE_URL:
            emb_cfg["openai_base_url"] = settings.OPENAI_BASE_URL
        cfg["embedder"] = {"provider": "openai", "config": emb_cfg}
    else:
        cfg["embedder"] = {
            "provider": "huggingface",
            "config": {"model": settings.EMBEDDING_MODEL, "embedding_dims": settings.EMBEDDING_DIMENSION},
        }

    # Vector store：milvus 复用实例（独立 collection）；chroma 用独立本地目录
    if settings.VECTOR_STORE_PROVIDER == "milvus":
        cfg["vector_store"] = {
            "provider": "milvus",
            "config": {
                "collection_name": settings.MEM0_COLLECTION,
                "embedding_model_dims": _active_embedding_dims(),
                "url": f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}",
                # mem0ai 2.0.x 的 MilvusDBConfig.token 标注为 str 但默认 None，
                # 其内部遥测配置重建时会把 None 显式传回 pydantic 触发校验失败；
                # 本地无认证场景显式传空串（pymilvus 视为无 token）
                "token": "",
            },
        }
    else:
        cfg["vector_store"] = {
            "provider": "chroma",
            "config": {"collection_name": settings.MEM0_COLLECTION, "path": "./data/mem0_chroma"},
        }
    return cfg


def _get_memory():
    """懒加载 mem0 Memory 单例；不可用时返回 None（调用方降级）。"""
    global _memory, _init_failed
    if _memory is not None:
        return _memory
    if _init_failed or not settings.MEM0_ENABLED:
        return None
    try:
        from mem0 import Memory  # 延迟导入：未安装 mem0ai 时降级
    except ImportError:
        logger.info("mem0ai 未安装，长期记忆功能停用（pip install mem0ai 可启用）")
        _init_failed = True
        return None

    cfg = _build_config()
    if cfg.get("llm") is None:
        logger.info("LLM 非 openai 兼容协议，mem0 长期记忆暂不支持，已停用")
        _init_failed = True
        return None
    try:
        _memory = Memory.from_config(cfg)
        logger.info("mem0 长期记忆已启用：collection=%s", settings.MEM0_COLLECTION)
    except Exception:
        logger.exception("mem0 初始化失败，长期记忆停用")
        _init_failed = True
        return None
    return _memory


def _extract_facts(result) -> list[str]:
    """兼容 mem0 不同版本的 search 返回结构（dict.results / list）。"""
    if isinstance(result, dict):
        items = result.get("results") or []
    else:
        items = result or []
    facts: list[str] = []
    for item in items:
        text = item.get("memory") if isinstance(item, dict) else str(item)
        if text:
            facts.append(text)
    return facts


def _namespace(user_id: str, agent_id: str) -> str:
    """mem0 user_id 命名空间：(user_id, agent_id) 双层隔离键。

    mem0 只有 user_id 一个隔离维度，把「终端用户 × 智能体」编码成复合键，
    保证不同用户与同一智能体的记忆互不可见（公共知识走 KB，不进 mem0）。
    """
    return f"user:{(user_id or 'default').strip()}:agent:{(agent_id or 'default').strip()}"


class MemoryStore:
    """长期记忆门面：available / search / add_background，全部安全降级。"""

    @staticmethod
    def available() -> bool:
        return _get_memory() is not None

    @staticmethod
    async def search(query: str, agent_id: str = "", user_id: str = "",
                     limit: int | None = None) -> list[str]:
        """按当前问题检索相关长期事实（(user_id, agent_id) 复合隔离）。"""
        mem = _get_memory()
        if mem is None or not (query or "").strip():
            return []
        try:
            # mem0ai 2.0.x：search 不再接受顶层 user_id/limit，改为 filters + top_k
            result = await asyncio.to_thread(
                mem.search,
                query=query,
                filters={"user_id": _namespace(user_id, agent_id)},
                top_k=limit or settings.MEM0_SEARCH_LIMIT,
            )
            return _extract_facts(result)
        except Exception:
            logger.exception("mem0 search 失败（降级为无长期记忆）")
            return []

    @staticmethod
    def add_background(query: str, answer: str, agent_id: str = "",
                       user_id: str = "", session_id: str = "") -> None:
        """问答结束后台写入（抽取 + 更新判决约 2 次 LLM 调用，不阻塞主链路）。"""
        mem = _get_memory()
        if mem is None:
            return

        async def _run():
            try:
                messages: list[dict[str, str]] = [
                    {"role": "user", "content": query},
                    {"role": "assistant", "content": answer},
                ]
                kwargs = {"user_id": _namespace(user_id, agent_id)}
                if session_id:
                    kwargs["metadata"] = {"session_id": session_id}
                await asyncio.to_thread(mem.add, messages, **kwargs)
            except Exception:
                logger.exception("mem0 add 失败（本次不写入长期记忆）")

        try:
            asyncio.get_running_loop()
            asyncio.create_task(_run())
        except RuntimeError:
            logger.debug("无运行中事件循环，跳过长期记忆后台写入")


def reset_memory_store() -> None:
    """重置单例（配置变更 / 测试用）。"""
    global _memory, _init_failed
    _memory = None
    _init_failed = False
