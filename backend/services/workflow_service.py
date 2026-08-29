"""工作流（Workflow）定义层：CRUD + 定义静态校验。"""
from __future__ import annotations

import json
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Workflow

NODE_TYPES = {"start", "end", "agent", "service", "llm", "condition", "code", "human"}

# ── 人工节点（human）配置约束 ──
HUMAN_MODES = {"approve", "form"}
HUMAN_FIELD_TYPES = {"text", "textarea", "number", "select", "date", "boolean"}
# 表单字段 key 作为输出字段名参与 {{node.key}} 引用，只允许小写英文 + 数字 + 下划线
HUMAN_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
# 变量引用 {{node_id.field}} 或 {{node_id.data.x[0]}} → 取 node_id 部分
VAR_REF_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*[.\[]")


def parse_definition(raw: str | None) -> dict:
    if not raw:
        return {"nodes": [], "edges": []}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"nodes": [], "edges": []}
    except (ValueError, TypeError):
        return {"nodes": [], "edges": []}


def validate_definition(definition: dict) -> str | None:
    """静态校验工作流定义，返回错误信息；None 表示通过。

    允许空定义（空 draft，运行前会再校验），结构非法才报错。
    """
    nodes = definition.get("nodes") or []
    edges = definition.get("edges") or []
    if not isinstance(nodes, list) or not isinstance(edges, list):
        return "定义格式错误：nodes/edges 须为数组"

    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(set(ids)):
        return "节点 id 重复"

    start_count = 0
    end_count = 0
    for n in nodes:
        if not isinstance(n, dict) or not n.get("id"):
            return "存在缺少 id 的节点"
        if n.get("type") not in NODE_TYPES:
            return f"未知节点类型：{n.get('type')}"
        if n.get("type") == "start":
            start_count += 1
        elif n.get("type") == "end":
            end_count += 1

    if start_count != 1:
        return "必须恰好一个「开始」节点"
    if end_count < 1:
        return "至少一个「结束」节点"

    id_set = set(ids)
    out_edges: dict[str, list[dict]] = {}
    for e in edges:
        if not isinstance(e, dict):
            return "边格式错误"
        if e.get("source") not in id_set:
            return f"边 source 指向不存在的节点：{e.get('source')}"
        if e.get("target") not in id_set:
            return f"边 target 指向不存在的节点：{e.get('target')}"
        if e["source"] == e["target"]:
            return "节点不能连接自己"
        out_edges.setdefault(e["source"], []).append(e)

    # 开始节点不能有入边；结束节点不能有出边
    node_by_id = {n["id"]: n for n in nodes}
    for e in edges:
        src_type = (node_by_id.get(e["source"]) or {}).get("type")
        tgt_type = (node_by_id.get(e["target"]) or {}).get("type")
        if tgt_type == "start":
            return "「开始」节点之前不能再连接其他节点"
        if src_type == "end":
            return "「结束」节点之后不能再连接其他节点"

    if _has_cycle(nodes, edges):
        return "工作流存在循环，不能包含环"

    # 人工节点专项校验（依赖无环图计算前驱，故放在环检测之后）
    preds = _predecessors(nodes, edges)
    for n in nodes:
        if n.get("type") != "human":
            continue
        err = _validate_human_node(n, out_edges.get(n["id"], []), preds.get(n["id"], set()))
        if err:
            return err
    return None


def _predecessors(nodes: list, edges: list) -> dict[str, set[str]]:
    """计算每个节点的全部上游（反向可达）节点 id 集合。"""
    prev_of: dict[str, list[str]] = {n["id"]: [] for n in nodes}
    for e in edges:
        prev_of[e["target"]].append(e["source"])
    preds: dict[str, set[str]] = {}
    for nid in prev_of:
        seen: set[str] = set()
        stack = list(prev_of[nid])
        while stack:
            cur = stack.pop()
            if cur in seen or cur == nid:
                continue
            seen.add(cur)
            stack.extend(prev_of.get(cur, []))
        preds[nid] = seen
    return preds


def _validate_human_node(node: dict, out_edges: list[dict], preds: set[str]) -> str | None:
    """人工节点配置校验，返回错误信息；None 表示通过。"""
    title = node.get("title") or node["id"]
    cfg = node.get("config") or {}
    mode = cfg.get("mode") or "approve"
    if mode not in HUMAN_MODES:
        return f"人工节点「{title}」的 mode 非法：{mode}（仅支持 approve / form）"

    # --- 出口校验 ---
    handles = [e.get("handle") or "default" for e in out_edges]
    if mode == "approve":
        if len(out_edges) > 2:
            return f"人工节点「{title}」最多两条出边（true / false 分支）"
        if len(out_edges) == 2 and sorted(handles) != ["false", "true"]:
            return f"人工节点「{title}」的两条出边必须分别为 true / false 分支"
        if len(out_edges) == 1 and handles[0] not in ("default", "true", "false"):
            return f"人工节点「{title}」出边 handle 非法：{handles[0]}"
    else:  # form：单出口，不支持「提交并驳回」
        if len(out_edges) > 1:
            return f"人工节点「{title}」为表单模式，仅支持单一出口（需要分支请在后面串审批或条件节点）"
        if handles and handles[0] in ("true", "false"):
            return f"人工节点「{title}」为表单模式，出口不能指定 true / false 分支"

    # --- 审批模式：决策项校验 ---
    if mode == "approve":
        decisions = cfg.get("decisions") or []
        if decisions:
            if len(decisions) < 2:
                return f"人工节点「{title}」的决策项至少需要两项（通过 / 驳回）"
            keys = [d.get("key") for d in decisions if isinstance(d, dict)]
            if len(keys) != len(set(keys)):
                return f"人工节点「{title}」的决策项 key 重复"
            for k in keys:
                if k not in ("approved", "rejected"):
                    return f"人工节点「{title}」的决策项 key 非法：{k}（仅支持 approved / rejected）"

    # --- 表单模式：字段定义校验 ---
    if mode == "form":
        fields = cfg.get("form_fields") or []
        if not fields:
            return f"人工节点「{title}」为表单模式，至少需要配置一个填写字段"
        keys = []
        for f in fields:
            if not isinstance(f, dict):
                return f"人工节点「{title}」的填写字段格式错误"
            key = (f.get("key") or "").strip()
            if not key:
                return f"人工节点「{title}」存在缺少 key 的填写字段"
            if not HUMAN_KEY_RE.match(key):
                return (f"人工节点「{title}」的字段 key「{key}」不合法："
                        f"仅允许小写英文字母、数字和下划线，且不能以数字开头（不允许中文）")
            if key in keys:
                return f"人工节点「{title}」的字段 key 重复：{key}"
            keys.append(key)
            ftype = f.get("type") or "text"
            if ftype not in HUMAN_FIELD_TYPES:
                return f"人工节点「{title}」的字段 {key} 类型非法：{ftype}"
            if ftype == "select" and not (f.get("options") or []):
                return f"人工节点「{title}」的下拉字段 {key} 必须配置选项"

    # --- 变量引用校验：必须引用拓扑上更早的节点 ---
    refs: list[str] = []
    for item in cfg.get("display_fields") or []:
        if isinstance(item, dict):
            refs.extend(VAR_REF_RE.findall(str(item.get("value") or "")))
    refs.extend(VAR_REF_RE.findall(str(cfg.get("description") or "")))
    for rid in refs:
        if rid == node["id"]:
            return f"人工节点「{title}」不能引用自己的输出：{rid}"
        if rid not in preds:
            return (f"人工节点「{title}」引用了不存在或不在其上游的节点：{rid}"
                    f"（变量只能引用拓扑上更早的节点输出）")
    return None


def _has_cycle(nodes: list, edges: list) -> bool:
    id_set = {n["id"] for n in nodes}
    indegree = {nid: 0 for nid in id_set}
    adj: dict[str, list[str]] = {nid: [] for nid in id_set}
    for e in edges:
        indegree[e["target"]] += 1
        adj[e["source"]].append(e["target"])
    queue = [nid for nid in id_set if indegree[nid] == 0]
    seen = 0
    while queue:
        nid = queue.pop()
        seen += 1
        for t in adj[nid]:
            indegree[t] -= 1
            if indegree[t] == 0:
                queue.append(t)
    return seen != len(id_set)


class WorkflowService:
    @staticmethod
    async def list(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(Workflow).order_by(Workflow.created_at))
        rows = result.scalars().all()
        out = []
        for w in rows:
            d = parse_definition(w.definition)
            out.append({
                "id": w.id,
                "name": w.name,
                "description": w.description or "",
                "node_count": len(d.get("nodes", [])),
                "edge_count": len(d.get("edges", [])),
                "is_published": w.is_published,
                "created_at": w.created_at,
                "updated_at": w.updated_at,
            })
        return out

    @staticmethod
    async def get(db: AsyncSession, workflow_id: str) -> dict | None:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        w = row.scalar_one_or_none()
        if not w:
            return None
        return {
            "id": w.id,
            "name": w.name,
            "description": w.description or "",
            "definition": parse_definition(w.definition),
            "is_published": w.is_published,
            "created_at": w.created_at,
            "updated_at": w.updated_at,
        }

    @staticmethod
    async def create(db: AsyncSession, data: dict) -> dict:
        data = dict(data)
        if isinstance(data.get("definition"), dict):
            data["definition"] = json.dumps(data["definition"], ensure_ascii=False)
        wf = Workflow(**data)
        db.add(wf)
        await db.commit()
        await db.refresh(wf)
        return {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description or "",
            "definition": parse_definition(wf.definition),
            "created_at": wf.created_at,
        }

    @staticmethod
    async def update(db: AsyncSession, workflow_id: str, data: dict) -> dict | None:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = row.scalar_one_or_none()
        if not wf:
            return None
        data = dict(data)
        if isinstance(data.get("definition"), dict):
            data["definition"] = json.dumps(data["definition"], ensure_ascii=False)
        for key, value in data.items():
            if value is not None:
                setattr(wf, key, value)
        wf.updated_at = datetime.now().isoformat()
        await db.commit()
        await db.refresh(wf)
        return {
            "id": wf.id,
            "name": wf.name,
            "description": wf.description or "",
            "definition": parse_definition(wf.definition),
            "created_at": wf.created_at,
            "updated_at": wf.updated_at,
        }

    @staticmethod
    async def delete(db: AsyncSession, workflow_id: str) -> bool:
        row = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
        wf = row.scalar_one_or_none()
        if not wf:
            return False
        await db.delete(wf)
        await db.commit()
        return True
