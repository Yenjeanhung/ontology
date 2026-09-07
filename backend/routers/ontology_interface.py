"""共享属性 + 本体接口（Interface）路由。

对应设计文档《本体能力对标Palantir_补强设计》S1/S2：

- 共享属性：``/api/shared-properties``，跨本体统一定义（契约引用，改一处全局生效）；
- 接口：``/api/ontology-categories/{cid}/interfaces``，共享属性/链接契约 + implements
  映射 + 多态查询（按接口取各实现本体的对象并投影为统一属性）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from schemas import (
    ApplySharedPropertyRequest,
    CreateInterfaceLinkRequest,
    CreateInterfacePropertyRequest,
    CreateInterfaceRequest,
    CreateSharedPropertyRequest,
    ImplementInterfaceRequest,
    UpdateInterfaceRequest,
    UpdateSharedPropertyRequest,
)
from services.ontology_interface_service import (
    OntologyInterfaceService,
    SharedPropertyService,
)

router = APIRouter()


def _bad_request(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail=msg)


def _not_found(msg: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg)


# ───────────────────────── 共享属性 ─────────────────────────


@router.get("/shared-properties")
async def list_shared_properties(q: str = "", db: AsyncSession = Depends(get_db)):
    return await SharedPropertyService.list_properties(db, q)


@router.post("/shared-properties")
async def create_shared_property(req: CreateSharedPropertyRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SharedPropertyService.create_property(db, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.get("/shared-properties/{prop_id}")
async def get_shared_property(prop_id: str, db: AsyncSession = Depends(get_db)):
    res = await SharedPropertyService.get_property(db, prop_id)
    if not res:
        raise _not_found("Shared property not found")
    return res


@router.put("/shared-properties/{prop_id}")
async def update_shared_property(prop_id: str, req: UpdateSharedPropertyRequest, db: AsyncSession = Depends(get_db)):
    try:
        res = await SharedPropertyService.update_property(db, prop_id, req)
    except ValueError as e:
        raise _bad_request(str(e))
    if not res:
        raise _not_found("Shared property not found")
    return res


@router.delete("/shared-properties/{prop_id}")
async def delete_shared_property(prop_id: str, db: AsyncSession = Depends(get_db)):
    try:
        deleted = await SharedPropertyService.delete_property(db, prop_id)
    except ValueError as e:
        raise _bad_request(str(e))
    if not deleted:
        raise _not_found("Shared property not found")
    return {"status": "deleted"}


@router.post("/shared-properties/{prop_id}/apply")
async def apply_shared_property(prop_id: str, req: ApplySharedPropertyRequest, db: AsyncSession = Depends(get_db)):
    """把共享属性挂到多个本体（无同名属性则新建，overwrite=true 时同步已有属性）。"""
    try:
        return await SharedPropertyService.apply_to_ontologies(
            db, prop_id, req.ontology_ids, overwrite=req.overwrite,
        )
    except ValueError as e:
        raise _bad_request(str(e))


# ───────────────────────── 本体接口 ─────────────────────────


@router.get("/ontology-categories/{category_id}/interfaces")
async def list_interfaces(category_id: str, db: AsyncSession = Depends(get_db)):
    return await OntologyInterfaceService.list_interfaces(db, category_id)


@router.post("/ontology-categories/{category_id}/interfaces")
async def create_interface(category_id: str, req: CreateInterfaceRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyInterfaceService.create_interface(db, category_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.get("/interfaces/{interface_id}")
async def get_interface(interface_id: str, db: AsyncSession = Depends(get_db)):
    res = await OntologyInterfaceService.get_interface_detail(db, interface_id)
    if not res:
        raise _not_found("Interface not found")
    return res


@router.put("/interfaces/{interface_id}")
async def update_interface(interface_id: str, req: UpdateInterfaceRequest, db: AsyncSession = Depends(get_db)):
    try:
        res = await OntologyInterfaceService.update_interface(db, interface_id, req)
    except ValueError as e:
        raise _bad_request(str(e))
    if not res:
        raise _not_found("Interface not found")
    return res


@router.delete("/interfaces/{interface_id}")
async def delete_interface(interface_id: str, db: AsyncSession = Depends(get_db)):
    try:
        deleted = await OntologyInterfaceService.delete_interface(db, interface_id)
    except ValueError as e:
        raise _bad_request(str(e))
    if not deleted:
        raise _not_found("Interface not found")
    return {"status": "deleted"}


@router.put("/interfaces/{interface_id}/properties")
async def set_interface_properties(interface_id: str, req: list[CreateInterfacePropertyRequest], db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyInterfaceService.set_properties(db, interface_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.put("/interfaces/{interface_id}/links")
async def set_interface_links(interface_id: str, req: list[CreateInterfaceLinkRequest], db: AsyncSession = Depends(get_db)):
    try:
        return await OntologyInterfaceService.set_links(db, interface_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.post("/interfaces/{interface_id}/implement")
async def implement_interface(interface_id: str, req: ImplementInterfaceRequest, db: AsyncSession = Depends(get_db)):
    """本体 implements 接口：提交属性映射，自动校验（缺失/类型不匹配→status=partial）。"""
    try:
        return await OntologyInterfaceService.implement(db, interface_id, req)
    except ValueError as e:
        raise _bad_request(str(e))


@router.delete("/interfaces/{interface_id}/implement/{ontology_id}")
async def remove_implementation(interface_id: str, ontology_id: str, db: AsyncSession = Depends(get_db)):
    deleted = await OntologyInterfaceService.remove_implementation(db, interface_id, ontology_id)
    if not deleted:
        raise _not_found("Implementation not found")
    return {"status": "deleted"}


@router.get("/ontologies/{ontology_id}/interfaces")
async def list_ontology_interfaces(ontology_id: str, db: AsyncSession = Depends(get_db)):
    """某本体实现的全部接口。"""
    return await OntologyInterfaceService.list_ontology_interfaces(db, ontology_id)


@router.get("/ontology-categories/{category_id}/interfaces/{interface_code}/objects")
async def resolve_interface_objects(
    category_id: str,
    interface_code: str,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """多态查询：按接口取各实现本体的对象，属性按映射投影为接口属性。"""
    res = await OntologyInterfaceService.resolve_objects(
        db, category_id, interface_code, q, limit, offset,
    )
    if res is None:
        raise _not_found("Interface not found")
    return res
