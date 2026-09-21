# -*- coding: utf-8 -*-
"""两级意图路由服务（业务无关）。

【第一级】0.6B 意图路由（v6 LoRA 已训好，OpenAI 兼容服务常驻，<100ms 量级）：

    用户 query
       │
    【第一级：意图路由 0.6B】
       ├─ chat      → 大模型直答，编排不启动        ★ 收益最硬
       ├─ data      → 精简组合 + 放行给第二级
       ├─ graph/kb  → 精简组合 / 触发改写门控
       └─ 低置信    → 全组合 + 老规则兜底

【第二级】NL2Filter 0.6B（建议追加微调；LoRA 未部署时走同服务 few-shot 提示词，
仍失败则 DataAgent 回落词频老路——「抽不出/抽错 → 词频老路兜底」）：

    "今天 MU5307 航班正常率怎么样"
       → {"entity_type": "flight", "name": "MU5307",
          "metric": "normal_rate", "time_range": "today"}

设计约束：
- 路由是加速器不是单点：服务不可达 / 超时 / 解析失败一律收敛为 fallback
  决策，流水线行为与接入前完全一致（全组合 + 老规则）；
- 决策表集中在本模块（SLIM_ROSTERS），场景层只消费语义字段
  （agents / chat_direct / nl2filter / rewrite_gate），不感知 0.6B 服务细节。
"""

import json
import logging
import re
import time
from datetime import date, timedelta
from typing import Optional, Union

import httpx

from config import settings

logger = logging.getLogger(__name__)

# ── 精简组合决策表：mode → 能力智能体子集（critic 保留做质控） ──
SLIM_ROSTERS: dict[str, list[str]] = {
    "data": ["data_agent", "graph_agent", "critic"],
    "graph": ["graph_agent", "critic"],
    "kb": ["retriever", "critic"],
}

VALID_MODES = {"chat", "data", "graph", "kb"}
NL2FILTER_KEYS = ("entity_type", "name", "metric", "time_range")

# 第一级路由提示词（v6 服务内置分类输出 {mode, confidence} 时仅兜底对齐）
_INTENT_SYSTEM = (
    "你是意图路由器。判断用户请求属于哪一类，只输出 JSON："
    '{"mode": "chat|data|graph|kb", "confidence": 0~1}。'
    "chat=闲聊问答/写作等通用请求；data=查实体台账结构化数据（数量/统计/明细）；"
    "graph=查实体之间关联关系；kb=查知识库文档资料；无法判断时 mode 取最可能的一类"
    "并给低 confidence。不要输出其他文字。"
)

# 第二级 NL2Filter few-shot（LoRA 未部署时的同服务兜底抽取）
_NL2F_SYSTEM = (
    "你是结构化查询抽取器。把用户的台账数据问题抽成 JSON filter，键固定为："
    'entity_type（实体类型，英文或中文）、name（实体名称）、metric（指标，英文蛇形）、'
    'time_range（today/yesterday/this_week/this_month 或留空）。'
    "只输出一个 JSON 对象，不要输出多个、不要输出数组。"
    "抽不出的键留空字符串。示例：\n"
    '问：今天 MU5307 航班正常率怎么样\n'
    '答：{"entity_type": "flight", "name": "MU5307", "metric": "normal_rate", '
    '"time_range": "today"}\n'
    '问：本月告警总量多少\n'
    '答：{"entity_type": "alert", "name": "", "metric": "total_count", '
    '"time_range": "this_month"}\n'
    '问：张三参与过哪些项目\n'
    '答：{"entity_type": "person", "name": "张三", "metric": "", "time_range": ""}'
)

_FALLBACK: dict = {
    "source": "fallback",   # router=走了 0.6B 路由；fallback=低置信/服务不可达，老规则
    "mode": "fuzzy",
    "confidence": 0.0,
    "agents": None,         # None = 默认全组合
    "chat_direct": False,
    "nl2filter": False,
    "rewrite_gate": False,
}


def _short(text: str, limit: int = 60) -> str:
    """日志用：压平换行并截断，避免长 query 刷屏。"""
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


async def _chat_once(system: str, user: str, *, url: str, model: str,
                     timeout: float, tag: str = "0.6B") -> str:
    """OpenAI 兼容 /chat/completions 单轮调用，返回 content 文本（失败向上抛）。"""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.0,
        "max_tokens": 160,
    }
    t0 = time.perf_counter()
    # trust_env=False：0.6B 是本机/内网服务，禁用系统代理（HTTP_PROXY），
    # 否则请求会被代理劫持（如 127.0.0.1:7898）→ 全部 ReadTimeout 白等 3s 兜底
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            # 个别服务不认 chat_template_kwargs（Qwen3 关思考压延迟），去掉重试一次
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    data = resp.json()
    logger.info("[%s] 0.6B 调用成功 model=%s 耗时=%.0fms task=%s",
                tag, model, (time.perf_counter() - t0) * 1000, _short(user))
    return (data["choices"][0]["message"].get("content") or "").strip()


def _parse_json_block(text: str) -> Optional[Union[dict, list]]:
    """从模型输出宽松提取第一个 JSON 对象/数组（容忍围栏与前后缀文本）。

    dict 与 list 均原样返回，由调用方按形态分支处理（数组 = 多 filter 意图）。
    """
    m = re.search(r"[\[{].*[\]}]", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


async def route_intent(task: str) -> Optional[dict]:
    """第一级：调 0.6B 路由服务。返回 {"mode", "confidence"}；不可用返回 None。"""
    url = (getattr(settings, "INTENT_ROUTER_URL", "") or "").strip()
    if not url:
        return None
    raw = await _chat_once(
        _INTENT_SYSTEM, task,
        url=url,
        model=getattr(settings, "INTENT_ROUTER_MODEL", "qwen3-0.6b-router"),
        timeout=float(getattr(settings, "INTENT_ROUTER_TIMEOUT", 3.0)),
        tag="意图路由",
    )
    data = _parse_json_block(raw)
    if not isinstance(data, dict):
        logger.warning("[意图路由] 输出解析失败 raw=%s → 回落全组合", _short(raw, 120))
        return None
    mode = str(data.get("mode") or data.get("intent") or "").strip().lower()
    try:
        conf = float(data.get("confidence", data.get("score", 0.5)))
    except (TypeError, ValueError):
        conf = 0.5
    if mode not in VALID_MODES:
        logger.warning("[意图路由] 非法 mode=%r → 回落全组合", mode)
        return None
    return {"mode": mode, "confidence": max(0.0, min(conf, 1.0))}


async def routing_decision(task: str) -> dict:
    """完整决策表（永不抛异常，任何失败回落 fallback=全组合老规则）。

    返回字段：
    - source: "router" | "fallback"
    - mode / confidence: 路由标签与置信度
    - agents: 精简组合列表（list[str]）或 None（=默认全组合）
    - chat_direct: chat 高置信 → 大模型直答，编排不启动
    - nl2filter:   data 类 → 放行第二级 NL2Filter
    - rewrite_gate: kb 类 → Retriever 检索前触发改写门控
    """
    if not getattr(settings, "INTENT_ROUTING_ENABLED", True):
        logger.debug("[意图路由] 开关关闭 → 全组合老规则（不调 0.6B）")
        return dict(_FALLBACK)
    try:
        r = await route_intent(task)
    except Exception as exc:
        logger.warning("[意图路由] 0.6B 服务不可达（%s: %s）→ 全组合老规则兜底",
                       type(exc).__name__, exc)
        return dict(_FALLBACK)
    if not r:
        return dict(_FALLBACK)

    mode, conf = r["mode"], r["confidence"]
    if conf < float(getattr(settings, "INTENT_CONF_MIN", 0.6)):
        # 低置信 → 全组合 + 老规则兜底（决策可见，行为不变）
        logger.info("[意图路由] mode=%s conf=%.2f 低于阈值 %.2f → 全组合老规则兜底",
                    mode, conf, float(getattr(settings, "INTENT_CONF_MIN", 0.6)))
        return {**_FALLBACK, "source": "router", "mode": mode, "confidence": conf}

    out = {**_FALLBACK, "source": "router", "mode": mode, "confidence": conf}
    if mode == "chat":
        out["chat_direct"] = True
        logger.info("[意图路由] mode=chat conf=%.2f → 大模型直答（编排不启动）", conf)
    elif mode in SLIM_ROSTERS:
        out["agents"] = list(SLIM_ROSTERS[mode])
        out["nl2filter"] = (mode == "data") and bool(
            getattr(settings, "NL2FILTER_ENABLED", True))
        out["rewrite_gate"] = (mode == "kb") and bool(
            getattr(settings, "KB_REWRITE_GATE", True))
        logger.info("[意图路由] mode=%s conf=%.2f → 精简组合 [%s]%s%s",
                    mode, conf, ",".join(out["agents"]),
                    " + NL2Filter" if out["nl2filter"] else "",
                    " + 改写门控" if out["rewrite_gate"] else "")
    return out


# ── 任务澄清判定（第三复用：同 0.6B 服务多一档提示词模式） ──────────────

_CLARIFY_SYSTEM = (
    "你是任务澄清器。判断用户的任务是否缺少完成所必需的关键信息"
    "（如对象/范围/时间范围/统计口径/输出形式等）。信息充足只输出 {\"need_clarify\": false}；"
    "信息不足输出 {\"need_clarify\": true, \"question\": \"一句话澄清提问\", "
    "\"options\": [\"候选项1\", \"候选项2\", \"候选项3\"]}，候选项 2~4 个、每个不超过 20 字。"
    "只输出 JSON，不要输出任何其他文字。"
)


async def clarify_check(task: str) -> Optional[dict]:
    """澄清判定（永不抛异常）：任务信息不足返回 {"question", "options"}，充足/不可用返回 None。

    复用 0.6B 路由服务（CLARIFY_URL → NL2FILTER_URL → INTENT_ROUTER_URL 同源策略）；
    开关 CLARIFY_ENABLED=False 或服务不可达/输出不合法时一律放行不澄清（行为与接入前一致）。
    """
    if not getattr(settings, "CLARIFY_ENABLED", True):
        return None
    url = ((getattr(settings, "CLARIFY_URL", "") or "").strip()
           or (getattr(settings, "NL2FILTER_URL", "") or "").strip()
           or (getattr(settings, "INTENT_ROUTER_URL", "") or "").strip())
    if not url:
        return None
    try:
        raw = await _chat_once(
            _CLARIFY_SYSTEM, task,
            url=url,
            model=getattr(settings, "CLARIFY_MODEL", "qwen3-0.6b-router"),
            timeout=float(getattr(settings, "CLARIFY_TIMEOUT", 5.0)),
            tag="澄清判定",
        )
    except Exception as exc:
        logger.warning("[澄清判定] 0.6B 调用失败（%s: %s）→ 不澄清直接执行",
                       type(exc).__name__, exc)
        return None
    data = _parse_json_block(raw)
    if not isinstance(data, dict) or not data.get("need_clarify"):
        return None
    question = str(data.get("question") or "").strip()
    options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()][:4]
    if not question or not options:
        return None
    logger.info("[澄清判定] 任务信息不足 → 澄清：%s 选项：%s", _short(question, 60), options)
    return {"question": question, "options": options}


def _time_floor(time_range: str) -> tuple[Optional[str], str]:
    """time_range 词 → created_at 下界（ISO 字符串可直接比较）与口径标签。"""
    mapping = {
        "today": (date.today().isoformat(), "今天"),
        "yesterday": ((date.today() - timedelta(days=1)).isoformat(), "昨天"),
        "this_week": (
            (date.today() - timedelta(days=date.today().weekday())).isoformat(), "本周"),
        "this_month": (date.today().replace(day=1).isoformat(), "本月"),
    }
    key = (time_range or "").strip().lower().replace("-", "_").replace(" ", "_")
    return mapping.get(key, (None, ""))


def normalize_filter(data: dict) -> Optional[dict]:
    """模型输出 → 规范 filter；抽不到任何定位条件返回 None（词频老路兜底）。"""
    out = {k: str(data.get(k) or "").strip() for k in NL2FILTER_KEYS}
    floor, label = _time_floor(out["time_range"])
    out["time_range"] = (label or out["time_range"]) if floor else ""
    if not (out["entity_type"] or out["name"]):
        return None
    return out


async def nl2filter(task: str) -> Optional[dict]:
    """第二级：data 类任务的 NL2Filter 抽取。任何失败 → None（DataAgent 词频老路）。

    NL2FILTER_URL 留空时复用 INTENT_ROUTER_URL（同服务双模式 / 多 adapter）；
    专属 LoRA 部署后配独立 URL + NL2FILTER_MODEL 即切换，代码零改动。
    """
    url = ((getattr(settings, "NL2FILTER_URL", "") or "").strip()
           or (getattr(settings, "INTENT_ROUTER_URL", "") or "").strip())
    if not url:
        return None
    try:
        raw = await _chat_once(
            _NL2F_SYSTEM, task,
            url=url,
            model=getattr(settings, "NL2FILTER_MODEL", "qwen3-0.6b-nl2filter"),
            timeout=float(getattr(settings, "NL2FILTER_TIMEOUT", 3.0)),
            tag="NL2Filter",
        )
    except Exception as exc:
        logger.warning("[NL2Filter] 0.6B 调用失败（%s: %s）→ 词频老路兜底",
                       type(exc).__name__, exc)
        return None   # 服务不可达 / 超时 → 词频老路兜底
    data = _parse_json_block(raw)
    if not isinstance(data, dict):
        # 多 filter 形态（数组包裹 / 逗号并列）= 分组统计意图（如"高、中、低各有多少"）：
        # 单 filter 口径只能覆盖一组、必然答非所问 → 词频老路的聚合统计可正确分组
        if isinstance(data, list):
            logger.info("[NL2Filter] 输出 JSON 数组（多 filter，分组统计意图）→ 词频老路兜底")
        elif data is None and len(re.findall(r"\{[^{}]*\}", raw or "")) > 1:
            logger.info("[NL2Filter] 并列输出多个 filter（分组统计意图）→ 词频老路兜底")
        else:
            logger.warning("[NL2Filter] 输出解析失败 raw=%s → 词频老路兜底",
                           _short(raw, 120))
        return None
    out = normalize_filter(data)
    if out is None:
        logger.info("[NL2Filter] 未抽出定位条件 → DataAgent 词频老路兜底")
    else:
        logger.info("[NL2Filter] 抽取成功 filter=%s", out)
    return out
