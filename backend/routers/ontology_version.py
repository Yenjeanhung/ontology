"""S7（P1-4）版本 / 提案 / 影响分析 / 回滚 路由。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import CreateVersionRequest, MergeProposalRequest, UpgradeSuggestionRequest
from services.ontology_version_service import OntologyVersionService

router = APIRouter()


def _nf(detail: str):
    return HTTPException(status_code=404, detail=detail)


def _bad_request(detail: str):
    return HTTPException(status_code=400, detail=detail)


# ===== 版本快照 / 回滚 =====


@router.post("/ontology-categories/{category_id}/versions")
async def create_version(
    category_id: str, req: CreateVersionRequest, db: AsyncSession = Depends(get_db)
):
    return await OntologyVersionService.create_version(
        db, category_id, note=req.note, source=req.source, created_by=req.created_by
    )


@router.get("/ontology-categories/{category_id}/versions")
async def list_versions(category_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyVersionService.list_versions(db, category_id)


@router.get("/ontology-categories/{category_id}/versions/{version_id}")
async def get_version(category_id: str, version_id: str, db: AsyncSession = Depends(get_db)):
    v = await OntologyVersionService.get_version(db, version_id)
    if not v:
        raise _nf("版本不存在")
    return v


@router.post("/ontology-categories/{category_id}/versions/{version_id}/rollback")
async def rollback_version(
    category_id: str, version_id: str, db: AsyncSession = Depends(get_db)
):
    res, err = await OntologyVersionService.rollback(db, version_id)
    if err:
        raise _bad_request(err)
    return res


# ===== 影响分析（Usages）=====


@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/usages")
async def analyze_usages(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    return await OntologyVersionService.analyze_usages(db, category_id, ontology_id)


# ===== 提案升级 / 合并 =====


@router.post("/ontology-suggestions/{suggestion_id}/upgrade")
async def upgrade_suggestion(
    suggestion_id: str, req: UpgradeSuggestionRequest, db: AsyncSession = Depends(get_db)
):
    res, err = await OntologyVersionService.upgrade_suggestion(
        db, suggestion_id, note=req.note, reviewers=req.reviewers,
        base_version=req.base_version, diff=req.diff,
    )
    if err:
        raise _nf(err)
    return res


@router.post("/ontology-suggestions/{suggestion_id}/merge")
async def merge_proposal(
    suggestion_id: str, req: MergeProposalRequest, db: AsyncSession = Depends(get_db)
):
    res, err = await OntologyVersionService.merge_proposal(
        db, suggestion_id, note=req.note, created_by=req.created_by
    )
    if err:
        raise _bad_request(err)
    return res
