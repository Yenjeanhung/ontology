"""本体服务（动作）路由：本体级 CRUD + 实体继承/自定义/调用。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Entity, Ontology
from schemas import (
    InvokeEntityServiceRequest,
    SaveOntologyServiceRequest,
    TestOntologyServiceRequest,
)
from services.ontology_action_service import (
    OntologyServiceService,
    ServiceRuntimeService,
)

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _bad_request(detail: str):
    return HTTPException(status_code=400, detail=detail)


async def _ensure_ontology(db: AsyncSession, ontology_id: str) -> Ontology:
    row = await db.execute(select(Ontology).where(Ontology.id == ontology_id))
    ont = row.scalar_one_or_none()
    if not ont:
        raise _nf("Ontology not found")
    return ont


async def _ensure_entity(db: AsyncSession, entity_id: str) -> Entity:
    row = await db.execute(select(Entity).where(Entity.id == entity_id))
    ent = row.scalar_one_or_none()
    if not ent:
        raise _nf("Entity not found")
    return ent


# ===== 本体服务（本体编辑器）=====

@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/services")
async def list_ontology_services(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    await _ensure_ontology(db, ontology_id)
    return await OntologyServiceService.list_for_ontology(db, ontology_id)


@router.post("/ontology-categories/{category_id}/ontologies/{ontology_id}/services")
async def create_ontology_service(
    category_id: str,
    ontology_id: str,
    req: SaveOntologyServiceRequest,
    db: AsyncSession = Depends(get_db),
):
    await _ensure_ontology(db, ontology_id)
    svc, err = await OntologyServiceService.create(
        db, owner_type="ontology", ontology_id=ontology_id, entity_id=None, req=req
    )
    if err:
        raise _bad_request(err)
    return svc


@router.put("/ontology-services/{service_id}")
async def update_ontology_service(
    service_id: str, req: SaveOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    svc, err = await OntologyServiceService.update(db, service_id, req)
    if err == "服务不存在":
        raise _nf(err)
    if err:
        raise _bad_request(err)
    return svc


@router.delete("/ontology-services/{service_id}")
async def delete_ontology_service(service_id: str, db: AsyncSession = Depends(get_db)):
    if not await OntologyServiceService.delete(db, service_id):
        raise _nf("服务不存在")
    return {"status": "deleted"}


@router.post("/ontology-services/{service_id}/test")
async def test_ontology_service(
    service_id: str, req: TestOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    result, err = await ServiceRuntimeService.test_run(db, service_id, req)
    if err:
        raise _bad_request(err)
    return result


# ===== 实体服务（实体详情页：继承 + 自定义 + 调用）=====

@router.get("/entities/{entity_id}/services")
async def list_entity_services(entity_id: str, db: AsyncSession = Depends(get_db)):
    ent = await _ensure_entity(db, entity_id)
    return await OntologyServiceService.get_effective_services(db, ent)


@router.post("/entities/{entity_id}/services")
async def create_entity_service(
    entity_id: str, req: SaveOntologyServiceRequest, db: AsyncSession = Depends(get_db)
):
    ent = await _ensure_entity(db, entity_id)
    svc, err = await OntologyServiceService.create(
        db, owner_type="entity", ontology_id=ent.ontology_id, entity_id=ent.id, req=req
    )
    if err:
        raise _bad_request(err)
    return svc


@router.post("/entities/{entity_id}/services/{service_id}/invoke")
async def invoke_entity_service(
    entity_id: str,
    service_id: str,
    req: InvokeEntityServiceRequest,
    db: AsyncSession = Depends(get_db),
):
    result, err = await ServiceRuntimeService.invoke(db, entity_id, service_id, req.params)
    if err:
        raise _bad_request(err)
    return result


@router.post("/entities/{entity_id}/services/{service_id}/copy")
async def copy_service_to_entity(
    entity_id: str, service_id: str, db: AsyncSession = Depends(get_db)
):
    """把本体服务复制为该实体的自定义服务（覆盖起点）。"""
    ent = await _ensure_entity(db, entity_id)
    svc, err = await OntologyServiceService.copy_to_entity(db, ent, service_id)
    if err:
        raise _bad_request(err)
    return svc
