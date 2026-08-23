"""定时调度模块验证脚本（纯逻辑 + 集成）。

运行方式（在 backend 目录下）：
    python test_scheduler.py

不依赖 LLM / 向量库 / KG，仅验证：
  - 触发器校验（cron / interval / once）
  - input_params 强校验（必填、未知字段、类型）
  - next_run 计算
  - Schedule CRUD + 触发器摘要（需临时 SQLite）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="scheduler_test_"))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}")
os.environ.setdefault("SCHEDULER_ENABLED", "false")  # 测试不启动引擎
os.environ.setdefault("SCHEDULER_TIMEZONE", "Asia/Shanghai")

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from services import scheduler_service as svc  # noqa: E402
from database import init_db, async_session  # noqa: E402
from models import Schedule, Workflow  # noqa: E402


def assert_eq(a, b, msg):
    if a != b:
        raise AssertionError(f"{msg}: 期望 {b!r}，实际 {a!r}")
    print(f"  [OK] {msg}")


# ───────────────────────── 纯逻辑测试 ─────────────────────────

def test_trigger_validation():
    print("[测试] 触发器校验")
    assert_eq(svc.validate_trigger("cron", {"minute": "0", "hour": "8"}), None, "合法 cron")
    assert_eq(svc.validate_trigger("cron", {"minute": "99", "hour": "8"}) is not None, True, "非法 cron 分钟被拒")
    assert_eq(svc.validate_trigger("interval", {"every": 30, "unit": "minutes"}), None, "合法 interval")
    assert_eq(svc.validate_trigger("interval", {"every": 0, "unit": "minutes"}) is not None, True, "interval every<=0 被拒")
    assert_eq(svc.validate_trigger("interval", {"every": 1, "unit": "years"}) is not None, True, "非法 interval unit 被拒")
    assert_eq(svc.validate_trigger("once", {"run_at": "2026-09-01T09:00:00"}), None, "合法 once")
    assert_eq(svc.validate_trigger("once", {"run_at": "not-a-date"}) is not None, True, "非法 once 时间被拒")
    assert_eq(svc.validate_trigger("bogus", {}) is not None, True, "未知触发器被拒")


def test_input_validation():
    print("[测试] input_params 强校验")
    wf_def = {
        "nodes": [{
            "type": "start",
            "data": {"config": {"inputs": [
                {"name": "q", "label": "问题", "type": "text", "required": True},
                {"name": "n", "label": "次数", "type": "number", "required": False},
            ]}},
        }]
    }
    assert_eq(svc.validate_inputs(wf_def, {"q": "hi"}) is None, True, "必填齐全通过")
    assert_eq(svc.validate_inputs(wf_def, {}) is not None, True, "缺必填被拒")
    assert_eq(svc.validate_inputs(wf_def, {"q": "hi", "wrong": 1}) is not None, True, "未知字段被拒")
    assert_eq(svc.validate_inputs(wf_def, {"q": "hi", "n": "x"}) is not None, True, "类型不符被拒")
    assert_eq(svc.validate_inputs(wf_def, {"q": "hi", "n": 3}) is None, True, "选填数字类型正确通过")


def test_next_run():
    print("[测试] next_run 计算")
    nxt = svc.compute_next_run("cron", {"minute": "0", "hour": "8"})
    assert_eq(nxt is not None, True, "cron 返回下次运行时间")
    nxt = svc.compute_next_run("interval", {"every": 30, "unit": "minutes"})
    assert_eq(nxt is not None, True, "interval 返回下次运行时间")
    assert_eq(svc.trigger_summary("cron", {"minute": "0", "hour": "8", "day": "*", "month": "*", "day_of_week": "*"}),
              "每天 8:00", "cron 摘要正确")
    assert_eq(svc.trigger_summary("interval", {"every": 30, "unit": "minutes"}),
              "每 30 minutes", "interval 摘要正确")


# ───────────────────────── 集成测试 ─────────────────────────

async def test_crud():
    print("[测试] Schedule CRUD 集成")
    await init_db()

    # 准备一个工作流
    async with async_session() as db:
        wf = Workflow(name="测试工作流", definition=json.dumps({
            "nodes": [{"type": "start", "data": {"config": {"inputs": [
                {"name": "q", "type": "text", "required": True}]}}}]
        }))
        db.add(wf)
        await db.commit()
        await db.refresh(wf)
        wf_id = wf.id

    # 创建
    s = await svc.create_schedule({
        "name": "每日早报", "workflow_id": wf_id, "trigger": "cron",
        "trigger_config": {"minute": "0", "hour": "8"}, "input_params": {"q": "早报"},
    })
    sid = s["id"]
    assert_eq(s["enabled"], True, "新建默认启用")
    assert_eq(s["next_run_at"] is not None, True, "新建计算 next_run_at")

    # 强校验拦截：未知入参
    try:
        await svc.create_schedule({
            "name": "x", "workflow_id": wf_id, "trigger": "cron",
            "trigger_config": {"minute": "0", "hour": "8"}, "input_params": {"bad": 1},
        })
        raise AssertionError("未知入参未被拦截")
    except ValueError:
        print("  ✓ 未知入参被 create 拦截")

    # 列表
    rows = await svc.list_schedules()
    assert_eq(len(rows) >= 1, True, "列表包含计划")
    assert_eq(rows[0]["workflow_name"], "测试工作流", "列表携带工作流名")

    # 启停
    s2 = await svc.set_enabled(sid, False)
    assert_eq(s2["enabled"], False, "停用成功")
    assert_eq(s2["next_run_at"], None, "停用时清空 next_run_at")

    # 删除
    await svc.delete_schedule(sid)
    assert_eq(await svc.get_schedule(sid) is None, True, "删除后查不到")


def json():
    return __import__("json")


async def main():
    test_trigger_validation()
    test_input_validation()
    test_next_run()
    await test_crud()
    print("\nAll scheduler tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
