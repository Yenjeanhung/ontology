# -*- coding: utf-8 -*-
"""
航班运行监控场景 · 演示数据（场景一：组合告警复核）。

业务底稿：《G 航空公司航班运行风险管理研究》——规则引擎（Drools）负责告警
"发现"（论文对比实验召回率 0.96，误报 8923 起）；本模块模拟规则引擎产出的
告警卡片与多源证据包，全部为**内置演示数据**（界面显著标注）。

真实接入时，仅需把 evidence_pack / fact_pack 的取数替换为航班动态 / 气象 /
机组排班等业务系统接口与 Kuzu 图谱查询，流水线与接口契约不变。
"""

# ── 证据分级（对应《多智能体交互.md》§6：图谱事实 > 有出处文档 > 值班记录 > 模型知识）──
GRADE_PRIORITY = {
    "graph_fact": 5,       # 图谱事实（本体查询结果）
    "sourced_doc": 4,      # 有出处文档（手册条款、气象报文、系统记录）
    "duty_record": 3,      # 值班记录（新近人工事实）
    "model_knowledge": 1,  # 模型参数知识（兜底）
}

# 各取证域的冲突话题名（critic 冲突卡展示用；域对齐 flight_store 证据包）
DOMAIN_TOPIC = {
    "ops": "航班动态（当前航段 vs 前序衔接）",
    "rule": "告警规则阈值适用",
    "resource": "机组与机队保障余度",
    "weather": "气象趋势（本场观测 vs 航路预报）",
}

# 等级 → 建议动作元数据（对齐论文三级响应制度）
LEVEL_META = {
    "yellow": {
        "label": "黄色 · 中风险候选",
        "suggest": "upgrade_mid",
        "suggest_label": "升级中风险",
        "dept": "运行控制室（主责部门）",
        "deadline": "5min 内介入跟踪",
    },
    "orange": {
        "label": "橙色 · 高风险候选",
        "suggest": "upgrade_high",
        "suggest_label": "升级高风险",
        "dept": "总值班室（牵头）",
        "deadline": "3min 内响应、跨部门联动",
    },
    "blue": {
        "label": "蓝色 · 低风险",
        "suggest": "keep_low",
        "suggest_label": "维持低风险跟踪",
        "dept": "首响岗位",
        "deadline": "持续跟踪",
    },
}


def _card(cid, domain, source, title, summary, quote, stance="neutral"):
    return {
        "id": cid,
        "domain": domain,
        "grade": "sourced_doc",
        "source": source,
        "title": title,
        "summary": summary,
        "quote": quote,
        "stance": stance,
    }


def _fact(fid, title, detail):
    return {"id": fid, "grade": "graph_fact", "title": title, "detail": detail}


ALARMS: list[dict] = [
    # ── 主 demo：证据含口径冲突（场面观测 vs 航路预报），critic 主场 ──
    {
        "alarm_id": "alm-g5862",
        "flight_no": "G5862",
        "route": "广州白云 → 上海虹桥",
        "aircraft": "A321 / B-6832",
        "rule_name": "晚关门×雷雨 组合告警",
        "trigger_kind": "combined",
        "level": "yellow",
        "triggered_at": "14:23",
        "conditions": [
            {"code": "DOOR_LATE", "desc": "SOBT 超出计划 14min，仍未收到关舱门信号"},
            {"code": "WX_THUNDER", "desc": "本场雷雨黄色预警 / 放行走廊雷雨活动"},
        ],
        "evidence_pack": {
            "weather": [
                _card(
                    "ev-g5862-obs", "weather", "气象台 · 场面观测(METAR) 14:20",
                    "本场雷雨减弱",
                    "本场雷雨回波减弱东移，小时雨强由 8mm 降至 2mm，主降跑道 RVR 恢复至 5500m 以上，地面运行条件趋于好转。",
                    "14:20 OBS：TS 减弱，RA 2mm/h，RVR>5500m",
                    stance="improve",
                ),
                _card(
                    "ev-g5862-fcst", "weather", "气象台 · 航路预报 14:00",
                    "放行走廊雷雨持续",
                    "预计 16:00–18:00 雷雨东移影响放行走廊 A599，走廊通行能力指数下降，时段与计划落地窗口重叠，存在二次延误风险。",
                    "14:00 FCST：CB 东移影响 A599（16-18Z）",
                    stance="worsen",
                ),
            ],
            "manual": [
                _card(
                    "ev-g5862-manual", "manual", "《运行控制手册》4.3.2 组合告警处置",
                    "组合告警升级条款",
                    "组合告警成立后 20–30min 未解除，升级为中风险；主责部门（运行控制室）5min 内介入跟踪，同步评估放行时刻调整。",
                    "4.3.2：组合告警 20–30min 未解除 → 中风险，主责部门 5min 介入",
                    stance="worsen",
                ),
            ],
            "crew": [
                _card(
                    "ev-g5862-crew", "crew", "机组排班系统",
                    "执勤时限余量",
                    "机长 TZ2210 本期执勤剩余 45min，备降场余度充足，飞行时限不构成升级约束。",
                    "FTL 剩余 45min（截止 16:40Z）",
                    stance="improve",
                ),
            ],
        },
        "fact_pack": [
            _fact(
                "fact-g5862-1", "历史同类处置统计",
                "近一年「晚关门×雷雨」组合告警 12 起：9 起升级中风险、3 起维持低风险；升级案例平均解除时长 26min。",
            ),
            _fact(
                "fact-g5862-2", "关联实体",
                "G5862 — 执飞机组 G283（机长 TZ2210）— 机型 A321/B-6832 — 本场 CAN；上一航段准点，无继发超时风险。",
            ),
        ],
    },
    # ── 对照 demo：证据无冲突，直接研判升级 ──
    {
        "alarm_id": "alm-kn5807",
        "flight_no": "KN5807",
        "route": "北京大兴 → 乌鲁木齐",
        "aircraft": "B737-800 / B-5421",
        "rule_name": "超时未落×流控 组合告警",
        "trigger_kind": "combined",
        "level": "orange",
        "triggered_at": "15:02",
        "conditions": [
            {"code": "ARRIVAL_OVERDUE", "desc": "巡航阶段预计到达超时 25min"},
            {"code": "FLOW_CONTROL", "desc": "前方进近点流控限制（EDM-14）"},
        ],
        "evidence_pack": {
            "weather": [
                _card(
                    "ev-kn5807-flow", "weather", "流量管理系统 15:00",
                    "前方流控限制",
                    "URC 进近点 EDM-14 时段流控，预计等待放行 30min，落地时刻将进一步推迟。",
                    "EDM-14：CTOP 预计延误 30min",
                    stance="worsen",
                ),
            ],
            "manual": [
                _card(
                    "ev-kn5807-manual", "manual", "《运行控制手册》5.1 超时未落处置",
                    "超时未落高风险响应",
                    "巡航预计到达超时 ≥20min 触发高风险：总值班室牵头 3min 内响应，联系空管评估直飞/备降，必要时启动公司级应急响应并向主管部门报告。",
                    "5.1：预计到达超时≥20min → 高风险，总值班室 3min 响应",
                    stance="worsen",
                ),
            ],
            "crew": [
                _card(
                    "ev-kn5807-fuel", "crew", "签派放行系统",
                    "剩余燃油评估",
                    "剩余燃油可续航 1h50min，覆盖预计等待 30min 加备降裕度，燃油不构成约束。",
                    "FOB：续航 1h50min",
                    stance="improve",
                ),
            ],
        },
        "fact_pack": [
            _fact(
                "fact-kn5807-1", "历史同类处置统计",
                "近一年「超时未落×流控」组合告警 5 起，全部升级高风险，平均处置时长 41min。",
            ),
            _fact(
                "fact-kn5807-2", "关联实体",
                "KN5807 — 执飞机组 G117 — 机型 B737-800/B-5421 — 目标场 URC；备降场 KRY 余度充足。",
            ),
        ],
    },
    # ── 克制边界：单条件低风险，规则引擎已给结论，不进多智能体 ──
    {
        "alarm_id": "alm-g5290",
        "flight_no": "G5290",
        "route": "深圳宝安 → 杭州萧山",
        "aircraft": "A320 / B-1867",
        "rule_name": "晚关门 单条件告警",
        "trigger_kind": "single",
        "level": "blue",
        "triggered_at": "15:40",
        "conditions": [
            {"code": "DOOR_LATE", "desc": "SOBT 超出计划 8min，未收到关舱门信号"},
        ],
        "rule_verdict": "规则引擎结论：低风险（SOBT 超出 10min 内），首响岗位持续跟踪即可，无需升级。",
        "evidence_pack": {},
        "fact_pack": [],
    },
]

_ALARMS_BY_ID = {a["alarm_id"]: a for a in ALARMS}


def list_alarms() -> list[dict]:
    """告警列表（面向卡片展示，不输出原始证据包，只给计数）。"""
    out = []
    for a in ALARMS:
        meta = LEVEL_META.get(a["level"], {})
        out.append({
            "alarm_id": a["alarm_id"],
            "flight_no": a["flight_no"],
            "route": a["route"],
            "aircraft": a["aircraft"],
            "rule_name": a["rule_name"],
            "trigger_kind": a["trigger_kind"],
            "level": a["level"],
            "level_label": meta.get("label", a["level"]),
            "level_suggest": meta.get("suggest_label", ""),
            "triggered_at": a["triggered_at"],
            "conditions": a["conditions"],
            "evidence_count": sum(len(v) for v in a["evidence_pack"].values()),
            "fact_count": len(a["fact_pack"]),
            "rule_verdict": a.get("rule_verdict", ""),
        })
    return out


def get_alarm(alarm_id: str) -> dict | None:
    """取完整告警（含证据包 / 事实包，供复核流水线使用）。"""
    return _ALARMS_BY_ID.get(alarm_id)


def hard_rule_verdict(alarm: dict, evidence: dict, facts: list[dict]) -> dict:
    """
    Critic 的确定性兜底裁定（无 LLM 也成立）：
    - 同域证据出现 improve/worsen 双向 → 记冲突，按保守原则（宁误报不漏报）采信升级方向；
    - 建议动作取告警候选等级对应的响应制度；历史事实支持度决定置信度。
    LLM 只做解释增强，不改变裁定——保证结论可复现、可审计。
    """
    conflicts = []
    for domain, cards in (evidence or {}).items():
        stances = {c.get("stance") for c in cards}
        if "improve" in stances and "worsen" in stances:
            conflicts.append({
                "topic": DOMAIN_TOPIC.get(domain, domain),
                "cards": [c.get("id") for c in cards],
                "verdict": "同级证据口径冲突（时点观测 vs 趋势预报），按保守原则采信趋势（升级）方向",
                "basis": "两张证据卡分级同为 sourced_doc；论文 §5 风险控制原则：宁可误报、不可漏报",
                "need_human": False,
                "review_hint": "预报为整点起报，建议 30min 后复核气象更新再确认",
            })

    meta = LEVEL_META.get(alarm.get("level", ""), {})
    support = sum(1 for f in (facts or []) if "升级" in (f.get("detail") or ""))
    return {
        "conflicts": conflicts,
        "suggestion": meta.get("suggest", "keep_low"),
        "suggest_label": meta.get("suggest_label", "维持原等级"),
        "need_human": any(c.get("need_human") for c in conflicts),
        "confidence": "高" if support else "中",
        "dept": meta.get("dept", ""),
        "deadline": meta.get("deadline", ""),
    }
