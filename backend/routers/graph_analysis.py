"""图计算任务 API（图分析工作台 Tab2 计算任务 + Tab3 推理洞察）。

设计来源：《图迁入与图计算推理_功能设计》8 节。
- 计算任务是长任务（大图 betweenness 可达分钟级）：POST 创建任务后立即返回 task_id，
  后台 BackgroundTasks 执行，前端轮询 GET tasks/{task_id} 展示进度与结果。
- 推理：规则推理长任务同上；传播分析为同步秒级 API；建议审核闭环共用
  relation_suggestions（source='rule'）。
类别列表复用 `GET /api/graph-sync/categories`（含图/投影状态），不重复建。
"""
import json

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services.graph_analysis_service import GraphAnalysisService
from services.graph_inference_service import GraphInferenceService

router = APIRouter()


class AnalysisTaskRequest(BaseModel):
    algorithm: str                              # pagerank/betweenness/louvain/node_similarity/degree
    top_n: int = 20                             # 榜单行数（上限 100）
    write_back: bool = False                    # 写回分析图节点属性（仅支持的算法）
    label_filter: str = ""                      # 只算指定本体类型（空 = 全部）
    similarity_cutoff: float = 0.0              # node_similarity 专属：相似度下限


@router.get("/graph-analysis/algorithms")
async def list_algorithms():
    """算法注册表元数据（前端算法卡片直出）。"""
    return GraphAnalysisService.list_algorithms()


@router.post("/graph-analysis/{category_id}/tasks")
async def start_analysis_task(
    category_id: str,
    body: AnalysisTaskRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """启动图计算任务；返回任务摘要，后台执行，轮询进度与结果。"""
    try:
        task = await GraphAnalysisService.start_task(
            db, category_id, body.algorithm,
            top_n=body.top_n, write_back=body.write_back,
            label_filter=body.label_filter,
            similarity_cutoff=body.similarity_cutoff,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    background_tasks.add_task(
        GraphAnalysisService.execute_task,
        task["id"], category_id, body.algorithm,
        json.dumps(task["params"], ensure_ascii=False),
    )
    return task


@router.get("/graph-analysis/tasks/{task_id}")
async def get_analysis_task(task_id: str, db: AsyncSession = Depends(get_db)):
    """计算任务进度/结果轮询。"""
    task = await GraphAnalysisService.get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="计算任务不存在")
    return task


@router.get("/graph-analysis/{category_id}/tasks")
async def list_analysis_tasks(
    category_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """某类别的计算任务历史。"""
    return await GraphAnalysisService.list_tasks(db, category_id, limit=limit)


# ── 推理（Tab3）：规则推理长任务 + 传播分析同步 API + 建议审核闭环 ──


@router.post("/graph-analysis/{category_id}/inference/run")
async def run_inference(
    category_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """启动规则推理（rules_v1：5 内置规则 → 建议 pending 待审）。"""
    try:
        task = await GraphInferenceService.start_inference_task(db, category_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    background_tasks.add_task(
        GraphInferenceService.execute_inference_task, task["id"], category_id,
    )
    return task


@router.get("/graph-analysis/{category_id}/propagation")
async def propagation(
    category_id: str,
    entity_id: str = Query(..., min_length=1),
    max_hops: int = Query(default=3, ge=1, le=5),
):
    """故障往下会引发什么：沿「导致」变长路径，置信度随跳数衰减。"""
    try:
        return await GraphInferenceService.propagation(category_id, entity_id, max_hops)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/graph-analysis/{category_id}/impact")
async def impact(category_id: str, entity_id: str = Query(..., min_length=1)):
    """部件出问题影响哪些系统/机型：组成向上 ∪ 装于链 ∪ 发生于反向。"""
    return await GraphInferenceService.impact(category_id, entity_id)


@router.get("/graph-analysis/{category_id}/similar")
async def similar(
    category_id: str,
    entity_id: str = Query(..., min_length=1),
    top_k: int = Query(default=10, ge=1, le=50),
):
    """相似故障案例（nodeSimilarity 流式，需 GDS）。"""
    try:
        return await GraphInferenceService.similar(category_id, entity_id, top_k)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/graph-analysis/{category_id}/path")
async def path(
    category_id: str,
    source_id: str = Query(..., min_length=1),
    target_id: str = Query(..., min_length=1),
):
    """两实体的关系链（shortestPath 全语义关系）。"""
    return await GraphInferenceService.path(category_id, source_id, target_id)


@router.get("/graph-analysis/{category_id}/entities")
async def search_graph_entities(
    category_id: str,
    q: str = Query(default=""),
    limit: int = Query(default=10, ge=1, le=30),
):
    """分析图内实体搜索（查询器下拉数据源）。"""
    return await GraphInferenceService.search_entities(category_id, q, limit)


@router.get("/graph-analysis/{category_id}/suggestions")
async def list_suggestions(
    category_id: str,
    status: str = Query(default="pending"),
    db: AsyncSession = Depends(get_db),
):
    """隐含关系建议列表（status=pending/approved/rejected）。"""
    return await GraphInferenceService.list_suggestions(db, category_id, status)


class ReviewRequest(BaseModel):
    reviewer: str = ""


@router.post("/graph-analysis/suggestions/{suggestion_id}/approve")
async def approve_suggestion(
    suggestion_id: str, body: ReviewRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """批准建议：走关系创建双写链路 + 顺带写分析图。"""
    try:
        return await GraphInferenceService.approve_suggestion(
            db, suggestion_id, (body.reviewer if body else "") or "")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/graph-analysis/suggestions/{suggestion_id}/reject")
async def reject_suggestion(
    suggestion_id: str, body: ReviewRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """拒绝建议：写 tombstone，之后不再重推。"""
    try:
        return await GraphInferenceService.reject_suggestion(
            db, suggestion_id, (body.reviewer if body else "") or "")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
