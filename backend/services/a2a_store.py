# -*- coding: utf-8 -*-
"""A2A 远端智能体注册中心（a2a_agents 表 CRUD + 校验）。

与 mcp_store.py 同构的管理层：name 唯一、enabled 停用即切断（组队面板
不可选 + 巡检跳过）、auth_token 永不回传（只回 has_token）、card_json
缓存最近一次成功拉取的 AgentCard（前端 skills 展示与委派选技能用）。

与 mcp_servers 的分工：MCP=Agent↔工具（纵向，函数粒度）；
A2A=Agent↔Agent（横向，任务粒度，不透明协作）。设计见
doc/智能体/A2A/00-A2A智能体互操作协议设计方案.md。
"""

import json
import re
import time
import uuid

from sqlalchemy import select

from models import A2aAgent

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def validate_agent(data: dict) -> list[str]:
    """落库前校验，返回错误列表（空 = 通过）。路由层 422。"""
    errs: list[str] = []
    name = str(data.get("name") or "").strip()
    if not name:
        errs.append("name 不能为空")
    elif not _NAME_RE.match(name):
        errs.append("name 仅限字母/数字/下划线/中划线（1~32 位）")
    base_url = str(data.get("base_url") or "").strip()
    if not base_url:
        errs.append("base_url 不能为空（远端 AgentCard 同源根地址）")
    elif not base_url.lower().startswith(("http://", "https://")):
        errs.append("base_url 必须以 http:// 或 https:// 开头")
    return errs


def parse_card(card_json: str) -> dict:
    """card_json 容错解析（坏缓存不抛异常，返回空名片）。"""
    raw = (card_json or "").strip()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def card_skills(card: dict) -> list[dict]:
    """AgentCard.skills 归一化（缺省容错）。"""
    out = []
    for s in (card.get("skills") or []) if isinstance(card, dict) else []:
        if isinstance(s, dict) and s.get("id"):
            out.append({
                "id": str(s["id"]),
                "name": str(s.get("name") or s["id"]),
                "description": str(s.get("description") or ""),
                "tags": [str(t) for t in (s.get("tags") or [])][:8],
            })
    return out


def serialize_agent(row: A2aAgent) -> dict:
    """行 → 接口 dict：token 永不回传（只回 has_token），card 摘要展开。"""
    card = parse_card(getattr(row, "card_json", "") or "")
    skills = card_skills(card)
    return {
        "id": row.id,
        "name": row.name,
        "base_url": row.base_url,
        "has_token": bool((getattr(row, "auth_token", "") or "").strip()),
        "enabled": int(getattr(row, "enabled", 1) or 0),
        "sort_order": int(getattr(row, "sort_order", 0) or 0),
        "description": (row.description or "")[:300],
        "card": {
            "name": str(card.get("name") or ""),
            "description": str(card.get("description") or ""),
            "protocolVersion": str(card.get("protocolVersion") or ""),
            "streaming": bool((card.get("capabilities") or {}).get("streaming")),
            "skills": skills,
            "fetched": bool(card),
        },
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def list_agents(db) -> list[A2aAgent]:
    rows = (await db.execute(
        select(A2aAgent).order_by(A2aAgent.sort_order, A2aAgent.created_at)
    )).scalars().all()
    return list(rows)


async def get_agent(db, agent_id: str) -> A2aAgent | None:
    return (await db.execute(
        select(A2aAgent).where(A2aAgent.id == agent_id)
    )).scalar_one_or_none()


async def has_any_agent(db) -> bool:
    row = (await db.execute(select(A2aAgent.id).limit(1))).first()
    return row is not None


async def load_enabled_agents(db) -> list[dict]:
    """启用中的远端智能体（组队装配/面板清单用），含名片缓存摘要。"""
    rows = (await db.execute(
        select(A2aAgent)
        .where(A2aAgent.enabled == 1)
        .order_by(A2aAgent.sort_order, A2aAgent.created_at)
    )).scalars().all()
    out = []
    for r in rows:
        card = parse_card(getattr(r, "card_json", "") or "")
        out.append({
            "id": r.id,
            "name": r.name,
            "base_url": r.base_url,
            "auth_token": (getattr(r, "auth_token", "") or "").strip(),
            "card_name": str(card.get("name") or r.name),
            "card_description": str(card.get("description") or r.description or ""),
            "streaming": bool((card.get("capabilities") or {}).get("streaming")),
            "skills": card_skills(card),
        })
    return out


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


async def create_agent(db, data: dict) -> A2aAgent:
    row = A2aAgent(
        id=new_id(),
        name=str(data.get("name") or "").strip(),
        base_url=str(data.get("base_url") or "").strip().rstrip("/"),
        auth_token=str(data.get("auth_token") or "").strip(),
        card_json=data.get("card_json") or "",
        description=str(data.get("description") or "").strip()[:300],
        enabled=1 if data.get("enabled", True) else 0,
        sort_order=int(data.get("sort_order") or 0),
        created_at=now_iso(),
        updated_at=now_iso(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def update_agent(db, row: A2aAgent, data: dict) -> A2aAgent:
    """部分更新：auth_token=None = 不改（编辑留空保持原值的接口语义在路由层转换）。"""
    if "name" in data and str(data["name"] or "").strip():
        row.name = str(data["name"]).strip()
    if "base_url" in data and str(data["base_url"] or "").strip():
        row.base_url = str(data["base_url"]).strip().rstrip("/")
    token = data.get("auth_token")
    if token is not None:
        row.auth_token = str(token).strip()
    if "card_json" in data:
        row.card_json = data.get("card_json") or ""
    if "description" in data:
        row.description = str(data.get("description") or "").strip()[:300]
    if "enabled" in data:
        row.enabled = 1 if data.get("enabled") else 0
    if "sort_order" in data:
        try:
            row.sort_order = int(data.get("sort_order") or 0)
        except (TypeError, ValueError):
            pass
    row.updated_at = now_iso()
    await db.commit()
    await db.refresh(row)
    return row


async def delete_agent_row(db, agent_id: str) -> bool:
    """删除注册记录；返回是否存在。"""
    row = await get_agent(db, agent_id)
    if not row:
        return False
    await db.delete(row)
    await db.commit()
    return True
