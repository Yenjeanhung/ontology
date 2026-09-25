"""数据源管理 API：外部数据源注册表 CRUD + 不落库试连。

设计：doc/本体管理/数据源管理/00-数据源管理设计方案.md
安全口径：password 永不回传，dsn 掩码；test 失败也是 200 + ok:false（对齐 MCP 注册中心）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from services import datasource_service as dss

router = APIRouter()


class DataSourceBody(BaseModel):
    name: str = ""
    dialect: str = "postgres"
    host: str = ""
    port: int = 0
    dbname: str = ""
    username: str = ""
    password: str = ""          # 更新时传空串 = 保持原值
    dsn: str = ""               # 直填完整连接串（优先于结构化字段拼装）
    description: str = ""
    enabled: bool | None = None


class DataSourceTestBody(BaseModel):
    """试连请求：可直接透传表单（不落库），也可传已存记录 id。"""
    id: str | None = None
    name: str = ""
    dialect: str = "postgres"
    host: str = ""
    port: int = 0
    dbname: str = ""
    username: str = ""
    password: str = ""
    dsn: str = ""


@router.get("/datasources")
async def list_datasources(db: AsyncSession = Depends(get_db)):
    """清单（password 不回传，dsn 掩码，附 used_by 引用类别数）。"""
    return await dss.list_datasources(db)


@router.post("/datasources")
async def create_datasource(req: DataSourceBody, db: AsyncSession = Depends(get_db)):
    out, err = await dss.create_datasource(db, req.model_dump())
    if err:
        raise HTTPException(status_code=400, detail=err)
    return out


@router.put("/datasources/{ds_id}")
async def update_datasource(ds_id: str, req: DataSourceBody, db: AsyncSession = Depends(get_db)):
    out, err = await dss.update_datasource(db, ds_id, req.model_dump(exclude_none=True))
    if err:
        raise HTTPException(status_code=400, detail=err)
    return out


@router.delete("/datasources/{ds_id}")
async def delete_datasource(ds_id: str, db: AsyncSession = Depends(get_db)):
    ok, err = await dss.delete_datasource(db, ds_id)
    if not ok:
        raise HTTPException(status_code=409 if "引用" in err else 404, detail=err)
    return {"status": "deleted"}


@router.post("/datasources/test")
async def test_datasource(req: DataSourceTestBody, db: AsyncSession = Depends(get_db)):
    """试连（SELECT 1，3s 超时）：body.id 提供时以库中配置为准（表单密码留空场景）。"""
    return await dss.test_connection(req.model_dump(), db)
