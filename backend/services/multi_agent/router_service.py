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

# 第一级路由提示词（v6 服务内置分类输出 {mode, confidence} 时仅兜底对齐）。
# 判性边界（v6.4 补）：0.6B 曾把「CA1503有哪些乘客」判成 graph（0.93）——它把
# "航班→乘客"理解成实体关联；但名单/明细/有哪些X 语义上是台账结构化查询（data），
# 只有问实体之间的关联/关系网络（谁和谁有关系/关系链/共现网络）才是 graph。
_INTENT_SYSTEM = (
    "你是意图路由器。判断用户请求属于哪一类，只输出 JSON："
    '{"mode": "chat|data|graph|kb", "confidence": 0~1}。'
    "chat=闲聊问答/写作等通用请求；data=查实体台账结构化数据（数量/统计/明细/名单/清单）；"
    "graph=查实体之间关联关系（谁与谁关联/关系链/共现网络）；kb=查知识库文档资料；"
    "无法判断时 mode 取最可能的一类并给低 confidence。"
    "注意：「X有哪些Y」「X的Y名单/清单/明细」是查台账数据=data；"
    "例：CA1503有哪些乘客→data；MU5307的机组名单→data；"
    "CA1503与哪些航班共用机型→graph。不要输出其他文字。"
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
    "注意：只罗列了几个名词/实体、没说要做什么的任务属于信息不足，必须判 true。"
    "示例：\n"
    "问：北京重庆\n"
    "答：{\"need_clarify\": true, \"question\": \"你想了解北京和重庆的什么？\", "
    "\"options\": [\"城市概况介绍\", \"多维度对比分析\", \"两地旅游攻略\"]}\n"
    "问：对比分析北京和重庆\n答：{\"need_clarify\": false}\n"
    "问：介绍一下北京的城市概况\n答：{\"need_clarify\": false}\n"
    "问：CA1503有哪些乘客\n答：{\"need_clarify\": false}\n"
    "（有明确查询对象+要查的内容时不算信息不足，即使没有更多细节）"
    "只输出 JSON，不要输出任何其他文字。"
)

# 超短模糊任务本地短路：0.6B 对「纯名词串」判定不可靠（实测 need_clarify 恒 false），
# 任务过短且无任何意图动词时不再依赖模型，本地直接判澄清（确定性兜底）。
_VAGUE_LEN = 16
_INTENT_HINT_RE = re.compile(
    r"介绍|对比|分析|查询|查一下|查找|列出|统计|总结|摘要|生成|撰写|编写|翻译|解释|说明"
    r"|多少|哪些|什么|怎么|如何|为什么|帮我|帮忙|请|看看|了解|搜索|找一?找|评估|预测|报告")
_VAGUE_QUESTION = "这个任务比较简短，你想让我具体做什么？（可从下面选，或自己填写）"
_VAGUE_OPTIONS = ["介绍相关概况", "多维度对比分析", "查询相关数据", "总结生成报告"]


def _vague_task(task: str) -> bool:
    """超短且无意图动词 → 视为信息不足（如「北京重庆」「CA1503和MU5307」）。

    完整疑问句（「…吗/…呢/…？」）不拦：用户已给出明确问题，澄清反而打断
    （如「北京到南京有航班吗」应直接进 data 链路查询）。
    """
    t = (task or "").strip()
    if len(t) >= _VAGUE_LEN or not t:
        return False
    if _INTENT_HINT_RE.search(t) or re.search(r"[吗呢]$|[？?]\s*$", t):
        return False
    return True


# 澄清选项生成器（只生成不判定——0.6B 判 need_clarify 不可靠，但生成贴合任务的
# 问句/候选项没问题）：是否澄清由本地规则/判定先行确定，这里只负责产出动态选项。
_CLARIFY_GEN_SYSTEM = (
    "你是澄清选项生成器。用户的任务信息不足需要澄清，请针对这个任务生成"
    "一句话澄清提问和 2~4 个最相关的候选项（每个不超过 20 字）。"
    "候选项必须贴合任务内容（如航班类任务给「查询航班时刻」「查询票价」），"
    "禁止给「介绍概况」「生成报告」这类与任务无关的泛泛选项。"
    "只输出 JSON：{\"question\": \"一句话提问\", \"options\": [\"候选项\", \"…\"]}，"
    "不要输出任何其他文字。"
)


async def _clarify_generate(task: str, url: str, model: str) -> Optional[dict]:
    """针对信息不足任务动态生成澄清问句与候选项；失败返回 None（调用方通用选项兜底）。"""
    try:
        raw = await _chat_once(
            _CLARIFY_GEN_SYSTEM, task,
            url=url, model=model,
            timeout=float(getattr(settings, "CLARIFY_TIMEOUT", 5.0)),
            tag="澄清生成",
        )
    except Exception as exc:
        logger.warning("[澄清生成] 0.6B 调用失败（%s: %s）→ 通用选项兜底",
                       type(exc).__name__, exc)
        return None
    data = _parse_json_block(raw)
    if not isinstance(data, dict):
        return None
    question = str(data.get("question") or "").strip()
    options = [str(o).strip() for o in (data.get("options") or []) if str(o).strip()][:4]
    if not question or not options:
        return None
    return {"question": question, "options": options}


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
    # model 与 url 同源回退：CLARIFY_MODEL 留空 → NL2FILTER_MODEL → INTENT_ROUTER_MODEL
    # （此前 config 默认值 "qwen3-0.6b-router" 与实际部署名 qwen06b-router 不一致 → 404 → 静默放行）
    model = ((getattr(settings, "CLARIFY_MODEL", "") or "").strip()
             or (getattr(settings, "NL2FILTER_MODEL", "") or "").strip()
             or (getattr(settings, "INTENT_ROUTER_MODEL", "") or "").strip()
             or "qwen3-0.6b-router")
    # 超短模糊任务：是否澄清由本地规则确定（不依赖 0.6B 判性），
    # 候选项动态生成贴合任务内容；生成失败回退通用四选项
    if _vague_task(task):
        gen = await _clarify_generate(task, url, model) if url else None
        if gen:
            logger.info("[澄清判定] 超短任务 → 动态澄清选项：%s %s",
                        _short(gen["question"], 60), gen["options"])
            return gen
        logger.info("[澄清判定] 超短任务无意图词 → 通用澄清：%r", _short(task))
        return {"question": _VAGUE_QUESTION, "options": _VAGUE_OPTIONS}
    if not url:
        return None
    try:
        raw = await _chat_once(
            _CLARIFY_SYSTEM, task,
            url=url,
            model=model,
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
