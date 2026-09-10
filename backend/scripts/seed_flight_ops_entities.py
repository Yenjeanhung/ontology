"""Seed 国航（CA）航班运行监控示例实体/关系到专用知识库（实例层）。

用法：
    cd backend
    python scripts/seed_flight_ops_entities.py            # 全量重建（145航班×14天≈2030航段）
    python scripts/seed_flight_ops_entities.py --small    # 小样例（8航班×2天=16航段）

前提：先运行 seed_flight_ops_ontology.py 生成「民航航班运行监控本体」。

数据设计（随机种子固定，可复现）：
- 全部为国航 CA 航班号：145 个航班号 × 14 个运行日（2026-08-27 ~ 09-09）≈ 2030 航段；
- 以数据快照时刻 NOW=运行种子脚本的当下（分钟取整）为基准推导各航段当前状态
  （到达/滑入/巡航/滑出/登机/计划/取消），时间线自洽，且与工作流代码里的
  datetime.now() 时态一致——告警扫描/升级巡检演示不会因数据日期漂移而失真；
- 航空器按日排班、同机过站衔接（「衔接前序」关系），延误自然传播；
- 含 出发/到达延误、复飞、空中等待、取消，以及论文第5章分级口径的运行告警
  （晚关门/超时未落/机组变更 低中高三级，登机未出港航段天然构成
  「低风险待处理超20分钟」样本供升级巡检演示），便于验证 函数/派生属性/告警工作流。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import delete, insert, select

from database import async_session, init_db
from models import (
    Entity,
    KnowledgeBase,
    Ontology,
    OntologyCategory,
    OntologyRelation,
    Relation,
)
from flight_ops_domain import CATEGORY_NAME, CONSTRAINTS, LIFECYCLE_EVENTS

SEED_KB_NAME = "航班运行监控图谱（种子数据）"

NOW = datetime.now().replace(second=0, microsecond=0)   # 数据快照时刻（运行种子的当下）
TODAY = NOW.date()
DATE_LIST = [TODAY - timedelta(days=d) for d in range(13, -1, -1)]  # 14 个运行日


def _gid() -> str:
    return uuid.uuid4().hex[:12]


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _shift(dt: datetime, minutes: int) -> datetime:
    return dt + timedelta(minutes=minutes)


RNG = random.Random(42)

# ──────────────────────── 静态基础数据 ────────────────────────

AIRPORTS = {  # IATA -> (ICAO, 名称, 城市)
    "PEK": ("ZBAA", "北京首都国际机场", "北京"),
    "SHA": ("ZSSS", "上海虹桥国际机场", "上海"),
    "PVG": ("ZSPD", "上海浦东国际机场", "上海"),
    "CAN": ("ZGGG", "广州白云国际机场", "广州"),
    "SZX": ("ZGSZ", "深圳宝安国际机场", "深圳"),
    "CTU": ("ZUUU", "成都双流国际机场", "成都"),
    "TFU": ("ZUTF", "成都天府国际机场", "成都"),
    "XIY": ("ZLXY", "西安咸阳国际机场", "西安"),
    "CKG": ("ZUCK", "重庆江北国际机场", "重庆"),
    "KMG": ("ZPPP", "昆明长水国际机场", "昆明"),
    "HGH": ("ZSHC", "杭州萧山国际机场", "杭州"),
    "CSX": ("ZGHA", "长沙黄花国际机场", "长沙"),
    "WUH": ("ZHHH", "武汉天河国际机场", "武汉"),
    "TAO": ("ZSQD", "青岛胶东国际机场", "青岛"),
    "XMN": ("ZSAM", "厦门高崎国际机场", "厦门"),
    "HAK": ("ZJHK", "海口美兰国际机场", "海口"),
}
# 出发机场池：枢纽 PEK/SHA/CTU 权重更高
HUB_POOL = (["PEK"] * 5 + ["SHA"] * 2 + ["CTU"] * 2
            + [c for c in AIRPORTS if c not in ("PEK", "SHA", "CTU")])

AIRCRAFT_TYPES = {  # 机型 -> (厂商, 宽窄, 座位, 巡航马赫, 航程km)
    "A320neo": ("AIRBUS", "窄体", 174, 0.78, 6300),
    "B737-800": ("BOEING", "窄体", 178, 0.785, 5400),
    "A330-300": ("AIRBUS", "宽体", 260, 0.82, 11750),
    "A350-900": ("AIRBUS", "宽体", 314, 0.85, 15000),
    "B787-9": ("BOEING", "宽体", 284, 0.85, 14140),
}
FLEET_PLAN = {"A320neo": 24, "B737-800": 20, "A330-300": 6, "A350-900": 8, "B787-9": 4}

CRUISE_ALTS = [8928, 9449, 10058, 10668, 11277]
DELAY_CAUSES = [  # (原因分类, 责任认定)
    ("天气", "非承运人责任"), ("航空公司", "承运人责任"), ("空管", "非承运人责任"),
    ("旅客", "非承运人责任"), ("机场保障", "非承运人责任"), ("离港系统", "非承运人责任"),
]
DATA_SOURCES = ["ACARS", "A-CDM", "CDM", "ADS-B"]


def _gen_fleet() -> dict[str, str]:
    """注册号 -> 机型，共 62 架。"""
    regs = RNG.sample(range(1000, 9999), sum(FLEET_PLAN.values()))
    fleet, i = {}, 0
    for t, n in FLEET_PLAN.items():
        for _ in range(n):
            fleet[f"B-{regs[i]}"] = t
            i += 1
    return fleet


FLEET = _gen_fleet()


def _gen_crew() -> dict[str, tuple[str, str, str, str, int]]:
    """姓名 -> (工号, 岗位, 执照, 机型资质, 累计小时)。"""
    surnames = ["张", "王", "李", "赵", "刘", "陈", "杨", "黄", "周", "吴",
                "徐", "孙", "马", "朱", "胡", "郭", "何", "林", "罗", "郑"]
    given = ["伟", "芳", "强", "敏", "静", "磊", "军", "洋", "勇", "艳",
             "杰", "涛", "明", "超", "霞", "平", "刚", "辉", "娜", "鹏"]
    crew: dict[str, tuple] = {}
    for i in range(40):
        name = surnames[i % 20] + given[(i * 7 + 3) % 20]
        while name in crew:
            name += "·"
        if i < 30:
            role, lic = ("机长", "ATPL") if i % 2 == 0 else ("副驾驶", "CPL")
        else:
            role, lic = "乘务长", "-"
        rating = ("A320/B737" if role != "机长"
                  else RNG.choice(["A320/B737", "A330/A350/B787"]))
        crew[name] = (f"FC-{2000 + i:04d}", role, lic, rating,
                      RNG.randrange(1500, 18000, 50))
    return crew


CREW = _gen_crew()
CREW_NAMES = sorted(CREW)


def _pick_type() -> str:
    r = RNG.random()
    if r < 0.42:
        return "A320neo"
    if r < 0.72:
        return "B737-800"
    if r < 0.84:
        return "A330-300"
    if r < 0.94:
        return "A350-900"
    return "B787-9"


def _gen_flights() -> list[dict]:
    """145 个国航航班号：航班号/航线/计划时刻/机型/轮档时长。"""
    numbers = RNG.sample(range(1000, 9000), 145)
    codes = sorted(AIRPORTS)
    flights = []
    for n in numbers:
        dep = RNG.choice(HUB_POOL)
        arr = RNG.choice([c for c in codes if c != dep])
        flights.append({
            "flight_no": f"CA{n}", "dep": dep, "arr": arr,
            "sobt_min": RNG.randrange(360, 1291, 5),   # 06:00-21:30
            "block_min": RNG.randrange(100, 216),      # 轮档时长（分钟）
            "actype": _pick_type(),
        })
    flights.sort(key=lambda f: (f["sobt_min"], f["flight_no"]))
    return flights


FLIGHTS = _gen_flights()


def gen_stands() -> dict[str, tuple[str, str, str]]:
    """机位号 -> (机场, 类型, 适配)。每机场 3 个机位。"""
    stands = {}
    for iata in AIRPORTS:
        stands[f"{iata}-A101"] = (iata, "廊桥位", "C/D/E")
        stands[f"{iata}-A102"] = (iata, "廊桥位", "C/D")
        stands[f"{iata}-B201"] = (iata, "远机位", "C")
    return stands


def gen_runways() -> dict[str, tuple[str, int, str]]:
    """跑道号 -> (机场, 长度m, 方向)。每机场 2 条。"""
    rws = {}
    for iata in AIRPORTS:
        rws[f"{iata}-18R"] = (iata, RNG.randrange(3200, 3801, 100), "南向")
        rws[f"{iata}-36L"] = (iata, RNG.randrange(3200, 3801, 100), "北向")
    return rws


STANDS = gen_stands()
RUNWAYS = gen_runways()
AP_STANDS = {iata: sorted(c for c, v in STANDS.items() if v[0] == iata) for iata in AIRPORTS}
AP_RWYS = {iata: sorted(c for c, v in RUNWAYS.items() if v[0] == iata) for iata in AIRPORTS}
CREW_LIST = list(CREW)

# ──────────────────────── 航段模拟 ────────────────────────

TURNOAROUND_MIN = 55      # 同机过站最短衔接时间（分钟）
CANCEL_RATE = 0.02        # 取消率
GO_AROUND_RATE = 0.012    # 复飞率（已落地航段中）


def _day_base(day) -> datetime:
    return datetime(day.year, day.month, day.day)


def gen_legs(small: bool = False) -> list[dict]:
    """生成航段实例：同机按日排班、过站衔接、按 NOW 推导状态。"""
    flights = FLIGHTS[:8] if small else FLIGHTS
    days = DATE_LIST[-2:] if small else DATE_LIST
    legs: list[dict] = []
    leg_seq = 0

    for day in days:
        base = _day_base(day)
        free_at: dict[str, datetime] = {}          # 注册号 -> 空闲时刻
        prev_key: dict[str, str] = {}              # 注册号 -> 上一航段临时key
        order = sorted(flights, key=lambda f: f["sobt_min"])

        for f in order:
            leg_seq += 1
            key = f"T{leg_seq:05d}"
            # 贪心指派最早空闲的同型机（贴近真实排班衔接口径）。随机指派会让
            # 同机时刻大面积冲突，撤轮档被迫推迟到上一航段落地之后，造出
            # 「预计 vs 计划」相差数小时的失真延误数据。
            same_type = [r for r, t in FLEET.items() if t == f["actype"]]
            reg = min(same_type, key=lambda r: free_at.get(r, datetime.min))

            sobt = base + timedelta(minutes=f["sobt_min"])
            # 计划过站：同机衔接时取 ready-sobt 与 55min 的合理值
            ready = free_at.get(reg, sobt - timedelta(minutes=9999))
            delay0 = RNG.choice([0, 0, 0, 5, 10, 15, 20, 25, 30, 45])
            aobt = max(sobt + timedelta(minutes=delay0), ready)
            turnaround = int((aobt - ready).total_seconds() // 60) if key != "" and reg in prev_key else 0
            sched_turn = max(turnaround, TURNOAROUND_MIN) if reg in prev_key else None

            atot = aobt + timedelta(minutes=RNG.randrange(13, 21))
            aldt = atot + timedelta(minutes=f["block_min"] + RNG.randrange(-5, 13))
            aibt = aldt + timedelta(minutes=RNG.randrange(5, 9))
            sibt = sobt + timedelta(minutes=f["block_min"])

            cancelled = RNG.random() < CANCEL_RATE
            go_around = (not cancelled) and RNG.random() < GO_AROUND_RATE
            if go_around:  # 复飞延误 20 分钟
                aldt = aldt + timedelta(minutes=20)
                aibt = aibt + timedelta(minutes=20)

            free_at[reg] = aibt if not cancelled else free_at.get(reg, aobt)

            legs.append({
                "key": key, "leg_no": f"{f['flight_no']}-{day:%Y%m%d}-01",
                "flight_no": f["flight_no"], "flight_date": f"{day:%Y-%m-%d}",
                "dep": f["dep"], "arr": f["arr"], "actype": f["actype"], "reg": reg,
                "sobt": sobt, "sibt": sibt,
                "aobt": None if cancelled else aobt,
                "atot": None if cancelled else atot,
                "aldt": None if cancelled else aldt,
                "aibt": None if cancelled else aibt,
                "cancelled": cancelled, "go_around": go_around,
                "prev_key": prev_key.get(reg), "sched_turn": sched_turn,
                "cruise_alt": RNG.choice(CRUISE_ALTS),
                "pax": RNG.randrange(60, 96) * {"窄体": 1, "宽体": 3}[AIRCRAFT_TYPES[f["actype"]][1]] // 1,
            })
            prev_key[reg] = key
    return legs

# ──────────────────────── 状态推导与生命周期事件 ────────────────────────

ACTUALS = {  # 状态 -> 「时间性质=实际」的事件类型集合
    "登机": {"开始登机"},
    "滑出": {"开始登机", "登机结束", "关闭舱门", "撤轮档", "滑出"},
    "巡航": {"开始登机", "登机结束", "关闭舱门", "撤轮档", "滑出", "跑道等待",
             "起飞", "爬升", "巡航"},
    "滑入": {"开始登机", "登机结束", "关闭舱门", "撤轮档", "滑出", "跑道等待",
             "起飞", "爬升", "巡航", "下降", "进近", "落地", "滑入"},
    "到达": set(LIFECYCLE_EVENTS),
}


def leg_status(leg: dict) -> str:
    if leg["cancelled"]:
        return "取消"
    if leg["aibt"] <= NOW:
        return "到达"
    if leg["aldt"] <= NOW:
        return "滑入"
    if leg["atot"] <= NOW:
        return "巡航"
    if leg["aobt"] <= NOW:
        return "滑出"
    if leg["sobt"] <= NOW:
        return "登机"
    return "计划"


def build_events(leg: dict) -> list[dict]:
    """按状态生成事件列表（含时间性质），取消航段仅两条计划事件。"""
    status = leg["status"]
    sobt, aobt, atot = leg["sobt"], leg["aobt"], leg["atot"]
    aldt, aibt, sibt, eta = leg["aldt"], leg["aibt"], leg["sibt"], leg["eta"]

    if status == "取消":
        evs = [("计划发布", sobt - timedelta(minutes=720)),
               ("关闭舱门", aobt or sobt)]
        return [{"type": t, "time": tm, "kind": "计划",
                 "seq": i + 1, "remark": "航班取消，后续节点不再执行" if i else ""}
                for i, (t, tm) in enumerate(evs)]

    # 计划/登机航段的实际时刻已被置 None：用预计口径兜底（etd=预计撤轮档）
    aobt = aobt or leg.get("etd") or sobt
    atot = atot or (aobt + timedelta(minutes=15))

    timeline = [  # (事件类型, 时刻, 相对实际/预计)
        ("计划发布", sobt - timedelta(minutes=720), "计划"),
        ("开始登机", aobt - timedelta(minutes=30), "actual"),
        ("登机结束", aobt - timedelta(minutes=8), "actual"),
        ("关闭舱门", aobt - timedelta(minutes=5), "actual"),
        ("撤轮档", aobt, "actual"),
        ("滑出", atot - timedelta(minutes=13), "actual"),
        ("跑道等待", atot - timedelta(minutes=4), "actual"),
        ("起飞", atot, "actual"),
        ("爬升", atot + timedelta(minutes=6), "actual"),
        ("巡航", atot + timedelta(minutes=22), "actual"),
        ("下降", (aldt or eta) - timedelta(minutes=38), "actual" if status in ACTUALS["滑入"] or status == "到达" else "est"),
        ("进近", (aldt or eta) - timedelta(minutes=12), "actual" if status in ACTUALS["滑入"] or status == "到达" else "est"),
        ("落地", aldt or eta, "actual" if status in ACTUALS["滑入"] or status == "到达" else "est"),
        ("滑入", aibt or (eta + timedelta(minutes=7)), "actual" if status == "到达" else "est"),
        ("挡轮档", aibt or (eta + timedelta(minutes=7)), "actual" if status == "到达" else "est"),
        ("到达结束", aibt or (eta + timedelta(minutes=10)), "actual" if status == "到达" else "est"),
    ]
    actuals = ACTUALS.get(status, set())
    evs = []
    for i, (typ, tm, kind) in enumerate(timeline):
        is_actual = typ in actuals
        remark = ""
        if typ == "进近" and leg["go_around"] and is_actual:
            remark = "首次进近复飞，盘旋后二次进近"
        evs.append({
            "type": typ,
            "time": tm,
            "kind": "实际" if is_actual else ("预计" if tm > NOW else "计划"),
            "seq": i + 1, "remark": remark,
        })
    return evs

# ──────────────────────── 延误 / 异常 / 告警派生 ────────────────────────

def finalize_legs(legs: list[dict]) -> None:
    """推导 status，并按状态决定实际/预计时间口径（未来节点置 None）。"""
    for leg in legs:
        st = leg_status(leg)
        leg["status"] = st
        aobt, atot, aldt, aibt = leg["aobt"], leg["atot"], leg["aldt"], leg["aibt"]
        sobt, sibt = leg["sobt"], leg["sibt"]
        block = int((sibt - sobt).total_seconds() // 60)
        if st in ("计划", "登机", "取消"):    # 未撤轮档（含取消）：全部为预计
            # 滚动预计 = 计划时刻 + 运行预测偏差（出发 0~60 分钟、到达再加 0~20 分钟），
            # 贴近真实 OCC 预测口径；不直接取模拟实际值（链式推迟会造出数小时的离谱延误）
            shift = RNG.choice([0, 0, 0, 5, 10, 15, 20, 25, 30, 45, 60])
            leg.update(aobt=None, atot=None, aldt=None, aibt=None,
                       etd=sobt + timedelta(minutes=shift),
                       eta=sibt + timedelta(minutes=shift + RNG.randrange(0, 21)))
        elif st in ("滑出", "巡航"):          # 已撤轮档未落地：出发预计定格为实际，到达仍为预计
            leg.update(aldt=None, aibt=None, etd=aobt,
                       eta=aobt + timedelta(minutes=block + RNG.randrange(-10, 36)))
        else:                                  # 滑入/到达：预计时刻定格为最终实际
            leg.update(etd=aobt, eta=aibt)
        # 论文第5章风险事件「机组人员变更」：放行后（计划/登机阶段）小概率名单变动
        leg["crew_changed"] = st in ("计划", "登机") and RNG.random() < 0.08


def build_derived(leg: dict) -> dict:
    """由航段时间线派生 延误记录 / 异常事件 / 运行告警 列表。"""
    st = leg["status"]
    delays, abnormals, alerts = [], [], []
    src = RNG.choice(DATA_SOURCES)

    # ── 延误（≥15 分钟，民航统计口径）──
    if st == "到达" or st == "滑入":
        dep_delay = int((leg["aobt"] - leg["sobt"]).total_seconds() // 60)
        if dep_delay >= 15:
            cat, resp = DELAY_CAUSES[1] if leg["prev_key"] else RNG.choice(DELAY_CAUSES)
            delays.append({"no": f"DLY-{_gid().upper()}", "type": "出发延误",
                           "minutes": dep_delay, "cat": cat, "resp": resp,
                           "detail": f"前序过站衔接影响，撤轮档晚于计划{dep_delay}分钟"
                                     if leg["prev_key"] else f"{cat}原因导致撤轮档延迟",
                           "at": leg["aobt"]})
        if st == "到达":
            arr_delay = int((leg["aibt"] - leg["sibt"]).total_seconds() // 60)
            if arr_delay >= 15:
                cat, resp = DELAY_CAUSES[1] if dep_delay >= 15 else RNG.choice(DELAY_CAUSES)
                delays.append({"no": f"DLY-{_gid().upper()}", "type": "到达延误",
                               "minutes": arr_delay, "cat": cat, "resp": resp,
                               "detail": "出发延误传导至到达" if dep_delay >= 15
                                         else f"空中流控/绕飞，到达晚于计划{arr_delay}分钟",
                               "at": leg["aibt"]})

    # ── 异常事件 ──
    if st == "取消":
        abnormals.append({"no": f"ABN-{_gid().upper()}", "type": "取消",
                          "at": leg["sobt"], "severity": "严重",
                          "disposal": "取消航班，旅客签转后续航班",
                          "reason": RNG.choice(["雷雨天气机场关闭", "机械故障排故超时",
                                                "机组超时"])})
    elif leg["go_around"] and st in ("滑入", "到达"):
        abnormals.append({"no": f"ABN-{_gid().upper()}", "type": "复飞",
                          "at": leg["aldt"] - timedelta(minutes=12), "severity": "一般",
                          "disposal": "盘旋等待后二次进近正常落地",
                          "reason": "前机未脱离跑道 / 侧风超限"})
    elif st == "巡航" and RNG.random() < 0.15 and leg["eta"] and \
            (leg["eta"] - leg["sibt"]).total_seconds() >= 20 * 60:
        wait_min = RNG.randrange(10, 35, 5)
        abnormals.append({"no": f"ABN-{_gid().upper()}", "type": "空中等待",
                          "at": NOW - timedelta(minutes=RNG.randrange(5, 60)),
                          "severity": "一般", "disposal": f"空中盘旋{wait_min}分钟后继续进近",
                          "reason": "目的机场流量控制"})

    # ── 运行告警（论文第5章分级口径：低/中/高三级 + 分级响应牵头部门）──
    _SEV = {"低": "提示", "中": "警告", "高": "严重"}
    _DEPT = {"低": "飞行控制室", "中": "运行控制室", "高": "总值班室"}

    def _alert(al_type, level, value, thresh, msg, at, status="待处理", handler=""):
        alerts.append({"no": f"ALR-{_gid().upper()}", "type": al_type,
                       "risk_level": level, "severity": _SEV[level],
                       "trigger_value": value, "threshold": thresh, "message": msg,
                       "at": at, "status": status, "handler": handler,
                       "handle_dept": _DEPT[level]})

    late_min = int((NOW - leg["sobt"]).total_seconds() // 60)
    if st == "登机" and late_min >= 10:
        # 晚关门：计划撤轮档(SOBT)后仍未出港（未撤轮档⇔未关舱门），10/20/30 分钟三档
        level = "高" if late_min > 30 else ("中" if late_min > 20 else "低")
        _alert("晚关门", level, late_min,
               "计划撤轮档(SOBT)后10/20/30分钟仍未出港，分级低/中/高",
               f"{leg['leg_no']} 已过计划撤轮档时间{late_min}分钟仍未出港，请核实保障进度",
               leg["sobt"] + timedelta(minutes=10))
    elif st == "巡航":
        # 超时未落：ATOT+计划飞行时长后 5/10/15 分钟仍无落地报
        plan_land = leg["atot"] + (leg["sibt"] - leg["sobt"])
        over_min = int((NOW - plan_land).total_seconds() // 60)
        if over_min >= 5:
            level = "高" if over_min > 15 else ("中" if over_min > 10 else "低")
            _alert("超时未落", level, over_min,
                   "计划到达(ATOT+计划飞行时长)后5/10/15分钟仍无落地报，分级低/中/高",
                   f"{leg['leg_no']} 超过计划到达时间{over_min}分钟仍无落地报，请核实动态",
                   plan_land + timedelta(minutes=5))
    if (st in ("计划", "登机")) and leg.get("crew_changed"):
        # 机组人员变更：放行后名单变动（低风险，建议人工复核是否升级）
        _alert("机组变更", "低", 0,
               "放行后机组名单发生变更",
               f"{leg['leg_no']} 放行后机组名单变更，请确认资质与连飞限制",
               NOW - timedelta(minutes=RNG.randrange(3, 25)),
               status="待处理")
    if st == "滑出" and (NOW - leg["aobt"]).total_seconds() > 25 * 60:
        _alert("滑出时间过长", "低", int((NOW - leg["aobt"]).total_seconds() // 60),
               "撤轮档后25分钟仍未起飞",
               f"{leg['leg_no']} 滑出后长时间未起飞，可能排队等待",
               leg["aobt"] + timedelta(minutes=25),
               status="已解除", handler=RNG.choice(CREW_NAMES[:10]))

    leg["delays"], leg["abnormals"], leg["alerts"] = delays, abnormals, alerts
    leg["data_source"] = src

# ──────────────────────── 批量写入器 ────────────────────────

CHUNK = 1000


class BulkSeeder:
    """缓冲 + executemany 分块批量写入，含 CONSTRAINTS 校验与关系去重。"""

    def __init__(self, db, kb_id: str,
                 ont_ids: dict[str, str], rel_ids: dict[str, str]):
        self.db = db
        self.kb_id = kb_id
        self.ont_ids = ont_ids
        self.rel_ids = rel_ids
        self.ent_buf: list[dict] = []
        self.rel_buf: list[dict] = []
        self.eids: dict[str, str] = {}
        self.etypes: dict[str, str] = {}
        self.rel_seen: set[tuple] = set()
        self.constraint_set = set(CONSTRAINTS)
        self.stat = Counter()

    async def add_entity(self, key: str, ont_name: str, name: str,
                         props: dict, description: str = "") -> str:
        eid = _gid()
        self.eids[key] = eid
        self.etypes[key] = ont_name
        self.ent_buf.append({
            "id": eid, "kb_id": self.kb_id,
            "ontology_id": self.ont_ids[ont_name], "entity_type": ont_name,
            "name": name, "description": description,
            "properties": json.dumps(props, ensure_ascii=False),
        })
        self.stat[ont_name] += 1
        if len(self.ent_buf) >= CHUNK:
            await self._flush_entities()
        return eid

    async def add_relation(self, src_key: str, rel_name: str, tgt_key: str,
                           props: dict | None = None) -> None:
        st, tt = self.etypes[src_key], self.etypes[tgt_key]
        if (st, rel_name, tt) not in self.constraint_set:
            raise ValueError(f"违反约束：{st} --{rel_name}--> {tt}")
        pair = (self.eids[src_key], rel_name, self.eids[tgt_key])
        if pair in self.rel_seen:
            return
        self.rel_seen.add(pair)
        self.rel_buf.append({
            "id": _gid(), "kb_id": self.kb_id,
            "relation_def_id": self.rel_ids[rel_name],
            "relation_type": rel_name,
            "source_entity_id": pair[0], "target_entity_id": pair[2],
            "description": "", "properties":
                json.dumps(props, ensure_ascii=False) if props else None,
        })
        self.stat[rel_name] += 1
        if len(self.rel_buf) >= CHUNK:
            await self._flush_relations()

    async def _flush_entities(self) -> None:
        if self.ent_buf:
            await self.db.execute(insert(Entity), self.ent_buf)
            self.ent_buf.clear()

    async def _flush_relations(self) -> None:
        if self.rel_buf:
            await self.db.execute(insert(Relation), self.rel_buf)
            self.rel_buf.clear()

    async def finish(self) -> None:
        await self._flush_entities()
        await self._flush_relations()

# ──────────────────────── 主流程 ────────────────────────

WIDE_CAPTAINS = [n for n, v in CREW.items() if v[1] == "机长" and "A330" in v[3]]
NARROW_CAPTAINS = [n for n, v in CREW.items() if v[1] == "机长" and "A320" in v[3]]
FIRST_OFFICERS = [n for n, v in CREW.items() if v[1] == "副驾驶"]
PURSERS = [n for n, v in CREW.items() if v[1] == "乘务长"]


async def seed(small: bool = False, sync_graph: bool = True) -> None:
    await init_db()
    async with async_session() as db:
        # 1. 定义层检查
        cat = (await db.execute(
            select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
        )).scalar_one_or_none()
        if not cat:
            print(f"[错误] 未找到本体类别「{CATEGORY_NAME}」，请先运行 seed_flight_ops_ontology.py")
            return
        ont_rows = (await db.execute(
            select(Ontology).where(Ontology.category_id == cat.id))).scalars().all()
        ont_ids = {r.name: r.id for r in ont_rows}
        rel_rows = (await db.execute(
            select(OntologyRelation).where(OntologyRelation.category_id == cat.id))).scalars().all()
        rel_ids = {r.name: r.id for r in rel_rows}
        print(f"定义层：{len(ont_ids)} 本体 / {len(rel_ids)} 关系定义")

        # 2. KB：存在则清空实例层（wipe），否则创建
        kb = (await db.execute(
            select(KnowledgeBase).where(KnowledgeBase.name == SEED_KB_NAME)
        )).scalar_one_or_none()
        if kb:
            await db.execute(delete(Relation).where(Relation.kb_id == kb.id))
            await db.execute(delete(Entity).where(Entity.kb_id == kb.id))
            await db.flush()
            print(f"已清空知识库「{SEED_KB_NAME}」（id={kb.id}）旧数据")
        else:
            kb = KnowledgeBase(name=SEED_KB_NAME,
                               description="国航航班运行监控示例数据（脚本自动生成）")
            db.add(kb)
            await db.flush()
            print(f"已创建知识库「{SEED_KB_NAME}」（id={kb.id}）")

        bs = BulkSeeder(db, kb.id, ont_ids, rel_ids)

        # 3. 静态实体：机场 / 机位 / 跑道 / 机型 / 航空器 / 航班 / 机组
        for iata, (icao, name, city) in AIRPORTS.items():
            await bs.add_entity(f"APT:{iata}", "机场", name, {
                "iata_code": iata, "icao_code": icao, "name": name, "city": city,
                "country": "中国", "airport_type": "国际",
                "timezone": "Asia/Shanghai", "utc_offset": "+08:00",
            }, f"{city} {name}")
        for no, (iata, typ, comp) in STANDS.items():
            await bs.add_entity(f"STD:{no}", "机位", no, {
                "stand_no": no, "airport": iata, "stand_type": typ,
                "compatible_types": comp, "name": no,
            })
        for no, (iata, length, direction) in RUNWAYS.items():
            await bs.add_entity(f"RWY:{no}", "跑道", no, {
                "runway_no": no, "airport": iata, "length_m": length,
                "direction": direction, "name": no,
            })
        for iata in AIRPORTS:
            for s in AP_STANDS[iata]:
                await bs.add_relation(f"APT:{iata}", "拥有机位", f"STD:{s}")
            for r in AP_RWYS[iata]:
                await bs.add_relation(f"APT:{iata}", "拥有跑道", f"RWY:{r}")
        for t, (mfr, body, seat, mach, rng) in AIRCRAFT_TYPES.items():
            await bs.add_entity(f"TYPE:{t}", "机型", t, {
                "type_code": t, "manufacturer": mfr, "body_type": body,
                "seat_count": seat, "cruise_speed": mach, "max_range_km": rng,
            })
        for reg, t in FLEET.items():
            await bs.add_entity(f"AC:{reg}", "航空器", reg, {
                "registration": reg, "msn": f"MSN-{RNG.randrange(1000, 9999)}",
                "aircraft_type": t, "carrier_code": "CA",
                "entry_date": f"{RNG.randrange(2013, 2025)}-{RNG.randrange(1, 13):02d}-15",
                "status": RNG.choice(["在役"] * 8 + ["定检"]),
            })
            await bs.add_relation(f"AC:{reg}", "属于机型", f"TYPE:{t}")
        for i, f in enumerate(FLIGHTS[:8] if small else FLIGHTS):
            await bs.add_entity(f"FLT:{f['flight_no']}", "航班", f["flight_no"], {
                "flight_no": f["flight_no"], "carrier_code": "CA",
                "carrier_name": "中国国际航空", "flight_kind": "国内",
                "route_desc": f"{AIRPORTS[f['dep']][2]}{f['dep']}-{AIRPORTS[f['arr']][2]}{f['arr']}",
                "effective_from": "2026-03-30", "effective_to": "2026-10-25",
            })
        for name, (eno, role, lic, rating, hours) in CREW.items():
            await bs.add_entity(f"CRW:{name}", "机组", name, {
                "employee_no": eno, "name": name, "role": role,
                "license_type": lic, "type_rating": rating, "total_hours": hours,
            })
        print(f"静态实体：机场{len(AIRPORTS)} 机位{len(STANDS)} 跑道{len(RUNWAYS)} "
              f"机型{len(AIRCRAFT_TYPES)} 航空器{len(FLEET)} "
              f"航班{8 if small else len(FLIGHTS)} 机组{len(CREW)}")

        # 4. 航段实例 + 生命周期事件 + 延误/异常/告警
        legs = gen_legs(small)
        finalize_legs(legs)
        ground_events = {"开始登机", "登机结束", "关闭舱门", "撤轮档",
                         "滑入", "挡轮档", "到达结束"}
        for leg in legs:
            build_derived(leg)
            st = leg["status"]
            stand_dep = RNG.choice(AP_STANDS[leg["dep"]])
            stand_arr = RNG.choice(AP_STANDS[leg["arr"]])
            rwy_dep = RNG.choice(AP_RWYS[leg["dep"]])
            rwy_arr = RNG.choice(AP_RWYS[leg["arr"]])
            future = st in ("计划", "登机", "取消")

            props = {
                "leg_no": leg["leg_no"], "flight_date": leg["flight_date"],
                "flight_no": leg["flight_no"], "dep_airport": leg["dep"],
                "arr_airport": leg["arr"],
                "sobt": iso(leg["sobt"]), "sibt": iso(leg["sibt"]),
                "etd": iso(leg["etd"]), "eta": iso(leg["eta"]),
                "aobt": iso(leg["aobt"]), "atot": iso(leg["atot"]),
                "aldt": iso(leg["aldt"]), "aibt": iso(leg["aibt"]),
                "leg_status": st, "cruise_altitude": leg["cruise_alt"],
                "sched_turnaround_min": leg["sched_turn"],
                "pax_count": leg["pax"], "data_source": leg["data_source"],
                "is_crew_changed": bool(leg.get("crew_changed")),
            }
            await bs.add_entity(leg["key"], "航段", leg["leg_no"], props,
                          f"{leg['flight_no']} {leg['dep']}-{leg['arr']} [{st}]")

            await bs.add_relation(f"FLT:{leg['flight_no']}", "拆分航段", leg["key"])
            await bs.add_relation(leg["key"], "出发于", f"APT:{leg['dep']}")
            await bs.add_relation(leg["key"], "到达于", f"APT:{leg['arr']}")
            await bs.add_relation(leg["key"], "计划机型", f"TYPE:{leg['actype']}")
            await bs.add_relation(leg["key"], "执飞航空器", f"AC:{leg['reg']}")
            await bs.add_relation(leg["key"], "撤轮档于", f"STD:{stand_dep}")
            await bs.add_relation(leg["key"], "挡轮档于", f"STD:{stand_arr}")
            caps = WIDE_CAPTAINS if AIRCRAFT_TYPES[leg["actype"]][1] == "宽体" \
                else NARROW_CAPTAINS
            for cn in (RNG.choice(caps), RNG.choice(FIRST_OFFICERS),
                       RNG.choice(PURSERS)):
                await bs.add_relation(leg["key"], "由机组执飞", f"CRW:{cn}")
            if leg["prev_key"]:
                await bs.add_relation(leg["key"], "衔接前序", leg["prev_key"],
                                {"turnaround_min": leg["sched_turn"]})

            # 生命周期事件链
            ev_keys = []
            for ev in build_events(leg):
                ek = f"{leg['key']}:EV{ev['seq']:02d}"
                await bs.add_entity(ek, "航段状态事件",
                              f"{leg['leg_no']}·{ev['type']}", {
                                  "event_no": f"EV-{leg['leg_no']}-{ev['seq']:02d}",
                                  "event_type": ev["type"],
                                  "event_time": iso(ev["time"]),
                                  "time_kind": ev["kind"],
                                  "event_seq": ev["seq"],
                                  "data_source": leg["data_source"],
                                  "remark": ev["remark"],
                              }, f"[{st}] {ev['type']} {ev['kind']}")
                await bs.add_relation(leg["key"], "产生事件", ek)
                if ev_keys:
                    await bs.add_relation(ev_keys[-1], "后继事件", ek)
                if ev["type"] in ground_events:
                    std_key = f"STD:{stand_dep}" if ev["seq"] <= 4 else f"STD:{stand_arr}"
                    await bs.add_relation(ek, "发生于机位", std_key)
                if ev["type"] == "起飞":
                    await bs.add_relation(ek, "发生于跑道", f"RWY:{rwy_dep}")
                elif ev["type"] == "落地":
                    await bs.add_relation(ek, "发生于跑道", f"RWY:{rwy_arr}")
                ev_keys.append(ek)

            # 派生：延误 / 异常 / 告警
            for d in leg["delays"]:
                dk = f"{leg['key']}:DLY:{d['type']}"
                await bs.add_entity(dk, "延误记录", f"{leg['leg_no']}·{d['type']}", {
                    "delay_no": d["no"], "delay_type": d["type"],
                    "delay_minutes": d["minutes"], "cause_category": d["cat"],
                    "cause_detail": d["detail"], "responsibility": d["resp"],
                    "recorded_at": iso(d["at"]),
                    "start_time": iso(leg["sobt"]), "end_time": iso(
                        leg["aobt"] if d["type"] == "出发延误" else leg["aibt"]),
                    "duration": d["minutes"],
                }, f"延误{d['minutes']}分钟（{d['cat']}）")
                await bs.add_relation(leg["key"], "产生延误", dk)
            for a in leg["abnormals"]:
                ak = f"{leg['key']}:ABN"
                await bs.add_entity(ak, "异常事件", f"{leg['leg_no']}·{a['type']}", {
                    "abn_no": a["no"], "abnormal_type": a["type"],
                    "occurred_at": iso(a["at"]), "disposal": a["disposal"],
                    "reason": a["reason"], "severity": a["severity"],
                    "start_time": iso(a["at"]), "end_time": iso(
                        leg["aibt"] or leg["eta"]),
                    "handle_status": "已处置" if st in ("到达", "取消") else "处置中",
                }, f"{a['type']}：{a['disposal']}")
                await bs.add_relation(leg["key"], "发生异常", ak)
                for d in leg["delays"]:
                    await bs.add_relation(f"{leg['key']}:DLY:{d['type']}", "源于异常", ak)
            for al in leg["alerts"]:
                lk = f"{leg['key']}:ALR:{al['type']}"
                await bs.add_entity(lk, "运行告警", f"{leg['leg_no']}·{al['type']}", {
                    "alert_no": al["no"], "alert_type": al["type"],
                    "severity": al["severity"], "risk_level": al["risk_level"],
                    "triggered_at": iso(al["at"]),
                    "threshold": al["threshold"], "trigger_value": al["trigger_value"],
                    "lasted_min": int((NOW - al["at"]).total_seconds() // 60)
                                  if al["status"] == "待处理" else 0,
                    "message": al["message"], "handle_dept": al["handle_dept"],
                    "related_leg_no": leg["leg_no"],
                    "handle_status": al["status"], "handler": al["handler"],
                }, al["message"])
                await bs.add_relation(leg["key"], "触发告警", lk)
            _ = future  # 预留（未来航段 props 已由 finalize 处理）

        await bs.finish()
        await db.commit()
        print(f"\n航段：{len(legs)} 条（状态分布 {dict(Counter(l['status'] for l in legs))}）")
        print(f"[完成] 知识库「{SEED_KB_NAME}」写入："
              f"实体 {sum(v for k, v in bs.stat.items() if k in ont_ids)} 条、"
              f"关系 {sum(v for k, v in bs.stat.items() if k in rel_ids)} 条")
        for k in ("取消", "复飞", "空中等待"):
            n = sum(len(l["abnormals"]) for l in legs)
            if k == "复飞":
                n = sum(1 for l in legs if l["go_around"])
            if k == "取消":
                n = sum(1 for l in legs if l["cancelled"])
            print(f"  {k}: {n}")
        print(f"  延误记录: {sum(len(l['delays']) for l in legs)}  "
              f"运行告警: {sum(len(l['alerts']) for l in legs)}")

        if sync_graph:
            await sync_analysis_graph(cat.id)


async def sync_analysis_graph(category_id: str) -> None:
    """PG 写完后，复用页面「图迁入」同一路径把该类别实例同步进 Neo4j 分析图。

    - 走 GraphSyncService.start_run + execute_run：UNWIND 批量写入、
      category_id 圈定（按本体类别隔离）、迁入后自动重建 GDS 投影，
      任务进度落在 graph_sync_runs 表（页面迁入管理可见）；
    - Neo4j 不可用 / graph provider 非 neo4j 时仅警告，不影响种子结果。
    """
    from services.graph_sync_service import GraphSyncService

    print("\n[图迁入] 同步实例到 Neo4j 分析图（按本体类别隔离）...")
    try:
        async with async_session() as db:
            run = await GraphSyncService.start_run(db, category_id)
        await GraphSyncService.execute_run(run["id"], category_id)
        async with async_session() as db:
            final = await GraphSyncService.get_run(db, run["id"])
        if final and final["status"] == "done":
            print(f"[图迁入] 完成：实体 {final['entity_count']:,} / "
                  f"关系 {final['relation_count']:,}（run={final['id']}）")
        else:
            print(f"[图迁入] 状态异常：{final and final['status']} "
                  f"{final and final['error'] or ''}")
    except Exception as exc:  # noqa: BLE001
        print(f"[图迁入] 跳过：{exc}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 国航航班运行监控实例数据")
    parser.add_argument("--small", action="store_true", help="小样例（8航班×2天）")
    parser.add_argument("--skip-graph", action="store_true",
                        help="跳过 Neo4j 分析图迁入（仅写 PostgreSQL）")
    args = parser.parse_args()
    asyncio.run(seed(args.small, sync_graph=not args.skip_graph))


if __name__ == "__main__":
    main()
