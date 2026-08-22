"""实体/关系实例层 API。

资源路径与"定义层"严格区分：
- 实例层：`/api/entities`、`/api/relations`（抽取后的实体/关系数据）
- 定义层：`/api/ontology-categories/...`（本体类型与关系字典）

每次写入操作由 service 层 best-effort 同步到 Kùzu 图数据库。
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import (
    ApplyCleanupRequest,
    BatchDeleteRequest,
    CreateEntityRequest,
    CreateRelationRequest,
    MergeEntitiesRequest,
    UpdateEntityRequest,
    UpdateRelationRequest,
)
from services.entity_service import EntityService
from services.graph_cleanup_service import GraphCleanupService

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


# ===== 实体实例 =====

@router.get("/entities")
async def list_entities(
    kb_id: str | None = Query(default=None),
    ontology_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
    entity_type: str | None = Query(default=None),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await EntityService.list_entities(
        db,
        kb_id=kb_id,
        ontology_id=ontology_id,
        category_id=category_id,
        entity_type=entity_type,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/entities/stats")
async def entity_stats(
    kb_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    return await EntityService.stats(db, kb_id=kb_id)


@router.post("/entities/merge")
async def merge_entities(req: MergeEntitiesRequest, db: AsyncSession = Depends(get_db)):
    """把多个实体并入一个规范实体（重写关系端点 + 合并属性 + 删除冗余）。"""
    try:
        return await EntityService.merge_entities(
            db,
            canonical_id=req.canonical_id,
            merged_ids=req.merged_ids,
            kb_id=req.kb_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/entities/batch-delete")
async def batch_delete_entities(req: BatchDeleteRequest, db: AsyncSession = Depends(get_db)):
    deleted = await EntityService.delete_entities(db, req.ids)
    return {"deleted": deleted, "requested": len(req.ids)}


@router.post("/relations/batch-delete")
async def batch_delete_relations(req: BatchDeleteRequest, db: AsyncSession = Depends(get_db)):
    deleted = await EntityService.delete_relations(db, req.ids)
    return {"deleted": deleted, "requested": len(req.ids)}


@router.get("/entities/{entity_id}")
async def get_entity(entity_id: str, db: AsyncSession = Depends(get_db)):
    res = await EntityService.get_entity(db, entity_id)
    if not res:
        raise _nf("Entity not found")
    return res


@router.post("/entities")
async def create_entity(req: CreateEntityRequest, db: AsyncSession = Depends(get_db)):
    return await EntityService.create_entity(
        db,
        kb_id=req.kb_id,
        ontology_id=req.ontology_id,
        entity_type=req.entity_type,
        name=req.name,
        description=req.description or "",
        properties=req.properties,
    )


@router.put("/entities/{entity_id}")
async def update_entity(
    entity_id: str,
    req: UpdateEntityRequest,
    db: AsyncSession = Depends(get_db),
):
    res = await EntityService.update_entity(
        db,
        entity_id,
        name=req.name,
        description=req.description,
        properties=req.properties,
    )
    if not res:
        raise _nf("Entity not found")
    return res


@router.delete("/entities/{entity_id}")
async def delete_entity(entity_id: str, db: AsyncSession = Depends(get_db)):
    if not await EntityService.delete_entity(db, entity_id):
        raise _nf("Entity not found")
    return {"status": "deleted"}


# ===== 关系实例 =====

@router.get("/relations")
async def list_relations(
    kb_id: str | None = Query(default=None),
    relation_type: str | None = Query(default=None),
    relation_def_id: str | None = Query(default=None),
    entity_id: str | None = Query(default=None),
    q: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    return await EntityService.list_relations(
        db,
        kb_id=kb_id,
        relation_type=relation_type,
        relation_def_id=relation_def_id,
        entity_id=entity_id,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/relations/{relation_id}")
async def get_relation(relation_id: str, db: AsyncSession = Depends(get_db)):
    res = await EntityService.get_relation(db, relation_id)
    if not res:
        raise _nf("Relation not found")
    return res


@router.post("/relations")
async def create_relation(req: CreateRelationRequest, db: AsyncSession = Depends(get_db)):
    return await EntityService.create_relation(
        db,
        kb_id=req.kb_id,
        relation_def_id=req.relation_def_id,
        relation_type=req.relation_type,
        source_entity_id=req.source_entity_id,
        target_entity_id=req.target_entity_id,
        description=req.description or "",
    )


@router.put("/relations/{relation_id}")
async def update_relation(
    relation_id: str,
    req: UpdateRelationRequest,
    db: AsyncSession = Depends(get_db),
):
    res = await EntityService.update_relation(
        db,
        relation_id,
        relation_type=req.relation_type,
        description=req.description,
    )
    if not res:
        raise _nf("Relation not found")
    return res


@router.delete("/relations/{relation_id}")
async def delete_relation(relation_id: str, db: AsyncSession = Depends(get_db)):
    if not await EntityService.delete_relation(db, relation_id):
        raise _nf("Relation not found")
    return {"status": "deleted"}


# ===== 图谱清洗 =====


@router.get("/graph-cleanup/suggestions")
async def graph_cleanup_suggestions(kb_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    """对指定知识库给出清洗建议（合并组 / 待删实体 / 待删通用关系），纯只读。"""
    return await GraphCleanupService.suggest_cleanup(db, kb_id)


@router.post("/graph-cleanup/apply")
async def graph_cleanup_apply(req: ApplyCleanupRequest, db: AsyncSession = Depends(get_db)):
    """执行清洗：逐组合并 + 批量删除关系/实体。"""
    try:
        return await GraphCleanupService.apply_cleanup(
            db,
            kb_id=req.kb_id,
            merges=[m.model_dump() for m in req.merges],
            delete_entity_ids=req.delete_entity_ids,
            delete_relation_ids=req.delete_relation_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
