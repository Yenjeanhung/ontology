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
    ApproveExtractionReviewRequest,
    BatchDeleteRequest,
    BatchExtractionReviewRequest,
    CreateEntityRequest,
    CreateRelationRequest,
    MergeEntitiesRequest,
    RejectExtractionReviewRequest,
    UpdateEntityRequest,
    UpdateExtractionReviewRequest,
    UpdateRelationRequest,
)
from services.entity_service import EntityService
from services.extraction_review_service import ExtractionReviewService
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
async def graph_cleanup_suggestions(
    kb_id: str | None = Query(default=None),
    category_id: str | None = Query(default=None),
    ontology_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """对指定知识库或本体类别给出清洗建议（合并组 / 待删实体 / 待删通用关系），纯只读。"""
    if not kb_id and not category_id and not ontology_id:
        raise HTTPException(status_code=400, detail="kb_id / category_id / ontology_id 至少提供一个")
    return await GraphCleanupService.suggest_cleanup(
        db,
        kb_id=kb_id,
        category_id=category_id,
        ontology_id=ontology_id,
    )


@router.post("/graph-cleanup/apply")
async def graph_cleanup_apply(req: ApplyCleanupRequest, db: AsyncSession = Depends(get_db)):
    """执行清洗：逐组合并 + 批量删除关系/实体。支持知识库或本体类别范围。"""
    try:
        return await GraphCleanupService.apply_cleanup(
            db,
            kb_id=req.kb_id,
            category_id=req.category_id,
            ontology_id=req.ontology_id,
            merges=[m.model_dump() for m in req.merges],
            delete_entity_ids=req.delete_entity_ids,
            delete_relation_ids=req.delete_relation_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ===== 抽取复核队列：未通过规则的实体，待人工审核 =====
# 注意：/stats 与 /batch 必须定义在 /{review_id} 之前，避免被路径参数吞掉。

@router.get("/entities/extraction-reviews/stats")
async def extraction_review_stats(
    kb_id: str = Query(...),
    file_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """按状态 / 规则的复核计数，用于徽标与筛选器。"""
    return await ExtractionReviewService.stats(db, kb_id=kb_id, file_id=file_id)


@router.get("/entities/extraction-reviews")
async def list_extraction_reviews(
    kb_id: str = Query(...),
    status: str | None = Query(default=None),
    file_id: str | None = Query(default=None),
    rule: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """待复核实体列表。"""
    return await ExtractionReviewService.list_reviews(
        db, kb_id=kb_id, status=status, file_id=file_id,
        rule=rule, page=page, page_size=page_size,
    )


@router.post("/entities/extraction-reviews/batch")
async def batch_extraction_reviews(
    req: BatchExtractionReviewRequest, db: AsyncSession = Depends(get_db)
):
    """批量通过 / 驳回，返回每条的成功与失败明细。"""
    if not req.ids:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    if req.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail=f"不支持的批量操作：{req.action}")
    return await ExtractionReviewService.batch_action(
        db, req.ids, req.action, reviewer=req.reviewer or "",
    )


@router.post("/entities/extraction-reviews/expire-by-file")
async def expire_extraction_reviews(
    file_id: str = Query(...), db: AsyncSession = Depends(get_db)
):
    """文件重新处理前，把该文件下旧的待审核记录置为已过期。"""
    count = await ExtractionReviewService.expire_by_file(db, file_id)
    return {"file_id": file_id, "expired": count}


@router.get("/entities/extraction-reviews/{review_id}")
async def get_extraction_review(review_id: str, db: AsyncSession = Depends(get_db)):
    res = await ExtractionReviewService.get_review(db, review_id)
    if not res:
        raise _nf("Extraction review not found")
    return res


@router.put("/entities/extraction-reviews/{review_id}")
async def update_extraction_review(
    review_id: str,
    req: UpdateExtractionReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """修改待复核实体的名称 / 描述 / 属性，仅 pending 可改。"""
    try:
        res = await ExtractionReviewService.update_review(
            db, review_id,
            name=req.name, description=req.description,
            properties=req.properties, review_notes=req.review_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not res:
        raise _nf("Extraction review not found")
    return res


@router.post("/entities/extraction-reviews/{review_id}/approve")
async def approve_extraction_review(
    review_id: str,
    req: ApproveExtractionReviewRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """审核通过并入库。入库失败时记录保持 pending 并返回错误原因。"""
    try:
        return await ExtractionReviewService.approve(
            db, review_id,
            reviewer=req.reviewer if req else "",
            name=req.name if req else None,
            description=req.description if req else None,
            properties=req.properties if req else None,
            review_notes=(req.review_notes or "") if req else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/entities/extraction-reviews/{review_id}/reject")
async def reject_extraction_review(
    review_id: str,
    req: RejectExtractionReviewRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """驳回：不入库，记录审核人与备注。"""
    try:
        res = await ExtractionReviewService.reject(
            db, review_id,
            reviewer=req.reviewer if req else "",
            review_notes=(req.review_notes or "") if req else "",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not res:
        raise _nf("Extraction review not found")
    return res
