"""实体抽取规则引擎：属性级规则校验 + 证据驱动置信度打分 + 实体级判决。

设计文档：doc/知识库/实体抽取属性级规则与人工复核设计.md

职责边界：
- 只做"判定"，不做落库；调用方（graph_extraction_service）按 verdict 决定去向。
- 不依赖 graph_extraction_service，避免循环导入。
- 未配置任何规则时，行为与接入前完全一致（仅保留类型规整与属性键白名单），
  保证存量本体零影响（设计文档 §10）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from config import settings

logger = logging.getLogger(__name__)

# 违规处置策略（设计文档 §3.2）
ON_DROP_ATTRIBUTE = "drop_attribute"
ON_REVIEW = "review"
ON_DROP_ENTITY = "drop_entity"
_VALID_ACTIONS = (ON_DROP_ATTRIBUTE, ON_REVIEW, ON_DROP_ENTITY)

# 参与长度校验的类型（设计文档 §3.2：min/max_length 对字符串生效）
_LENGTH_TYPES = ("string", "text", "date", "datetime")
# 参与数值范围比较的类型（其余按字典序比较，即 ISO 日期可直接用）
_NUMERIC_TYPES = ("number",)


# ===== 判决结果对象（设计文档 §4.2）=====

@dataclass
class Violation:
    """一条规则违规记录。"""

    level: str                      # "attribute" | "entity"
    rule: str                       # enum | pattern | range | length | confidence
                                    # | required | name_pattern | min_attributes
    target: str                     # 属性名 或 实体名
    code: str = ""                  # 属性 code，便于前端精确定位
    value: Any = None
    reason: str = ""
    # 与 on_violation 取值保持一致，避免两套枚举
    action: str = ON_DROP_ATTRIBUTE  # drop_attribute | review | drop_entity

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "rule": self.rule,
            "target": self.target,
            "code": self.code,
            # 值统一转字符串，避免非 JSON 类型（如 datetime）导致整体序列化失败
            "value": self.value if isinstance(self.value, (str, int, float, bool)) or self.value is None
                     else str(self.value),
            "reason": self.reason,
            "action": self.action,
        }


@dataclass
class EntityVerdict:
    """实体级判决结果。"""

    verdict: str = "pass"                       # pass | review | drop
    violations: list[Violation] = field(default_factory=list)
    confidence: float = 1.0                     # 实体级置信度（证据计算，§4.5.2）
    attr_confidence: dict[str, float] = field(default_factory=dict)  # 属性级（§4.5.1）
    raw_properties: str = ""                    # 原始抽取结果，供复核队列人工对照（§5.2）

    @property
    def primary_rule(self) -> str:
        """汇总主因，用于复核队列分组筛选（§5.2）。"""
        for v in self.violations:
            if v.action in (ON_REVIEW, ON_DROP_ENTITY):
                return v.rule
        return self.violations[0].rule if self.violations else ""


# ===== 证据聚合（设计文档 §4.5.3）=====

class ExtractionEvidence:
    """批内证据聚合：跨 chunk 一致性统计。

    只在当前批次的 payload 内统计，不跨批次、不查库，开销 O(N)。
    """

    def __init__(self, payload: dict):
        self._attr_hits: dict[tuple[str, str, str], set[str]] = {}
        self._entity_hits: dict[tuple[str, str], set[str]] = {}
        for item in (payload or {}).get("chunks", []):
            chunk_id = str(item.get("chunk_id", "")).strip()
            for raw in item.get("entities", []) or []:
                if not isinstance(raw, dict):
                    continue
                name = str(raw.get("name", "")).strip().lower()
                etype = str(raw.get("entity_type", "")).strip().lower()
                if name:
                    self._entity_hits.setdefault((name, etype), set()).add(chunk_id)
                for key, value in self._iter_props(raw.get("properties")):
                    k = (name, key.strip().lower(), value.strip())
                    self._attr_hits.setdefault(k, set()).add(chunk_id)

    @staticmethod
    def _iter_props(raw_props: Any):
        """统一遍历属性值，兼容 dict 与 JSON 字符串两种形态。"""
        props = raw_props
        if isinstance(props, str):
            try:
                props = json.loads(props)
            except (json.JSONDecodeError, TypeError):
                return
        if not isinstance(props, dict):
            return
        for key, value in props.items():
            if value is None:
                continue
            yield str(key), str(value)

    def attr_chunk_count(self, entity_name: str, attr_name: str, value: Any) -> int:
        key = (
            (entity_name or "").strip().lower(),
            (attr_name or "").strip().lower(),
            str(value or "").strip(),
        )
        return len(self._attr_hits.get(key, ()))

    def entity_chunk_count(self, entity_name: str, entity_type: str) -> int:
        key = ((entity_name or "").strip().lower(), (entity_type or "").strip().lower())
        return len(self._entity_hits.get(key, ()))


# ===== 置信度打分（设计文档 §4.5）=====

class ConfidenceScorer:
    """证据驱动的置信度计算。

    不采用模型自报：与项目既有实践一致（PROPAGATION_DECAY 公式 / INFERENCE_RULES
    规则常量 / doc_profiler 规则常量），且阈值含义不随模型更换而漂移。
    """

    ATTR_BASE = 0.5
    W_EXACT_HIT = 0.3        # 属性值在原文精确出现
    W_TYPE_OK = 0.1          # 类型校验通过且无需强制转换
    W_CROSS_CHUNK = 0.1      # 同 (实体, 属性, 值) 在多个 chunk 一致出现
    W_INFERRED = -0.2        # 值来自推断（原文无精确匹配）

    ENTITY_BASE = 0.4
    W_COMPLETENESS = 0.3     # 属性完整度权重（乘以 有效/定义 比例）
    W_NAME_EXACT = 0.2       # 实体名在原文精确出现
    W_ENTITY_CROSS = 0.1     # 实体在 ≥2 个 chunk 被抽到

    @classmethod
    def _clamp(cls, value: float) -> float:
        return 0.0 if value < 0 else (1.0 if value > 1 else round(value, 4))

    @classmethod
    def attr_score(
        cls,
        value: Any,
        *,
        content: str,
        type_ok: bool,
        cross_chunk_count: int,
    ) -> float:
        """属性级置信度（§4.5.1）。"""
        text = str(value or "").strip()
        score = cls.ATTR_BASE
        if text and content and text in content:
            score += cls.W_EXACT_HIT
        else:
            score += cls.W_INFERRED
        if type_ok:
            score += cls.W_TYPE_OK
        if cross_chunk_count >= 2:
            score += cls.W_CROSS_CHUNK
        return cls._clamp(score)

    @classmethod
    def entity_score(
        cls,
        *,
        valid_attr_count: int,
        defined_attr_count: int,
        name_in_content: bool,
        entity_chunk_count: int,
    ) -> float:
        """实体级置信度（§4.5.2）。"""
        score = cls.ENTITY_BASE
        if defined_attr_count > 0:
            ratio = min(1.0, max(0, valid_attr_count) / defined_attr_count)
            score += cls.W_COMPLETENESS * ratio
        else:
            # 无属性定义时按满完整度计入，避免空属性本体被系统性低估
            score += cls.W_COMPLETENESS
        if name_in_content:
            score += cls.W_NAME_EXACT
        if entity_chunk_count >= 2:
            score += cls.W_ENTITY_CROSS
        return cls._clamp(score)


# ===== 类型规整（从 graph_extraction_service 迁移，保持行为一致）=====

def coerce_property_value(value: Any, data_type: str) -> Any:
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
        # string / date / datetime / text → 统一为字符串
        text = str(value).strip()
        return text if text else None
    except (ValueError, TypeError):
        return None


# ===== 抽取报告（设计文档 §7.2）=====

class ExtractionReport:
    """规则违规统计，最终落入 File.detail.extraction_report。"""

    CONF_BUCKETS = ("0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0")
    MAX_SAMPLES = 20

    def __init__(self):
        self.total_entities_raw = 0
        self.passed = 0
        self.reviewed = 0
        self.dropped = 0
        self.downgraded_attributes = 0
        self.by_rule: dict[str, int] = {}
        self.samples: list[dict[str, Any]] = []
        self.confidence_histogram: dict[str, int] = {b: 0 for b in self.CONF_BUCKETS}

    def record_entity(self, *, entity_name: str, entity_type: str, verdict: EntityVerdict) -> None:
        self.total_entities_raw += 1
        if verdict.verdict == "drop":
            self.dropped += 1
        elif verdict.verdict == "review":
            self.reviewed += 1
        else:
            self.passed += 1

        for violation in verdict.violations:
            self.by_rule[violation.rule] = self.by_rule.get(violation.rule, 0) + 1
            if violation.level == "attribute" and violation.action == ON_DROP_ATTRIBUTE:
                self.downgraded_attributes += 1

        if verdict.violations and len(self.samples) < self.MAX_SAMPLES:
            self.samples.append({
                "entity_type": entity_type,
                "name": entity_name,
                "rule": verdict.primary_rule,
                "reason": verdict.violations[0].reason,
            })

        confidence = max(0.0, min(1.0, float(verdict.confidence or 0.0)))
        bucket = self.CONF_BUCKETS[min(len(self.CONF_BUCKETS) - 1, int(confidence / 0.2))]
        self.confidence_histogram[bucket] = self.confidence_histogram.get(bucket, 0) + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_entities_raw": self.total_entities_raw,
            "passed": self.passed,
            "reviewed": self.reviewed,
            "dropped": self.dropped,
            "downgraded_attributes": self.downgraded_attributes,
            "by_rule": dict(self.by_rule),
            "confidence_histogram": dict(self.confidence_histogram),
            "samples": list(self.samples),
        }


# ===== 规则校验器 =====

class ExtractionRuleValidator:
    """按本体属性规则校验单个实体的属性集合，并给出实体级判决。"""

    def __init__(self, ontology_constraint: dict | None):
        self.constraint = ontology_constraint or {}
        self.has_constraint = bool(self.constraint.get("ontologies"))
        self.strict_mode = bool(self.constraint.get("strict_mode"))
        self.min_confidence = self._global_min_confidence()

    @staticmethod
    def _global_min_confidence() -> float:
        """全局兜底阈值（§4.5.6），默认 0.0 即关闭。"""
        try:
            return max(0.0, float(getattr(settings, "GRAPH_MIN_ENTITY_CONFIDENCE", 0.0) or 0.0))
        except (TypeError, ValueError):
            return 0.0

    # ---------- 主入口 ----------

    def validate(
        self,
        *,
        entity_name: str,
        entity_type: str,
        properties: str,
        ont_def: dict | None = None,
        chunk_content: str = "",
        evidence: ExtractionEvidence | None = None,
        from_relation: bool = False,
    ) -> tuple[dict[str, Any], EntityVerdict]:
        """校验单个实体。

        返回 (清洗后的属性 dict, 判决)。调用方负责序列化 properties。
        ont_def 为 None（自由抽取模式）时只做 JSON 解析，不做任何规则校验。
        from_relation=True 表示这是由关系补全出来的占位实体：属性为空是其常态，
        因此跳过 required / min_valid_attributes / 置信度闸门，只做名称模式校验。
        """
        verdict = EntityVerdict(raw_properties=properties if isinstance(properties, str) else "")
        props = self._parse_props(properties)
        if not ont_def:
            return props, verdict

        attr_defs = ont_def.get("attributes", []) or []
        attr_map = {a.get("name"): a for a in attr_defs if a.get("name")}
        defined_count = len([a for a in attr_defs if not a.get("is_edit_only")])

        content = chunk_content or ""
        cleaned: dict[str, Any] = {}
        attr_conf: dict[str, float] = {}

        for key, raw_value in props.items():
            attr_def = attr_map.get(key)
            if not attr_def:
                continue  # 属性键白名单（与接入前行为一致）
            data_type = attr_def.get("data_type", "string")
            raw_before = raw_value
            value = coerce_property_value(raw_value, data_type)
            type_ok = value is not None and (
                data_type != "number" or isinstance(raw_before, (int, float))
            )
            if value is None:
                verdict.violations.append(Violation(
                    level="attribute", rule="type", target=key,
                    code=attr_def.get("code") or "", value=raw_value,
                    reason=f"值无法按 {data_type} 类型解析",
                    action=ON_DROP_ATTRIBUTE,
                ))
                continue

            # 属性级置信度（§4.5.1）
            conf = ConfidenceScorer.attr_score(
                value,
                content=content,
                type_ok=type_ok,
                cross_chunk_count=(
                    evidence.attr_chunk_count(entity_name, key, value) if evidence else 0
                ),
            )
            attr_conf[key] = conf

            violation = self._check_rules(attr_def, key, value, conf)
            if violation:
                action = self._resolve_action(violation.action)
                violation.action = action
                verdict.violations.append(violation)
                if action == ON_DROP_ATTRIBUTE:
                    continue      # 只丢该属性值，实体保留
                if action == ON_DROP_ENTITY:
                    verdict.verdict = "drop"
                    return cleaned, verdict
                # review：保留值，实体最终进队列
                cleaned[key] = value
                verdict.verdict = "review"
                continue
            cleaned[key] = value

        # 必填校验（R6）
        if not from_relation:
            for attr_def in attr_defs:
                if not attr_def.get("is_required"):
                    continue
                name = attr_def.get("name")
                if name and name not in cleaned:
                    verdict.violations.append(Violation(
                        level="entity", rule="required", target=name,
                        code=attr_def.get("code") or "",
                        reason=f"缺少必填属性：{name}",
                        action=ON_REVIEW,
                    ))
                    verdict.verdict = "review"

            # 最少有效属性数（R7）
            min_valid = int(ont_def.get("min_valid_attributes") or 0)
            if min_valid > 0 and len(cleaned) < min_valid:
                verdict.violations.append(Violation(
                    level="entity", rule="min_attributes", target=entity_name,
                    reason=f"有效属性 {len(cleaned)} 个，少于要求的 {min_valid} 个",
                    action=ON_REVIEW,
                ))
                verdict.verdict = "review"

        # 实体名模式（R8）
        name_pattern = ont_def.get("_compiled_name_pattern")
        if name_pattern is not None and not name_pattern.search(entity_name or ""):
            verdict.violations.append(Violation(
                level="entity", rule="name_pattern", target=entity_name,
                reason=f"实体名不匹配模式 {name_pattern.pattern}",
                action=ON_REVIEW,
            ))
            verdict.verdict = "review"

        # 实体级置信度（§4.5.2）+ 阈值闸门（R9）
        entity_conf = ConfidenceScorer.entity_score(
            valid_attr_count=len(cleaned),
            defined_attr_count=defined_count,
            name_in_content=bool(entity_name and content and entity_name in content),
            entity_chunk_count=(
                evidence.entity_chunk_count(entity_name, entity_type) if evidence else 0
            ),
        )
        threshold = ont_def.get("min_confidence")
        if threshold is None:
            threshold = self.min_confidence
        if threshold and not from_relation and entity_conf < float(threshold):
            verdict.violations.append(Violation(
                level="entity", rule="min_confidence", target=entity_name,
                value=entity_conf,
                reason=f"实体置信度 {entity_conf:.2f} 低于门槛 {float(threshold):.2f}",
                action=ON_REVIEW,
            ))
            verdict.verdict = "review"

        verdict.confidence = entity_conf
        verdict.attr_confidence = attr_conf
        return cleaned, verdict

    # ---------- 内部实现 ----------

    @staticmethod
    def _parse_props(properties: Any) -> dict[str, Any]:
        if not properties:
            return {}
        try:
            props = json.loads(properties) if isinstance(properties, str) else properties
        except (json.JSONDecodeError, TypeError):
            return {}
        return props if isinstance(props, dict) else {}

    def _resolve_action(self, action: str) -> str:
        """严格模式下把 drop_attribute 升级为 review（§3.4）。"""
        if self.strict_mode and action == ON_DROP_ATTRIBUTE:
            return ON_REVIEW
        return action if action in _VALID_ACTIONS else ON_DROP_ATTRIBUTE

    def _check_rules(
        self, attr_def: dict, key: str, value: Any, confidence: float
    ) -> Violation | None:
        """按 正则 → 枚举 → 范围 → 长度 → 置信度 顺序短路校验（§3.4 R1~R5）。"""
        code = attr_def.get("code") or ""

        # R2 正则
        pattern = attr_def.get("_compiled_pattern")
        if pattern is not None and not pattern.search(str(value)):
            return Violation(
                level="attribute", rule="pattern", target=key, code=code,
                value=value, reason=f"值不匹配正则 {pattern.pattern}",
                action=self._on_violation(attr_def),
            )

        # R1 枚举
        enum_values = attr_def.get("enum_values") or []
        if enum_values and str(value).strip() not in {str(v).strip() for v in enum_values}:
            return Violation(
                level="attribute", rule="enum", target=key, code=code,
                value=value,
                reason=f"值 '{value}' 不在允许集合 [{ '|'.join(str(v) for v in enum_values) }] 中",
                action=self._on_violation(attr_def),
            )

        # R3 范围
        range_err = self._check_range(attr_def, value)
        if range_err:
            return Violation(
                level="attribute", rule="range", target=key, code=code,
                value=value, reason=range_err,
                action=self._on_violation(attr_def),
            )

        # R4 长度
        data_type = attr_def.get("data_type", "string")
        if data_type in _LENGTH_TYPES:
            min_len = int(attr_def.get("min_length") or 0)
            max_len = int(attr_def.get("max_length") or 0)
            length = len(str(value))
            if min_len and length < min_len:
                return Violation(
                    level="attribute", rule="length", target=key, code=code,
                    value=value, reason=f"长度 {length} 少于最小值 {min_len}",
                    action=self._on_violation(attr_def),
                )
            if max_len and length > max_len:
                return Violation(
                    level="attribute", rule="length", target=key, code=code,
                    value=value, reason=f"长度 {length} 超过最大值 {max_len}",
                    action=self._on_violation(attr_def),
                )

        # R5 属性级置信度门槛
        threshold = attr_def.get("confidence_threshold")
        if threshold and confidence < float(threshold):
            return Violation(
                level="attribute", rule="confidence", target=key, code=code,
                value=value,
                reason=f"属性置信度 {confidence:.2f} 低于门槛 {float(threshold):.2f}",
                action=self._on_violation(attr_def),
            )
        return None

    @staticmethod
    def _on_violation(attr_def: dict) -> str:
        action = attr_def.get("on_violation") or ON_DROP_ATTRIBUTE
        return action if action in _VALID_ACTIONS else ON_DROP_ATTRIBUTE

    @staticmethod
    def _check_range(attr_def: dict, value: Any) -> str | None:
        """范围校验：number 按数值，date/datetime 按 ISO 字典序（即时间序）。"""
        min_value = (attr_def.get("min_value") or "").strip()
        max_value = (attr_def.get("max_value") or "").strip()
        if not (min_value or max_value):
            return None
        data_type = attr_def.get("data_type", "string")
        if data_type in _NUMERIC_TYPES:
            try:
                current = float(value)
            except (TypeError, ValueError):
                return f"值 '{value}' 不是合法数值，无法做范围校验"
            if min_value:
                try:
                    if current < float(min_value):
                        return f"值 {current} 小于下界 {min_value}"
                except ValueError:
                    return None
            if max_value:
                try:
                    if current > float(max_value):
                        return f"值 {current} 大于上界 {max_value}"
                except ValueError:
                    return None
            return None

        if data_type in ("date", "datetime"):
            current = str(value).strip()
            if min_value and current < min_value:
                return f"日期 {current} 早于下界 {min_value}"
            if max_value and current > max_value:
                return f"日期 {current} 晚于上界 {max_value}"
        return None
