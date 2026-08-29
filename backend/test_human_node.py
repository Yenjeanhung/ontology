"""人工节点（human node）测试。

轻量用例（默认）：定义校验 + 决策校验 + 批量处理 —— 不加载 LangGraph，秒级完成。
端到端用例（--e2e）：挂起 / 续跑全链路 —— 需加载 LangGraph，较慢。

运行：
    python -X utf8 test_human_node.py            # 轻量
    python -X utf8 test_human_node.py --e2e      # 含端到端
"""
from __future__ import annotations

import asyncio
import os
import sys

# 使用独立临时库，避免污染开发数据（必须在导入 database / models 之前设置）
_TMP_DB = "./data/_test_human_node.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

# 清理上次中断运行残留的库文件，保证用例从干净状态开始
for _suffix in ("", "-wal", "-shm"):
    _p = _TMP_DB + _suffix
    if os.path.exists(_p):
        try:
            os.remove(_p)
        except OSError:
            pass

# ─────────────────────────── 通用工具 ───────────────────────────


def _node(nid, ntype, config=None, title=None):
    return {"id": nid, "type": ntype, "title": title or nid, "config": config or {}}


def _def(nodes, edges):
    return {"nodes": nodes, "edges": edges}


def _human_definition(config_extra=None):
    """start → human(审批) → 双出口 end。"""
    cfg = {
        "mode": "approve",
        "description": "请审核：{{start.question}}",
        "display_fields": [{"label": "问题", "value": "{{start.question}}", "type": "text"}],
    }
    cfg.update(config_extra or {})
    nodes = [
        _node("start", "start", {"inputs": [{"name": "question", "type": "text"}]}),
        _node("h1", "human", cfg, title="人工审核"),
        _node("end_ok", "end", {"outputs": [{"name": "result", "value": "通过：{{h1.comment}}"}]}, title="通过"),
        _node("end_no", "end", {"outputs": [{"name": "result", "value": "驳回：{{h1.comment}}"}]}, title="驳回"),
    ]
    edges = [
        {"id": "e1", "source": "start", "target": "h1"},
        {"id": "e2", "source": "h1", "target": "end_ok", "handle": "true"},
        {"id": "e3", "source": "h1", "target": "end_no", "handle": "false"},
    ]
    return _def(nodes, edges)


def _parse_sse(chunk: str):
    if not chunk.startswith("data: "):
        return None
    import json
    text = chunk[6:].strip()
    if not text or text == "[DONE]":
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _collect(agen):
    events = []
    async for chunk in agen:
        evt = _parse_sse(chunk)
        if evt:
            events.append(evt)
    return events


async def _run_checks(checks):
    failed = 0
    for label, ok in checks:
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            failed += 1
    return failed


# ─────────────────────────── 1. 定义校验 ───────────────────────────

def test_validate():
    from services.workflow_service import validate_definition

    ok = [
        ("approve 双出口",
         _def([_node("start", "start"), _node("h", "human", {"mode": "approve"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"},
               {"id": "b", "source": "h", "target": "end", "handle": "true"},
               {"id": "c", "source": "h", "target": "end", "handle": "false"}])),
        ("approve 单出口",
         _def([_node("start", "start"), _node("h", "human", {"mode": "approve"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("mode 缺省视为 approve",
         _def([_node("start", "start"), _node("h", "human", {}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("form 单出口",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "name", "label": "名称", "type": "text"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("引用上游变量",
         _def([_node("start", "start"), _node("a", "agent"), _node("h", "human", {
             "mode": "approve", "display_fields": [{"label": "Q", "value": "{{a.answer}}"}]}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "a"}, {"id": "b", "source": "a", "target": "h"},
               {"id": "c", "source": "h", "target": "end"}])),
    ]

    bad = [
        ("未知 mode",
         _def([_node("start", "start"), _node("h", "human", {"mode": "vote"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("approve 三条出边",
         _def([_node("start", "start"), _node("h", "human", {"mode": "approve"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"},
               {"id": "b", "source": "h", "target": "end", "handle": "true"},
               {"id": "c", "source": "h", "target": "end", "handle": "false"},
               {"id": "d", "source": "h", "target": "end", "handle": "x"}])),
        ("approve 两条出边 handle 重复",
         _def([_node("start", "start"), _node("h", "human", {"mode": "approve"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"},
               {"id": "b", "source": "h", "target": "end", "handle": "true"},
               {"id": "c", "source": "h", "target": "end", "handle": "true"}])),
        ("form 多出口（提交并驳回）",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "name", "label": "名称", "type": "text"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"},
               {"id": "b", "source": "h", "target": "end", "handle": "true"},
               {"id": "c", "source": "h", "target": "end", "handle": "false"}])),
        ("form 无字段",
         _def([_node("start", "start"), _node("h", "human", {"mode": "form"}), _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("字段 key 中文",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "客户名", "label": "客户名", "type": "text"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("字段 key 大写",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "Name", "label": "名称", "type": "text"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("字段 key 重复",
         _def([_node("start", "start"), _node("h", "human", {"mode": "form", "form_fields": [
             {"key": "a", "label": "A", "type": "text"}, {"key": "a", "label": "B", "type": "text"}]}),
             _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("字段类型非法",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "a", "label": "A", "type": "file"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("select 缺选项",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "form", "form_fields": [{"key": "lv", "label": "等级", "type": "select"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("引用下游节点",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "approve", "display_fields": [{"label": "R", "value": "{{end.answer}}"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("引用不存在节点",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "approve", "display_fields": [{"label": "R", "value": "{{nope.x}}"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
        ("引用自己",
         _def([_node("start", "start"),
               _node("h", "human", {"mode": "approve", "display_fields": [{"label": "R", "value": "{{h.approved}}"}]}),
               _node("end", "end")],
              [{"id": "a", "source": "start", "target": "h"}, {"id": "b", "source": "h", "target": "end"}])),
    ]

    failed = 0
    for name, d in ok:
        err = validate_definition(d)
        if err:
            print(f"  [FAIL] {name}: 期望通过，实际报错 -> {err}")
            failed += 1
        else:
            print(f"  [ ok ] {name}")
    for name, d in bad:
        err = validate_definition(d)
        if not err:
            print(f"  [FAIL] {name}: 期望报错，实际通过")
            failed += 1
        else:
            print(f"  [ ok ] {name} -> {err}")
    return failed


# ─────────────────────────── 2. 决策校验（轻量） ───────────────────────────

async def test_decision_rules():
    from database import async_session, init_db
    from services.human_task_service import HumanTaskService

    await init_db()
    checks = []

    async with async_session() as db:
        # 审批：驳回必填理由
        t1 = await HumanTaskService.create(
            db, run_id="run1", workflow_id="wf1", workflow_name="测试",
            node_id="h1", node_title="审核",
            config={"mode": "approve", "comment": {"label": "驳回理由", "required_on": ["rejected"]}},
            description="", form_data={})
        try:
            await HumanTaskService.decide(db, t1["id"], decision="rejected", comment="")
            checks.append(("驳回必填理由生效", False))
        except ValueError:
            checks.append(("驳回必填理由生效", True))

        ok = await HumanTaskService.decide(db, t1["id"], decision="rejected",
                                           comment="口径有误", operator="张三")
        checks.append(("填了理由即可驳回", ok is not None and ok["status"] == "rejected"))
        checks.append(("重复决策被拒（乐观锁）",
                       await HumanTaskService.decide(db, t1["id"], decision="approved", comment="") is None))

        # 通过不受 required_on 影响
        t2 = await HumanTaskService.create(
            db, run_id="run2", workflow_id="wf1", workflow_name="测试",
            node_id="h1", node_title="审核",
            config={"mode": "approve", "comment": {"required_on": ["rejected"]}},
            description="", form_data={})
        ok2 = await HumanTaskService.decide(db, t2["id"], decision="approved", comment="")
        checks.append(("通过无需理由", ok2 is not None and ok2["status"] == "approved"))

        # 非法 decision
        t3 = await HumanTaskService.create(
            db, run_id="run3", workflow_id="wf1", workflow_name="测试",
            node_id="h1", node_title="审核", config={"mode": "approve"},
            description="", form_data={})
        try:
            await HumanTaskService.decide(db, t3["id"], decision="vote", comment="")
            checks.append(("非法 decision 被拒", False))
        except ValueError:
            checks.append(("非法 decision 被拒", True))

        # 表单：必填 / 下拉选项 / 转换 / 只接受 submitted
        fcfg = {"mode": "form", "form_fields": [
            {"key": "customer_name", "label": "客户名称", "type": "text", "required": True},
            {"key": "level", "label": "等级", "type": "select", "required": True, "options": ["A", "B", "C"]},
            {"key": "credit", "label": "额度", "type": "number"},
            {"key": "vip", "label": "VIP", "type": "boolean"},
        ]}
        t4 = await HumanTaskService.create(
            db, run_id="run4", workflow_id="wf1", workflow_name="测试",
            node_id="h2", node_title="补录", config=fcfg, description="", form_data={})
        try:
            await HumanTaskService.decide(db, t4["id"], decision="submitted",
                                          data={"customer_name": "华为"})
            checks.append(("表单必填校验生效", False))
        except ValueError as e:
            checks.append(("表单必填校验生效", "level" in str(e)))
        try:
            await HumanTaskService.decide(db, t4["id"], decision="submitted",
                                          data={"customer_name": "华为", "level": "D"})
            checks.append(("下拉非法选项被拒", False))
        except ValueError as e:
            checks.append(("下拉非法选项被拒", "level" in str(e)))
        try:
            await HumanTaskService.decide(db, t4["id"], decision="rejected", comment="x")
            checks.append(("表单模式仅接受 submitted", False))
        except ValueError:
            checks.append(("表单模式仅接受 submitted", True))

        ok4 = await HumanTaskService.decide(
            db, t4["id"], decision="submitted", operator="李四",
            data={"customer_name": "华为", "level": "A", "credit": "100000.0", "vip": "true"})
        filled = (ok4 or {}).get("filled_data") or {}
        checks.append(("表单提交成功", (ok4 or {}).get("status") == "submitted"))
        checks.append(("number 强转", filled.get("credit") == 100000))
        checks.append(("boolean 强转", filled.get("vip") is True))
        checks.append(("未声明字段被忽略", "unknown" not in filled and set(filled) == {
            "customer_name", "level", "credit", "vip"}))
    return checks


# ─────────────────────────── 3. 批量处理（轻量） ───────────────────────────

async def test_batch():
    from database import async_session, init_db
    from services.human_task_service import HumanTaskService

    await init_db()
    checks = []
    async with async_session() as db:
        ids = []
        for i in range(3):
            t = await HumanTaskService.create(
                db, run_id=f"run_{i}", workflow_id="wf", workflow_name="w",
                node_id="h1", node_title="审核", config={"mode": "approve"},
                description="", form_data={})
            ids.append(t["id"])
        ft = await HumanTaskService.create(
            db, run_id="run_f", workflow_id="wf", workflow_name="w",
            node_id="h2", node_title="填表",
            config={"mode": "form", "form_fields": [{"key": "a", "label": "A", "type": "text"}]},
            description="", form_data={})

        res = await HumanTaskService.batch_decide(
            db, ids + [ft["id"]], decision="approved", comment="批量通过", operator="王五")
        checks.append(("3 条审批任务批量成功", len(res["succeeded"]) == 3))
        checks.append(("表单任务被拒并给出原因",
                       any(f["id"] == ft["id"] and "逐条" in f["reason"] for f in res["failed"])))
        res2 = await HumanTaskService.batch_decide(db, ids, decision="approved", operator="王五")
        checks.append(("重复批量提交全部失败",
                       len(res2["failed"]) == 3 and not res2["succeeded"]))

        # 任务不存在
        res3 = await HumanTaskService.batch_decide(db, ["nope"], decision="approved")
        checks.append(("不存在的任务计入失败",
                       any(f["id"] == "nope" for f in res3["failed"])))
    return checks


# ─────────────────────────── 4~5. 端到端（--e2e） ───────────────────────────

async def test_suspend_resume_approve():
    """审批模式：挂起 → 驳回 → 续跑走 false 分支。"""
    from database import async_session, init_db
    from models import WorkflowHumanTask, WorkflowRun
    from services.human_task_service import HumanTaskService
    from services.workflow_engine import resume_run_stream, run_stream
    from services.workflow_service import WorkflowService

    await init_db()
    d = _human_definition()
    async with async_session() as db:
        wf = await WorkflowService.create(db, {"name": "测试-审批", "definition": d})
        wf_id = wf["id"]

    events = await _collect(run_stream(wf_id, d, {"question": "华为股价如何"}))
    types = [e.get("type") for e in events]
    checks = []
    waiting = next((e for e in events if e.get("type") == "node_waiting"), None)
    checks.append(("挂起时发出 node_waiting", waiting is not None))
    checks.append(("node_waiting 携带 task_id", bool((waiting or {}).get("task_id"))))
    checks.append(("待审内容已渲染变量",
                   (waiting or {}).get("form_data", {}).get("问题") == "华为股价如何"))
    fin = next((e for e in events if e.get("type") == "workflow_finished"), None)
    checks.append(("工作流以 waiting 收尾", (fin or {}).get("status") == "waiting"))
    checks.append(("挂起时不补发 node_skipped", "node_skipped" not in types))

    task_id = (waiting or {}).get("task_id")
    run_id = next((e.get("run_id") for e in events if e.get("type") == "workflow_started"), None)

    async with async_session() as db:
        row = await db.get(WorkflowRun, run_id)
        checks.append(("run 状态 waiting", row.status == "waiting"))
        checks.append(("context_snapshot 已落库", (row.context_snapshot or "{}") != "{}"))
        checks.append(("definition_snapshot 已落库", bool(row.definition_snapshot)))
        checks.append(("pending_node_id = h1", row.pending_node_id == "h1"))
        task = await db.get(WorkflowHumanTask, task_id)
        checks.append(("任务 pending", task.status == "pending"))

    async with async_session() as db:
        updated = await HumanTaskService.decide(
            db, task_id, decision="rejected", comment="数据口径有误", operator="张三")
        checks.append(("决策成功", updated is not None and updated["status"] == "rejected"))

    events2 = await _collect(resume_run_stream(run_id, task_id))
    types2 = [e.get("type") for e in events2]
    checks.append(("续跑首帧 workflow_resumed", bool(types2) and types2[0] == "workflow_resumed"))
    checks.append(("人工节点 node_resumed", "node_resumed" in types2))
    checks.append(("已完成节点 node_replayed", "node_replayed" in types2))
    fin2 = next((e for e in events2 if e.get("type") == "workflow_finished"), None)
    checks.append(("续跑成功", (fin2 or {}).get("status") == "succeeded"))
    outs = (fin2 or {}).get("outputs") or {}
    checks.append(("走 false 分支（驳回）", outs.get("result") == "驳回：数据口径有误"))
    skipped = [e.get("node_id") for e in events2 if e.get("type") == "node_skipped"]
    checks.append(("true 分支被 skip", "end_ok" in skipped))

    async with async_session() as db:
        row = await db.get(WorkflowRun, run_id)
        checks.append(("run 最终 succeeded", row.status == "succeeded"))
        # 重复续跑应被拒
        ev3 = await _collect(resume_run_stream(run_id, task_id))
        err = next((e for e in ev3 if e.get("type") == "error"), None)
        checks.append(("重复续跑被拒", err is not None))
    return checks


async def test_form_mode_e2e():
    """表单模式：挂起 → 填表 → 续跑，下游引用填写值。"""
    from database import async_session, init_db
    from services.human_task_service import HumanTaskService
    from services.workflow_engine import resume_run_stream, run_stream
    from services.workflow_service import WorkflowService

    await init_db()
    cfg = {"mode": "form", "form_fields": [
        {"key": "customer_name", "label": "客户名称", "type": "text", "required": True},
        {"key": "level", "label": "客户等级", "type": "select", "required": True, "options": ["A", "B", "C"]},
    ]}
    d = _def(
        [_node("start", "start", {}),
         _node("h1", "human", cfg, title="补录客户"),
         _node("end", "end", {"outputs": [{"name": "name", "value": "{{h1.data.customer_name}}"},
                                          {"name": "level", "value": "{{h1.data.level}}"}]})],
        [{"id": "e1", "source": "start", "target": "h1"},
         {"id": "e2", "source": "h1", "target": "end"}])
    async with async_session() as db:
        wf = await WorkflowService.create(db, {"name": "测试-表单", "definition": d})
        wf_id = wf["id"]

    events = await _collect(run_stream(wf_id, d, {}))
    waiting = next((e for e in events if e.get("type") == "node_waiting"), None)
    checks = [("表单模式挂起", waiting is not None),
              ("下发 form_fields", len((waiting or {}).get("form_fields") or []) == 2)]
    task_id = (waiting or {}).get("task_id")
    run_id = next((e.get("run_id") for e in events if e.get("type") == "workflow_started"), None)

    async with async_session() as db:
        await HumanTaskService.decide(db, task_id, decision="submitted", operator="李四",
                                      data={"customer_name": "华为", "level": "A"})

    events2 = await _collect(resume_run_stream(run_id, task_id))
    fin2 = next((e for e in events2 if e.get("type") == "workflow_finished"), None)
    outs = (fin2 or {}).get("outputs") or {}
    checks.append(("续跑成功", (fin2 or {}).get("status") == "succeeded"))
    checks.append(("下游可引用填写值", outs.get("name") == "华为" and outs.get("level") == "A"))
    return checks


# ─────────────────────────── 入口 ───────────────────────────

async def main(e2e: bool = False):
    print("=== 人工节点 · 定义校验 ===")
    failed = test_validate()

    print("\n=== 人工节点 · 决策校验 ===")
    failed += await _run_checks(await test_decision_rules())

    print("\n=== 人工节点 · 批量处理 ===")
    failed += await _run_checks(await test_batch())

    if e2e:
        try:
            import langgraph  # noqa: F401
        except ImportError:
            print("\n(跳过端到端挂起/续跑用例：当前解释器未安装 langgraph，"
                  "请在项目运行环境执行 `python -X utf8 test_human_node.py --e2e`)")
        else:
            print("\n=== 人工节点 · 挂起 / 续跑（审批模式）===")
            failed += await _run_checks(await test_suspend_resume_approve())
            print("\n=== 人工节点 · 表单模式 ===")
            failed += await _run_checks(await test_form_mode_e2e())
    else:
        print("\n(跳过端到端挂起/续跑用例：加 --e2e 运行，需加载 LangGraph，较慢)")

    print(f"\n失败 {failed} 项")
    return failed


if __name__ == "__main__":
    code = 1
    try:
        code = asyncio.run(main("--e2e" in sys.argv))
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = _TMP_DB + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    sys.exit(1 if code else 0)
