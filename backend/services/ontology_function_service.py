"""S3（P0-3）：函数（Function）+ 派生属性（Derived property）业务层。

设计来源：《本体能力对标Palantir_补强设计》§4.3。

与动作（Action）的分工
====================
- 函数：**只读**、不写数据、可缓存、可被动作/视图/派生属性/智能体复用；
- 动作：事务性写入，带规则与副作用（见 §4.4 / ontology_action_enhance_service.py）。

两者共用同一套沙箱执行器 ``services.service_runtime.execute_service`` 与调用记录表
``ontology_runtime_invocations``（kind=function / action）。
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Entity,
    GraphAnalysisTask,
    Ontology,
    OntologyDerivedProperty,
    OntologyFunction,
    OntologyRuntimeInvocation,
)
from services.service_runtime import coerce_params, execute_service

RESULT_SNIPPET = 8 * 1024

# 进程内缓存：{function_id}:{entity_id}:{params_hash} -> (expire_ts, result)
# 仅对 is_deterministic=1 且 cache_seconds>0 的函数生效（重启即失效，属可重建数据）
_CACHE: dict[str, tuple[float, dict]] = {}

# 图指标 → 图分析任务里的算法名
_GRAPH_METRIC_ALGO = {
    "pagerank": "pagerank",
    "betweenness": "betweenness",
    "community": "louvain",
    "degree": "degree",
}


def _now() -> str:
    return datetime.now().isoformat()


def _load_json(raw: str | None, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _entity_payload(entity: Entity | None, mock: dict | None = None) -> dict:
    if entity is None:
        props = (mock or {}).get("properties") or {}
        return {
            "id": None,
            "name": (mock or {}).get("name") or "",
            "entity_type": (mock or {}).get("entity_type") or "",
            "ontology_id": None,
            "properties": props if isinstance(props, dict) else {},
        }
    props = _load_json(entity.properties, {}) or {}
    return {
        "id": entity.id,
        "name": entity.name,
        "entity_type": entity.entity_type,
        "ontology_id": entity.ontology_id,
        "properties": props if isinstance(props, dict) else {},
    }


def _serialize_function(fn: OntologyFunction) -> dict:
    return {
        "id": fn.id,
        "category_id": fn.category_id or "",
        "ontology_id": fn.ontology_id or "",
        "name": fn.name,
        "code": fn.code,
        "description": fn.description or "",
        "params_schema": _load_json(fn.params_schema, []),
        "return_schema": _load_json(fn.return_schema or "{}", {}),
        "code_text": fn.code_text or "",
        "language": fn.language,
        "timeout_seconds": fn.timeout_seconds,
        "is_deterministic": bool(fn.is_deterministic),
        "cache_seconds": fn.cache_seconds,
        "is_enabled": bool(fn.is_enabled),
        "sort_order": fn.sort_order,
        "created_at": fn.created_at,
        "updated_at": fn.updated_at,
    }


def _serialize_derived(dp: OntologyDerivedProperty) -> dict:
    return {
        "id": dp.id,
        "ontology_id": dp.ontology_id,
        "name": dp.name,
        "code": dp.code,
        "data_type": dp.data_type,
        "source_kind": dp.source_kind,
        "function_id": dp.function_id or "",
        "graph_metric": dp.graph_metric or "",
        "params": _load_json(dp.params, {}),
        "materialize_mode": dp.materialize_mode,
        "last_materialized_at": dp.last_materialized_at or "",
        "is_enabled": bool(dp.is_enabled),
        "sort_order": dp.sort_order,
        "created_at": dp.created_at,
    }


# 对外暴露的序列化入口（路由层使用）
serialize_function = _serialize_function
serialize_derived = _serialize_derived


async def _log_invocation(
    db: AsyncSession, *, kind: str, ref_id: str, entity_id: str = "",
    params: dict | None = None, result=None, status: str = "success",
    error: str | None = None, duration_ms: int = 0, triggered_by: str = "",
) -> None:
    try:
        db.add(OntologyRuntimeInvocation(
            kind=kind, ref_id=ref_id, entity_id=entity_id,
            params=_dump_json(params or {}),
            result=_dump_json(result)[:RESULT_SNIPPET] if result is not None else None,
            status=status, error=(error or "")[:4000] or None,
            duration_ms=duration_ms, triggered_by=triggered_by, created_at=_now(),
        ))
        await db.commit()
    except BaseException as e:  # 审计日志失败不影响业务主流程
        try:
            await db.rollback()
        except BaseException:
            pass
        print(f"[invocation-log] 写入调用日志失败: {e}")


class FunctionService:
    """函数 CRUD + 测试运行 + 实体调用 + 批量解析。"""

    @staticmethod
    async def list_functions(
        db: AsyncSession, category_id: str, ontology_id: str = "",
    ) -> list[dict]:
        stmt = select(OntologyFunction).where(OntologyFunction.category_id == category_id)
        if ontology_id:
            stmt = stmt.where(OntologyFunction.ontology_id == ontology_id)
        rows = (await db.execute(stmt.order_by(OntologyFunction.sort_order, OntologyFunction.name))).scalars().all()
        return [_serialize_function(f) for f in rows]

    @staticmethod
    async def get(db: AsyncSession, function_id: str) -> OntologyFunction | None:
        return await db.get(OntologyFunction, function_id)

    @staticmethod
    async def create(db: AsyncSession, category_id: str, req) -> tuple[dict | None, str | None]:
        name = (req.name or "").strip()
        code = (req.code or "").strip()
        if not name or not code:
            return None, "名称与编码必填"
        dup = (await db.execute(
            select(OntologyFunction).where(
                OntologyFunction.category_id == category_id,
                OntologyFunction.code == code,
            )
        )).scalar_one_or_none()
        if dup:
            return None, f"编码 {code} 已存在"
        fn = OntologyFunction(
            category_id=category_id,
            ontology_id=(req.ontology_id or "").strip(),
            name=name, code=code,
            description=(req.description or "").strip(),
            params_schema=_dump_json(req.params_schema or []),
            return_schema=_dump_json(req.return_schema or {}),
            code_text=req.code_text or "",
            language=req.language or "python",
            timeout_seconds=max(1, min(120, req.timeout_seconds or 30)),
            is_deterministic=int(bool(req.is_deterministic)),
            cache_seconds=max(0, req.cache_seconds or 0),
            is_enabled=int(bool(req.is_enabled)),
            sort_order=req.sort_order or 0,
            created_at=_now(), updated_at=_now(),
        )
        db.add(fn)
        await db.commit()
        await db.refresh(fn)
        return _serialize_function(fn), None

    @staticmethod
    async def update(db: AsyncSession, function_id: str, req) -> tuple[dict | None, str | None]:
        fn = await db.get(OntologyFunction, function_id)
        if not fn:
            return None, "函数不存在"
        if req.name is not None:
            fn.name = req.name.strip()
        if req.code is not None:
            fn.code = req.code.strip()
        if req.description is not None:
            fn.description = req.description.strip()
        if req.ontology_id is not None:
            fn.ontology_id = req.ontology_id.strip()
        if req.params_schema is not None:
            fn.params_schema = _dump_json(req.params_schema)
        if req.return_schema is not None:
            fn.return_schema = _dump_json(req.return_schema)
        if req.code_text is not None:
            fn.code_text = req.code_text
        if req.language is not None:
            fn.language = req.language
        if req.timeout_seconds is not None:
            fn.timeout_seconds = max(1, min(120, req.timeout_seconds))
        if req.is_deterministic is not None:
            fn.is_deterministic = int(bool(req.is_deterministic))
        if req.cache_seconds is not None:
            fn.cache_seconds = max(0, req.cache_seconds)
        if req.is_enabled is not None:
            fn.is_enabled = int(bool(req.is_enabled))
        if req.sort_order is not None:
            fn.sort_order = req.sort_order
        fn.updated_at = _now()
        await db.commit()
        await db.refresh(fn)
        _purge_cache(function_id)
        return _serialize_function(fn), None

    @staticmethod
    async def delete(db: AsyncSession, function_id: str) -> bool:
        fn = await db.get(OntologyFunction, function_id)
        if not fn:
            return False
        await db.execute(
            delete(OntologyDerivedProperty).where(OntologyDerivedProperty.function_id == function_id)
        )
        await db.delete(fn)
        await db.commit()
        _purge_cache(function_id)
        return True

    @staticmethod
    async def _run(
        db: AsyncSession, fn: OntologyFunction, entity: Entity | None,
        params_raw: dict, mock_entity: dict | None = None, triggered_by: str = "entity",
    ) -> dict:
        params, perr = coerce_params(_load_json(fn.params_schema, []), params_raw or {})
        if perr:
            return {"success": False, "data": None, "error": perr, "stdout": "", "duration_ms": 0}

        # 确定性 + 有缓存期 → 命中直接返回
        # key 中纳入代码指纹：代码一变（即使未触发保存清理）旧缓存立即失效
        cache_key = ""
        if fn.is_deterministic and fn.cache_seconds > 0:
            code_fp = hashlib.md5((fn.code_text or "").encode("utf-8")).hexdigest()[:8]
            cache_key = f"{fn.id}:{entity.id if entity else ''}:{_dump_json(params)}:{code_fp}"
            hit = _CACHE.get(cache_key)
            if hit:
                if hit[0] > time.monotonic():
                    return {**hit[1], "cached": True}
                _CACHE.pop(cache_key, None)  # 顺手清理已过期项，防内存滞留

        result = await execute_service(
            code_text=fn.code_text,
            language=fn.language,
            params=params,
            entity=_entity_payload(entity, mock_entity),
            context={
                "kb_id": entity.kb_id if entity else "",
                "category_id": fn.category_id,
                "function_code": fn.code,
                "triggered_by": triggered_by,
            },
            timeout_seconds=fn.timeout_seconds,
        )
        if cache_key and result.get("success"):
            _CACHE[cache_key] = (time.monotonic() + fn.cache_seconds, result)
        return result

    @staticmethod
    async def test_run(db: AsyncSession, function_id: str, req) -> tuple[dict | None, str | None]:
        fn = await db.get(OntologyFunction, function_id)
        if not fn:
            return None, "函数不存在"
        if not fn.is_enabled:
            return None, "函数已停用"
        return await FunctionService._run(db, fn, None, req.params or {}, req.mock_entity, "test"), None

    @staticmethod
    async def invoke(
        db: AsyncSession, entity_id: str, function_id: str, params: dict,
    ) -> tuple[dict | None, str | None]:
        entity = await db.get(Entity, entity_id)
        if not entity:
            return None, "实体不存在"
        fn = await db.get(OntologyFunction, function_id)
        if not fn:
            return None, "函数不存在"
        if not fn.is_enabled:
            return None, "函数已停用"
        result = await FunctionService._run(db, fn, entity, params or {}, None, "entity")
        await _log_invocation(
            db, kind="function", ref_id=fn.id, entity_id=entity_id,
            params=params, result=result.get("data") if result.get("success") else None,
            status="success" if result.get("success") else "error",
            error=result.get("error"), duration_ms=int(result.get("duration_ms") or 0),
            triggered_by="entity",
        )
        return result, None

    @staticmethod
    async def resolve_batch(
        db: AsyncSession, function_id: str, entity_ids: list[str], params: dict,
    ) -> tuple[dict | None, str | None]:
        """批量解析（对象集/视图用）：逐个实体求值，失败不影响其它。"""
        fn = await db.get(OntologyFunction, function_id)
        if not fn:
            return None, "函数不存在"
        items = []
        for eid in entity_ids or []:
            entity = await db.get(Entity, eid)
            if not entity:
                items.append({"entity_id": eid, "name": "", "ok": False, "error": "实体不存在"})
                continue
            res = await FunctionService._run(db, fn, entity, params or {}, None, "batch")
            items.append({
                "entity_id": eid, "name": entity.name,
                "ok": bool(res.get("success")),
                "value": res.get("data"),
                "error": res.get("error"),
            })
        return {"function_id": fn.id, "code": fn.code, "items": items}, None


def _purge_cache(function_id: str) -> None:
    for k in [k for k in _CACHE if k.startswith(f"{function_id}:")]:
        _CACHE.pop(k, None)


class DerivedPropertyService:
    """派生属性：函数型实时计算 / 图指标回填 / 物化写入实体属性。"""

    @staticmethod
    async def list_for_ontology(db: AsyncSession, ontology_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyDerivedProperty)
            .where(OntologyDerivedProperty.ontology_id == ontology_id)
            .order_by(OntologyDerivedProperty.sort_order, OntologyDerivedProperty.name)
        )).scalars().all()
        return [_serialize_derived(d) for d in rows]

    @staticmethod
    async def list_all(db: AsyncSession, ontology_id: str = "") -> list[dict]:
        """跨本体列出派生属性（可选按本体过滤），附带本体名称，供全局列表页使用。"""
        stmt = select(OntologyDerivedProperty, Ontology.name).join(
            Ontology, Ontology.id == OntologyDerivedProperty.ontology_id, isouter=True
        )
        if ontology_id:
            stmt = stmt.where(OntologyDerivedProperty.ontology_id == ontology_id)
        stmt = stmt.order_by(
            Ontology.name, OntologyDerivedProperty.sort_order, OntologyDerivedProperty.name
        )
        
        rows = (await db.execute(stmt)).all()
        return [{**_serialize_derived(dp), "ontology_name": onto_name or ""} for dp, onto_name in rows]

    @staticmethod
    async def create(db: AsyncSession, ontology_id: str, req) -> tuple[dict | None, str | None]:
        name = (req.name or "").strip()
        code = (req.code or "").strip()
        if not name or not code:
            return None, "名称与编码必填"
        dup = (await db.execute(
            select(OntologyDerivedProperty).where(
                OntologyDerivedProperty.ontology_id == ontology_id,
                OntologyDerivedProperty.code == code,
            )
        )).scalar_one_or_none()
        if dup:
            return None, f"编码 {code} 已存在"
        if req.source_kind == "function" and not (req.function_id or "").strip():
            return None, "函数型派生属性必须指定函数"
        dp = OntologyDerivedProperty(
            ontology_id=ontology_id, name=name, code=code,
            data_type=req.data_type or "number",
            source_kind=req.source_kind or "function",
            function_id=(req.function_id or "").strip(),
            graph_metric=(req.graph_metric or "").strip(),
            params=_dump_json(req.params or {}),
            materialize_mode=req.materialize_mode or "virtual",
            is_enabled=int(bool(req.is_enabled)),
            sort_order=req.sort_order or 0,
            created_at=_now(),
        )
        db.add(dp)
        await db.commit()
        await db.refresh(dp)
        return _serialize_derived(dp), None

    @staticmethod
    async def update(db: AsyncSession, prop_id: str, req) -> tuple[dict | None, str | None]:
        dp = await db.get(OntologyDerivedProperty, prop_id)
        if not dp:
            return None, "派生属性不存在"
        for field in ("name", "code", "data_type", "source_kind", "function_id",
                      "graph_metric", "materialize_mode", "sort_order"):
            value = getattr(req, field, None)
            if value is not None:
                setattr(dp, field, value.strip() if isinstance(value, str) else value)
        if req.params is not None:
            dp.params = _dump_json(req.params)
        if req.is_enabled is not None:
            dp.is_enabled = int(bool(req.is_enabled))
        await db.commit()
        await db.refresh(dp)
        return _serialize_derived(dp), None

    @staticmethod
    async def delete(db: AsyncSession, prop_id: str) -> bool:
        dp = await db.get(OntologyDerivedProperty, prop_id)
        if not dp:
            return False
        await db.delete(dp)
        await db.commit()
        return True

    @staticmethod
    async def _graph_metric_value(db: AsyncSession, dp: OntologyDerivedProperty, entity: Entity) -> object | None:
        """从最近的图分析任务结果里取该实体的指标值（不依赖图库直连）。"""
        algo = _GRAPH_METRIC_ALGO.get(dp.graph_metric or "", dp.graph_metric or "")
        if not algo:
            return None
        row = (await db.execute(
            select(GraphAnalysisTask)
            .where(
                GraphAnalysisTask.category_id == _entity_category(db, entity),
                GraphAnalysisTask.algorithm == algo,
                GraphAnalysisTask.status == "done",
            )
            .order_by(GraphAnalysisTask.finished_at.desc(), GraphAnalysisTask.created_at.desc())
        )).scalars().first()
        if not row:
            return None
        results = _load_json(row.results, None)
        if not isinstance(results, list):
            return None
        for item in results:
            if not isinstance(item, dict):
                continue
            if str(item.get("entity_id") or "") == entity.id or str(item.get("name") or "") == entity.name:
                for key in ("score", "value", "size", dp.graph_metric, algo):
                    if key in item:
                        return item[key]
                return None
        return None

    @staticmethod
    async def resolve_value(
        db: AsyncSession, dp: OntologyDerivedProperty, entity: Entity,
        params_override: dict | None = None,
    ) -> dict:
        """计算实体上某个派生属性的当前值。"""
        if not dp.is_enabled:
            return {"ok": False, "value": None, "error": "派生属性已停用"}
        if dp.source_kind == "graph_metric":
            value = await DerivedPropertyService._graph_metric_value(db, dp, entity)
            return {"ok": value is not None, "value": value,
                    "error": None if value is not None else "暂无图计算结果"}
        fn = await db.get(OntologyFunction, dp.function_id)
        if not fn:
            return {"ok": False, "value": None, "error": "关联函数不存在"}
        params = params_override if params_override is not None else _load_json(dp.params, {})
        res = await FunctionService._run(db, fn, entity, params, None, "derived")
        return {
            "ok": bool(res.get("success")),
            "value": res.get("data"),
            "error": res.get("error"),
            "cached": bool(res.get("cached")),
        }

    @staticmethod
    async def resolve_for_entity(
        db: AsyncSession, entity_id: str, refresh: bool = False
    ) -> list[dict]:
        """实体详情页：列出该实体所属本体的全部派生属性及当前值。

        默认（refresh=False）只读 entities.properties 里的存储值（物化/测试写入的结果），
        不触发函数执行；refresh=True 时逐项实时计算，并返回 stored/stale 供前端对比标注。
        """
        entity = await db.get(Entity, entity_id)
        if not entity:
            return []
        rows = (await db.execute(
            select(OntologyDerivedProperty)
            .where(OntologyDerivedProperty.ontology_id == entity.ontology_id)
            .order_by(OntologyDerivedProperty.sort_order)
        )).scalars().all()
        props = _load_json(entity.properties, {}) or {}
        out = []
        for dp in rows:
            stored = props.get(dp.code)
            if not refresh:
                out.append({
                    **_serialize_derived(dp),
                    "value": stored,
                    "ok": stored is not None,
                    "error": None if stored is not None else "尚未写入存储值，可点击「刷新计算」实时试算",
                    "source": "stored",
                })
                continue
            computed = await DerivedPropertyService.resolve_value(db, dp, entity)
            val = computed.get("value")
            out.append({
                **_serialize_derived(dp),
                "value": val if computed.get("ok") else stored,
                "ok": bool(computed.get("ok")) or stored is not None,
                "error": computed.get("error"),
                "stored": stored,
                "stale": bool(computed.get("ok")) and val != stored,
                "source": "computed",
            })
        return out

    @staticmethod
    async def materialize(db: AsyncSession, prop_id: str, limit: int = 1000) -> tuple[dict | None, str | None]:
        """物化：把派生属性值写入 entities.properties（键 = 派生属性 code）。"""
        dp = await db.get(OntologyDerivedProperty, prop_id)
        if not dp:
            return None, "派生属性不存在"
        entities = (await db.execute(
            select(Entity).where(Entity.ontology_id == dp.ontology_id).limit(limit)
        )).scalars().all()
        ok = fail = 0
        for ent in entities:
            computed = await DerivedPropertyService.resolve_value(db, dp, ent)
            if not computed.get("ok"):
                fail += 1
                continue
            props = _load_json(ent.properties, {}) or {}
            props[dp.code] = computed.get("value")
            ent.properties = _dump_json(props)
            ent.updated_at = _now()
            ok += 1
        dp.materialize_mode = "materialized"
        dp.last_materialized_at = _now()
        await db.commit()
        return {"property_id": dp.id, "updated": ok, "failed": fail,
                "last_materialized_at": dp.last_materialized_at}, None

    @staticmethod
    async def test_run(db: AsyncSession, prop_id: str, req) -> tuple[dict | None, str | None]:
        """测试派生属性：对单个真实实体试算，可选把结果写入实体属性（相当于调用一次接口）。"""
        dp = await db.get(OntologyDerivedProperty, prop_id)
        if not dp:
            return None, "派生属性不存在"
        entity = await db.get(Entity, req.entity_id)
        if not entity:
            return None, "实体不存在"
        if entity.ontology_id != dp.ontology_id:
            return None, "该实体不属于派生属性所在本体，请重新选择"

        stored_before = (_load_json(entity.properties, {}) or {}).get(dp.code)
        # 入参：派生属性已存参数为底，测试时动态填写的参数覆盖
        merged_params = {**_load_json(dp.params, {}), **(req.params or {})}
        computed = await DerivedPropertyService.resolve_value(db, dp, entity, merged_params)
        written = False
        if req.write and computed.get("ok"):
            props = _load_json(entity.properties, {}) or {}
            props[dp.code] = computed.get("value")
            entity.properties = _dump_json(props)
            entity.updated_at = _now()
            dp.params = _dump_json(merged_params)  # 测试确定的入参固化为该派生属性的运行参数
            await db.commit()
            written = True
        await _log_invocation(
            db, kind="dp_test", ref_id=dp.id, entity_id=entity.id,
            params=merged_params,
            result={"value": computed.get("value"), "written": written},
            status="success" if computed.get("ok") else "failed",
            error=computed.get("error"), triggered_by="manual",
        )
        return {
            "property_id": dp.id, "property_name": dp.name, "code": dp.code,
            "entity_id": entity.id, "entity_name": entity.name,
            "ok": bool(computed.get("ok")), "value": computed.get("value"),
            "stored_before": stored_before, "written": written,
            "cached": bool(computed.get("cached")),
            "error": computed.get("error"),
        }, None


async def _entity_category(db: AsyncSession, entity: Entity) -> str:
    from models import Ontology  # 局部导入避免循环
    row = await db.execute(select(Ontology.category_id).where(Ontology.id == entity.ontology_id))
    return row.scalar_one_or_none() or ""
