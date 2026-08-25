"""OAG（Ontology-Augmented Generation，本体增强生成）智能体服务。

与纯向量 RAG（rag_service）的差异：
1. 检索融合：向量召回 + 图谱召回（实体 MENTIONS 反查分片），RRF 融合重排
2. 上下文融合：把命中实体的属性 + 1 跳关系注入 prompt 作为「图谱事实」
3. 推理过程可视化：先下发 entities / subgraph / chunks，再流式 token

设计原则：不改动 RAGService，复用同一套 providers；图谱调用全部只读。
KB 未绑定本体 / 无图谱 / 实体链接为空时，自动降级为纯向量（行为等同问答菜单）。
"""
from __future__ import annotations

import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from providers.embedding import create_embeddings
from providers.graph_store import (
    chunks_mentioning_entities,
    entities_mentioned_by_chunks,
    entity_neighborhood,
    list_kb_entities,
)
from providers.llm import chunk_text, create_llm, extract_reasoning
from providers.retrieval import chunk_id_from_vector_metadata, rrf_fuse
from providers.vector_store import create_vector_store

logger = logging.getLogger(__name__)

OAG_SYSTEM_PROMPT = (
    "你是基于知识库与知识图谱的智能体。回答必须依据【参考资料】与【图谱事实】。"
    "图谱事实是结构化、可信的关系与属性，优先采信；若与参考资料冲突，请指出。"
    "引用参考资料时标注[来源N]（N 对应参考资料序号），引用图谱事实时标注[事实]。"
    "如果资料和事实中都没有相关信息，请如实说明，不要编造答案。"
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

# 图谱事实文本的软上限（字符），避免 prompt 过长
_FACTS_CHAR_BUDGET = 1600


def _vector_chunk_id(metadata: dict) -> str | None:
    """从向量分片元数据重建与 Kùzu Chunk.id 一致的 chunk_id（委托共享工具）。"""
    return chunk_id_from_vector_metadata(metadata)


def _rrf_fuse(rank_lists: list[list[str]], k: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：多个有序 id 列表 → 按 RRF 分数降序（委托共享工具）。"""
    return rrf_fuse(rank_lists, k=k or settings.OAG_RRF_K)


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
    async def query_stream(kb_id: str, query: str, kb_name: str, ontology_schema, skills=None, persona=None):
        """智能体查询（SSE 流式）：推理过程 → 流式回答。

        ontology_schema 由路由层预加载；skills 由 SkillService.resolve 预加载。
        persona 为智能体自定义人设（覆盖 OAG_SYSTEM_PROMPT），空则用默认人设。
        """
        skills = skills or []
        if not settings.OAG_ENABLED:
            # 总开关关闭：完全降级为纯向量
            async for event in OAGService._stream_vector_only(kb_id, query, skills=skills, persona=persona):
                yield event
            return

        embeddings = create_embeddings()
        llm = create_llm()

        # ===== 2. 向量召回 =====
        vec_docs: list[tuple] = []
        try:
            vectorstore = create_vector_store(kb_id, embeddings)
            docs_with_scores = await asyncio.to_thread(
                vectorstore.similarity_search_with_score, query, k=settings.OAG_VEC_K,
            )
            vec_docs = [
                (doc, score) for doc, score in docs_with_scores
                if (1 - float(score)) >= settings.SIMILARITY_THRESHOLD
            ]
        except Exception:
            logger.exception("OAG vector recall failed: kb_id=%s", kb_id)
            vec_docs = []

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

        # ===== 3. 实体链接 =====
        seed_entities = await _link_entities(kb_id, query, vector_chunk_ids)
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

        # ===== 5. RRF 融合重排 =====
        vector_rank_list = list(vector_by_id.keys())  # 已按相似度降序
        graph_rank_list = list(graph_by_id.keys())
        fused = _rrf_fuse([vector_rank_list, graph_rank_list])
        top_n = fused[: settings.OAG_TOP_N]

        final_chunks: list[dict] = []
        for idx, (cid, _rrf) in enumerate(top_n):
            in_vec = cid in vector_id_set
            in_graph = cid in graph_id_set
            retrieval = "both" if (in_vec and in_graph) else ("vector" if in_vec else "graph")
            base = vector_by_id.get(cid) or graph_by_id.get(cid)
            if not base:
                continue
            final_chunks.append({
                "chunk_id": cid,
                "file_id": base.get("file_id", ""),
                "file_name": base.get("file_name", ""),
                "text": base.get("content", ""),
                "score": base.get("score"),
                "index": idx + 1,
                "retrieval": retrieval,
                "start_offset": base.get("start_offset"),
                "end_offset": base.get("end_offset"),
                "page_number": base.get("page_number"),
                "file_ext": base.get("file_ext", ""),
            })

        both_count = sum(1 for c in final_chunks if c["retrieval"] == "both")
        retrieval_path = {
            "vector": sum(1 for c in final_chunks if c["retrieval"] in ("vector", "both")),
            "graph": sum(1 for c in final_chunks if c["retrieval"] in ("graph", "both")),
            "both": both_count,
            "entities": len(seed_entities),
            "degraded": len(seed_entity_ids) == 0,
        }

        facts_text = _format_subgraph_facts(neighborhood)

        logger.info(
            "OAG pipeline: kb_id=%s query=%r vec=%d graph=%d fused=%d entities=%d degraded=%s",
            kb_id, query[:40], len(vector_chunks), len(graph_chunks), len(final_chunks),
            len(seed_entities), retrieval_path["degraded"],
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

        if not final_chunks:
            yield _sse({"type": "token", "content": "在知识库与图谱中均未找到相关内容。"})
            yield "data: [DONE]\n\n"
            return

        system_prompt = build_system_prompt(skills, base_prompt=persona)
        context_parts = [f"[来源{c['index']}]\n{c['text']}" for c in final_chunks]
        context_with_sources = "\n\n".join(context_parts)
        prompt = OAG_USER_TEMPLATE.format(
            subgraph_facts=facts_text,
            context_with_sources=context_with_sources,
            question=query,
        )
        messages = [
            SystemMessage(content=system_prompt),
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
    async def run(kb_id: str, query: str, kb_name: str, ontology_schema, skills=None, persona=None) -> dict:
        """非流式：完整执行 OAG 管线，返回 {answer, chunks, entities, subgraph}。

        供工作流引擎等需要「拿到完整结果」的调用方使用；内部复用 query_stream，
        收集 token 与结构化事件，不重复检索逻辑。
        """
        answer_parts: list[str] = []
        chunks: list[dict] = []
        entities: list[dict] = []
        subgraph: dict | None = None
        async for s in OAGService.query_stream(kb_id, query, kb_name, ontology_schema, skills, persona):
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
    async def _stream_vector_only(kb_id: str, query: str, skills=None, persona=None):
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

        system_prompt = build_system_prompt(skills, base_prompt=persona)
        context_parts = [f"[来源{c['index']}]\n{c['text']}" for c in final_chunks]
        prompt = OAG_USER_TEMPLATE.format(
            subgraph_facts="（无）",
            context_with_sources="\n\n".join(context_parts),
            question=query,
        )
        messages = [SystemMessage(content=system_prompt), HumanMessage(content=prompt)]
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
