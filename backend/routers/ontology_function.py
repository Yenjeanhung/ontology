"""S3（P0-3）函数与派生属性路由。

对应设计文档《本体能力对标Palantir_补强设计》§4.3：

- 函数：只读计算，可被动作/视图/派生属性/智能体复用，确定性函数支持 TTL 缓存；
- 派生属性：来源为函数（实时算）或图计算指标（读图分析结果），可物化写入实体属性。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import (
    InvokeFunctionRequest,
    ResolveFunctionsRequest,
    SaveDerivedPropertyRequest,
    SaveFunctionRequest,
    TestFunctionRequest,
)
from services.ontology_function_service import (
    DerivedPropertyService,
    FunctionService,
    serialize_function,
)

router = APIRouter()


def _nf(msg: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg)


def _bad(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


# ===== 函数 =====


@router.get("/ontology-categories/{category_id}/functions")
async def list_functions(
    category_id: str, ontology_id: str = "", db: AsyncSession = Depends(get_db)
):
    return await FunctionService.list_functions(db, category_id, ontology_id)


@router.post("/ontology-categories/{category_id}/functions")
async def create_function(
    category_id: str, req: SaveFunctionRequest, db: AsyncSession = Depends(get_db)
):
    fn, err = await FunctionService.create(db, category_id, req)
    if err:
        raise _bad(err)
    return fn


@router.get("/functions/{function_id}")
async def get_function(function_id: str, db: AsyncSession = Depends(get_db)):
    fn = await FunctionService.get(db, function_id)
    if not fn:
        raise _nf("函数不存在")
    return serialize_function(fn)


@router.put("/functions/{function_id}")
async def update_function(
    function_id: str, req: SaveFunctionRequest, db: AsyncSession = Depends(get_db)
):
    fn, err = await FunctionService.update(db, function_id, req)
    if err:
        raise _nf(err)
    return fn


@router.delete("/functions/{function_id}")
async def delete_function(function_id: str, db: AsyncSession = Depends(get_db)):
    if not await FunctionService.delete(db, function_id):
        raise _nf("函数不存在")
    return {"status": "deleted"}


@router.post("/functions/{function_id}/test")
async def test_function(
    function_id: str, req: TestFunctionRequest, db: AsyncSession = Depends(get_db)
):
    res, err = await FunctionService.test_run(db, function_id, req)
    if err:
        raise _bad(err)
    return res


@router.post("/entities/{entity_id}/functions/{function_id}/invoke")
async def invoke_function(
    entity_id: str,
    function_id: str,
    req: InvokeFunctionRequest,
    db: AsyncSession = Depends(get_db),
):
    res, err = await FunctionService.invoke(db, entity_id, function_id, req.params or {})
    if err:
        raise _bad(err)
    return res


@router.post("/functions/resolve")
async def resolve_functions(req: ResolveFunctionsRequest, db: AsyncSession = Depends(get_db)):
    """批量解析：对象集/视图一次取多个实体的函数值。"""
    res, err = await FunctionService.resolve_batch(
        db, req.function_id, req.entity_ids or [], req.params or {}
    )
    if err:
        raise _bad(err)
    return res


# ===== 派生属性 =====


@router.get("/ontology-categories/{category_id}/ontologies/{ontology_id}/derived-properties")
async def list_derived_properties(
    category_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)
):
    return await DerivedPropertyService.list_for_ontology(db, ontology_id)


@router.post("/ontology-categories/{category_id}/ontologies/{ontology_id}/derived-properties")
async def create_derived_property(
    category_id: str,
    ontology_id: str,
    req: SaveDerivedPropertyRequest,
    db: AsyncSession = Depends(get_db),
):
    dp, err = await DerivedPropertyService.create(db, ontology_id, req)
    if err:
        raise _bad(err)
    return dp


@router.put("/derived-properties/{prop_id}")
async def update_derived_property(
    prop_id: str, req: SaveDerivedPropertyRequest, db: AsyncSession = Depends(get_db)
):
    dp, err = await DerivedPropertyService.update(db, prop_id, req)
    if err:
        raise _nf(err)
    return dp


@router.delete("/derived-properties/{prop_id}")
async def delete_derived_property(prop_id: str, db: AsyncSession = Depends(get_db)):
    if not await DerivedPropertyService.delete(db, prop_id):
        raise _nf("派生属性不存在")
    return {"status": "deleted"}


@router.get("/entities/{entity_id}/derived-properties")
async def resolve_entity_derived_properties(
    entity_id: str, db: AsyncSession = Depends(get_db)
):
    """实体详情页用：该实体所属本体的派生属性及当前值。"""
    return await DerivedPropertyService.resolve_for_entity(db, entity_id)


@router.post("/derived-properties/{prop_id}/materialize")
async def materialize_derived_property(
    prop_id: str, limit: int = 1000, db: AsyncSession = Depends(get_db)
):
    res, err = await DerivedPropertyService.materialize(db, prop_id, limit)
    if err:
        raise _nf(err)
    return res
