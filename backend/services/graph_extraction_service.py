from __future__ import annotations

import asyncio
import json
import logging
from time import perf_counter
import re
from typing import Any, Awaitable, Callable

from langchain_core.messages import HumanMessage, SystemMessage

from config import settings
from providers.graph_store import ChunkGraphData, GraphEntity, GraphRelation
from providers.llm import create_llm

logger = logging.getLogger(__name__)

# ===== 自由抽取模式（无本体约束，向后兼容）=====

GRAPH_EXTRACTION_SYSTEM_PROMPT = """你是知识图谱抽取助手。

你的任务是从文档分块中抽取实体和实体间关系，并严格输出 JSON。

要求：
1. 仅基于提供文本抽取，不要编造。
2. 实体类型用中文，如 人物、组织、产品、项目、技术、地点、日期、事件、概念、文件、法规、指标、方法、算法、模型、数据集。
3. 关系类型用中文，如 任职于、属于、使用、位于、依赖、包含、提到、开发、合作、发布、属于组织、担任、涉及、基于、参考、定义。
4. 每个 chunk 单独输出 entities 和 relations。
5. relation 的 source_name 和 target_name 必须引用同一 chunk 内出现的实体名。
6. 如果某个 chunk 没有合适结果，返回空数组。
7. 只输出一个 JSON 对象，不要输出解释。
"""

GRAPH_EXTRACTION_TEMPLATE = """请从以下 chunks 中抽取实体和关系。

返回格式必须是：
{{
  "chunks": [
    {{
      "chunk_id": "xxx",
      "entities": [
        {{
          "name": "实体名",
          "entity_type": "实体类型",
          "description": "可选的简短描述"
        }}
      ],
      "relations": [
        {{
          "source_name": "源实体名",
          "source_type": "源实体类型",
          "target_name": "目标实体名",
          "target_type": "目标实体类型",
          "relation_type": "关系类型",
          "description": "可选的关系描述"
        }}
      ]
    }}
  ]
}}

文件名：{file_name}

Chunks:
{chunk_payload}
"""

# ===== 本体约束模式（有本体约束时使用）=====

GRAPH_EXTRACTION_CONSTRAINED_SYSTEM_PROMPT = """你是知识图谱抽取助手。

你的任务是从文档分块中抽取实体和实体间关系，并严格输出 JSON。

{ontology_block}

要求：
1. 仅基于提供文本抽取，不要编造。
2. 实体类型必须从上述允许的类型中选择，不要使用其他类型。
3. 每个实体尽量抽取上述列出的属性（properties 字段），缺失则留空；必填属性尽量给出。
4. 关系必须是上述三元组之一（起点类型 + 关系 + 终点类型 都要匹配），不得使用其他关系或组合。
5. 每个 chunk 单独输出 entities 和 relations。
6. relation 的 source_name 和 target_name 必须引用同一 chunk 内出现的实体名。
7. 如果某个 chunk 没有合适结果，返回空数组。
8. 只输出一个 JSON 对象，不要输出解释。
"""

GRAPH_EXTRACTION_CONSTRAINED_TEMPLATE = """请从以下 chunks 中抽取实体和关系。

返回格式必须是：
{{
  "chunks": [
    {{
      "chunk_id": "xxx",
      "entities": [
        {{
          "name": "实体名",
          "entity_type": "实体类型",
          "description": "可选的简短描述",
          "properties": {{
            "属性名": "属性值"
          }}
        }}
      ],
      "relations": [
        {{
          "source_name": "源实体名",
          "source_type": "源实体类型",
          "target_name": "目标实体名",
          "target_type": "目标实体类型",
          "relation_type": "关系类型",
          "description": "可选的关系描述"
        }}
      ]
    }}
  ]
}}

文件名：{file_name}

Chunks:
{chunk_payload}
"""

ProgressCallback = Callable[[int, int, int, int, int, int], Awaitable[None]]
LogCallback = Callable[[str], Awaitable[None]]
BatchResultCallback = Callable[[list[ChunkGraphData]], Awaitable[None]]
CancelCheck = Callable[[], None]


class GraphExtractionService:
    @staticmethod
    async def extract(
        file_name: str,
        chunks: list[ChunkGraphData],
        progress_callback: ProgressCallback | None = None,
        log_callback: LogCallback | None = None,
        batch_result_callback: BatchResultCallback | None = None,
        cancel_check: CancelCheck | None = None,
        ontology_constraint: dict | None = None,
    ) -> list[ChunkGraphData]:
        if cancel_check:
            cancel_check()
        if not settings.GRAPH_ENTITY_EXTRACTION_ENABLED or not chunks:
            return chunks

        min_chars = max(0, settings.GRAPH_MIN_CHARS_FOR_EXTRACTION)
        candidate_chunks = [chunk for chunk in chunks if len((chunk.content or "").strip()) >= min_chars]
        skipped_chunks = len(chunks) - len(candidate_chunks)
        if not candidate_chunks:
            logger.info("Graph extraction skipped: no chunks exceed min chars threshold=%s", min_chars)
            return chunks

        llm = create_llm()
        chunk_map = {chunk.chunk_id: chunk for chunk in chunks}
        batch_size = max(1, settings.GRAPH_EXTRACTION_BATCH_SIZE)
        concurrency = max(1, settings.GRAPH_EXTRACTION_CONCURRENCY)
        batches = [
            candidate_chunks[index:index + batch_size]
            for index in range(0, len(candidate_chunks), batch_size)
        ]
        total_batches = len(batches)
        total_candidate_chunks = len(candidate_chunks)

        # 本体约束模式：预构建 System Prompt 与 Human Template
        has_constraint = bool(ontology_constraint and ontology_constraint.get("ontologies"))
        if has_constraint:
            system_prompt = GraphExtractionService._build_constrained_system_prompt(ontology_constraint)
            human_template = GRAPH_EXTRACTION_CONSTRAINED_TEMPLATE
            constraint_summary = GraphExtractionService._constraint_summary(ontology_constraint)
            logger.info(
                "Graph extraction with ontology constraint: category=%s ontologies=%s relations=%s constraints=%s",
                ontology_constraint.get("category_name"),
                len(ontology_constraint.get("ontologies", [])),
                len(ontology_constraint.get("relation_names", [])),
                len(ontology_constraint.get("constraints", [])),
            )
        else:
            system_prompt = GRAPH_EXTRACTION_SYSTEM_PROMPT
            human_template = GRAPH_EXTRACTION_TEMPLATE
            constraint_summary = ""

        logger.info(
            "Graph extraction started: file_name=%s total_chunks=%s candidate_chunks=%s skipped_chunks=%s batch_size=%s concurrency=%s total_batches=%s constrained=%s",
            file_name,
            len(chunks),
            total_candidate_chunks,
            skipped_chunks,
            batch_size,
            concurrency,
            total_batches,
            has_constraint,
        )
        if log_callback:
            mode_msg = f"（本体约束模式：{constraint_summary}）" if has_constraint else ""
            await log_callback(
                f"开始请求大模型抽取实体和关系：候选分片 {total_candidate_chunks}，批次 {total_batches}，并发 {concurrency}{mode_msg}"
            )

        semaphore = asyncio.Semaphore(concurrency)
        progress_lock = asyncio.Lock()
        processed_batches = 0
        processed_chunks = 0
        started_batches = 0
        running_batches = 0
        started_at = perf_counter()

        if progress_callback:
            await progress_callback(
                processed_batches,
                total_batches,
                processed_chunks,
                total_candidate_chunks,
                started_batches,
                running_batches,
            )

        async def run_batch(batch_index: int, batch: list[ChunkGraphData]):
            if cancel_check:
                cancel_check()
            prompt = human_template.format(
                file_name=file_name,
                chunk_payload=GraphExtractionService._render_chunks(batch),
            )
            async with semaphore:
                nonlocal started_batches, running_batches
                batch_started = perf_counter()
                async with progress_lock:
                    started_batches += 1
                    running_batches += 1
                    current_started_batches = started_batches
                    current_running_batches = running_batches
                if progress_callback:
                    await progress_callback(
                        processed_batches,
                        total_batches,
                        processed_chunks,
                        total_candidate_chunks,
                        current_started_batches,
                        current_running_batches,
                    )
                if log_callback:
                    await log_callback(
                        f"请求大模型抽取：批次 {batch_index + 1}/{total_batches}，本批 {len(batch)} 个分片"
                    )
                try:
                    response = await llm.ainvoke([
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=prompt),
                    ])
                    if cancel_check:
                        cancel_check()
                    payload = GraphExtractionService._parse_response(response.content)
                    duration = perf_counter() - batch_started
                    logger.info(
                        "Graph extraction batch completed: file_name=%s batch=%s/%s chunk_count=%s duration_ms=%.0f",
                        file_name,
                        batch_index + 1,
                        total_batches,
                        len(batch),
                        duration * 1000,
                    )
                    if log_callback:
                        entity_count, relation_count = GraphExtractionService._payload_counts(payload)
                        await log_callback(
                            f"抽取完成：批次 {batch_index + 1}/{total_batches}，耗时 {duration:.1f}s，实体 {entity_count}，关系 {relation_count}"
                        )
                    return batch, payload, False
                except Exception as exc:
                    duration = perf_counter() - batch_started
                    error_msg = str(exc)
                    # 检查是否包含HTTP错误信息
                    if "502" in error_msg or "Bad Gateway" in error_msg:
                        error_detail = f"大模型服务调用失败: POST http://192.168.20.67:3000/v1/chat/completions - HTTP/1.1 502 Bad Gateway"
                    else:
                        error_detail = f"抽取失败: {error_msg}"
                    logger.error(
                        "Graph extraction batch failed: file_name=%s batch=%s/%s chunk_count=%s duration_ms=%.0f error=%s",
                        file_name,
                        batch_index + 1,
                        total_batches,
                        len(batch),
                        duration * 1000,
                        error_detail,
                    )
                    if log_callback:
                        await log_callback(
                            f"抽取失败：批次 {batch_index + 1}/{total_batches}，耗时 {duration:.1f}s，{error_detail}"
                        )
                    return batch, {"chunks": []}, True

        failed_batches = 0
        first_error = None
        tasks = [asyncio.create_task(run_batch(index, batch)) for index, batch in enumerate(batches)]
        try:
            for future in asyncio.as_completed(tasks):
                if cancel_check:
                    cancel_check()
                batch, payload, is_failed = await future
                if cancel_check:
                    cancel_check()
                if is_failed:
                    failed_batches += 1
                    # 如果是第一个失败的批次，记录错误信息
                    if first_error is None:
                        first_error = f"Graph extraction batch failed: batch={len(batch)} chunks"
                GraphExtractionService._merge_payload(chunk_map, payload, ontology_constraint)
                async with progress_lock:
                    processed_batches += 1
                    processed_chunks += len(batch)
                    running_batches = max(0, running_batches - 1)
                    current_processed_batches = processed_batches
                    current_processed_chunks = processed_chunks
                    current_started_batches = started_batches
                    current_running_batches = running_batches
                if batch_result_callback:
                    await batch_result_callback(batch)
                if progress_callback:
                    await progress_callback(
                        current_processed_batches,
                        total_batches,
                        current_processed_chunks,
                        total_candidate_chunks,
                        current_started_batches,
                        current_running_batches,
                    )
        except asyncio.CancelledError:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        if failed_batches > 0:
            if failed_batches == total_batches:
                error_message = f"Graph extraction failed: all {total_batches} batches failed"
                logger.error(error_message)
                if log_callback:
                    await log_callback(f"抽取阶段失败：全部 {total_batches} 个批次均未成功")
                raise RuntimeError(error_message)
            logger.warning(
                "Graph extraction partial success: file_name=%s successful=%s/%s failed=%s/%s",
                file_name,
                total_batches - failed_batches,
                total_batches,
                failed_batches,
                total_batches,
            )
            if log_callback:
                await log_callback(
                    f"抽取阶段部分失败：{failed_batches}/{total_batches} 个批次未成功，"
                    f"已保留 {total_batches - failed_batches} 个成功批次的数据"
                )

        logger.info(
            "Graph extraction finished: file_name=%s total_chunks=%s candidate_chunks=%s total_batches=%s failed_batches=%s duration_ms=%.0f",
            file_name,
            len(chunks),
            total_candidate_chunks,
            total_batches,
            failed_batches,
            (perf_counter() - started_at) * 1000,
        )
        if log_callback:
            await log_callback("大模型实体与关系抽取阶段完成")
        return chunks

    @staticmethod
    def _render_chunks(chunks: list[ChunkGraphData]) -> str:
        rendered = []
        for chunk in chunks:
            rendered.append(
                f"[chunk_id={chunk.chunk_id}]\n"
                f"[chunk_index={chunk.chunk_index}]\n"
                f"{chunk.content}\n"
            )
        return "\n".join(rendered)

    @staticmethod
    def _parse_response(content: Any) -> dict[str, Any]:
        if isinstance(content, list):
            text = "".join(
                item.get("text", "") if isinstance(item, dict) else getattr(item, "text", str(item))
                for item in content
            )
        else:
            text = str(content)

        text = text.strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
        if fenced:
            text = fenced.group(1)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                text = text[start:end + 1]

        return json.loads(text)

    @staticmethod
    def _payload_counts(payload: dict[str, Any]) -> tuple[int, int]:
        entities = 0
        relations = 0
        for item in payload.get("chunks", []):
            entities += len(item.get("entities", []))
            relations += len(item.get("relations", []))
        return entities, relations

    @staticmethod
    def _merge_payload(
        chunk_map: dict[str, ChunkGraphData],
        payload: dict[str, Any],
        ontology_constraint: dict | None = None,
    ):
        has_constraint = bool(ontology_constraint and ontology_constraint.get("ontologies"))
        ontology_by_name = ontology_constraint.get("ontology_by_name", {}) if has_constraint else {}
        constraint_set = ontology_constraint.get("constraint_set", set()) if has_constraint else set()
        relation_id_by_name = ontology_constraint.get("relation_id_by_name", {}) if has_constraint else {}

        for item in payload.get("chunks", []):
            chunk_id = str(item.get("chunk_id", "")).strip()
            chunk = chunk_map.get(chunk_id)
            if not chunk:
                continue

            entities = []
            seen_entities = set()
            for raw_entity in item.get("entities", [])[:settings.GRAPH_MAX_ENTITIES_PER_CHUNK]:
                entity = GraphExtractionService._to_entity(raw_entity)
                if not entity:
                    continue

                # 本体约束后处理：过滤不在允许列表中的实体类型
                if has_constraint:
                    ont_def = ontology_by_name.get(entity.entity_type)
                    if not ont_def:
                        # 实体类型不在本体定义中，跳过
                        continue
                    # 回填本体 id 与属性规整
                    entity.ontology_id = ont_def.get("id")
                    entity.properties = GraphExtractionService._normalize_properties(
                        entity.properties, ont_def.get("attributes", [])
                    )

                entity_key = (entity.name.lower(), entity.entity_type.lower())
                if entity_key in seen_entities:
                    continue
                seen_entities.add(entity_key)
                entities.append(entity)

            entity_lookup = {
                (entity.name.lower(), entity.entity_type.lower()): entity
                for entity in entities
            }
            relations = []
            seen_relations = set()
            for raw_relation in item.get("relations", [])[:settings.GRAPH_MAX_RELATIONS_PER_CHUNK]:
                relation = GraphExtractionService._to_relation(raw_relation, entity_lookup)
                if not relation:
                    continue

                # 本体约束后处理：过滤不匹配任何三元组的关系
                if has_constraint:
                    triple_key = (
                        relation.source_type,
                        relation.relation_type,
                        relation.target_type,
                    )
                    if triple_key not in constraint_set:
                        # 不在三元组约束中，跳过
                        continue
                    # 回填关系定义 id
                    relation.relation_def_id = relation_id_by_name.get(relation.relation_type)

                relation_key = (
                    relation.source_name.lower(),
                    relation.source_type.lower(),
                    relation.relation_type.lower(),
                    relation.target_name.lower(),
                    relation.target_type.lower(),
                )
                if relation_key in seen_relations:
                    continue
                seen_relations.add(relation_key)
                relations.append(relation)

                GraphExtractionService._ensure_entity_from_relation(
                    entities,
                    entity_lookup,
                    relation.source_name,
                    relation.source_type,
                    ontology_constraint if has_constraint else None,
                )
                GraphExtractionService._ensure_entity_from_relation(
                    entities,
                    entity_lookup,
                    relation.target_name,
                    relation.target_type,
                    ontology_constraint if has_constraint else None,
                )

            chunk.entities = entities
            chunk.relations = relations

    @staticmethod
    def _to_entity(raw_entity: Any) -> GraphEntity | None:
        if not isinstance(raw_entity, dict):
            return None
        name = str(raw_entity.get("name", "")).strip()
        if not name:
            return None
        entity_type = str(raw_entity.get("entity_type", "")).strip() or "UNKNOWN"
        description = str(raw_entity.get("description", "")).strip()
        # 解析 properties（本体约束模式下 LLM 会输出此字段）
        raw_props = raw_entity.get("properties")
        properties_str = ""
        if raw_props is not None:
            if isinstance(raw_props, dict):
                properties_str = json.dumps(raw_props, ensure_ascii=False)
            elif isinstance(raw_props, str) and raw_props.strip():
                properties_str = raw_props.strip()
        return GraphEntity(
            name=name[:100],
            entity_type=entity_type[:50],
            description=description[:240],
            properties=properties_str,
        )

    @staticmethod
    def _to_relation(raw_relation: Any, entity_lookup: dict[tuple[str, str], GraphEntity]) -> GraphRelation | None:
        if not isinstance(raw_relation, dict):
            return None
        source_name = str(raw_relation.get("source_name", "")).strip()
        target_name = str(raw_relation.get("target_name", "")).strip()
        relation_type = str(raw_relation.get("relation_type", "")).strip()
        if not source_name or not target_name or not relation_type:
            return None

        source_type = str(raw_relation.get("source_type", "")).strip()
        target_type = str(raw_relation.get("target_type", "")).strip()
        if not source_type:
            source_type = GraphExtractionService._find_entity_type(entity_lookup, source_name)
        if not target_type:
            target_type = GraphExtractionService._find_entity_type(entity_lookup, target_name)
        description = str(raw_relation.get("description", "")).strip()
        return GraphRelation(
            source_name=source_name[:100],
            source_type=(source_type or "UNKNOWN")[:50],
            target_name=target_name[:100],
            target_type=(target_type or "UNKNOWN")[:50],
            relation_type=relation_type[:60],
            description=description[:240],
        )

    @staticmethod
    def _find_entity_type(entity_lookup: dict[tuple[str, str], GraphEntity], entity_name: str) -> str:
        entity_name = entity_name.lower()
        for (name, entity_type), _entity in entity_lookup.items():
            if name == entity_name:
                return entity_type.upper()
        return "UNKNOWN"

    @staticmethod
    def _ensure_entity_from_relation(
        entities: list[GraphEntity],
        entity_lookup: dict[tuple[str, str], GraphEntity],
        entity_name: str,
        entity_type: str,
        ontology_constraint: dict | None = None,
    ):
        key = (entity_name.lower(), entity_type.lower())
        if key in entity_lookup:
            return
        # 本体约束模式下，关系引用的实体类型也必须在允许列表中
        if ontology_constraint:
            ont_def = ontology_constraint.get("ontology_by_name", {}).get(entity_type)
            if not ont_def:
                return  # 实体类型不在本体定义中，不补全
        entity = GraphEntity(name=entity_name, entity_type=entity_type or "UNKNOWN")
        if ontology_constraint:
            ont_def = ontology_constraint.get("ontology_by_name", {}).get(entity_type)
            if ont_def:
                entity.ontology_id = ont_def.get("id")
        entities.append(entity)
        entity_lookup[key] = entity

    # ===== 本体约束模式：Prompt 构建与后处理校验 =====

    @staticmethod
    def _build_constrained_system_prompt(ontology_constraint: dict) -> str:
        """根据本体约束动态构建 System Prompt 中的约束块。"""
        ontologies = ontology_constraint.get("ontologies", [])
        constraints = ontology_constraint.get("constraints", [])

        lines = ["【本体约束 - 必须严格遵守】"]

        # 允许的实体类型及其属性
        lines.append("允许的实体类型及其属性：")
        for ont in ontologies:
            ont_name = ont.get("name", "")
            ont_desc = ont.get("description", "")
            attrs = ont.get("attributes", [])
            header = f"  - {ont_name}"
            if ont_desc:
                header += f"：{ont_desc}"
            lines.append(header)
            if attrs:
                attr_parts = []
                for a in attrs:
                    parts = [a["name"], f"{a['data_type']}"]
                    if a.get("is_required"):
                        parts.append("必填")
                    enum_vals = a.get("enum_values")
                    if enum_vals:
                        parts.append(f"枚举:{','.join(str(v) for v in enum_vals)}")
                    attr_parts.append("、".join(parts))
                lines.append(f"      属性：{'；'.join(attr_parts)}")
            else:
                lines.append("      属性：（无）")

        # 允许的关系三元组
        lines.append("")
        lines.append("允许的关系（仅以下三元组成立，不得越界）：")
        if constraints:
            for c in constraints:
                lines.append(
                    f"  - ({c['source']}) ─{c['relation']}→ ({c['target']})"
                )
        else:
            lines.append("  （无三元组约束，不允许抽取任何关系）")

        ontology_block = "\n".join(lines)
        return GRAPH_EXTRACTION_CONSTRAINED_SYSTEM_PROMPT.format(ontology_block=ontology_block)

    @staticmethod
    def _constraint_summary(ontology_constraint: dict) -> str:
        """生成用于日志/进度提示的约束摘要。"""
        ont_count = len(ontology_constraint.get("ontologies", []))
        rel_count = len(ontology_constraint.get("relation_names", []))
        const_count = len(ontology_constraint.get("constraints", []))
        cat_name = ontology_constraint.get("category_name", "")
        return f"{cat_name}（{ont_count}本体/{rel_count}关系/{const_count}三元组）"

    @staticmethod
    def _normalize_properties(properties_str: str, attr_defs: list[dict]) -> str:
        """对实体属性做轻量校验与类型规整。

        - 剔除不在属性定义列表中的属性键
        - number 类型转浮点失败则置空
        - enum 值不在候选内则置空
        - boolean 转布尔
        """
        if not properties_str:
            return ""
        try:
            props = json.loads(properties_str) if isinstance(properties_str, str) else properties_str
        except (json.JSONDecodeError, TypeError):
            return ""
        if not isinstance(props, dict):
            return ""

        attr_map = {a["name"]: a for a in attr_defs}
        normalized: dict[str, Any] = {}
        for key, value in props.items():
            attr_def = attr_map.get(key)
            if not attr_def:
                continue  # 剔除未定义的属性键
            data_type = attr_def.get("data_type", "string")
            normalized_value = GraphExtractionService._coerce_property_value(value, data_type, attr_def.get("enum_values"))
            if normalized_value is not None:
                normalized[key] = normalized_value

        return json.dumps(normalized, ensure_ascii=False) if normalized else ""

    @staticmethod
    def _coerce_property_value(
        value: Any, data_type: str, enum_values: list | None = None
    ) -> Any:
        """按属性类型规整单个属性值，不合法时返回 None。"""
        if value is None:
            return None
        try:
            if data_type == "number":
                if isinstance(value, (int, float)):
                    return float(value)
                return float(str(value).strip())
            if data_type == "boolean":
                if isinstance(value, bool):
                    return value
                text = str(value).strip().lower()
                if text in ("true", "1", "是", "yes"):
                    return True
                if text in ("false", "0", "否", "no"):
                    return False
                return None
            if data_type == "enum":
                text = str(value).strip()
                if enum_values and text in [str(v) for v in enum_values]:
                    return text
                return None
            # string / date / datetime / text → 统一为字符串
            text = str(value).strip()
            return text if text else None
        except (ValueError, TypeError):
            return None
