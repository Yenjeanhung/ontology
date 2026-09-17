# -*- coding: utf-8 -*-
"""Agent 工具调用循环（Function Calling / Tool Use 的执行侧基座）。

对标 ReAct / OpenAI tool-calling 范式：
    LLM.bind_tools(工具规范) → ainvoke → 有 tool_calls？→ 执行工具 →
    ToolMessage 回填结果 → 继续推理 …… 直到 LLM 给出最终回答或达到轮数上限。

设计要点：
- 与 LLM 提供商无关：任何实现 bind_tools/ainvoke 的 LangChain ChatModel 皆可
  （providers/llm 工厂产出的 ChatOpenAI / ChatZhipuAI 等原生兼容）；
- 每轮工具调用产出 ToolCallRecord（结构化 raw 保留），供调用方做素材卡渲染；
- 事件回调 on_event 把 tool_call / tool_result 过程外抛（多智能体引擎转 SSE）；
- 兜底：达到最大轮数仍想调工具 → 摘除工具再强制回答一轮，保证循环必然终止；
- 降级：当前 LLM 不支持 bind_tools（如部分本地模型）→ 退化为单轮直接回答，
  不抛异常中断上层流水线。
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config import settings


@dataclass
class ToolCallRecord:
    """一次工具调用的完整记录（含给前端/素材卡的原始数据）。"""
    name: str
    arguments: dict = field(default_factory=dict)
    ok: bool = False
    result_text: str = ""            # 回填给 LLM 的文本（可能截断）
    raw: Any = None                  # 工具结构化返回（渲染素材卡用）
    error: str = ""
    duration_ms: int = 0


@dataclass
class ToolLoopResult:
    """工具循环最终产出。"""
    final_text: str = ""
    calls: list[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0                        # 实际经历的 LLM 轮数
    degraded: bool = False                     # True=LLM 不支持工具/无工具，退化为单轮回答
    degrade_note: str = ""


def _content_text(content: Any) -> str:
    """AIMessage.content 兼容：str 或多模态分段列表。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return ""


async def run_tool_loop(
    llm: Any,
    registry: Any,
    system: str,
    user: str,
    max_iterations: Optional[int] = None,
    turn_timeout: float = 120.0,
    tool_timeout: Optional[float] = None,
    on_event: Optional[Callable[[dict], None]] = None,
) -> ToolLoopResult:
    """运行工具调用循环，返回最终回答与全部工具调用记录。

    - max_iterations：LLM 轮数上限（默认取 settings.TOOL_LOOP_MAX_ITERATIONS）；
    - on_event：过程事件回调，事件类型 tool_call / tool_result / tool_degrade；
    - RuntimeError（LLM 未配置）原样上抛，其余 LLM 异常折入 final_text 降级返回。
    """
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    max_iterations = max_iterations or getattr(settings, "TOOL_LOOP_MAX_ITERATIONS", 4)
    result = ToolLoopResult()

    def _emit(evt: dict) -> None:
        if on_event:
            try:
                on_event(evt)
            except Exception:
                pass

    specs = registry.specs() if hasattr(registry, "specs") else []
    tool_llm = llm
    if specs:
        try:
            tool_llm = llm.bind_tools(specs)
        except Exception as exc:   # 模型不支持工具 → 单轮降级
            result.degraded = True
            result.degrade_note = f"当前 LLM 不支持 Function Calling（{type(exc).__name__}）"
            _emit({"type": "tool_degrade", "note": result.degrade_note})
            tool_llm = llm

    messages = [SystemMessage(content=system), HumanMessage(content=user)]

    for iteration in range(1, max_iterations + 1):
        result.iterations = iteration
        try:
            msg: AIMessage = await asyncio.wait_for(tool_llm.ainvoke(messages),
                                                    timeout=turn_timeout)
        except RuntimeError:
            raise   # LLM 未配置等致命错误，交上层处理
        except asyncio.TimeoutError:
            result.final_text = "（LLM 响应超时，工具循环中止）"
            return result
        except Exception as exc:
            result.final_text = f"（LLM 调用失败：{type(exc).__name__}: {exc}）"
            return result

        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            result.final_text = _content_text(msg.content)
            return result

        # 回填本轮 AIMessage（含 tool_calls），再逐个执行工具
        messages.append(msg)
        for tc in tool_calls:
            name = str(tc.get("name", ""))
            args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
            _emit({"type": "tool_call", "name": name, "arguments": args})
            t0 = time.monotonic()
            text, raw = await registry.call(name, args, timeout=tool_timeout)
            record = ToolCallRecord(
                name=name, arguments=args,
                ok=not (isinstance(raw, dict) and "error" in raw),
                result_text=text, raw=raw,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            if not record.ok:
                try:
                    record.error = str(json.loads(text).get("error", ""))
                except Exception:
                    record.error = record.result_text[:120]
            result.calls.append(record)
            _emit({"type": "tool_result", "name": name, "ok": record.ok,
                   "duration_ms": record.duration_ms,
                   "summary": record.result_text[:120]})

            tool_msg = ToolMessage(content=text, tool_call_id=str(tc.get("id", name)))
            messages.append(tool_msg)

    # 达到最大轮数仍想继续调工具 → 摘除工具强制收尾一轮，保证循环终止
    try:
        msg = await asyncio.wait_for(llm.ainvoke(messages), timeout=turn_timeout)
        result.final_text = _content_text(msg.content)
    except Exception as exc:
        result.final_text = f"（收尾回答失败：{type(exc).__name__}）"
    result.final_text += (
        f"\n（已达工具调用轮数上限 {max_iterations}，基于已获取数据收尾）"
        if result.calls else ""
    )
    return result
