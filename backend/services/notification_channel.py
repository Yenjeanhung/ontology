"""外发通知渠道：统一出口 + 事件契约。

当前状态：**v1 骨架** —— 系统尚无邮件 / Webhook / 企业微信 / 钉钉等渠道实现，
`settings.NOTIFY_CHANNELS` 默认为空，dispatch 只落日志。

后续对接方式（业务代码零改动）：
1. 在 `_HANDLERS` 注册渠道名 → 处理函数；
2. 在配置中启用：`NOTIFY_CHANNELS=webhook,email`。

设计约束：任何异常都必须吞掉，绝不能因通知失败影响工作流主流程。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from config import settings

logger = logging.getLogger(__name__)

# 事件名常量
TASK_CREATED = "human_task.created"
TASK_DECIDED = "human_task.decided"
TASK_TIMEOUT = "human_task.timeout"

_Handler = Callable[[str, dict], Awaitable[None]]


async def _noop_handler(event: str, payload: dict) -> None:
    """占位渠道：仅记录日志。真实渠道接入前用于验证链路。"""


# 渠道注册表：渠道名 → 处理函数（v1 为空，见模块 docstring）
_HANDLERS: dict[str, _Handler] = {}


class NotificationChannel:
    """外发通知统一出口。"""

    # 事件名（同时以模块级常量暴露，便于调用方按需引用）
    TASK_CREATED = TASK_CREATED
    TASK_DECIDED = TASK_DECIDED
    TASK_TIMEOUT = TASK_TIMEOUT

    @staticmethod
    def enabled_channels() -> list[str]:
        return [c.strip() for c in (settings.NOTIFY_CHANNELS or "").split(",") if c.strip()]

    @staticmethod
    async def dispatch(event: str, payload: dict) -> None:
        """发送一个通知事件；未启用渠道时仅打日志，异常一律忽略。"""
        try:
            channels = NotificationChannel.enabled_channels()
            if not channels:
                logger.info("[notify] %s %s", event, payload)
                return
            for name in channels:
                handler = _HANDLERS.get(name)
                if handler is None:
                    logger.warning("[notify] 未注册的渠道：%s（事件 %s 未外发）", name, event)
                    continue
                await handler(event, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("[notify] 外发失败（已忽略）：%s", event)
