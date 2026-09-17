"""Function Calling 工具链单元测试（ToolRegistry + run_tool_loop）。

运行：cd backend && python -m pytest test/test_tool_loop.py -q
不依赖真实 LLM / 数据库：脚本化 FakeLLM + 假工具处理器。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import AIMessage, ToolMessage

from services.agent_loop import ToolCallRecord, run_tool_loop
from services.tool_registry import Tool, ToolRegistry


class FakeLLM:
    """脚本化假 LLM：按序返回预设 AIMessage，记录 bind_tools 与消息演化。"""

    def __init__(self, script, fail_bind=False):
        self.script = list(script)
        self.fail_bind = fail_bind
        self.bound_specs = None
        self.seen_messages = []

    def bind_tools(self, specs):
        if self.fail_bind:
            raise AttributeError("FakeLLM does not support bind_tools")
        self.bound_specs = specs
        return self

    async def ainvoke(self, messages):
        self.seen_messages.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _make_registry():
    reg = ToolRegistry()

    async def add(a: int, b: int = 0) -> dict:
        return {"sum": a + b}

    async def boom(x: str) -> dict:
        raise ValueError("工具内部炸了")

    reg.register(Tool(
        name="add", description="加法",
        parameters={"type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a"]},
        handler=add,
    ))
    reg.register(Tool(
        name="boom", description="必炸工具",
        parameters={"type": "object", "properties": {"x": {"type": "string"}},
                    "required": ["x"]},
        handler=boom,
    ))
    return reg


# ───────────────────────── ToolRegistry ─────────────────────────

def test_registry_specs_format():
    reg = _make_registry()
    specs = reg.specs()
    assert specs[0]["type"] == "function"
    fn = specs[0]["function"]
    assert fn["name"] == "add"
    assert fn["parameters"]["required"] == ["a"]
    assert reg.names() == ["add", "boom"]


def test_registry_call_ok_and_raw():
    reg = _make_registry()
    text, raw = asyncio.run(reg.call("add", {"a": 2, "b": 3}))
    assert json.loads(text) == {"sum": 5}
    assert raw == {"sum": 5}


def test_registry_call_json_string_args():
    """LLM 可能给 JSON 字符串形式的 arguments —— 容错解析。"""
    reg = _make_registry()
    text, raw = asyncio.run(reg.call("add", '{"a": 1}'))
    assert raw == {"sum": 1}


def test_registry_call_unknown_tool():
    reg = _make_registry()
    text, raw = asyncio.run(reg.call("nope", {}))
    assert "未知工具" in text
    assert "error" in raw


def test_registry_call_tool_error_wrapped():
    """工具内部异常 → 包装为 error 文本回传 LLM，不向上抛。"""
    reg = _make_registry()
    text, raw = asyncio.run(reg.call("boom", {"x": "1"}))
    assert "工具内部炸了" in text
    assert "工具内部炸了" in raw["error"]


def test_registry_call_bad_args():
    """参数缺失/类型错 → TypeError 包装为参数错误提示（LLM 侧附带期望参数 schema）。"""
    reg = _make_registry()
    text, raw = asyncio.run(reg.call("add", {"b": 1}))
    assert "参数错误" in text
    assert "expected" in json.loads(text)   # 期望 schema 只在回填 LLM 的文本里
    assert "参数错误" in raw["error"]


def test_registry_result_truncated():
    reg = ToolRegistry()

    async def big() -> dict:
        return {"data": "x" * 99999}

    reg.register(Tool(name="big", description="", parameters={"type": "object", "properties": {}},
                      handler=big))
    text, _ = asyncio.run(reg.call("big", {}))
    assert len(text) < 99999
    assert text.endswith("字符）")


# ───────────────────────── run_tool_loop ─────────────────────────

def test_loop_two_rounds_with_tool():
    """第一轮发起 tool_call → 工具执行 → ToolMessage 回填 → 第二轮最终回答。"""
    reg = _make_registry()
    llm = FakeLLM([
        AIMessage(content="", tool_calls=[
            {"name": "add", "args": {"a": 2, "b": 40}, "id": "call_1"}]),
        AIMessage(content="答案是 42"),
    ])
    result = asyncio.run(run_tool_loop(llm, reg, system="s", user="u"))

    assert llm.bound_specs is not None                 # bind_tools 被调用
    assert result.final_text == "答案是 42"
    assert result.iterations == 2
    assert not result.degraded
    assert len(result.calls) == 1
    c = result.calls[0]
    assert isinstance(c, ToolCallRecord)
    assert c.ok and c.name == "add" and c.raw == {"sum": 42}
    # 第二轮消息里应含 AIMessage(tool_calls) + ToolMessage(tool_call_id 匹配)
    second = llm.seen_messages[1]
    tool_msgs = [m for m in second if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].tool_call_id == "call_1"
    assert "42" in tool_msgs[0].content


def test_loop_max_iterations_forced_finish():
    """持续请求工具 → 达到轮数上限后摘除工具强制收尾，循环必然终止。"""
    reg = _make_registry()
    looping = AIMessage(content="", tool_calls=[
        {"name": "add", "args": {"a": 1}, "id": "call_x"}])
    llm = FakeLLM([looping, looping, AIMessage(content="收尾回答")])
    events = []
    result = asyncio.run(run_tool_loop(llm, reg, system="s", user="u",
                                       max_iterations=2,
                                       on_event=events.append))
    assert result.iterations == 2
    assert len(result.calls) == 2
    assert "已达工具调用轮数上限" in result.final_text
    assert "收尾回答" in result.final_text
    # 事件外抛：tool_call / tool_result 各两次
    assert sum(1 for e in events if e["type"] == "tool_call") == 2
    assert sum(1 for e in events if e["type"] == "tool_result") == 2


def test_loop_degrade_when_bind_fails():
    """模型不支持 bind_tools → 降级为单轮直接回答，不抛异常。"""
    reg = _make_registry()
    llm = FakeLLM([AIMessage(content="无工具回答")], fail_bind=True)
    result = asyncio.run(run_tool_loop(llm, reg, system="s", user="u"))
    assert result.degraded
    assert "不支持 Function Calling" in result.degrade_note
    assert result.final_text == "无工具回答"
    assert result.calls == []


def test_loop_empty_registry_direct_answer():
    """无工具可用 → 直接单轮回答（不 bind）。"""
    llm = FakeLLM([AIMessage(content="直接回答")])
    result = asyncio.run(run_tool_loop(llm, ToolRegistry(), system="s", user="u"))
    assert result.final_text == "直接回答"
    assert llm.bound_specs is None
    assert result.iterations == 1


def test_loop_tool_error_feeds_back_and_recovers():
    """工具报错不中断循环：错误文本回填后 LLM 继续给出最终回答。"""
    reg = _make_registry()
    llm = FakeLLM([
        AIMessage(content="", tool_calls=[{"name": "boom", "args": {"x": "1"}, "id": "c1"}]),
        AIMessage(content="工具失败了，我换了个说法"),
    ])
    result = asyncio.run(run_tool_loop(llm, reg, system="s", user="u"))
    assert result.final_text == "工具失败了，我换了个说法"
    assert not result.calls[0].ok
    assert "工具内部炸了" in result.calls[0].error


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
