"""本体服务（动作）定义层：本体级通用动作 + 实体级个性化动作。

继承机制（参照 get_merged_attributes 范式）：
- 实体有效服务集 = 本体(owner_type='ontology', ontology_id=实体所属本体)的服务
  + 实体(owner_type='entity', entity_id=实体)的服务；
- 同 code 的实体服务覆盖本体服务（source='entity_override'）。
"""

from __future__ import annotations

import json

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, OntologyService
from services.service_runtime import coerce_params, execute_service


PARAM_TYPES = {"string", "number", "boolean", "date", "datetime", "text"}
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120


def _parse_params(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append({
            "name": str(item.get("name", "")),
            "label": str(item.get("label") or item.get("name", "")),
            "type": item.get("type") if item.get("type") in PARAM_TYPES else "string",
            "required": bool(item.get("required", False)),
            "default": item.get("default"),
            "description": str(item.get("description") or ""),
        })
    return out


def serialize_service(svc: OntologyService, *, source: str | None = None) -> dict:
    return {
        "id": svc.id,
        "owner_type": svc.owner_type,
        "ontology_id": svc.ontology_id,
        "entity_id": svc.entity_id,
        "name": svc.name,
        "code": svc.code,
        "description": svc.description or "",
        "params": _parse_params(svc.params_schema),
        "code_text": svc.code_text or "",
        "language": svc.language,
        "timeout_seconds": svc.timeout_seconds,
        "is_enabled": bool(svc.is_enabled),
        "sort_order": svc.sort_order,
        "source": source,  # ontology | entity | entity_override（有效服务集时给出）
        "created_at": svc.created_at,
        "updated_at": svc.updated_at,
    }


class OntologyServiceService:
    """本体服务 CRUD 与继承解析。"""

    # ===== 查询 =====

    @staticmethod
    async def get(db: AsyncSession, service_id: str) -> OntologyService | None:
        row = await db.execute(select(OntologyService).where(OntologyService.id == service_id))
        return row.scalar_one_or_none()

    @staticmethod
    async def list_for_ontology(db: AsyncSession, ontology_id: str) -> list[dict]:
        """本体编辑器：仅本体自有服务（含停用）。"""
        row = await db.execute(
            select(OntologyService)
            .where(
                (OntologyService.owner_type == "ontology")
                & (OntologyService.ontology_id == ontology_id)
            )
            .order_by(OntologyService.sort_order, OntologyService.created_at)
        )
        return [serialize_service(s) for s in row.scalars().all()]

    @staticmethod
    async def get_effective_services(db: AsyncSession, entity: Entity) -> list[dict]:
        """实体有效服务集：本体服务 + 实体服务，同 code 实体覆盖本体。"""
        row = await db.execute(
            select(OntologyService)
            .where(
                (
                    ((OntologyService.owner_type == "ontology") & (OntologyService.ontology_id == entity.ontology_id))
                    | ((OntologyService.owner_type == "entity") & (OntologyService.entity_id == entity.id))
                )
                & (OntologyService.is_enabled == 1)
            )
            .order_by(OntologyService.sort_order, OntologyService.created_at)
        )
        services = row.scalars().all()
        merged: dict[str, dict] = {}
        ontology_codes: set[str] = set()
        for svc in services:
            if svc.owner_type == "ontology":
                ontology_codes.add(svc.code)
                merged[svc.code] = serialize_service(svc, source="ontology")
        for svc in services:
            if svc.owner_type == "entity":
                source = "entity_override" if svc.code in ontology_codes else "entity"
                merged[svc.code] = serialize_service(svc, source=source)
        return list(merged.values())

    @staticmethod
    async def resolve_effective(
        db: AsyncSession, entity: Entity, service_id: str
    ) -> tuple[OntologyService, str] | None:
        """校验服务属于该实体的有效服务集，返回 (服务, source)。"""
        svc = await OntologyServiceService.get(db, service_id)
        if not svc or not svc.is_enabled:
            return None
        if svc.owner_type == "entity" and svc.entity_id == entity.id:
            return svc, "entity"
        if svc.owner_type == "ontology" and svc.ontology_id == entity.ontology_id:
            # 实体自定义同 code 服务存在时，本体版本被覆盖、不可直接调用
            row = await db.execute(
                select(OntologyService.id).where(
                    (OntologyService.owner_type == "entity")
                    & (OntologyService.entity_id == entity.id)
                    & (OntologyService.code == svc.code)
                    & (OntologyService.is_enabled == 1)
                )
            )
            if row.scalar_one_or_none() is not None:
                return None
            return svc, "ontology"
        return None

    # ===== 创建 / 更新 / 删除 =====

    @staticmethod
    def _validate(req) -> str | None:
        """返回错误信息；None 表示通过。"""
        if not (req.name or "").strip():
            return "服务名称不能为空"
        if not (req.code or "").strip():
            return "动作标识(code)不能为空"
        if " " in req.code.strip():
            return "动作标识不能包含空格"
        if not (req.code_text or "").strip():
            return "代码不能为空"
        if req.language != "python":
            return f"暂不支持 {req.language}，当前仅支持 python"
        for p in req.params:
            if p.type not in PARAM_TYPES:
                return f"参数 {p.name} 类型无效：{p.type}"
        return None

    @staticmethod
    async def _code_conflicts(
        db: AsyncSession,
        *,
        owner_type: str,
        ontology_id: str,
        entity_id: str | None,
        code: str,
        exclude_id: str | None = None,
    ) -> bool:
        cond = (
            (OntologyService.owner_type == owner_type)
            & (OntologyService.code == code)
        )
        if owner_type == "ontology":
            cond &= OntologyService.ontology_id == ontology_id
        else:
            cond &= OntologyService.entity_id == (entity_id or "")
        if exclude_id:
            cond &= OntologyService.id != exclude_id
        row = await db.execute(select(OntologyService.id).where(cond).limit(1))
        return row.scalar_one_or_none() is not None

    @staticmethod
    async def create(
        db: AsyncSession,
        *,
        owner_type: str,
        ontology_id: str,
        entity_id: str | None,
        req,
    ) -> tuple[dict | None, str | None]:
        err = OntologyServiceService._validate(req)
        if err:
            return None, err
        code = req.code.strip()
        if await OntologyServiceService._code_conflicts(
            db, owner_type=owner_type, ontology_id=ontology_id,
            entity_id=entity_id, code=code,
        ):
            scope = "该本体" if owner_type == "ontology" else "该实体"
            return None, f"动作标识 {code} 在{scope}下已存在"
        svc = OntologyService(
            owner_type=owner_type,
            ontology_id=ontology_id,
            entity_id=entity_id,
            name=req.name.strip(),
            code=code,
            description=(req.description or "").strip(),
            params_schema=json.dumps([p.model_dump() for p in req.params], ensure_ascii=False),
            code_text=req.code_text,
            language=req.language or "python",
            timeout_seconds=max(1, min(MAX_TIMEOUT, req.timeout_seconds or DEFAULT_TIMEOUT)),
            is_enabled=1 if req.is_enabled else 0,
            sort_order=req.sort_order or 0,
        )
        db.add(svc)
        await db.commit()
        return serialize_service(svc), None

    @staticmethod
    async def update(db: AsyncSession, service_id: str, req) -> tuple[dict | None, str | None]:
        svc = await OntologyServiceService.get(db, service_id)
        if not svc:
            return None, "服务不存在"
        err = OntologyServiceService._validate(req)
        if err:
            return None, err
        code = req.code.strip()
        if await OntologyServiceService._code_conflicts(
            db, owner_type=svc.owner_type, ontology_id=svc.ontology_id,
            entity_id=svc.entity_id, code=code, exclude_id=svc.id,
        ):
            scope = "该本体" if svc.owner_type == "ontology" else "该实体"
            return None, f"动作标识 {code} 在{scope}下已存在"
        svc.name = req.name.strip()
        svc.code = code
        svc.description = (req.description or "").strip()
        svc.params_schema = json.dumps([p.model_dump() for p in req.params], ensure_ascii=False)
        svc.code_text = req.code_text
        svc.language = req.language or "python"
        svc.timeout_seconds = max(1, min(MAX_TIMEOUT, req.timeout_seconds or DEFAULT_TIMEOUT))
        svc.is_enabled = 1 if req.is_enabled else 0
        svc.sort_order = req.sort_order or 0
        await db.commit()
        return serialize_service(svc), None

    @staticmethod
    async def delete(db: AsyncSession, service_id: str) -> bool:
        svc = await OntologyServiceService.get(db, service_id)
        if not svc:
            return False
        await db.delete(svc)
        await db.commit()
        return True

    @staticmethod
    async def copy_to_entity(db: AsyncSession, entity: Entity, service_id: str) -> tuple[dict | None, str | None]:
        """把（本体/其他）服务复制为该实体的自定义服务，作为覆盖起点。"""
        svc = await OntologyServiceService.get(db, service_id)
        if not svc:
            return None, "服务不存在"
        code = svc.code
        if await OntologyServiceService._code_conflicts(
            db, owner_type="entity", ontology_id=entity.ontology_id,
            entity_id=entity.id, code=code,
        ):
            return None, f"实体已有同标识 {code} 的自定义服务"
        from datetime import datetime

        clone = OntologyService(
            owner_type="entity",
            ontology_id=entity.ontology_id,
            entity_id=entity.id,
            name=svc.name,
            code=code,
            description=svc.description,
            params_schema=svc.params_schema,
            code_text=svc.code_text,
            language=svc.language,
            timeout_seconds=svc.timeout_seconds,
            is_enabled=svc.is_enabled,
            sort_order=svc.sort_order,
        )
        db.add(clone)
        await db.commit()
        return serialize_service(clone, source="entity"), None

    # ===== 级联清理（本体/实体删除时调用）=====

    @staticmethod
    async def delete_for_ontology(db: AsyncSession, ontology_id: str) -> None:
        await db.execute(
            delete(OntologyService).where(
                (OntologyService.owner_type == "ontology")
                & (OntologyService.ontology_id == ontology_id)
            )
        )

    @staticmethod
    async def delete_for_entities(db: AsyncSession, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        await db.execute(
            delete(OntologyService).where(
                (OntologyService.owner_type == "entity")
                & (OntologyService.entity_id.in_(entity_ids))
            )
        )


class ServiceRuntimeService:
    """服务执行编排：本体级测试运行 + 实体调用。"""

    @staticmethod
    def _entity_payload(entity: Entity | None, mock_entity: dict | None = None) -> dict:
        if entity is None:
            # 本体级测试运行：可用 mock_entity 模拟
            props = (mock_entity or {}).get("properties") or {}
            return {
                "id": None,
                "name": (mock_entity or {}).get("name") or "",
                "entity_type": (mock_entity or {}).get("entity_type") or "",
                "ontology_id": None,
                "properties": props if isinstance(props, dict) else {},
            }
        try:
            props = json.loads(entity.properties) if entity.properties else {}
        except (TypeError, ValueError):
            props = {}
        return {
            "id": entity.id,
            "name": entity.name,
            "entity_type": entity.entity_type,
            "ontology_id": entity.ontology_id,
            "properties": props if isinstance(props, dict) else {},
        }

    @staticmethod
    async def test_run(db: AsyncSession, service_id: str, req) -> tuple[dict | None, str | None]:
        """本体级/实体级服务的测试运行。返回 (执行结果, 错误)。"""
        svc = await OntologyServiceService.get(db, service_id)
        if not svc:
            return None, "服务不存在"
        if not svc.is_enabled:
            return None, "服务已停用"
        params, perr = coerce_params(_parse_params(svc.params_schema), req.params or {})
        if perr:
            return None, perr
        result = await execute_service(
            code_text=svc.code_text,
            language=svc.language,
            params=params,
            entity=ServiceRuntimeService._entity_payload(None, req.mock_entity),
            context={"service_code": svc.code, "triggered_by": "test"},
            timeout_seconds=svc.timeout_seconds,
        )
        return result, None

    @staticmethod
    async def invoke(db: AsyncSession, entity_id: str, service_id: str, params_raw: dict) -> tuple[dict | None, str | None]:
        """实体调用有效服务集中的动作。返回 (执行结果, 错误)。"""
        row = await db.execute(select(Entity).where(Entity.id == entity_id))
        entity = row.scalar_one_or_none()
        if not entity:
            return None, "实体不存在"
        resolved = await OntologyServiceService.resolve_effective(db, entity, service_id)
        if not resolved:
            return None, "服务不存在或不属于该实体的有效服务集"
        svc, _source = resolved
        params, perr = coerce_params(_parse_params(svc.params_schema), params_raw or {})
        if perr:
            return None, perr
        result = await execute_service(
            code_text=svc.code_text,
            language=svc.language,
            params=params,
            entity=ServiceRuntimeService._entity_payload(entity),
            context={"kb_id": entity.kb_id, "service_code": svc.code, "triggered_by": "entity"},
            timeout_seconds=svc.timeout_seconds,
        )
        return result, None
