# -*- coding: utf-8 -*-
"""
多智能体场景适配器：基类 + 注册表。

多智能体能力本身与业务无关；具体业务场景（航班告警复核、合同风控、
设备巡检……）以适配器形式接入：
- 声明场景元信息（名称 / 所属业务 / 描述）；
- list_targets()：返回该场景可研判对象的通用目标卡；
- build_engine(target_id)：为某个目标构建 MultiAgentEngine（提供团队、
  计划、节点实现与上下文）。

路由层与前端页面只面向本注册表编程，新增场景零改动扩散。
"""

from typing import Optional

from ..engine import MultiAgentEngine


class MultiAgentScenario:
    """场景适配器基类。"""

    scenario_id: str = ""
    name: str = ""
    business: str = ""       # 所属业务域，如「航班运行监控」
    description: str = ""
    adhoc: bool = False      # True = 支持自由任务输入（build_engine_from_task）

    async def list_targets(self) -> list[dict]:
        """可研判对象列表（通用目标卡，异步——场景可能查库/调远程）。

        通用卡字段约定：
        - id: 目标唯一标识（拼进 run 接口路径）
        - title / subtitle / headline: 展示用主副标题与一句话摘要
        - level / level_label: 风险/紧急等级（可选，前端着色）
        - details: 明细行（字符串数组）
        - runnable: 是否可发起多智能体研判
        - run_label: 发起按钮文案（如「AI 复核」）
        - badge: 来源徽标文案（可选，如「平台数据」「生成数据」）
        - note: 不可发起时的原因（克制边界提示）
        adhoc 场景的卡可作为「示例任务」：额外带 task 字段（任务全文）。
        """
        raise NotImplementedError

    async def build_engine(self, target_id: str) -> MultiAgentEngine:
        """为目标构建执行引擎；目标不存在时抛 KeyError（路由层转 404）。"""
        raise NotImplementedError

    async def target_task(self, target_id: str) -> str:
        """目标对应的任务文本（深度模式取任务用）；目标不存在返回空串。

        深度路径不走 build_engine（那是普通团队装配），路由层先取任务全文
        再交 DeepAgents 自主规划；默认不支持（空串 = 路由层 404）。
        """
        return ""

    async def build_engine_from_task(
        self, task: str, agents: Optional[list[str]] = None
    ) -> MultiAgentEngine:
        """自由任务入口（仅 adhoc 场景实现）；agents 为可选组合（能力智能体 id），
        None = 场景默认组合；不支持时路由层转 400。"""
        raise NotImplementedError(f"场景 {self.scenario_id} 不支持自由任务输入")

    def meta(self) -> dict:
        return {
            "id": self.scenario_id,
            "name": self.name,
            "business": self.business,
            "description": self.description,
            "adhoc": self.adhoc,
        }


_REGISTRY: dict[str, MultiAgentScenario] = {}


def register(scenario: MultiAgentScenario) -> None:
    if not scenario.scenario_id:
        raise ValueError("scenario_id 不能为空")
    _REGISTRY[scenario.scenario_id] = scenario


def get_scenario(scenario_id: str) -> Optional[MultiAgentScenario]:
    return _REGISTRY.get(scenario_id)


def list_scenarios() -> list[dict]:
    return [s.meta() for s in _REGISTRY.values()]


# 注册内置场景：仅通用智能体团队（universal）——多智能体是动态的，任意任务
# 类型皆可跑，不绑定业务。新业务如需专属编排，实现 MultiAgentScenario 适配器
# 后在此追加一行 import 即可接入，路由与引擎零改动。
from . import universal as _universal      # noqa: E402,F401  (注册副作用)
