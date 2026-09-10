"""动作编排（flow）执行器：让动作（Action）通过可视化编排调用只读函数（Function）。

设计来源：《本体服务_函数编排_功能设计》。

要点
====
- 编排图是 **DAG**：`start → (function | code | condition)* → end`，串行执行、无循环无并行；
- 调度在主进程，计算仍在沙箱子进程：
  - ``function`` 节点走 ``FunctionService._run``（与 UI 调用函数同一条链路，安全模型不变）；
  - ``code`` 节点走 ``execute_service``（与动作代码模式同一执行器）；
  - ``condition`` 节点复用工作流规则树 ``_eval_rule_tree``，不 eval 任意代码。
- 变量引用语法与工作流一致：``{{ f1.value }}``（``render`` / ``_lookup``）。
- 输出：``result``（返回数据）+ ``edits``（写回，交给 ActionEnhanceService 统一落库）
  + ``trace``（节点级输入/输出/耗时，用于前端着色与排障）。
"""

from __future__ import annotations

import json
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Entity, OntologyFunction
from services.ontology_function_service import FunctionService
from services.service_runtime import execute_service
from services.workflow_engine import _eval_rule_tree, render

SCHEMA_VERSION = 1
MAX_NODES = 50
NODE_TYPES = {"start", "function", "code", "condition", "end"}


def empty_flow() -> dict:
    return {"schema_version": SCHEMA_VERSION, "nodes": [], "edges": [], "layout": {}}


def _load_flow(svc) -> dict:
    raw = getattr(svc, "flow", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ===== 校验 =====


def _edges_of(flow: dict) -> list[tuple[str, str, str]]:
    """返回 [(source, target, handle)]，handle 为条件节点分支（'true'/'false'/''）。"""
    out = []
    for e in flow.get("edges") or []:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if not src or not tgt:
            continue
        out.append((str(src), str(tgt), str(e.get("source_handle") or "")))
    return out


def _nodes_of(flow: dict) -> list[dict]:
    return [n for n in (flow.get("nodes") or []) if isinstance(n, dict) and n.get("id")]


def validate_structure(flow: dict) -> str | None:
    """纯结构校验（不查库）：节点类型、唯一性、起止、成环、规模。"""
    if not isinstance(flow, dict):
        return "编排图格式无效"
    nodes = _nodes_of(flow)
    if not nodes:
        return "编排图为空"
    if len(nodes) > MAX_NODES:
        return f"节点数量超过上限（{MAX_NODES}）"

    ids = [str(n["id"]) for n in nodes]
    if len(set(ids)) != len(ids):
        return "节点 id 重复"

    types = [str(n.get("type") or "") for n in nodes]
    bad = [t for t in types if t not in NODE_TYPES]
    if bad:
        return f"未知节点类型：{bad[0]}"
    if types.count("start") != 1:
        return "编排图必须且只能有一个「开始」节点"
    if types.count("end") < 1:
        return "编排图至少需要一个「结束」节点"

    id_set = set(ids)
    for src, tgt, _h in _edges_of(flow):
        if src not in id_set or tgt not in id_set:
            return "存在指向不存在节点的连线"
        if src == tgt:
            return "节点不能连向自己"

    # 成环检测（ Kahn 拓扑：剩余节点即环内节点 ）
    order, _ = _topo_order(nodes, _edges_of(flow))
    if order is None:
        return "编排图存在循环，请检查连线"
    return None


def _topo_order(nodes: list[dict], edges: list[tuple[str, str, str]]):
    """拓扑排序。返回 (顺序 list[str], 入度 dict)；有环时 (None, None)。"""
    indeg: dict[str, int] = {str(n["id"]): 0 for n in nodes}
    adj: dict[str, list[str]] = {str(n["id"]): [] for n in nodes}
    for src, tgt, _h in edges:
        if src in adj and tgt in indeg:
            adj[src].append(tgt)
            indeg[tgt] += 1
    queue = [i for i, d in indeg.items() if d == 0]
    order: list[str] = []
    while queue:
        cur = queue.pop(0)
        order.append(cur)
        for nxt in adj.get(cur, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(indeg):
        return None, None
    return order, indeg


async def validate_flow(db: AsyncSession, flow: dict) -> str | None:
    """完整校验：结构 + 引用的函数存在且启用。返回错误信息，None 表示通过。"""
    err = validate_structure(flow)
    if err:
        return err
    fn_ids = [
        str((n.get("config") or {}).get("function_id") or "")
        for n in _nodes_of(flow)
        if n.get("type") == "function"
    ]
    fn_ids = [i for i in fn_ids if i]
    if not fn_ids:
        return None
    rows = (await db.execute(
        select(OntologyFunction.id, OntologyFunction.name, OntologyFunction.is_enabled)
        .where(OntologyFunction.id.in_(fn_ids))
    )).all()
    found = {r[0]: r for r in rows}
    for fid in fn_ids:
        if fid not in found:
            return f"引用的函数不存在：{fid}"
        if not found[fid][2]:
            return f"引用的函数已停用：{found[fid][1]}"
    return None


# ===== 执行 =====


class ActionFlowService:
    """动作编排的执行与校验入口。"""

    @staticmethod
    def load_flow(svc) -> dict:
        """读取动作的编排图；为空时返回空图骨架。"""
        return _load_flow(svc) or empty_flow()

    @staticmethod
    async def validate(db: AsyncSession, flow: dict) -> str | None:
        return await validate_flow(db, flow)

    @staticmethod
    async def run(
        db: AsyncSession,
        svc,
        entity: Entity | None,
        entity_payload: dict,
        params: dict,
        *,
        mock_entity: dict | None = None,
        triggered_by: str = "flow",
    ) -> dict:
        """按编排图执行动作。

        返回统一结构：{success, data, error, stdout, duration_ms, trace, edits}
        - data 为 dict 且含 ``edits`` 时，由 ActionEnhanceService 统一落库（可撤销）
        - trace 为节点级执行明细，供前端着色与排障
        """
        flow = _load_flow(svc)
        err = validate_structure(flow)
        if err:
            return {"success": False, "data": None, "error": err,
                    "stdout": "", "duration_ms": 0, "trace": [], "edits": []}

        nodes = {str(n["id"]): n for n in _nodes_of(flow)}
        edges = _edges_of(flow)
        order, _ = _topo_order(_nodes_of(flow), edges)
        if order is None:
            return {"success": False, "data": None, "error": "编排图存在循环",
                    "stdout": "", "duration_ms": 0, "trace": [], "edits": []}

        incoming: dict[str, list[tuple[str, str]]] = {}
        for src, tgt, handle in edges:
            incoming.setdefault(tgt, []).append((src, handle))

        timeout = max(1, min(120, int(getattr(svc, "timeout_seconds", 30) or 30)))
        started = time.monotonic()
        ctx: dict[str, Any] = {"entity": entity_payload or {}, "params": params or {}}
        trace: list[dict] = []
        stdout_parts: list[str] = []
        activated: dict[str, bool] = {}
        branch_of: dict[str, bool | None] = {}   # condition 节点实际走的分支

        for node_id in order:
            node = nodes[node_id]
            ntype = node.get("type")
            title = node.get("title") or node_id

            # 是否激活：start 恒激活；否则需有来自已激活节点的、分支匹配的入边
            if ntype == "start":
                active = True
            else:
                active = False
                for src, handle in incoming.get(node_id, []):
                    if not activated.get(src):
                        continue
                    if handle in ("true", "false") and branch_of.get(src) is not None:
                        if handle != ("true" if branch_of[src] else "false"):
                            continue
                    active = True
                    break
            activated[node_id] = active
            if not active:
                trace.append({"node": node_id, "type": ntype, "title": title,
                              "ok": True, "skipped": True, "duration_ms": 0})
                continue

            elapsed = int((time.monotonic() - started) * 1000)
            if elapsed > timeout * 1000:
                return ActionFlowService._fail(
                    f"编排执行超时（{timeout}s），已终止", trace, stdout_parts, started)

            t0 = time.monotonic()
            entry: dict[str, Any] = {"node": node_id, "type": ntype, "title": title}

            try:
                if ntype == "start":
                    ctx[node_id] = {"ok": True, "value": None}
                    entry.update(ok=True, duration_ms=0)

                elif ntype == "function":
                    res, fn_input = await ActionFlowService._run_function(
                        db, node, entity, params, ctx, mock_entity, triggered_by)
                    stdout_parts.append(res.get("stdout") or "")
                    ctx[node_id] = {
                        "ok": bool(res.get("success")),
                        "value": res.get("data"),
                        "error": res.get("error"),
                        "duration_ms": res.get("duration_ms") or 0,
                    }
                    entry.update(ok=bool(res.get("success")), input=fn_input,
                                 output=res.get("data"), error=res.get("error"))
                    if not res.get("success"):
                        on_error = (node.get("config") or {}).get("on_error", "fail")
                        if on_error != "continue":
                            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                            trace.append(entry)
                            return ActionFlowService._fail(
                                f"[{title}] {res.get('error') or '函数执行失败'}",
                                trace, stdout_parts, started)

                elif ntype == "code":
                    res = await ActionFlowService._run_code(
                        svc, node, entity_payload, ctx, params, timeout, triggered_by)
                    stdout_parts.append(res.get("stdout") or "")
                    ctx[node_id] = {
                        "ok": bool(res.get("success")),
                        "value": res.get("data"),
                        "error": res.get("error"),
                        "duration_ms": res.get("duration_ms") or 0,
                    }
                    entry.update(ok=bool(res.get("success")), output=res.get("data"),
                                 error=res.get("error"))
                    if not res.get("success"):
                        on_error = (node.get("config") or {}).get("on_error", "fail")
                        if on_error != "continue":
                            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                            trace.append(entry)
                            return ActionFlowService._fail(
                                f"[{title}] {res.get('error') or '代码节点执行失败'}",
                                trace, stdout_parts, started)

                elif ntype == "condition":
                    rule = (node.get("config") or {}).get("rule") or {}
                    passed = bool(_eval_rule_tree(rule, ctx))
                    branch_of[node_id] = passed
                    ctx[node_id] = {"ok": True, "passed": passed, "value": passed}
                    entry.update(ok=True, output=passed, passed=passed)
                    if not passed and (node.get("config") or {}).get("on_false") == "abort":
                        msg = str(render(
                            (node.get("config") or {}).get("abort_message") or f"条件未通过：{title}", ctx))
                        entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                        trace.append(entry)
                        return {
                            "success": False, "data": None, "error": msg,
                            "stdout": "".join(stdout_parts),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "trace": trace, "edits": [],
                        }

                elif ntype == "end":
                    cfg = node.get("config") or {}
                    if cfg.get("mode") == "abort":
                        msg = str(render(cfg.get("abort_message") or "动作中止", ctx))
                        entry.update(ok=False, error=msg)
                        entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                        trace.append(entry)
                        return {
                            "success": False, "data": None, "error": msg,
                            "stdout": "".join(stdout_parts),
                            "duration_ms": int((time.monotonic() - started) * 1000),
                            "trace": trace, "edits": [],
                        }
                    result = render(cfg.get("result") or {}, ctx)
                    edits_raw = render(cfg.get("edits") or [], ctx)
                    edits = [e for e in edits_raw if isinstance(e, dict) and e.get("op")]
                    data = result if isinstance(result, dict) else {"value": result}
                    if edits:
                        data = {**data, "edits": edits}
                    entry.update(ok=True, output=data)
                    entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                    trace.append(entry)
                    return {
                        "success": True, "data": data, "error": None,
                        "stdout": "".join(stdout_parts),
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "trace": trace, "edits": edits,
                    }

                else:  # pragma: no cover —— validate_structure 已拦截
                    entry.update(ok=False, error=f"不支持的节点类型：{ntype}")
            except Exception as e:  # noqa: BLE001（节点异常不得中断整条编排）
                entry.update(ok=False, error=str(e)[:500])
                entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
                trace.append(entry)
                return ActionFlowService._fail(f"[{title}] {e}", trace, stdout_parts, started)

            entry["duration_ms"] = int((time.monotonic() - t0) * 1000)
            trace.append(entry)

        # 走完全部节点但未命中任何 end：视为无输出的成功
        return {
            "success": True, "data": None, "error": None,
            "stdout": "".join(stdout_parts),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "trace": trace, "edits": [],
        }

    # ===== 节点执行 =====

    @staticmethod
    async def _run_function(
        db: AsyncSession, node: dict, entity: Entity | None, params: dict,
        ctx: dict, mock_entity: dict | None, triggered_by: str,
    ) -> tuple[dict, dict]:
        cfg = node.get("config") or {}
        fn_id = str(cfg.get("function_id") or "")
        fn = await db.get(OntologyFunction, fn_id)
        if not fn:
            return {"success": False, "data": None, "error": f"函数不存在：{fn_id or '(空)'}",
                    "stdout": "", "duration_ms": 0}, {}
        if not fn.is_enabled:
            return {"success": False, "data": None, "error": f"函数已停用：{fn.name}",
                    "stdout": "", "duration_ms": 0}, {}
        raw = cfg.get("params") or {}
        resolved = render(raw, ctx) if isinstance(raw, dict) else {}
        res = await FunctionService._run(
            db, fn, entity, resolved, mock_entity, triggered_by=triggered_by)
        return res, resolved

    @staticmethod
    async def _run_code(
        svc, node: dict, entity_payload: dict, ctx: dict,
        params: dict, timeout: int, triggered_by: str,
    ) -> dict:
        cfg = node.get("config") or {}
        code_text = cfg.get("code_text") or ""
        if not code_text.strip():
            return {"success": True, "data": None, "stdout": "", "duration_ms": 0}
        node_params = render(cfg.get("params") or {}, ctx)
        context = {
            "kb_id": (entity_payload or {}).get("ontology_id") or "",
            "service_code": getattr(svc, "code", ""),
            "triggered_by": triggered_by,
            "entity": entity_payload,
            "params": params,
            **{k: v for k, v in ctx.items() if k not in ("entity", "params")},
        }
        return await execute_service(
            code_text=code_text, language="python",
            params=node_params if isinstance(node_params, dict) else {},
            entity=entity_payload, context=context,
            timeout_seconds=min(timeout, int(cfg.get("timeout_seconds") or timeout) or timeout),
        )

    @staticmethod
    def _fail(message: str, trace: list[dict], stdout_parts: list[str], started: float) -> dict:
        return {
            "success": False, "data": None, "error": message,
            "stdout": "".join(stdout_parts),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "trace": trace, "edits": [],
        }
