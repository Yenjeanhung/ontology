"""OAG（Ontology-Augmented Generation，本体增强生成）智能体服务。

与纯向量 RAG（rag_service）的差异：
1. 检索融合：向量 + BM25 + 图谱（实体 MENTIONS 反查分片）三路 RRF 融合，Rerank 精排后截断
2. 上下文融合：把命中实体的属性 + 1 跳关系注入 prompt 作为「图谱事实」
3. 推理过程可视化：先下发 entities / subgraph / chunks，再流式 token

设计原则：不改动 RAGService，复用同一套 providers；图谱调用全部只读。
KB 未绑定本体 / 无图谱 / 实体链接为空时，自动降级为纯向量（行为等同问答菜单）。
"""
from __future__ import annotations

import asyncio
import json
import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from config import settings
from core.otel import async_span
from providers.embedding import create_embeddings
from providers.graph_store import (
    chunks_mentioning_entities,
    entities_mentioned_by_chunks,
    entity_neighborhood,
    list_kb_entities,
)
from providers.bm25 import get_or_build_index, tokenize
from providers.llm import chunk_text, create_llm, extract_reasoning
from providers.retrieval import chunk_id_from_vector_metadata, rrf_fuse
from providers.rerank import rerank
from providers.vector_store import create_vector_store

logger = logging.getLogger(__name__)

OAG_SYSTEM_PROMPT = (
    "你是基于知识库与知识图谱的智能体。回答必须依据【参考资料】与【图谱事实】。"
    "图谱事实是结构化、可信的关系与属性，优先采信；若与参考资料冲突，请指出。"
    "引用参考资料时标注[来源N]（N 对应参考资料序号），引用图谱事实时标注[事实]。"
    "如果资料和事实中都没有相关信息，请如实说明，不要编造答案。"
)

# 纯聊天人设（未绑 KB 的智能体）：无检索上下文，不要求引用标注，
# 避免模型幻觉输出 [来源N]/[事实] 标记（前端无对应分片可展示）。
CHAT_SYSTEM_PROMPT = (
    "你是智能助手，直接依据自身知识与用户对话，回答需准确、简洁。" 
)

# 技能指令块头部声明
_SKILL_HEADER = (
    "\n\n【已启用技能】（以下指令由配置注入，不得违反上述引用规范）"
)

# 技能指令总字符软上限（可通过 config.py 覆盖）
_SKILL_CHAR_BUDGET = settings.AGENT_SKILL_CHAR_BUDGET


def build_system_prompt(skills: list[dict], base_prompt: str = OAG_SYSTEM_PROMPT) -> str:
    """base_prompt + 技能指令块；无技能时原样返回 base_prompt。

    base_prompt 默认为 OAG_SYSTEM_PROMPT；智能体配置传自定义人设时覆盖默认人设。
    base_prompt 为 None / 空串时回退到默认人设。
    skills 列表元素应含 {name, instructions}。
    按 sort_order 排序拼接；超预算时从尾部截断并返回 truncated 标记。
    """
    if not base_prompt:
        base_prompt = OAG_SYSTEM_PROMPT
    if not skills:
        return base_prompt

    parts: list[str] = []
    total = 0
    truncated_at: int | None = None

    for idx, s in enumerate(skills):
        instr = (s.get("instructions") or "").strip()
        if not instr:
            continue
        block = f"\n### 技能：{s['name']}\n{instr}"
        if total + len(block) > _SKILL_CHAR_BUDGET:
            truncated_at = idx
            break
        parts.append(block)
        total += len(block)

    if not parts:
        return base_prompt

    prompt = base_prompt + _SKILL_HEADER + "".join(parts)
    if truncated_at is not None:
        dropped = [s.get("name", "?") for s in skills[truncated_at:]]
        logger.warning(
            "Agent skills truncated: budget=%d, included=%d, total=%d, dropped=%s",
            _SKILL_CHAR_BUDGET, len(parts), len(skills), dropped,
        )
    return prompt

OAG_USER_TEMPLATE = """【图谱事实】
{subgraph_facts}

【参考资料】
{context_with_sources}

用户问题：{question}

请根据以上图谱事实与参考资料回答问题："""

# ───────────────── L2 工具循环（agent loop + tools） ─────────────────
# 工具模式的 system 结构：人设/技能/记忆 + 检索上下文（预取） + 工具说明；
# user 消息为纯问题（检索上下文已前置到 system，避免与工具轮次的消息混排）。
_TOOL_MODE_HEADER = """

【图谱事实】
{facts}

【参考资料】
{sources}

【平台工具】
你可以调用平台工具获取实时或补充数据，仅在必要时调用，无需每次都调：
- data_query：实体台账结构化统计（问数量/分类统计/明细等真实数据时必须调用，禁止凭资料估算数字）；
- kb_search：跨全部知识库的向量检索（上述参考资料不足时补充）；
- graph_search：实体图谱关系检索（实体关联/依赖结构问题补充）；
- 其余以工具清单为准（可能含 MCP 接入的外部工具）。
要求：
1. 工具返回数据与检索上下文冲突时，以工具返回为准并明确指出差异；
2. 引用检索上下文仍按 [来源N]/[事实] 标注；来自工具的数据须注明工具名，如（台账 data_query）；
3. 依据充分时直接回答，不要为调工具而调工具。
"""


async def _tool_loop_sse(llm, system_prompt: str, query: str, history=None):
    """L2 工具循环的 SSE 封装：工具事件即时下发 + 结束后汇总 + 最终回答。

    - PlatformTools 异步上下文内注册内置工具（kb_search/graph_search/data_query）
      与 MCP 远程工具，进入时下发 tools 事件（前端展示可用工具）；
    - run_tool_loop 的 tool_call / tool_result 事件经队列转为 SSE 逐条下发；
    - 循环结束下发 tool_calls 汇总（含结构化 raw，供素材卡渲染），final_text
      作为一次性 token 事件下发（与问答页流式渲染协议兼容）。
    """
    from services.agent_loop import run_tool_loop
    from services.tool_registry import PlatformTools

    queue: asyncio.Queue = asyncio.Queue()

    def _on_event(evt: dict) -> None:
        queue.put_nowait(evt)

    try:
        async with PlatformTools() as pt:
            yield _sse({"type": "tools", "tools": pt.registry.describe(),
                        "mcp_status": pt.mcp_status})
            task = asyncio.create_task(run_tool_loop(
                llm, pt.registry, system_prompt, query, history=history,
                on_event=_on_event,
            ))
            while not task.done() or not queue.empty():
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                yield _sse({**evt})
            try:
                result = await task
            except RuntimeError as exc:   # LLM 未配置等致命错误
                yield _sse({"type": "token", "content": f"（工具循环无法启动：{exc}）"})
                return
    except Exception:
        logger.exception("agent tool loop failed")
        yield _sse({"type": "token", "content": "\n\n[工具调用循环执行出错]"})
        return

    if result.degraded and result.degrade_note:
        logger.warning("Agent tool loop degraded: %s", result.degrade_note)
        yield _sse({"type": "tool_degrade", "note": result.degrade_note})
    if result.calls:
        yield _sse({"type": "tool_calls", "calls": [
            {"name": c.name, "arguments": c.arguments, "ok": c.ok,
             "error": c.error, "duration_ms": c.duration_ms, "raw": c.raw}
            for c in result.calls
        ]})
    if result.final_text:
        yield _sse({"type": "token", "content": result.final_text})

# 图谱事实文本的软上限（字符），避免 prompt 过长
_FACTS_CHAR_BUDGET = 1600


def _augment_system_prompt(system_prompt: str, summary: str = "",
                           memories: list[str] | None = None) -> str:
    """把会话摘要（P2）与长期记忆事实（P3 mem0）追加到 system prompt 尾部。"""
    blocks: list[str] = []
    if summary:
        blocks.append(f"【会话摘要】\n以下是本会话较早轮次的要点，供延续对话时参考：\n{summary}")
    if memories:
        facts = "\n".join(f"- {m}" for m in memories if m)
        if facts:
            blocks.append(f"【长期记忆】\n以下是关于该用户/智能体的历史事实，与当前问题相关时参考：\n{facts}")
    if not blocks:
        return system_prompt
    return f"{system_prompt}\n\n" + "\n\n".join(blocks)


def _history_messages(history: list[dict] | None) -> list:
    """历史轮次 → LangChain 消息序列（role 映射：user→Human，assistant→AI）。"""
    out: list = []
    for item in history or []:
        role = item.get("role")
        content = item.get("content") or ""
        if not content:
            continue
        if role == "user":
            out.append(HumanMessage(content=content))
        elif role == "assistant":
            out.append(AIMessage(content=content))
    return out


def _vector_chunk_id(metadata: dict) -> str | None:
    """从向量分片元数据重建与 Kùzu Chunk.id 一致的 chunk_id（委托共享工具）。"""
    return chunk_id_from_vector_metadata(metadata)


def _rrf_fuse(rank_lists: list[list[str]], k: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：多个有序 id 列表 → 按 RRF 分数降序（委托共享工具）。"""
    return rrf_fuse(rank_lists, k=k or settings.OAG_RRF_K)


def _keyword_query(query: str, top_k: int = 6) -> str:
    """元问题改写：jieba 分词（去停用词/标点）后取前 top_k 个关键词，空格拼接。

    「本知识库规定的处置原则有哪些？」→「知识库 规定 处置 原则」，
    作为空召回兜底的补充查询，使命中词贴近语料用词。
    """
    tokens: list[str] = []
    for t in tokenize(query):
        if t not in tokens:
            tokens.append(t)
    return " ".join(tokens[:top_k])


async def _vector_recall(vectorstore, query: str, threshold: float) -> list[tuple]:
    """单查询向量召回 + 相似度阈值过滤，返回 [(doc, score), ...]（分数降序）。"""
    docs_with_scores = await asyncio.to_thread(
        vectorstore.similarity_search_with_score, query, k=settings.OAG_VEC_K,
    )
    return [
        (doc, score) for doc, score in docs_with_scores
        if (1 - float(score)) >= threshold
    ]


def _format_subgraph_facts(neighborhood: dict) -> str:
    """把 1 跳子图序列化为「图谱事实」文本块。"""
    entities = neighborhood.get("entities", []) or []
    relations = neighborhood.get("relations", []) or []
    if not entities and not relations:
        return "（无）"

    rel_by_source: dict[str, list[dict]] = {}
    for r in relations:
        rel_by_source.setdefault(r.get("source_name") or "未知", []).append(r)

    lines: list[str] = []
    for e in entities:
        name = e.get("name") or "未知"
        header = f"- 实体[{name}]（类型:{e.get('entity_type') or '未知'}）"
        props = e.get("properties") or {}
        if props:
            prop_str = ", ".join(f"{k}:{v}" for k, v in props.items())
            header += f" 属性:{{{prop_str}}}"
        desc = (e.get("description") or "").strip()
        if desc:
            header += f" 描述:{desc}"
        lines.append(header)
        for r in rel_by_source.get(name, []):
            lines.append(
                f"  ─ {r.get('relation_type') or '相关'} → "
                f"[{r.get('target_name')}]（{r.get('target_type') or ''}）"
            )

    text = "\n".join(lines) or "（无）"
    if len(text) > _FACTS_CHAR_BUDGET:
        text = text[:_FACTS_CHAR_BUDGET] + "\n…（更多图谱事实已省略）"
    return text


async def _link_entities(kb_id: str, query: str, vector_chunk_ids: list[str]) -> list[dict]:
    """实体链接双通道：词面匹配（A）∪ 向量分片反查 MENTIONS（B），去重取 top-M。"""
    q_lower = query.lower()

    # 通道 A：词面匹配
    try:
        all_entities = await asyncio.to_thread(
            list_kb_entities, kb_id, settings.OAG_ENTITY_LIST_LIMIT
        )
    except Exception:
        logger.exception("list_kb_entities failed")
        all_entities = []
    lexical: list[dict] = []
    for e in all_entities:
        name = (e.get("name") or "").strip()
        if name and name.lower() in q_lower:
            lexical.append({
                "id": e.get("entity_id"),
                "name": name,
                "type": e.get("entity_type"),
                "score": 1.0,
                "source": "lexical",
                "_mention": 0,
            })

    # 通道 B：向量分片反查
    try:
        mentioned = await asyncio.to_thread(
            entities_mentioned_by_chunks, kb_id, vector_chunk_ids
        )
    except Exception:
        logger.exception("entities_mentioned_by_chunks failed")
        mentioned = []
    mention_entities: list[dict] = []
    for e in mentioned:
        mention_entities.append({
            "id": e.get("entity_id"),
            "name": (e.get("name") or "").strip(),
            "type": e.get("entity_type"),
            "score": float(e.get("mention_count") or 0),
            "source": "mention",
            "_mention": int(e.get("mention_count") or 0),
        })

    # 合并去重：词面命中优先，mention 补充
    by_id: dict[str, dict] = {}
    for e in lexical:
        by_id[e["id"]] = e
    for e in mention_entities:
        existing = by_id.get(e["id"])
        if existing:
            existing["source"] = "lexical+mention"
            existing["_mention"] = max(existing["_mention"], e["_mention"])
        else:
            by_id[e["id"]] = e

    # 排序：词面命中置前，再按 mention 计数降序
    ranked = sorted(
        by_id.values(),
        key=lambda x: (0 if x["source"].startswith("lexical") else 1, -x["_mention"], x["name"]),
    )
    seed = ranked[: settings.OAG_SEED_ENTITY_LIMIT]
    for e in seed:
        e.pop("_mention", None)
        e["score"] = round(float(e.get("score") or 0.0), 3)
    return seed


class OAGService:
    @staticmethod
    async def query_stream(kb_id: str, query: str, kb_name: str, ontology_schema, skills=None,
                           persona=None, history=None, summary: str = "", memories=None,
                           use_tools: bool = False):
        """智能体查询（SSE 流式）。薄壳：包 OAG 根 span，实现见 _query_stream_impl。"""
        async with async_span(
            "oag.query_stream",
            {
                "rag.kb_id": kb_id,
                "rag.kb_name": kb_name,
                "rag.query_chars": len(query),
                "oag.use_tools": bool(use_tools),
            },
        ):
            async for event in OAGService._query_stream_impl(
                kb_id, query, kb_name, ontology_schema, skills=skills,
                persona=persona, history=history, summary=summary,
                memories=memories, use_tools=use_tools,
            ):
                yield event

    @staticmethod
    async def _query_stream_impl(kb_id: str, query: str, kb_name: str, ontology_schema,
                                 skills=None, persona=None, history=None,
                                 summary: str = "", memories=None, use_tools: bool = False):
        """智能体查询实现：推理过程 → 流式回答。

        ontology_schema 由路由层预加载；skills 由 SkillService.resolve 预加载。
        persona 为智能体自定义人设（覆盖 OAG_SYSTEM_PROMPT），空则用默认人设。
        history 为会话历史（[{role, content}]，旧→新），summary 为滚动摘要，
        memories 为 mem0 长期记忆事实；三者拼接为多轮上下文注入 prompt。
        use_tools=True 时（L2 工具循环）：检索管线照跑（引用体系不变），生成阶段
        换为 agent loop——LLM 可自主调用平台工具补充检索；KB 未命中不再短路，
        允许工具兜底后回答。
        """
        skills = skills or []
        if not settings.OAG_ENABLED:
            # 总开关关闭：完全降级为纯向量
            async for event in OAGService._stream_vector_only(
                kb_id, query, skills=skills, persona=persona,
                history=history, summary=summary, memories=memories,
            ):
                yield event
            return

        embeddings = create_embeddings()
        llm = create_llm()

        # ===== 2. 向量召回（空召回时兜底：关键词改写 + 降阈值重试一次）=====
        vectorstore = None
        vec_docs: list[tuple] = []
        try:
            vectorstore = create_vector_store(kb_id, embeddings)
            vec_docs = await _vector_recall(vectorstore, query, settings.SIMILARITY_THRESHOLD)
        except Exception:
            logger.exception("OAG vector recall failed: kb_id=%s", kb_id)
            vec_docs = []

        empty_recall_retry = False
        if not vec_docs and vectorstore is not None and settings.OAG_EMPTY_RECALL_THRESHOLD > 0:
            empty_recall_retry = True
            kw_query = _keyword_query(query)
            retry_queries = [query] + ([kw_query] if kw_query else [])
            seen_ids: set[str] = set()
            retry_docs: list[tuple] = []
            for rq in retry_queries:
                for doc, score in await _vector_recall(
                    vectorstore, rq, settings.OAG_EMPTY_RECALL_THRESHOLD
                ):
                    cid = _vector_chunk_id(doc.metadata or {})
                    if cid and cid in seen_ids:
                        continue
                    if cid:
                        seen_ids.add(cid)
                    retry_docs.append((doc, score))
            retry_docs.sort(key=lambda x: -float(x[1]))
            vec_docs = retry_docs
            if vec_docs:
                logger.info(
                    "OAG empty-recall retry: kb_id=%s threshold=%.2f kw_query=%r recalled=%d",
                    kb_id, settings.OAG_EMPTY_RECALL_THRESHOLD, kw_query, len(vec_docs),
                )

        # 构建 vector 分片字典（重建 chunk_id 以对接图谱）
        vector_chunks: list[dict] = []
        for doc, score in vec_docs:
            meta = doc.metadata or {}
            cid = _vector_chunk_id(meta)
            vector_chunks.append({
                "chunk_id": cid,
                "file_id": meta.get("file_id", ""),
                "file_name": meta.get("file_name", ""),
                "content": doc.page_content,
                "score": round(1 - float(score), 4),
                "start_offset": meta.get("start_offset"),
                "end_offset": meta.get("end_offset"),
                "page_number": meta.get("page_number"),
                "file_ext": meta.get("file_ext", ""),
            })
        vector_chunk_ids = [c["chunk_id"] for c in vector_chunks if c["chunk_id"]]
        vector_by_id = {c["chunk_id"]: c for c in vector_chunks if c["chunk_id"]}
        vector_id_set = set(vector_by_id.keys())

        # ===== 2.5 BM25 关键词召回（与向量 / 图谱三路 RRF 融合）=====
        bm25_by_id: dict[str, dict] = {}
        bm25_rank: list[str] = []
        if settings.OAG_BM25_ENABLED:
            try:
                index = await asyncio.to_thread(get_or_build_index, kb_id)
            except Exception:
                logger.exception("OAG BM25 index load failed: kb_id=%s", kb_id)
                index = None
            if index is not None:
                try:
                    hits = await asyncio.to_thread(
                        index.search, query, settings.OAG_BM25_RECALL_K,
                    )
                except Exception:
                    logger.exception("OAG BM25 search failed: kb_id=%s", kb_id)
                    hits = []
                for cid, s, doc in hits:
                    if not cid or cid in bm25_by_id:
                        continue
                    meta = doc.get("metadata") or {}
                    bm25_by_id[cid] = {
                        "chunk_id": cid,
                        "file_id": meta.get("file_id", ""),
                        "file_name": meta.get("file_name", ""),
                        "content": doc.get("content", ""),
                        "score": None,
                        "bm25_score": round(float(s), 4),
                        "start_offset": meta.get("start_offset"),
                        "end_offset": meta.get("end_offset"),
                        "page_number": meta.get("page_number"),
                        "file_ext": meta.get("file_ext", ""),
                    }
                    bm25_rank.append(cid)

        # ===== 3. 实体链接（词面匹配 ∪ 向量+BM25 分片 MENTIONS 反查）=====
        mention_chunk_ids = list(dict.fromkeys(vector_chunk_ids + bm25_rank))
        seed_entities = await _link_entities(kb_id, query, mention_chunk_ids)
        seed_entity_ids = [e["id"] for e in seed_entities if e.get("id")]

        # ===== 4. 图谱召回 + 子图事实 =====
        graph_rows: list[dict] = []
        neighborhood: dict = {"entities": [], "relations": []}
        if seed_entity_ids:
            try:
                graph_rows = await asyncio.to_thread(
                    chunks_mentioning_entities, kb_id, seed_entity_ids, settings.OAG_GRAPH_CHUNK_LIMIT,
                )
            except Exception:
                logger.exception("chunks_mentioning_entities failed")
                graph_rows = []
            try:
                neighborhood = await asyncio.to_thread(
                    entity_neighborhood, kb_id, seed_entity_ids,
                    settings.OAG_NEIGHBOR_HOPS, settings.OAG_NEIGHBOR_LIMIT,
                )
            except Exception:
                logger.exception("entity_neighborhood failed")
                neighborhood = {"entities": [], "relations": []}

        graph_chunks = [
            {
                "chunk_id": r.get("chunk_id"),
                "file_id": r.get("file_id", ""),
                "file_name": r.get("file_name", ""),
                "content": r.get("content", ""),
                "score": None,
            }
            for r in graph_rows
            if r.get("chunk_id")
        ]
        graph_by_id = {c["chunk_id"]: c for c in graph_chunks}
        graph_id_set = set(graph_by_id.keys())

        # ===== 5. RRF 三路融合（向量 + BM25 + 图谱）→ Rerank 精排 =====
        vector_rank_list = list(vector_by_id.keys())  # 已按相似度降序
        bm25_rank_list = list(bm25_rank)              # 已按 BM25 分数降序
        graph_rank_list = list(graph_by_id.keys())
        fused = _rrf_fuse([vector_rank_list, bm25_rank_list, graph_rank_list])
        # 精排需要更大的候选池，否则只是把 TOP_N 内部重新排序，收益有限（与 rag_service 同策略）
        candidate_limit = (
            max(settings.OAG_TOP_N, settings.RERANK_CANDIDATE_K)
            if settings.RERANK_ENABLED else settings.OAG_TOP_N
        )

        candidates: list[dict] = []
        for cid, _rrf in fused[:candidate_limit]:
            in_vec = cid in vector_id_set
            in_bm25 = cid in bm25_by_id
            in_graph = cid in graph_id_set
            tags = [
                t for t, hit in (("vector", in_vec), ("bm25", in_bm25), ("graph", in_graph))
                if hit
            ]
            retrieval = "+".join(tags) if tags else "vector"
            base = vector_by_id.get(cid) or bm25_by_id.get(cid) or graph_by_id.get(cid)
            if not base:
                continue
            candidates.append({
                "chunk_id": cid,
                "file_id": base.get("file_id", ""),
                "file_name": base.get("file_name", ""),
                "text": base.get("content", ""),
                "score": base.get("score"),
                "retrieval": retrieval,
                "start_offset": base.get("start_offset"),
                "end_offset": base.get("end_offset"),
                "page_number": base.get("page_number"),
                "file_ext": base.get("file_ext", ""),
            })

        # Rerank 精排：cross-encoder/LLM 对候选分片与问题逐对打分重排；失败降级保留 RRF 序
        limit = settings.OAG_TOP_N
        rerank_applied = False
        if settings.RERANK_ENABLED and candidates:
            reranked = await rerank(query, candidates, settings.RERANK_TOP_N, llm)
            if reranked:
                candidates = reranked
                limit = settings.RERANK_TOP_N
                rerank_applied = True
            else:
                logger.info("OAG rerank unavailable, keep RRF order")

        final_chunks = candidates[:limit]
        for idx, item in enumerate(final_chunks):
            item["index"] = idx + 1

        def _has_tag(tag: str, r: str) -> bool:
            return tag in r.split("+")

        # ===== 5.5 检索流程明细（随 retrieval_path 下发，前端展示 + 导出）=====
        lexical_cnt = sum(1 for e in seed_entities if e.get("source") in ("lexical", "lexical+mention"))
        mention_cnt = sum(1 for e in seed_entities if e.get("source") in ("mention", "lexical+mention"))
        pipeline_steps = [
            {
                "key": "vector", "name": "向量检索", "count": len(vector_chunks), "unit": "条分片",
                "detail": f"对问题做向量相似度召回（相似度阈值 {settings.SIMILARITY_THRESHOLD}）"
                          + ("；首次零召回，已降阈值 + 关键词改写重试" if empty_recall_retry else ""),
            },
            {
                "key": "bm25", "name": "全文检索（BM25）", "count": len(bm25_rank), "unit": "条分片",
                "detail": "关键词全文索引检索，补充向量召回漏掉的精确词面命中"
                          if settings.OAG_BM25_ENABLED else "BM25 全文检索未启用，已跳过",
            },
            {
                "key": "link", "name": "实体识别（实体链接）", "count": len(seed_entities), "unit": "个实体",
                "detail": f"通道A 词面匹配（知识库实体名出现在问题文本中）命中 {lexical_cnt} 个 "
                          f"∪ 通道B 已召回分片反查 MENTIONS 关联 {mention_cnt} 个，去重合并为种子实体",
            },
            {
                "key": "graph", "name": "图谱检索", "count": len(graph_chunks), "unit": "条分片",
                "detail": f"以种子实体反查 MENTIONS 分片，并展开 {settings.OAG_NEIGHBOR_HOPS} 跳子图"
                          f"（实体 {len(neighborhood.get('entities') or [])} 个 / 关系 {len(neighborhood.get('relations') or [])} 条）"
                          "作为图谱事实注入上下文",
            },
            {
                "key": "fuse", "name": "RRF 融合", "count": len(candidates), "unit": "条候选",
                "detail": "三路召回按倒数排名融合去重，输出统一候选池",
            },
            {
                "key": "rerank", "name": "Rerank 精排", "count": len(final_chunks), "unit": "条引用",
                "detail": (
                    f"cross-encoder/LLM 对候选分片与问题逐对相关性打分重排（{settings.RERANK_PROVIDER}）"
                    if rerank_applied else "精排未启用或不可用，保留 RRF 融合顺序作为引用"
                ),
            },
        ]

        retrieval_path = {
            "vector": sum(1 for c in final_chunks if _has_tag("vector", c["retrieval"])),
            "bm25": sum(1 for c in final_chunks if _has_tag("bm25", c["retrieval"])),
            "graph": sum(1 for c in final_chunks if _has_tag("graph", c["retrieval"])),
            "both": sum(1 for c in final_chunks if "+" in c["retrieval"]),
            "entities": len(seed_entities),
            "degraded": len(seed_entity_ids) == 0,
            "empty_recall_retry": empty_recall_retry,
            "steps": pipeline_steps,
        }

        facts_text = _format_subgraph_facts(neighborhood)

        logger.info(
            "OAG pipeline: kb_id=%s query=%r vec=%d bm25=%d graph=%d fused=%d entities=%d "
            "degraded=%s retry=%s rerank=%s",
            kb_id, query[:40], len(vector_chunks), len(bm25_by_id), len(graph_chunks),
            len(final_chunks), len(seed_entities), retrieval_path["degraded"],
            empty_recall_retry, rerank_applied,
        )

        # ===== 6/7. 下发推理过程事件 + 流式生成 =====
        # 技能事件（最先下发）
        skills_meta = [
            {"id": s["id"], "name": s["name"], "code": s["code"]}
            for s in skills
        ]
        yield _sse({"type": "skills", "skills": skills_meta})

        yield _sse({"type": "entities", "entities": seed_entities})
        yield _sse({
            "type": "subgraph",
            "facts": facts_text,
            "entities": neighborhood.get("entities", []),
            "relations": neighborhood.get("relations", []),
            "retrieval_path": retrieval_path,
        })
        yield _sse({"type": "chunks", "chunks": final_chunks})

        if not final_chunks and not use_tools:
            yield _sse({"type": "token", "content": "在知识库与图谱中均未找到相关内容。"})
            yield "data: [DONE]\n\n"
            return

        system_prompt = _augment_system_prompt(
            build_system_prompt(skills, base_prompt=persona), summary, memories,
        )
        context_parts = [f"[来源{c['index']}]\n{c['text']}" for c in final_chunks]
        context_with_sources = "\n\n".join(context_parts)

        # L2 工具循环：检索上下文前置到 system，user 为纯问题，LLM 自主调用平台工具
        if use_tools:
            tool_system = system_prompt + _TOOL_MODE_HEADER.format(
                facts=facts_text,
                sources=context_with_sources
                or "（检索管线未命中语料，可调用 kb_search / data_query 补充）",
            )
            async for event in _tool_loop_sse(
                llm, tool_system, query, history=_history_messages(history),
            ):
                yield event
            yield "data: [DONE]\n\n"
            return

        prompt = OAG_USER_TEMPLATE.format(
            subgraph_facts=facts_text,
            context_with_sources=context_with_sources,
            question=query,
        )
        messages = [
            SystemMessage(content=system_prompt),
            *_history_messages(history),
            HumanMessage(content=prompt),
        ]

        try:
            reasoning_seen = False
            async for chunk in llm.astream(messages):
                reasoning = extract_reasoning(chunk)
                if reasoning:
                    if not reasoning_seen:
                        reasoning_seen = True
                        logger.debug("[OAG kb_id=%s] 首次收到反思内容（len=%d）", kb_id, len(reasoning))
                    yield _sse({"type": "reasoning", "content": reasoning})
                text = chunk_text(chunk)
                if text:
                    yield _sse({"type": "token", "content": text})
        except Exception:
            logger.exception("OAG LLM stream failed: kb_id=%s", kb_id)
            yield _sse({"type": "token", "content": "\n\n[生成回答时出错]"})

        yield "data: [DONE]\n\n"


    @staticmethod
    async def run(kb_id: str, query: str, kb_name: str, ontology_schema, skills=None, persona=None,
                  use_tools: bool = False) -> dict:
        """非流式：完整执行 OAG 管线，返回 {answer, chunks, entities, subgraph}。

        供工作流引擎等需要「拿到完整结果」的调用方使用；内部复用 query_stream，
        收集 token 与结构化事件，不重复检索逻辑。
        """
        answer_parts: list[str] = []
        chunks: list[dict] = []
        entities: list[dict] = []
        subgraph: dict | None = None
        async for s in OAGService.query_stream(kb_id, query, kb_name, ontology_schema,
                                               skills, persona, use_tools=use_tools):
            if not s.startswith("data: "):
                continue
            payload = s[len("data: "):].strip()
            if payload == "[DONE]":
                break
            try:
                evt = json.loads(payload)
            except ValueError:
                continue
            t = evt.get("type")
            if t == "token":
                answer_parts.append(evt.get("content") or "")
            elif t == "chunks":
                chunks = evt.get("chunks") or []
            elif t == "entities":
                entities = evt.get("entities") or []
            elif t == "subgraph":
                subgraph = evt
        return {
            "answer": "".join(answer_parts),
            "chunks": chunks,
            "entities": entities,
            "subgraph": subgraph or {"facts": "", "entities": [], "relations": [], "retrieval_path": {}},
        }

    @staticmethod
    async def _stream_vector_only(kb_id: str, query: str, skills=None, persona=None,
                                  history=None, summary: str = "", memories=None):
        """降级路径：仅向量召回 + LLM，事件结构保持一致（entities/subgraph 为空）。"""
        skills = skills or []
        embeddings = create_embeddings()
        llm = create_llm()
        docs_with_scores: list[tuple] = []
        try:
            vectorstore = create_vector_store(kb_id, embeddings)
            docs_with_scores = await asyncio.to_thread(
                vectorstore.similarity_search_with_score, query, k=settings.OAG_VEC_K,
            )
        except Exception:
            logger.exception("OAG degraded vector recall failed: kb_id=%s", kb_id)

        final_chunks: list[dict] = []
        for idx, (doc, score) in enumerate(
            (d for d in docs_with_scores if (1 - float(d[1])) >= settings.SIMILARITY_THRESHOLD)
        ):
            meta = doc.metadata or {}
            final_chunks.append({
                "chunk_id": _vector_chunk_id(meta),
                "file_id": meta.get("file_id", ""),
                "file_name": meta.get("file_name", ""),
                "text": doc.page_content,
                "score": round(1 - float(score), 4),
                "index": idx + 1,
                "retrieval": "vector",
                "start_offset": meta.get("start_offset"),
                "end_offset": meta.get("end_offset"),
                "page_number": meta.get("page_number"),
                "file_ext": meta.get("file_ext", ""),
            })

        # 技能事件
        skills_meta = [
            {"id": s["id"], "name": s["name"], "code": s["code"]}
            for s in skills
        ]
        yield _sse({"type": "skills", "skills": skills_meta})

        yield _sse({"type": "entities", "entities": []})
        yield _sse({
            "type": "subgraph", "facts": "（无）", "entities": [], "relations": [],
            "retrieval_path": {
                "vector": len(final_chunks), "graph": 0, "both": 0,
                "entities": 0, "degraded": True,
            },
        })
        yield _sse({"type": "chunks", "chunks": final_chunks})

        if not final_chunks:
            yield _sse({"type": "token", "content": "在知识库中未找到相关内容。"})
            yield "data: [DONE]\n\n"
            return

        system_prompt = _augment_system_prompt(
            build_system_prompt(skills, base_prompt=persona), summary, memories,
        )
        context_parts = [f"[来源{c['index']}]\n{c['text']}" for c in final_chunks]
        prompt = OAG_USER_TEMPLATE.format(
            subgraph_facts="（无）",
            context_with_sources="\n\n".join(context_parts),
            question=query,
        )
        messages = [
            SystemMessage(content=system_prompt),
            *_history_messages(history),
            HumanMessage(content=prompt),
        ]
        try:
            async for chunk in llm.astream(messages):
                reasoning = extract_reasoning(chunk)
                if reasoning:
                    yield _sse({"type": "reasoning", "content": reasoning})
                text = chunk_text(chunk)
                if text:
                    yield _sse({"type": "token", "content": text})
        except Exception:
            logger.exception("OAG degraded LLM stream failed: kb_id=%s", kb_id)
            yield _sse({"type": "token", "content": "\n\n[生成回答时出错]"})
        yield "data: [DONE]\n\n"


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
