"""S4（P1-1）动作补强：规则（precondition/validation/post）+ 副作用 + 执行记录 + 撤销 + 批量。

设计来源：《本体能力对标Palantir_补强设计》§4.4。

要点
====
- 规则表达式复用工作流引擎的规则树求值 ``services.workflow_engine._eval_rule_tree``，
  避免第二套语法；也支持 ``{kind:'python', code}`` 的表达式（走同一沙箱）。
- 撤销仅对写回型副作用（update_property / create_relation）与声明式 edits 生效：
  执行前存前像到 ``undo_payload``，撤销时回滚。
- 批量调用逐条执行并汇总成功/失败，单条失败不影响其它。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Entity,
    OntologyServiceEffect,
    OntologyServiceInvocation,
    OntologyServiceRule,
    Relation,
)
from services.ontology_action_service import (
    OntologyServiceService,
    ServiceRuntimeService,
)
from services.service_runtime import coerce_params, execute_service

RESULT_SNIPPET = 8 * 1024
RULE_TYPES = {"precondition", "validation", "post"}
EFFECT_TYPES = {"notify", "webhook", "update_property", "create_relation"}


def _now() -> str:
    return datetime.now().isoformat()


def _load_json(raw: str | None, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _dump_json(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _entity_props(entity: Entity | None) -> dict:
    return _load_json(entity.properties, {}) or {} if entity else {}


def _entity_payload(entity: Entity | None) -> dict:
    return ServiceRuntimeService._entity_payload(entity)


def serialize_rule(r: OntologyServiceRule) -> dict:
    return {
        "id": r.id,
        "service_id": r.service_id,
        "rule_type": r.rule_type,
        "expression": _load_json(r.expression, {}),
        "error_message": r.error_message or "",
        "sort_order": r.sort_order,
        "is_enabled": bool(r.is_enabled),
    }


def serialize_effect(e: OntologyServiceEffect) -> dict:
    return {
        "id": e.id,
        "service_id": e.service_id,
        "effect_type": e.effect_type,
        "config": _load_json(e.config, {}),
        "is_enabled": bool(e.is_enabled),
        "sort_order": e.sort_order,
    }


def serialize_invocation(i: OntologyServiceInvocation) -> dict:
    return {
        "id": i.id,
        "service_id": i.service_id,
        "entity_id": i.entity_id or "",
        "params": _load_json(i.params, {}),
        "result": _load_json(i.result, None) if i.result else None,
        "status": i.status,
        "error": i.error,
        "duration_ms": i.duration_ms,
        "can_undo": bool(i.undo_payload) and not i.undone_at,
        "triggered_by": i.triggered_by or "",
        "undone_at": i.undone_at or "",
        "created_at": i.created_at,
    }


async def _eval_rule(
    db: AsyncSession, rule: OntologyServiceRule, context: dict
) -> tuple[bool, str | None]:
    """求值单条规则。返回 (是否通过, 错误详情)。"""
    expr = _load_json(rule.expression, {})
    if isinstance(expr, dict) and expr.get("kind") == "python":
        res = await execute_service(
            code_text=expr.get("code") or "",
            language="python",
            params={},
            entity=context.get("entity") or {},
            context=context,
            timeout_seconds=10,
        )
        if not res.get("success"):
            return False, res.get("error") or "规则脚本执行失败"
        data = res.get("data")
        if isinstance(data, dict):
            ok = bool(data.get("ok", True))
            return ok, None if ok else (data.get("message") or rule.error_message or "规则未通过")
        return bool(data), None if data else (rule.error_message or "规则未通过")

    from services.workflow_engine import _eval_rule_tree  # 局部导入：规则树复用

    ok = _eval_rule_tree(expr if isinstance(expr, dict) else {}, context)
    return ok, None if ok else (rule.error_message or "规则未通过")


async def _apply_effect(
    db: AsyncSession, effect: OntologyServiceEffect, entity: Entity | None,
    result_data, undo: dict,
) -> dict:
    """执行单个副作用，并把可撤销信息写入 undo。"""
    cfg = _load_json(effect.config, {}) or {}
    etype = effect.effect_type

    if etype == "update_property":
        if not entity:
            return {"effect_type": etype, "ok": False, "error": "无实体上下文"}
        code = cfg.get("property_code") or ""
        if not code:
            return {"effect_type": etype, "ok": False, "error": "未配置 property_code"}
        props = _entity_props(entity)
        old = props.get(code)
        value = cfg.get("value")
        if value is None and isinstance(result_data, dict):
            value = (result_data or {}).get(code)
        props[code] = value
        entity.properties = _dump_json(props)
        entity.updated_at = _now()
        undo.setdefault("properties", {})[code] = old
        return {"effect_type": etype, "ok": True, "property_code": code, "old_value": old}

    if etype == "create_relation":
        if not entity:
            return {"effect_type": etype, "ok": False, "error": "无实体上下文"}
        result_data = result_data if isinstance(result_data, dict) else {}
        target_id = cfg.get("target_entity_id") or result_data.get("target_entity_id") or ""
        if not target_id:
            return {"effect_type": etype, "ok": False, "error": "未配置 target_entity_id"}
        rel = Relation(
            kb_id=entity.kb_id,
            relation_def_id=cfg.get("relation_def_id") or "",
            relation_type=cfg.get("relation_type") or cfg.get("relation_def_id") or "",
            source_entity_id=cfg.get("direction") == "incoming" and target_id or entity.id,
            target_entity_id=cfg.get("direction") == "incoming" and entity.id or target_id,
            description=cfg.get("description") or "",
            created_at=_now(), updated_at=_now(),
        )
        db.add(rel)
        await db.flush()
        undo.setdefault("relations", []).append(rel.id)
        return {"effect_type": etype, "ok": True, "relation_id": rel.id}

    if etype == "webhook":
        url = cfg.get("url") or ""
        if not url:
            return {"effect_type": etype, "ok": False, "error": "未配置 url"}
        import requests  # noqa: WPS433（副作用执行允许网络）
        try:
            payload = cfg.get("payload") or {
                "entity": _entity_payload(entity),
                "result": result_data,
            }
            resp = requests.request(
                (cfg.get("method") or "POST").upper(), url,
                json=payload, headers=cfg.get("headers") or {}, timeout=15,
            )
            return {"effect_type": etype, "ok": resp.status_code < 400, "status_code": resp.status_code}
        except Exception as e:  # noqa: BLE001
            return {"effect_type": etype, "ok": False, "error": str(e)[:300]}

    if etype == "notify":
        # 通知副作用：写入执行记录的 effects 报告（当前通知中心为计数聚合型 SSE，
        # 无单条消息投递接口；title/content 由前端在执行记录里展示）。
        return {
            "effect_type": etype, "ok": True,
            "title": cfg.get("title") or "动作执行通知",
            "content": cfg.get("content") or "",
            "level": cfg.get("level") or "info",
        }

    return {"effect_type": etype, "ok": False, "error": f"不支持的副作用类型：{etype}"}


class ActionEnhanceService:
    """动作规则 / 副作用 / 执行记录 / 撤销 / 批量。"""

    # ===== 规则 =====

    @staticmethod
    async def list_rules(db: AsyncSession, service_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyServiceRule)
            .where(OntologyServiceRule.service_id == service_id)
            .order_by(OntologyServiceRule.rule_type, OntologyServiceRule.sort_order)
        )).scalars().all()
        return [serialize_rule(r) for r in rows]

    @staticmethod
    async def create_rule(db: AsyncSession, service_id: str, req) -> tuple[dict | None, str | None]:
        if req.rule_type not in RULE_TYPES:
            return None, f"规则类型须为 {'/'.join(sorted(RULE_TYPES))}"
        rule = OntologyServiceRule(
            service_id=service_id,
            rule_type=req.rule_type,
            expression=_dump_json(req.expression if isinstance(req.expression, (dict, list)) else {}),
            error_message=(req.error_message or "").strip(),
            sort_order=req.sort_order or 0,
            is_enabled=int(bool(req.is_enabled)),
        )
        db.add(rule)
        await db.commit()
        await db.refresh(rule)
        return serialize_rule(rule), None

    @staticmethod
    async def update_rule(db: AsyncSession, rule_id: str, req) -> tuple[dict | None, str | None]:
        rule = await db.get(OntologyServiceRule, rule_id)
        if not rule:
            return None, "规则不存在"
        if req.rule_type and req.rule_type in RULE_TYPES:
            rule.rule_type = req.rule_type
        if req.expression is not None:
            rule.expression = _dump_json(req.expression if isinstance(req.expression, (dict, list)) else {})
        if req.error_message is not None:
            rule.error_message = req.error_message.strip()
        if req.sort_order is not None:
            rule.sort_order = req.sort_order
        if req.is_enabled is not None:
            rule.is_enabled = int(bool(req.is_enabled))
        await db.commit()
        await db.refresh(rule)
        return serialize_rule(rule), None

    @staticmethod
    async def delete_rule(db: AsyncSession, rule_id: str) -> bool:
        rule = await db.get(OntologyServiceRule, rule_id)
        if not rule:
            return False
        await db.delete(rule)
        await db.commit()
        return True

    # ===== 副作用 =====

    @staticmethod
    async def list_effects(db: AsyncSession, service_id: str) -> list[dict]:
        rows = (await db.execute(
            select(OntologyServiceEffect)
            .where(OntologyServiceEffect.service_id == service_id)
            .order_by(OntologyServiceEffect.sort_order)
        )).scalars().all()
        return [serialize_effect(e) for e in rows]

    @staticmethod
    async def create_effect(db: AsyncSession, service_id: str, req) -> tuple[dict | None, str | None]:
        if req.effect_type not in EFFECT_TYPES:
            return None, f"副作用类型须为 {'/'.join(sorted(EFFECT_TYPES))}"
        effect = OntologyServiceEffect(
            service_id=service_id,
            effect_type=req.effect_type,
            config=_dump_json(req.config or {}),
            is_enabled=int(bool(req.is_enabled)),
            sort_order=req.sort_order or 0,
        )
        db.add(effect)
        await db.commit()
        await db.refresh(effect)
        return serialize_effect(effect), None

    @staticmethod
    async def update_effect(db: AsyncSession, effect_id: str, req) -> tuple[dict | None, str | None]:
        effect = await db.get(OntologyServiceEffect, effect_id)
        if not effect:
            return None, "副作用不存在"
        if req.effect_type and req.effect_type in EFFECT_TYPES:
            effect.effect_type = req.effect_type
        if req.config is not None:
            effect.config = _dump_json(req.config)
        if req.sort_order is not None:
            effect.sort_order = req.sort_order
        if req.is_enabled is not None:
            effect.is_enabled = int(bool(req.is_enabled))
        await db.commit()
        await db.refresh(effect)
        return serialize_effect(effect), None

    @staticmethod
    async def delete_effect(db: AsyncSession, effect_id: str) -> bool:
        effect = await db.get(OntologyServiceEffect, effect_id)
        if not effect:
            return False
        await db.delete(effect)
        await db.commit()
        return True

    # ===== 增强执行：规则 → 沙箱 → 声明式写回 → 副作用 → 记录 =====

    @staticmethod
    async def invoke(
        db: AsyncSession, entity_id: str, service_id: str, params_raw: dict,
        triggered_by: str = "user",
    ) -> tuple[dict | None, str | None]:
        entity = await db.get(Entity, entity_id)
        if not entity:
            return None, "实体不存在"
        resolved = await OntologyServiceService.resolve_effective(db, entity, service_id)
        if not resolved:
            return None, "服务不存在或不属于该实体的有效服务集"
        svc, _source = resolved
        if not svc.is_enabled:
            return None, "服务已停用"

        params, perr = coerce_params(
            json.loads(svc.params_schema) if svc.params_schema else [], params_raw or {}
        )
        if perr:
            return None, perr

        entity_payload = _entity_payload(entity)
        context = {
            "kb_id": entity.kb_id,
            "service_code": svc.code,
            "triggered_by": triggered_by,
            "entity": entity_payload,
            "params": params,
            "properties": entity_payload.get("properties") or {},
        }

        # 1) 校验类规则（precondition / validation）
        rule_results = []
        for rule in await ActionEnhanceService._rules_of(db, svc.id, ("precondition", "validation")):
            ok, err = await _eval_rule(db, rule, context)
            rule_results.append({"id": rule.id, "rule_type": rule.rule_type, "ok": ok, "error": err})
            if not ok:
                await ActionEnhanceService._log(
                    db, svc.id, entity_id, params, None, "error",
                    f"[{rule.rule_type}] {err}", 0, triggered_by, None,
                )
                return {
                    "success": False,
                    "data": None,
                    "error": err,
                    "blocked_by": rule.rule_type,
                    "rule_results": rule_results,
                    "duration_ms": 0,
                }, None

        # 2) 执行动作本体（code：Python 沙箱；flow：函数编排）
        if (svc.execution_mode or "code") == "flow":
            from services.action_flow_service import ActionFlowService

            result = await ActionFlowService.run(
                db, svc, entity, entity_payload, params, triggered_by=triggered_by)
        else:
            result = await execute_service(
                code_text=svc.code_text, language=svc.language,
                params=params, entity=entity_payload, context=context,
                timeout_seconds=svc.timeout_seconds,
            )
        duration = int(result.get("duration_ms") or 0)
        undo: dict = {}

        # 3) 声明式写回 edits（set_property / add_relation），由后端统一落库
        if result.get("success"):
            edits = (result.get("data") or {}).get("edits") if isinstance(result.get("data"), dict) else None
            for edit in edits or []:
                applied = await _apply_effect(
                    db,
                    OntologyServiceEffect(
                        service_id=svc.id,
                        effect_type="update_property" if edit.get("op") == "set_property" else "create_relation",
                        config=_dump_json(edit),
                        is_enabled=1, sort_order=0,
                    ),
                    entity, result.get("data"), undo,
                )
                result.setdefault("edits_applied", []).append(applied)

        # 4) 提交后规则（post）
        if result.get("success"):
            for rule in await ActionEnhanceService._rules_of(db, svc.id, ("post",)):
                ok, err = await _eval_rule(db, rule, context)
                rule_results.append({"id": rule.id, "rule_type": "post", "ok": ok, "error": err})

        # 5) 副作用
        effects_report = []
        if result.get("success"):
            for effect in await ActionEnhanceService._effects_of(db, svc.id):
                report = await _apply_effect(db, effect, entity, result.get("data"), undo)
                effects_report.append(report)

        await db.commit()

        inv_id = await ActionEnhanceService._log(
            db, svc.id, entity_id, params, result.get("data"),
            "success" if result.get("success") else ("timeout" if "超时" in (result.get("error") or "") else "error"),
            result.get("error"), duration, triggered_by, undo or None,
        )

        return {
            **result,
            "rule_results": rule_results,
            "effects": effects_report,
            "invocation_id": inv_id,
            "can_undo": bool(undo),
        }, None

    @staticmethod
    async def _rules_of(db: AsyncSession, service_id: str, types: tuple[str, ...]):
        rows = (await db.execute(
            select(OntologyServiceRule)
            .where(
                OntologyServiceRule.service_id == service_id,
                OntologyServiceRule.rule_type.in_(types),
                OntologyServiceRule.is_enabled == 1,
            )
            .order_by(OntologyServiceRule.sort_order)
        )).scalars().all()
        return list(rows)

    @staticmethod
    async def _effects_of(db: AsyncSession, service_id: str):
        rows = (await db.execute(
            select(OntologyServiceEffect)
            .where(
                OntologyServiceEffect.service_id == service_id,
                OntologyServiceEffect.is_enabled == 1,
            )
            .order_by(OntologyServiceEffect.sort_order)
        )).scalars().all()
        return list(rows)

    @staticmethod
    async def _log(
        db: AsyncSession, service_id: str, entity_id: str, params: dict,
        result, status: str, error: str | None, duration_ms: int,
        triggered_by: str, undo: dict | None,
    ) -> str:
        inv = OntologyServiceInvocation(
            service_id=service_id, entity_id=entity_id,
            params=_dump_json(params or {}),
            result=_dump_json(result)[:RESULT_SNIPPET] if result is not None else None,
            status=status, error=(error or "")[:4000] or None,
            duration_ms=duration_ms, triggered_by=triggered_by,
            undo_payload=_dump_json(undo) if undo else None,
            created_at=_now(),
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)
        return inv.id

    # ===== 执行记录 / 撤销 / 批量 =====

    @staticmethod
    async def list_invocations(db: AsyncSession, service_id: str, limit: int = 50) -> list[dict]:
        rows = (await db.execute(
            select(OntologyServiceInvocation)
            .where(OntologyServiceInvocation.service_id == service_id)
            .order_by(OntologyServiceInvocation.created_at.desc())
            .limit(max(1, min(200, limit)))
        )).scalars().all()
        return [serialize_invocation(i) for i in rows]

    @staticmethod
    async def undo(db: AsyncSession, invocation_id: str, undone_by: str = "user") -> tuple[dict | None, str | None]:
        inv = await db.get(OntologyServiceInvocation, invocation_id)
        if not inv:
            return None, "执行记录不存在"
        if inv.undone_at:
            return None, "该执行已撤销"
        undo = _load_json(inv.undo_payload, {}) or {}
        if not undo:
            return None, "该执行无撤销数据（非写回型动作）"

        entity = await db.get(Entity, inv.entity_id) if inv.entity_id else None
        restored_props = {}
        if entity and undo.get("properties"):
            props = _entity_props(entity)
            for code, old in undo["properties"].items():
                restored_props[code] = old
                if old is None:
                    props.pop(code, None)
                else:
                    props[code] = old
            entity.properties = _dump_json(props)
            entity.updated_at = _now()

        removed_relations = []
        for rid in undo.get("relations") or []:
            rel = await db.get(Relation, rid)
            if rel:
                await db.delete(rel)
                removed_relations.append(rid)

        inv.status = "undone"
        inv.undone_at = _now()
        inv.undone_by = undone_by
        await db.commit()
        return {
            "invocation_id": inv.id,
            "restored_properties": restored_props,
            "removed_relations": removed_relations,
            "undone_at": inv.undone_at,
        }, None

    @staticmethod
    async def batch_invoke(
        db: AsyncSession, service_id: str, entity_ids: list[str], params: dict,
        triggered_by: str = "batch",
    ) -> dict:
        ok, fail = 0, 0
        items = []
        for eid in entity_ids or []:
            res, err = await ActionEnhanceService.invoke(db, eid, service_id, params or {}, triggered_by)
            if err:
                fail += 1
                items.append({"entity_id": eid, "ok": False, "error": err})
            else:
                if res.get("success"):
                    ok += 1
                else:
                    fail += 1
                items.append({
                    "entity_id": eid, "ok": bool(res.get("success")),
                    "error": res.get("error"), "invocation_id": res.get("invocation_id"),
                })
        return {"service_id": service_id, "total": len(entity_ids or []),
                "succeeded": ok, "failed": fail, "items": items}
