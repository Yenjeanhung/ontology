"""Seed 民航航班运行监控「运行时层」：函数 + 派生属性 + 工作流 + 定时计划。

前置：
    python scripts/seed_flight_ops_ontology.py   # 定义层（类别/本体/属性）
    python scripts/seed_flight_ops_entities.py   # 示例实体（航段/航班/机场…）

用法：
    cd backend
    python scripts/seed_flight_ops_runtime.py              # 首次生成
    python scripts/seed_flight_ops_runtime.py --force      # 删除同名数据后重建
    python scripts/seed_flight_ops_runtime.py --verify     # 挑一个航段实体试算全部派生属性

生成内容（挂在类别「民航航班运行监控本体」/ 本体「航段」下）：
    函数 ×4        flight_duration_min / etd_delay_min / eta_delay_min / leg_phase
    派生属性 ×4    与函数一一绑定，读时（virtual）模式 = 每次读取实时计算
    动作 ×3        编排模式（flow）：航班放行复核 / 航段运行快照 / 航班延误告警评估
    工作流 ×2      航段派生属性定时物化（HTTP×4 批量物化）/ 单航段运行简报（HTTP+LLM）
    定时计划 ×1    每 30 分钟触发「航段派生属性定时物化」

说明：
    - 读时模式天然满足「计划变动即重算」：etd/eta 等属性一改，下次查询
      GET /api/entities/{id}/derived-properties?refresh=true 即返回新值。
    - 工作流 A 的定时物化是把计算值批量写入实体属性（materialize），供列表页直读；
      注意物化接口会把派生属性的 materialize_mode 置为 materialized。
    - 定时计划由调度引擎在后端启动时从 DB 加载：若生成计划时后端正在运行，
      需重启后端使计划生效（或在前端「定时计划」页编辑保存一次触发同步）。
    - 工作流通过 HTTP 节点调用本机后端 API；默认 WORKFLOW_HTTP_ALLOW_PRIVATE_NET=true
      放行 localhost，若改为 false 需调整。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import async_session, init_db
from models import (
    Entity,
    OntologyCategory,
    OntologyDerivedProperty,
    OntologyFunction,
    OntologyService,
    Schedule,
    Workflow,
    WorkflowRun,
)
from services.ontology_function_service import DerivedPropertyService
from flight_ops_domain import CATEGORY_NAME

LEG_ONTOLOGY_NAME = "航段"

# 后端 API 基地址（HTTP 节点用）；HOST 为 0.0.0.0 时浏览器/节点侧应访问 127.0.0.1
_API_HOST = "127.0.0.1" if settings.HOST in ("0.0.0.0", "::") else settings.HOST
DEFAULT_BASE_URL = f"http://{_API_HOST}:{settings.PORT}"


# ══════════════════════════ 函数代码（沙箱仅放行标准库子集） ══════════════════════════

_HELPER_PARSE = '''\
def _parse(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).strip())
    except ValueError:
        return None
'''

FUNC_DURATION = '''\
from datetime import datetime

''' + _HELPER_PARSE + '''

def run(params, entity, context):
    """预计飞行时长（分钟）= ETA - ETD；缺预计时间时回退计划时间（SIBT/SOBT）。"""
    p = entity.get("properties") or {}
    etd = _parse(p.get("etd")) or _parse(p.get("sobt"))
    eta = _parse(p.get("eta")) or _parse(p.get("sibt"))
    if not etd or not eta or eta <= etd:
        return None
    return int((eta - etd).total_seconds() // 60)
'''

FUNC_ETD_DELAY = '''\
from datetime import datetime

''' + _HELPER_PARSE + '''

def run(params, entity, context):
    """预计起飞延误（分钟）= ETD - SOBT；无滚动预计（ETD为空）按正点 0 计。"""
    p = entity.get("properties") or {}
    sobt = _parse(p.get("sobt"))
    if not sobt:
        return None
    etd = _parse(p.get("etd")) or sobt
    delay = int((etd - sobt).total_seconds() // 60)
    if params.get("only_late") and delay < 0:
        return 0
    return delay
'''

FUNC_ETA_DELAY = '''\
from datetime import datetime

''' + _HELPER_PARSE + '''

def run(params, entity, context):
    """预计到达延误（分钟）= ETA - SIBT；无滚动预计（ETA为空）按正点 0 计。"""
    p = entity.get("properties") or {}
    sibt = _parse(p.get("sibt"))
    if not sibt:
        return None
    eta = _parse(p.get("eta")) or sibt
    delay = int((eta - sibt).total_seconds() // 60)
    if params.get("only_late") and delay < 0:
        return 0
    return delay
'''

FUNC_LEG_PHASE = '''\
from datetime import datetime

''' + _HELPER_PARSE + '''

_TERMINAL = ("取消", "备降", "返航")

def run(params, entity, context):
    """当前运行阶段：按当前时刻与 ETD/AOBT/ATOT/ALDT/AIBT 推断。"""
    p = entity.get("properties") or {}
    status = str(p.get("leg_status") or "").strip()
    if status in _TERMINAL:
        return status
    now = datetime.now()
    aibt, aldt = _parse(p.get("aibt")), _parse(p.get("aldt"))
    atot, aobt = _parse(p.get("atot")), _parse(p.get("aobt"))
    etd = _parse(p.get("etd")) or _parse(p.get("sobt"))
    if aibt and now >= aibt:
        return "已到达"
    if aldt and now >= aldt:
        return "落地滑入"
    if atot and now >= atot:
        return "空中飞行"
    if aobt and now >= aobt:
        return "已撤轮档"
    if etd and now >= etd:
        return "待撤轮档"
    if etd:
        mins = int((etd - now).total_seconds() // 60)
        if 0 <= mins <= 120:
            return f"地面保障（约{mins}分钟后撤轮档）"
    return "计划中"
'''


# ── 告警规则函数（每条规则一个函数，阈值/类型/部门从规则实体属性读取）──
# 约定：entity = 「告警规则」实体（threshold_low/mid/high + alert_type + handle_dept + is_enabled）；
#       params.legs = 当日航段列表（escalate 为 params.alerts = 未解除告警列表）；
#       返回统一 {alerts, alert_count, checked_count}，alerts 条目与入库字段对齐。

FUNC_RULE_LATE_GATE = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """晚关门：SOBT 后 threshold_low/mid/high 分钟仍未出港（未撤轮档=未关舱门）。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 10)
    mid = float(rp.get("threshold_mid") or 20)
    hi = float(rp.get("threshold_high") or 30)
    a_type = rp.get("alert_type") or "晚关门"
    dept_map = {"低": "飞行控制室", "中": "运行控制室", "高": "总值班室"}
    legs = params.get("legs") or []
    now = datetime.now()
    alerts = []
    for it in legs:
        p = it.get("properties") or {}
        sobt = _parse(p.get("sobt"))
        if not sobt or not p.get("leg_status"):
            continue
        late_min = int((now - sobt).total_seconds() // 60)
        if str(p.get("leg_status")) in ("计划", "登机") and late_min >= lo:
            level = "高" if late_min > hi else ("中" if late_min > mid else "低")
            leg_no = p.get("leg_no") or it.get("name", "")
            alerts.append({
                "alert_no": "ALT-%s-%s-%s" % (now.strftime("%Y%m%d"), leg_no, a_type[:2]),
                "leg_no": leg_no, "leg_entity_id": it.get("id", ""),
                "alert_type": a_type, "risk_level": level, "trigger_value": late_min,
                "message": "%s 已过计划撤轮档%d分钟仍未出港" % (leg_no, late_min),
                "handle_dept": dept_map.get(level, rp.get("handle_dept") or ""),
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_LATE_LAND = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """超时未落：ATOT+计划飞行时长 后 threshold_low/mid/high 分钟仍无落地报。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 5)
    mid = float(rp.get("threshold_mid") or 10)
    hi = float(rp.get("threshold_high") or 15)
    a_type = rp.get("alert_type") or "超时未落"
    dept_map = {"低": "飞行控制室", "中": "运行控制室", "高": "总值班室"}
    legs = params.get("legs") or []
    now = datetime.now()
    alerts = []
    for it in legs:
        p = it.get("properties") or {}
        if str(p.get("leg_status")) != "巡航":
            continue
        sobt = _parse(p.get("sobt"))
        sibt = _parse(p.get("sibt"))
        atot = _parse(p.get("atot"))
        if not (atot and sobt and sibt):
            continue
        over = int((now - (atot + (sibt - sobt))).total_seconds() // 60)
        if over >= lo:
            level = "高" if over > hi else ("中" if over > mid else "低")
            leg_no = p.get("leg_no") or it.get("name", "")
            alerts.append({
                "alert_no": "ALT-%s-%s-%s" % (now.strftime("%Y%m%d"), leg_no, a_type[:2]),
                "leg_no": leg_no, "leg_entity_id": it.get("id", ""),
                "alert_type": a_type, "risk_level": level, "trigger_value": over,
                "message": "%s 超过计划到达%d分钟仍无落地报" % (leg_no, over),
                "handle_dept": dept_map.get(level, rp.get("handle_dept") or ""),
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_CREW_CHANGE = '''\
from datetime import datetime

def run(params, entity, context):
    """机组变更：放行后名单变更即低风险告警，建议人工复核资质与连飞限制。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    a_type = rp.get("alert_type") or "机组变更"
    dept = rp.get("handle_dept") or "飞行控制室"
    legs = params.get("legs") or []
    now = datetime.now()
    alerts = []
    for it in legs:
        p = it.get("properties") or {}
        if str(p.get("is_crew_changed")).lower() not in ("true", "1"):
            continue
        if str(p.get("leg_status")) not in ("计划", "登机"):
            continue
        leg_no = p.get("leg_no") or it.get("name", "")
        alerts.append({
            "alert_no": "ALT-%s-%s-%s" % (now.strftime("%Y%m%d"), leg_no, a_type[:2]),
            "leg_no": leg_no, "leg_entity_id": it.get("id", ""),
            "alert_type": a_type, "risk_level": "低", "trigger_value": 0,
            "message": "%s 放行后机组名单变更，请确认资质与连飞限制" % leg_no,
            "handle_dept": dept,
        })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_AIRPORT_AGG = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """晚关门机场聚合：同一出发机场 2 小时窗口内晚关门 ≥threshold_low 架次（中风险）。

    晚关门航段口径：登机中且超过计划撤轮档 10 分钟（与晚关门规则低风险阈值一致）。
    """
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 5)
    a_type = rp.get("alert_type") or "晚关门机场聚合"
    dept = rp.get("handle_dept") or "运行控制室"
    legs = params.get("legs") or []
    now = datetime.now()
    by_ap = {}
    for it in legs:
        p = it.get("properties") or {}
        sobt = _parse(p.get("sobt"))
        if not sobt or str(p.get("leg_status")) != "登机":
            continue
        if int((now - sobt).total_seconds() // 60) >= 10:
            by_ap.setdefault(str(p.get("dep_airport") or "?"), []).append(p)
    alerts = []
    for ap, lst in sorted(by_ap.items()):
        if len(lst) >= lo:
            alerts.append({
                "alert_no": "ALT-%s-AP-%s" % (now.strftime("%Y%m%d"), ap),
                "leg_no": "-" + ap, "leg_entity_id": "",
                "alert_type": a_type, "risk_level": "中", "trigger_value": len(lst),
                "message": "机场 %s 近2小时晚关门 %d 架次，运行态势异常" % (ap, len(lst)),
                "handle_dept": dept,
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_ROUTE_AGG = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """晚关门航线聚合：当日同一航线晚关门 ≥threshold_low 班次（中风险），建议排查共因。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 3)
    a_type = rp.get("alert_type") or "晚关门航线聚合"
    dept = rp.get("handle_dept") or "运行控制室"
    legs = params.get("legs") or []
    now = datetime.now()
    by_rt = {}
    for it in legs:
        p = it.get("properties") or {}
        sobt = _parse(p.get("sobt"))
        if not sobt or str(p.get("leg_status")) != "登机":
            continue
        if int((now - sobt).total_seconds() // 60) >= 10:
            by_rt.setdefault("%s-%s" % (p.get("dep_airport"), p.get("arr_airport")), []).append(p)
    alerts = []
    for rt, lst in sorted(by_rt.items()):
        if len(lst) >= lo:
            alerts.append({
                "alert_no": "ALT-%s-RT-%s" % (now.strftime("%Y%m%d"), rt.replace("-", "")),
                "leg_no": "-" + rt, "leg_entity_id": "",
                "alert_type": a_type, "risk_level": "中", "trigger_value": len(lst),
                "message": "航线 %s 当日晚关门 %d 班次，建议排查共因" % (rt, len(lst)),
                "handle_dept": dept,
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_COMBO_RISK = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """组合风险：同一航段命中 ≥threshold_low 类风险事件（晚关门/超时未落/机组变更）升高。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 2)
    a_type = rp.get("alert_type") or "晚关门组合风险"
    dept = rp.get("handle_dept") or "总值班室"
    legs = params.get("legs") or []
    now = datetime.now()
    hit_types = {}
    for it in legs:
        p = it.get("properties") or {}
        sobt = _parse(p.get("sobt"))
        if not sobt or not p.get("leg_status"):
            continue
        st = str(p.get("leg_status"))
        leg_no = p.get("leg_no") or it.get("name", "")
        hits = hit_types.setdefault(leg_no, [])
        if st in ("计划", "登机") and int((now - sobt).total_seconds() // 60) >= 10:
            hits.append("晚关门")
        atot = _parse(p.get("atot"))
        sibt = _parse(p.get("sibt"))
        if st == "巡航" and atot and sobt and sibt:
            over = int((now - (atot + (sibt - sobt))).total_seconds() // 60)
            if over >= 5:
                hits.append("超时未落")
        if str(p.get("is_crew_changed")).lower() in ("true", "1") and st in ("计划", "登机"):
            hits.append("机组变更")
    alerts = []
    for leg_no, hits in hit_types.items():
        if len(set(hits)) >= lo:
            alerts.append({
                "alert_no": "ALT-%s-CB-%s" % (now.strftime("%Y%m%d"), leg_no),
                "leg_no": leg_no, "leg_entity_id": "",
                "alert_type": a_type, "risk_level": "高", "trigger_value": len(set(hits)),
                "message": "%s 同时命中多类风险事件（%s），升级高风险"
                           % (leg_no, "+".join(sorted(set(hits)))),
                "handle_dept": dept,
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(legs)}
'''

FUNC_RULE_ESCALATE = '''\
from datetime import datetime

def _parse(v):
    try:
        return datetime.fromisoformat(str(v).strip())
    except (ValueError, TypeError):
        return None

def run(params, entity, context):
    """持续未解除升级：低风险 >threshold_low 分钟升中、中风险 >threshold_mid 分钟升高。"""
    rp = entity.get("properties") or {}
    if str(rp.get("is_enabled")).lower() in ("false", "0", "none"):
        return {"alerts": [], "alert_count": 0, "checked_count": 0}
    lo = float(rp.get("threshold_low") or 20)
    mid = float(rp.get("threshold_mid") or 30)
    items = params.get("alerts") or []
    now = datetime.now()
    alerts = []
    for it in items:
        p = it.get("properties") or {}
        if str(p.get("handle_status")) not in ("待处理", "处置中"):
            continue
        trig = _parse(p.get("triggered_at"))
        if not trig:
            continue
        lasted = max(0, int((now - trig).total_seconds() // 60))
        level = str(p.get("risk_level") or "低")
        new_level, reason = None, ""
        if level == "低" and lasted > lo:
            new_level = "中"
            reason = "低风险告警持续%d分钟未解除（阈值%d分钟）" % (lasted, int(lo))
        elif level == "中" and lasted > mid:
            new_level = "高"
            reason = "中风险告警持续%d分钟未解除（处置时限%d分钟）" % (lasted, int(mid))
        if new_level:
            alerts.append({
                "entity_id": it.get("id"),
                "alert_no": p.get("alert_no") or it.get("name"),
                "alert_type": p.get("alert_type"), "leg_no": p.get("related_leg_no"),
                "old_level": level, "new_level": new_level, "lasted_min": lasted,
                "reason": reason,
                "handle_dept": {"中": "运行控制室", "高": "总值班室"}.get(new_level, "运行控制室"),
            })
    return {"alerts": alerts, "alert_count": len(alerts), "checked_count": len(items)}
'''


# ══════════════════════════ 种子定义 ══════════════════════════

FUNCTIONS: list[dict] = [
    {
        "name": "预计飞行时长",
        "code": "flight_duration_min",
        "description": "预计飞行时长（分钟）= ETA − ETD；预计时间缺失回退计划时间（SIBT/SOBT）。",
        "code_text": FUNC_DURATION,
        "params_schema": [],
        "return_schema": '{"type": "number", "unit": "minutes"}',
        "sort_order": 10,
    },
    {
        "name": "预计起飞延误",
        "code": "etd_delay_min",
        "description": "预计起飞延误（分钟）= ETD − SOBT，正值晚于计划；无滚动预计按正点 0 计。可选参数 only_late：true 时提前到达按 0 计。",
        "code_text": FUNC_ETD_DELAY,
        "params_schema": [{
            "name": "only_late", "label": "仅统计延误（提前按0计）",
            "type": "boolean", "required": False, "default": False,
        }],
        "return_schema": '{"type": "number", "unit": "minutes"}',
        "sort_order": 20,
    },
    {
        "name": "预计到达延误",
        "code": "eta_delay_min",
        "description": "预计到达延误（分钟）= ETA − SIBT，正值晚于计划；无滚动预计按正点 0 计。可选参数 only_late：true 时提前到达按 0 计。",
        "code_text": FUNC_ETA_DELAY,
        "params_schema": [{
            "name": "only_late", "label": "仅统计延误（提前按0计）",
            "type": "boolean", "required": False, "default": False,
        }],
        "return_schema": '{"type": "number", "unit": "minutes"}',
        "sort_order": 30,
    },
    {
        "name": "当前运行阶段",
        "code": "leg_phase",
        "description": "按当前时刻与 ETD/AOBT/ATOT/ALDT/AIBT 推断运行阶段：计划中/地面保障/待撤轮档/已撤轮档/空中飞行/落地滑入/已到达。",
        "code_text": FUNC_LEG_PHASE,
        "params_schema": [],
        "return_schema": '{"type": "string"}',
        "sort_order": 40,
    },
]

DERIVED_PROPS: list[dict] = [
    {
        "name": "预计飞行时长", "code": "flight_duration_min", "data_type": "number",
        "function_code": "flight_duration_min", "params": {},
        "description": "实时计算：ETA − ETD（分钟）", "sort_order": 10,
    },
    {
        "name": "预计起飞延误", "code": "etd_delay_min", "data_type": "number",
        "function_code": "etd_delay_min", "params": {"only_late": True},
        "description": "实时计算：ETD − SOBT（分钟），提前按 0 计", "sort_order": 20,
    },
    {
        "name": "预计到达延误", "code": "eta_delay_min", "data_type": "number",
        "function_code": "eta_delay_min", "params": {"only_late": True},
        "description": "实时计算：ETA − SIBT（分钟），提前按 0 计", "sort_order": 30,
    },
    {
        "name": "当前运行阶段", "code": "leg_phase", "data_type": "string",
        "function_code": "leg_phase", "params": {},
        "description": "实时推断：按当前时刻与各里程碑时间确定阶段", "sort_order": 40,
    },
]

WF_A_NAME = "航段派生属性定时物化"
WF_B_NAME = "单航段运行简报"
SCHEDULE_NAME = "航段派生属性物化（每30分钟）"


# ══════════════════════════ 动作（编排模式，组合上面的函数） ══════════════════════════

ACTION_CODES = ["leg.release.check", "leg.snapshot", "leg.delay.alert"]


def _fn_node(nid: str, title: str, code: str, ids: dict, params: dict | None = None) -> dict:
    return {
        "id": nid, "type": "function", "title": title,
        "config": {"function_id": ids.get(code, ""), "function_code": code,
                   "params": params or {}, "on_error": "fail"},
    }


def _cond_node(nid: str, title: str, rule: dict, on_false: str, abort_message: str = "") -> dict:
    return {
        "id": nid, "type": "condition", "title": title,
        "config": {"rule": rule, "on_false": on_false, "abort_message": abort_message},
    }


def _end_node(nid: str, title: str, result: dict | None = None,
              edits: list | None = None, abort_message: str = "") -> dict:
    if abort_message:
        return {"id": nid, "type": "end", "title": title,
                "config": {"mode": "abort", "abort_message": abort_message}}
    return {"id": nid, "type": "end", "title": title,
            "config": {"mode": "output", "result": result or {}, "edits": edits or []}}


# ── 告警规则函数与服务定义：7 条规则各一个函数；每条规则实体一个实体级 flow 服务，
#    服务仅做「实体(阈值参数) + 函数(规则逻辑)」的绑定，供告警扫描工作流以 service 节点引用 ──
RULE_ONTOLOGY_NAME = "告警规则"

RULE_FUNCTIONS: list[dict] = [
    {"name": "规则·晚关门", "code": "rule.late_gate",
     "description": "SOBT 后 10/20/30 分钟仍未出港，按规则实体阈值分级低/中/高",
     "code_text": FUNC_RULE_LATE_GATE,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 110},
    {"name": "规则·超时未落", "code": "rule.late_land",
     "description": "ATOT+计划飞行时长后 5/10/15 分钟仍无落地报，按规则实体阈值分级",
     "code_text": FUNC_RULE_LATE_LAND,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 111},
    {"name": "规则·机组变更", "code": "rule.crew_change",
     "description": "放行后机组名单变更即低风险告警",
     "code_text": FUNC_RULE_CREW_CHANGE,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 112},
    {"name": "规则·晚关门机场聚合", "code": "rule.airport_agg",
     "description": "同一出发机场 2 小时窗口内晚关门 ≥阈值 架次",
     "code_text": FUNC_RULE_AIRPORT_AGG,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 113},
    {"name": "规则·晚关门航线聚合", "code": "rule.route_agg",
     "description": "当日同一航线晚关门 ≥阈值 班次",
     "code_text": FUNC_RULE_ROUTE_AGG,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 114},
    {"name": "规则·晚关门组合风险", "code": "rule.combo_risk",
     "description": "同一航段命中 ≥阈值 类风险事件",
     "code_text": FUNC_RULE_COMBO_RISK,
     "params_schema": [{"name": "legs", "label": "航段列表", "type": "array", "required": True}],
     "sort_order": 115},
    {"name": "规则·持续未解除升级", "code": "rule.escalate",
     "description": "低/中风险告警超时未解除自动升级",
     "code_text": FUNC_RULE_ESCALATE,
     "params_schema": [{"name": "alerts", "label": "未解除告警列表", "type": "array", "required": True}],
     "sort_order": 116},
]

RULE_FUNC_CODES = [f["code"] for f in RULE_FUNCTIONS]


def build_rule_services(func_id_by_code: dict) -> list[dict]:
    """7 条规则各一个「实体级 flow 服务」：start → function(引用规则函数) → end。

    服务 owner 是对应的告警规则实体：函数执行时 entity 即规则实体，
    阈值/类型/牵头部门从实体属性读取——调阈值=改实体属性，无需改代码。
    """
    services = []
    for rdef in RULE_FUNCTIONS:
        rc = rdef["code"].split(".", 1)[1]
        input_name = rdef["params_schema"][0]["name"]
        title = rdef["name"].split("·", 1)[1]
        services.append({
            "rule_code": rc,
            "name": "规则评估·%s" % title,
            "code": rdef["code"],
            "description": "%s（实体服务：引用函数 %s，阈值取自规则实体属性）"
                           % (rdef["description"], rdef["code"]),
            "params": rdef["params_schema"],
            "flow": {
                "schema_version": 1,
                "nodes": [
                    {"id": "n1", "type": "start", "title": "开始", "config": {}},
                    {"id": "f1", "type": "function", "title": rdef["name"],
                     "config": {"function_id": func_id_by_code.get(rdef["code"], ""),
                                "function_code": rdef["code"],
                                "params": {input_name: "{{ params.%s }}" % input_name},
                                "on_error": "fail"}},
                    {"id": "e1", "type": "end", "title": "输出",
                     "config": {"mode": "output",
                                "result": {"alerts": "{{ f1.value.alerts }}",
                                           "alert_count": "{{ f1.value.alert_count }}",
                                           "checked_count": "{{ f1.value.checked_count }}"},
                                "edits": []}},
                ],
                "edges": [{"source": "n1", "target": "f1"}, {"source": "f1", "target": "e1"}],
                "layout": {"n1": {"x": 80, "y": 160}, "f1": {"x": 320, "y": 160},
                           "e1": {"x": 560, "y": 160}},
            },
        })
    return services


# 告警扫描工作流中的规则服务节点：(rule_code, 节点id, 节点标题)
RULE_FLOW_NODES = [
    ("late_gate", "svc_late_gate", "规则·晚关门"),
    ("late_land", "svc_late_land", "规则·超时未落"),
    ("crew_change", "svc_crew_change", "规则·机组变更"),
    ("airport_agg", "svc_airport_agg", "规则·机场聚合"),
    ("route_agg", "svc_route_agg", "规则·航线聚合"),
    ("combo_risk", "svc_combo_risk", "规则·组合风险"),
]


def build_actions(func_ids: dict) -> list[dict]:
    """生成 3 个编排动作（flow），全部只调用种子函数、不写业务代码。"""

    # 1) 航班放行复核：算延误 → 超阈值中止（转人工）→ 未超则看阶段并写回「放行」
    release = {
        "name": "航班放行复核", "code": "leg.release.check", "sort_order": 10,
        "description": "组合函数「预计起飞延误 / 当前运行阶段」：预计延误未超阈值时写回 leg_status=放行；"
                       "超阈值则中止并提示转人工复核。",
        "params": [{"name": "delay_threshold_min", "label": "放行延误阈值(分钟)", "type": "number",
                    "required": False, "default": 30, "description": "预计起飞延误超过该值不放行"}],
        "flow": {
            "schema_version": 1,
            "nodes": [
                {"id": "n1", "type": "start", "title": "开始", "config": {}},
                _fn_node("f1", "预计起飞延误", "etd_delay_min", func_ids, {"only_late": True}),
                _cond_node("c1", "延误未超阈值?", {
                    "combinator": "and",
                    "rules": [{"field": "{{ f1.value }}", "operator": "<",
                               "value": "{{ params.delay_threshold_min }}"}],
                }, "abort", "预计起飞延误 {{ f1.value }} 分钟已超阈值，暂不放行，请转人工复核"),
                _fn_node("f2", "当前运行阶段", "leg_phase", func_ids),
                _end_node("e1", "放行",
                          result={"decision": "放行", "delay_min": "{{ f1.value }}", "phase": "{{ f2.value }}"},
                          edits=[{"op": "set_property", "property_code": "leg_status", "value": "放行"}]),
            ],
            "edges": [
                {"source": "n1", "target": "f1"},
                {"source": "f1", "target": "c1"},
                {"source": "c1", "target": "f2", "source_handle": "true"},
                {"source": "f2", "target": "e1"},
            ],
            "layout": {"n1": {"x": 40, "y": 160}, "f1": {"x": 240, "y": 160},
                       "c1": {"x": 440, "y": 160}, "f2": {"x": 640, "y": 160},
                       "e1": {"x": 840, "y": 160}},
        },
    }

    # 2) 航段运行快照：串行调 4 个函数聚合为只读简报（不写回）
    snapshot = {
        "name": "航段运行快照", "code": "leg.snapshot", "sort_order": 20,
        "description": "组合全部 4 个函数，一次输出该航段的飞行时长 / 起飞延误 / 到达延误 / 当前阶段（只读，不写数据）。",
        "params": [],
        "flow": {
            "schema_version": 1,
            "nodes": [
                {"id": "n1", "type": "start", "title": "开始", "config": {}},
                _fn_node("f1", "预计飞行时长", "flight_duration_min", func_ids),
                _fn_node("f2", "预计起飞延误", "etd_delay_min", func_ids, {"only_late": True}),
                _fn_node("f3", "预计到达延误", "eta_delay_min", func_ids, {"only_late": True}),
                _fn_node("f4", "当前运行阶段", "leg_phase", func_ids),
                _end_node("e1", "快照输出", result={
                    "leg_no": "{{ entity.properties.leg_no }}",
                    "duration_min": "{{ f1.value }}",
                    "etd_delay_min": "{{ f2.value }}",
                    "eta_delay_min": "{{ f3.value }}",
                    "phase": "{{ f4.value }}",
                }),
            ],
            "edges": [
                {"source": "n1", "target": "f1"},
                {"source": "f1", "target": "f2"},
                {"source": "f2", "target": "f3"},
                {"source": "f3", "target": "f4"},
                {"source": "f4", "target": "e1"},
            ],
            "layout": {"n1": {"x": 40, "y": 160}, "f1": {"x": 240, "y": 160},
                       "f2": {"x": 440, "y": 160}, "f3": {"x": 640, "y": 160},
                       "f4": {"x": 840, "y": 160}, "e1": {"x": 1040, "y": 160}},
        },
    }

    # 3) 航班延误告警评估：起飞/到达延误 → 达阈值走告警分支，否则走正常分支（双出口演示）
    alert = {
        "name": "航班延误告警评估", "code": "leg.delay.alert", "sort_order": 30,
        "description": "组合函数「预计起飞延误 / 预计到达延误」：起飞延误达阈值输出告警结论，否则输出正常结论（双分支，不写数据）。",
        "params": [{"name": "alert_threshold_min", "label": "告警阈值(分钟)", "type": "number",
                    "required": False, "default": 45, "description": "预计起飞延误达到该值触发告警结论"}],
        "flow": {
            "schema_version": 1,
            "nodes": [
                {"id": "n1", "type": "start", "title": "开始", "config": {}},
                _fn_node("f1", "预计起飞延误", "etd_delay_min", func_ids, {"only_late": True}),
                _fn_node("f2", "预计到达延误", "eta_delay_min", func_ids, {"only_late": True}),
                _cond_node("c1", "延误达告警阈值?", {
                    "combinator": "and",
                    "rules": [{"field": "{{ f1.value }}", "operator": ">=",
                               "value": "{{ params.alert_threshold_min }}"}],
                }, "continue"),
                _end_node("e1", "告警结论", result={
                    "alert": True, "level": "中",
                    "etd_delay_min": "{{ f1.value }}", "eta_delay_min": "{{ f2.value }}",
                    "message": "预计起飞延误 {{ f1.value }} 分钟，已达告警阈值，请关注过站与衔接",
                }),
                _end_node("e2", "正常结论", result={
                    "alert": False,
                    "etd_delay_min": "{{ f1.value }}", "eta_delay_min": "{{ f2.value }}",
                    "message": "运行正常",
                }),
            ],
            "edges": [
                {"source": "n1", "target": "f1"},
                {"source": "f1", "target": "f2"},
                {"source": "f2", "target": "c1"},
                {"source": "c1", "target": "e1", "source_handle": "true"},
                {"source": "c1", "target": "e2", "source_handle": "false"},
            ],
            "layout": {"n1": {"x": 40, "y": 200}, "f1": {"x": 240, "y": 200},
                       "f2": {"x": 440, "y": 200}, "c1": {"x": 640, "y": 200},
                       "e1": {"x": 840, "y": 110}, "e2": {"x": 840, "y": 300}},
        },
    }

    return [release, snapshot, alert]


# ══════════════════════════ 工作流定义 ══════════════════════════

def _finalize_graph(graph: dict) -> dict:
    """把 graph 修整为前端编辑器契约（缺一则编辑页渲染崩溃/节点叠原点）：
    - 边带 id 与 handle：Vue Flow 的边 id 不能为空（undefined 会触发内部 .toString() 崩溃）
    - 节点带 position：拓扑分层布局，参数与前端「整理布局」一致（列距 260 / 行距 96）
    - 节点 config 补齐各类型默认字段（对齐前端 DEFAULT_CONFIG / OUTPUT_FIELDS_DEFAULT）
    """
    nodes, edges = graph["nodes"], graph["edges"]
    for i, e in enumerate(edges, 1):
        e.setdefault("id", f"e{i}")
        e.setdefault("handle", "default")
    # ── Kahn 拓扑分层 ──
    ids = [n["id"] for n in nodes]
    indeg = {i: 0 for i in ids}
    succ: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        if e["source"] in succ and e["target"] in indeg:
            succ[e["source"]].append(e["target"])
            indeg[e["target"]] += 1
    layer: dict[str, int] = {}
    cur = [i for i in ids if indeg[i] == 0]
    li = 0
    while cur:
        for nid in cur:
            layer[nid] = li
        nxt: list[str] = []
        for nid in cur:
            for t in succ[nid]:
                indeg[t] -= 1
                if indeg[t] == 0:
                    nxt.append(t)
        cur = nxt
        li += 1
    for nid in ids:                                  # 环兜底：未分层的放最后一列
        layer.setdefault(nid, li)
    cols: dict[int, list[str]] = {}
    for n in nodes:                                  # 同列按定义顺序分行
        cols.setdefault(layer[n["id"]], []).append(n["id"])
    for li_, col in cols.items():
        for ri_, nid in enumerate(col):
            node = next(n for n in nodes if n["id"] == nid)
            node["position"] = {"x": 40 + li_ * 260,
                                "y": 60 + ri_ * 96 - (len(col) - 1) * 96 / 2}
    # ── config 默认补齐 ──
    for n in nodes:
        cfg = n.setdefault("config", {})
        if n["type"] == "http":
            cfg.setdefault("headers", {})
            cfg.setdefault("auth", {"type": "none"})
            cfg.setdefault("body", {"type": "none", "data": None, "content_type": ""})
            cfg.setdefault("max_retries", 1)
            cfg.setdefault("verify_ssl", True)
            cfg.setdefault("follow_redirects", True)
            cfg.setdefault("fail_on_error", False)
            cfg.setdefault("output_fields", ["success", "status_code", "data"])
        elif n["type"] == "llm":
            cfg.setdefault("structured_outputs", [])
            cfg.setdefault("output_fields", ["text"])
        else:
            cfg.setdefault("output_fields", [])
    return graph


def _workflow_a(base_url: str, props: list[dict]) -> dict:
    """start → http×4（串行物化）→ end。prop 的 id 在种子时动态填入。"""
    nodes = [{"id": "start", "type": "start", "title": "开始", "config": {}}]
    edges: list[dict] = []
    prev = "start"
    for idx, prop in enumerate(props, 1):
        nid = f"http_mat_{idx}"
        nodes.append({
            "id": nid, "type": "http", "title": f"物化-{prop['name']}",
            "config": {
                "method": "POST",
                "url": f"{base_url}/api/derived-properties/{prop['id']}/materialize/stream",
                "params": {"limit": 5000},
                "timeout_seconds": 120,
            },
        })
        edges.append({"source": prev, "target": nid})
        prev = nid
    nodes.append({
        "id": "end", "type": "end", "title": "结束",
        "config": {"outputs": [{"name": "summary", "value": "已完成 4 个派生属性的批量物化"}]},
    })
    edges.append({"source": prev, "target": "end"})
    return _finalize_graph({"nodes": nodes, "edges": edges})


def _workflow_b(base_url: str, sample_entity_id: str) -> dict:
    """start(entity_id) → http（实时计算派生属性）→ llm（生成简报）→ end。"""
    prompt = (
        "以下是航段实体的派生属性实时计算结果（JSON 数组，含 name/code/value）：\n"
        "{{http_dp.data}}\n\n"
        "请基于以上数据生成一段 150 字以内的航段运行简报，要求：\n"
        "1. 逐项说明各派生属性值及业务含义（时长/起飞延误/到达延误/当前阶段）；\n"
        "2. 延误为正时给出关注提示，为 0 或负时说明运行正常；\n"
        "3. 语言精炼，面向运行值班人员，不要编造数据中不存在的信息。"
    )
    graph = {
        "nodes": [
            {
                "id": "start", "type": "start", "title": "开始",
                "config": {"inputs": [{"name": "entity_id", "label": "航段实体ID",
                                       "type": "string", "required": True}]},
            },
            {
                "id": "http_dp", "type": "http", "title": "实时计算派生属性",
                "config": {
                    "method": "GET",
                    "url": base_url + "/api/entities/{{start.entity_id}}/derived-properties",
                    "params": {"refresh": "true"},
                    "timeout_seconds": 60,
                },
            },
            {
                "id": "llm_brief", "type": "llm", "title": "生成运行简报",
                "config": {
                    "system_prompt": "你是民航运行控制中心（AOC）值班助理，负责编写简明准确的航段运行简报。",
                    "prompt_template": prompt,
                },
            },
            {
                "id": "end", "type": "end", "title": "结束",
                "config": {"outputs": [{"name": "report", "value": "{{llm_brief.text}}"}]},
            },
        ],
        "edges": [
            {"source": "start", "target": "http_dp"},
            {"source": "http_dp", "target": "llm_brief"},
            {"source": "llm_brief", "target": "end"},
        ],
    }
    return _finalize_graph(graph)


WF_G_NAME = "航班运行风险告警扫描"
WF_H_NAME = "告警升级巡检"
WF_I_NAME = "单告警深度研判"
SCHEDULE_G_NAME = "运行风险告警扫描（每15分钟）"
SCHEDULE_H_NAME = "告警升级巡检（每15分钟·错峰）"


# ── 工作流 G 代码节点：合并 6 个规则评估服务的输出 ──
# 规则本身已资产化：「告警规则」实体（阈值参数）+ 本体函数（rule.*，规则逻辑）
# + 实体级 flow 服务（规则评估·xx），本节点只做结果汇总，不再内嵌规则逻辑。
CODE_SCAN = '''\
from datetime import datetime

RULE_NODES = ["svc_late_gate", "svc_late_land", "svc_crew_change",
              "svc_airport_agg", "svc_route_agg", "svc_combo_risk"]

def run(params, entity, context):
    """合并各规则服务的告警输出：去重、累计检查数、计算最高风险等级。"""
    alerts, seen, errors, checked = [], set(), [], 0
    for nid in RULE_NODES:
        out = context.get(nid) or {}
        data = out.get("data") or {}
        checked += int(data.get("checked_count") or 0)
        if not out.get("success"):
            errors.append(nid)
            continue
        for a in data.get("alerts") or []:
            key = (a.get("leg_no"), a.get("alert_type"))
            if key in seen:
                continue
            seen.add(key)
            alerts.append(a)
    rank = {"低": 0, "中": 1, "高": 2}
    max_level = max((a["risk_level"] for a in alerts), key=lambda x: rank[x]) if alerts else ""
    return {
        "alerts": alerts,
        "alert_count": len(alerts),
        "max_level": max_level,
        "has_high": max_level == "高",
        "rule_errors": errors,
        "checked_count": checked,
        "scanned_at": datetime.now().replace(microsecond=0).isoformat(),
        "summary": "；".join("%s[%s-%s]" % (a["alert_no"], a["alert_type"], a["risk_level"])
                              for a in alerts[:8]) or "无",
    }
'''


# ── 工作流 G 代码节点：告警入库（查重 + 建实体 + 挂关系，按 min_level 过滤） ──
CODE_SAVE = '''\
import httpx

def run(params, entity, context):
    """把规则命中的告警批量写入「运行告警」本体；人工驳回时仅写中低风险。"""
    scan = (context.get("code_scan") or {})
    alerts = scan.get("alerts") or []
    advisories = (context.get("agent_judge") or {}).get("advisories") or []
    min_level = str(params.get("min_level") or "低")
    rank = {"低": 0, "中": 1, "高": 2}
    sev = {"低": "提示", "中": "警告", "高": "严重"}
    advice_map = {}
    for a in (advisories if isinstance(advisories, list) else []):
        if isinstance(a, dict) and a.get("leg_no"):
            advice_map[a["leg_no"]] = a
    base = str(params.get("base_url") or "http://127.0.0.1:8000") + "/api"
    created, skipped, exists = [], [], 0
    with httpx.Client(base_url=base, timeout=10.0) as c:
        for al in alerts:
            if rank.get(al.get("risk_level"), 0) < rank.get(min_level, 0):
                skipped.append(al.get("alert_no"))
                continue
            no = al.get("alert_no")
            r = c.get("/entities", params={
                "ontology_id": params.get("alert_ontology_id"),
                "q": no, "page_size": 1})
            if r.status_code == 200 and (r.json().get("items") or []):
                exists += 1
                continue
            adv = advice_map.get(al.get("leg_no"), {})
            payload = {
                "ontology_id": params.get("alert_ontology_id"),
                "entity_type": "运行告警", "name": no,
                "description": "[%s风险][%s] %s" % (
                    al.get("risk_level"), al.get("alert_type"), al.get("message")),
                "properties": {
                    "alert_no": no, "alert_type": al.get("alert_type"),
                    "severity": sev.get(al.get("risk_level"), "提示"),
                    "risk_level": al.get("risk_level"),
                    "triggered_at": scan.get("scanned_at"),
                    "trigger_value": al.get("trigger_value"), "lasted_min": 0,
                    "message": al.get("message"),
                    "handle_dept": adv.get("handle_dept") or al.get("handle_dept"),
                    "advice": adv.get("advice") or "",
                    "related_leg_no": al.get("leg_no"),
                    "handle_status": "待处理", "handler": "",
                },
            }
            r = c.post("/entities", json=payload)
            if r.status_code >= 400:
                skipped.append("%s(HTTP%d)" % (no, r.status_code))
                continue
            ent = r.json() if isinstance(r.json(), dict) else {}
            if ent.get("id") and params.get("alert_rel_def_id") and al.get("leg_entity_id"):
                c.post("/relations", json={
                    "kb_id": params.get("kb_id"),
                    "relation_def_id": params.get("alert_rel_def_id"),
                    "relation_type": "触发告警",
                    "source_entity_id": al.get("leg_entity_id"),
                    "target_entity_id": ent.get("id"),
                    "description": "%s 触发 %s" % (al.get("leg_no"), al.get("alert_type")),
                })
            created.append(no)
    return {"created_count": len(created), "skipped_count": len(skipped),
            "exists_count": exists, "created": created[:10]}
'''


def _workflow_g(base_url: str, ids: dict, scan_date: str) -> dict:
    """start → http拉航段 → 6个规则服务并行评估（引用告警规则实体的评估服务）
    → code汇总 → cond有告警 → agent研判 → cond含中高 → 人工审批（高）
    → code入库（全量/仅中低）；无告警或全低各走对应分支。

    规则阈值在「告警规则」实体属性上：调阈值=改实体属性；改规则逻辑=函数编辑器。
    """
    prompt = (
        "你是航空公司运行控制中心（AOC）的风险研判助理。以下是本轮规则引擎扫描出的"
        "航班运行告警列表（JSON）：\n{{code_scan.alerts}}\n\n"
        "请逐条给出研判结果，最终只输出一个 JSON 对象（不要输出数组），字段：\n"
        "max_level：本轮研判的最高风险等级（低/中/高，无告警时填低）；\n"
        "advisories：JSON 数组，逐条告警的处置建议，每条含 leg_no（航段号/对象标识）、"
        "alert_type（告警类型）、risk_level（低/中/高）、advice（一句话处置建议，"
        "含具体动作如协调机位/催促保障/签派复核/启动备份机组）、handle_dept"
        "（牵头部门：飞行控制室/运行控制室/签派放行室/总值班室）。\n"
        "分级口径：低=岗位持续关注；中=主责部门5分钟内介入；高=总值班室3分钟内响应、"
        "跨部门联动。聚合类告警请结合机场/航线维度给出共因排查方向。"
    )
    bindings = ids.get("rule_bindings") or {}
    nodes = [
        {"id": "start", "type": "start", "title": "开始",
         "config": {"inputs": [{"name": "scan_date", "label": "扫描日期(yyyymmdd)",
                                "type": "string", "required": False}],
                    "defaults": {"scan_date": scan_date}}},
        {"id": "http_legs", "type": "http", "title": "拉取当日航段",
         "config": {"method": "GET",
                    "url": base_url + "/api/entities",
                    # 快照数据即当日运行全量，不做关键字检索（航段号不含日期，q 必然空结果）
                    "params": {"ontology_id": ids["leg_ontology_id"], "page_size": 200},
                    "timeout_seconds": 60}},
    ]
    edges = [{"source": "start", "target": "http_legs"}]
    # 6 个规则评估服务节点：同一上游，引擎自动并行执行
    for rc, nid, title in RULE_FLOW_NODES:
        b = bindings.get(rc) or {}
        nodes.append({
            "id": nid, "type": "service", "title": title,
            "config": {"ontology_id": ids["alert_ontology_id"],
                       "service_id": b.get("service_id", ""),
                       "entity_id": b.get("entity_id", ""),
                       "params": {"legs": "{{http_legs.data.items}}"},
                       "timeout_seconds": 60}})
        edges.append({"source": "http_legs", "target": nid})
        edges.append({"source": nid, "target": "code_scan"})
    nodes.append(
        {"id": "code_scan", "type": "code", "title": "告警汇总（6条规则服务）",
         "config": {"code_text": CODE_SCAN, "params": {}, "timeout_seconds": 30,
                    "structured_outputs": [
                        {"name": "alerts", "type": "array", "description": "命中的告警列表"},
                        {"name": "alert_count", "type": "number", "description": "告警条数"},
                        {"name": "max_level", "type": "string", "description": "最高风险等级"},
                        {"name": "rule_errors", "type": "array", "description": "执行失败的规则服务节点"},
                        {"name": "scanned_at", "type": "string", "description": "扫描时刻"},
                        {"name": "summary", "type": "string", "description": "告警摘要"}]}})
    nodes += [
        {"id": "cond_any", "type": "condition", "title": "存在告警？",
         "config": {"mode": "simple", "left": "{{code_scan.alert_count}}",
                    "operator": "gt", "right": 0}},
        {"id": "agent_judge", "type": "agent", "title": "智能体风险研判",
         "config": {"query_template": prompt,
                    "structured_outputs": [
                        {"name": "max_level", "type": "string",
                         "description": "本轮最高风险等级（低/中/高，无告警时填低）"},
                        {"name": "advisories", "type": "array",
                         "description": "逐条告警的处置建议（leg_no/alert_type/risk_level/advice/handle_dept）"}],
                    "fail_on_error": False, "timeout_seconds": 120}},
        {"id": "cond_high", "type": "condition", "title": "含中高风险？",
         "config": {"mode": "simple", "left": "{{agent_judge.max_level}}",
                    "operator": "==", "right": "高"}},
        {"id": "human_approve", "type": "human", "title": "高风险处置审批",
         "config": {"mode": "approve",
                    "description": "本轮扫描发现高风险告警，请审批处置方案"
                                   "（通过=全部入库并启动分级响应；驳回=高风险按误报暂缓，仅中低风险入库）",
                    "display_fields": [
                        {"label": "告警总数", "value": "{{code_scan.alert_count}}", "type": "text"},
                        {"label": "最高风险等级", "value": "{{code_scan.max_level}}", "type": "text"},
                        {"label": "告警清单", "value": "{{code_scan.summary}}", "type": "text"},
                        {"label": "研判建议", "value": "{{agent_judge.advisories}}", "type": "text"}],
                    "submit_text": "提交审批",
                    "comment": {"label": "审批意见", "required": False},
                    "assignee": "总值班室"}},
        {"id": "code_save_all", "type": "code", "title": "告警入库（全部）",
         "config": {"code_text": CODE_SAVE,
                    "params": {"min_level": "低", "base_url": base_url,
                               "kb_id": ids.get("kb_id"),
                               "alert_ontology_id": ids["alert_ontology_id"],
                               "alert_rel_def_id": ids.get("alert_rel_def_id")},
                    "timeout_seconds": 120,
                    "structured_outputs": [
                        {"name": "created_count", "type": "number"},
                        {"name": "skipped_count", "type": "number"},
                        {"name": "exists_count", "type": "number"}]}},
        {"id": "code_save_midlow", "type": "code", "title": "告警入库（仅中低风险）",
         "config": {"code_text": CODE_SAVE,
                    "params": {"min_level": "中", "base_url": base_url,
                               "kb_id": ids.get("kb_id"),
                               "alert_ontology_id": ids["alert_ontology_id"],
                               "alert_rel_def_id": ids.get("alert_rel_def_id")},
                    "timeout_seconds": 120,
                    "structured_outputs": [
                        {"name": "created_count", "type": "number"},
                        {"name": "skipped_count", "type": "number"},
                        {"name": "exists_count", "type": "number"}]}},
        {"id": "end_done", "type": "end", "title": "结束",
         "config": {"outputs": [
             {"name": "summary", "value": "扫描完成：告警 {{code_scan.alert_count}} 条"
                                           "（最高等级 {{code_scan.max_level}}），"
                                           "按审批结果完成入库"}]}},
        {"id": "end_empty", "type": "end", "title": "结束（无告警）",
         "config": {"outputs": [{"name": "summary", "value": "本轮扫描无告警"}]}},
    ]
    edges += [
        {"source": "code_scan", "target": "cond_any"},
        {"source": "cond_any", "target": "agent_judge", "handle": "true"},
        {"source": "cond_any", "target": "end_empty", "handle": "false"},
        {"source": "agent_judge", "target": "cond_high"},
        {"source": "cond_high", "target": "human_approve", "handle": "true"},
        {"source": "cond_high", "target": "code_save_all", "handle": "false"},
        {"source": "human_approve", "target": "code_save_all", "handle": "true"},
        {"source": "human_approve", "target": "code_save_midlow", "handle": "false"},
        {"source": "code_save_all", "target": "end_done"},
        {"source": "code_save_midlow", "target": "end_done"},
    ]
    return _finalize_graph({"nodes": nodes, "edges": edges})



# ── 工作流 H 代码节点：升级写回（PUT 更新告警实体等级/状态/持续时长） ──
# 升级判定已资产化为规则函数 rule.escalate + 规则实体「持续未解除升级」的评估服务
CODE_UPGRADE = '''\
import httpx

def run(params, entity, context):
    """执行升级：更新告警实体的风险等级/严重级别/处理状态/持续分钟。"""
    esc = ((context.get("svc_escalate") or {}).get("data") or {}).get("alerts") or []
    base = str(params.get("base_url") or "http://127.0.0.1:8000") + "/api"
    sev = {"低": "提示", "中": "警告", "高": "严重"}
    updated = []
    with httpx.Client(base_url=base, timeout=10.0) as c:
        for e in esc:
            r = c.get("/entities/" + str(e.get("entity_id")), timeout=10.0)
            if r.status_code >= 400:
                continue
            props = (r.json() or {}).get("properties") or {}
            props.update({
                "risk_level": e.get("new_level"),
                "severity": sev.get(e.get("new_level"), "警告"),
                "handle_status": "已升级",
                "lasted_min": e.get("lasted_min"),
            })
            u = c.put("/entities/" + str(e.get("entity_id")),
                      json={"properties": props})
            if u.status_code < 400:
                updated.append(e.get("alert_no"))
    return {"updated": updated, "updated_count": len(updated)}
'''


def _workflow_h(base_url: str, ids: dict) -> dict:
    """http拉未解除告警 → 规则服务·持续未解除升级 → cond有升级 → code写回 → agent通报 → end。"""
    prompt = (
        "你是航空公司运行控制中心（AOC）值班经理。以下告警因持续未解除而自动升级：\n"
        "{{svc_escalate.data.alerts}}\n\n"
        "请生成一份面向各牵头部门的升级通报（JSON）：report 字段输出通报正文，"
        "包含：升级原因、涉事航班/机场/航线、新风险等级对应的响应要求"
        "（中=主责部门5分钟内介入，高=总值班室3分钟内响应并跨部门联动）、"
        "以及建议的解除条件。语言简练、可直接群发。"
    )
    b = (ids.get("rule_bindings") or {}).get("escalate") or {}
    nodes = [
        {"id": "start", "type": "start", "title": "开始", "config": {"inputs": []}},
        {"id": "http_open", "type": "http", "title": "拉取运行告警",
         "config": {"method": "GET", "url": base_url + "/api/entities",
                    "params": {"ontology_id": ids["alert_ontology_id"], "page_size": 200},
                    "timeout_seconds": 60}},
        {"id": "svc_escalate", "type": "service", "title": "规则·持续未解除升级",
         "config": {"ontology_id": ids["alert_ontology_id"],
                    "service_id": b.get("service_id", ""),
                    "entity_id": b.get("entity_id", ""),
                    "params": {"alerts": "{{http_open.data.items}}"},
                    "timeout_seconds": 60}},
        {"id": "cond_esc", "type": "condition", "title": "有告警升级？",
         "config": {"mode": "simple", "left": "{{svc_escalate.data.alert_count}}",
                    "operator": "gt", "right": 0}},
        {"id": "code_upgrade", "type": "code", "title": "执行升级写回",
         "config": {"code_text": CODE_UPGRADE, "params": {"base_url": base_url},
                    "timeout_seconds": 120,
                    "structured_outputs": [
                        {"name": "updated_count", "type": "number"},
                        {"name": "updated", "type": "array"}]}},
        {"id": "agent_notify", "type": "agent", "title": "生成升级通报",
         "config": {"query_template": prompt,
                    "structured_outputs": [
                        {"name": "report", "type": "string", "description": "升级通报正文"}],
                    "fail_on_error": False, "timeout_seconds": 120}},
        {"id": "end_report", "type": "end", "title": "结束",
         "config": {"outputs": [
             {"name": "report", "value": "升级 {{code_upgrade.updated_count}} 条：\n"
                                          "{{agent_notify.report}}"}]}},
        {"id": "end_quiet", "type": "end", "title": "结束（无升级）",
         "config": {"outputs": [{"name": "report", "value": "巡检完成，无告警升级"}]}},
    ]
    edges = [
        {"source": "start", "target": "http_open"},
        {"source": "http_open", "target": "svc_escalate"},
        {"source": "svc_escalate", "target": "cond_esc"},
        {"source": "cond_esc", "target": "code_upgrade", "handle": "true"},
        {"source": "cond_esc", "target": "end_quiet", "handle": "false"},
        {"source": "code_upgrade", "target": "agent_notify"},
        {"source": "agent_notify", "target": "end_report"},
    ]
    return _finalize_graph({"nodes": nodes, "edges": edges})


# ── 工作流 I 代码节点：聚合单告警研判上下文（航段实体 + 派生属性刷新） ──
CODE_GATHER = '''\
import httpx

def run(params, entity, context):
    """根据告警实体找到关联航段，刷新派生属性并汇总研判上下文。"""
    alert = ((context.get("http_alert") or {}).get("data") or {})
    p = alert.get("properties") or {}
    leg_no = str(p.get("related_leg_no") or "")
    base = str(params.get("base_url") or "http://127.0.0.1:8000") + "/api"
    leg = {}
    with httpx.Client(base_url=base, timeout=15.0) as c:
        if leg_no and params.get("leg_ontology_id"):
            r = c.get("/entities", params={
                "ontology_id": params.get("leg_ontology_id"),
                "q": leg_no, "page_size": 1})
            items = (r.json() or {}).get("items") or [] if r.status_code == 200 else []
            if items:
                d = c.get("/entities/%s/derived-properties" % items[0].get("id"),
                          params={"refresh": "true"}, timeout=60.0)
                if d.status_code == 200:
                    leg = d.json() or {}
                else:
                    leg = {"properties": items[0].get("properties")}
    return {"alert": {"alert_no": p.get("alert_no"), "alert_type": p.get("alert_type"),
                      "risk_level": p.get("risk_level"), "message": p.get("message"),
                      "trigger_value": p.get("trigger_value"),
                      "handle_dept": p.get("handle_dept")},
            "leg": leg, "leg_found": bool(leg)}
'''


def _workflow_i(base_url: str, ids: dict) -> dict:
    """start(alert_id) → http拉告警 → code聚合航段+派生属性 → agent深度研判 → end。"""
    prompt = (
        "你是航空公司运行控制中心的资深值班经理，请对下面这条运行告警做深度研判。\n"
        "告警信息：{{code_gather.alert}}\n"
        "关联航段及其派生属性（延误等级/预计延误/衔接风险等）：{{code_gather.leg}}\n\n"
        "请输出：1) 告警是否成立、是否可能误报及依据；2) 结合航段运行数据的影响面"
        "（旅客数/后续航班衔接/机组连飞限制）；3) 具体处置动作（按牵头部门分工）；"
        "4) 解除条件。最后以 JSON 输出：confirm（成立/疑似误报）、proposal（处置动作清单）。"
    )
    nodes = [
        {"id": "start", "type": "start", "title": "开始",
         "config": {"inputs": [{"name": "alert_id", "label": "告警实体ID",
                                "type": "string", "required": True}]}},
        {"id": "http_alert", "type": "http", "title": "拉取告警实体",
         "config": {"method": "GET", "url": base_url + "/api/entities/{{start.alert_id}}",
                    "timeout_seconds": 60}},
        {"id": "code_gather", "type": "code", "title": "聚合研判上下文",
         "config": {"code_text": CODE_GATHER,
                    "params": {"base_url": base_url,
                               "leg_ontology_id": ids["leg_ontology_id"]},
                    "timeout_seconds": 120,
                    "structured_outputs": [
                        {"name": "alert", "type": "object", "description": "告警摘要"},
                        {"name": "leg", "type": "object", "description": "航段及派生属性"},
                        {"name": "leg_found", "type": "boolean"}]}},
        {"id": "agent_deep", "type": "agent", "title": "智能体深度研判",
         "config": {"query_template": prompt,
                    "structured_outputs": [
                        {"name": "confirm", "type": "string", "description": "成立/疑似误报"},
                        {"name": "proposal", "type": "array", "description": "处置动作清单"}],
                    "fail_on_error": False, "timeout_seconds": 180}},
        {"id": "end", "type": "end", "title": "结束",
         "config": {"outputs": [
             {"name": "advice", "value": "{{agent_deep.answer}}"}]}},
    ]
    edges = [
        {"source": "start", "target": "http_alert"},
        {"source": "http_alert", "target": "code_gather"},
        {"source": "code_gather", "target": "agent_deep"},
        {"source": "agent_deep", "target": "end"},
    ]
    return _finalize_graph({"nodes": nodes, "edges": edges})


# ══════════════════════════ 主流程 ══════════════════════════

async def _locate(db: AsyncSession) -> tuple[str, str]:
    """定位类别与航段本体，返回 (category_id, leg_ontology_id)。"""
    cat = (await db.execute(
        select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
    )).scalar_one_or_none()
    if not cat:
        raise SystemExit(f"未找到类别「{CATEGORY_NAME}」，请先运行 seed_flight_ops_ontology.py")
    from models import Ontology
    leg = (await db.execute(
        select(Ontology).where(Ontology.category_id == cat.id,
                               Ontology.name == LEG_ONTOLOGY_NAME)
    )).scalar_one_or_none()
    if not leg:
        raise SystemExit(f"类别下未找到本体「{LEG_ONTOLOGY_NAME}」，请先运行 seed_flight_ops_ontology.py")
    return cat.id, leg.id


async def _pick_sample_entity(db: AsyncSession, ontology_id: str) -> dict | None:
    """挑演示价值最高的航段实体：优先今天且预计延误的，其次任一有 sobt/etd 的。"""
    rows = (await db.execute(
        select(Entity).where(Entity.ontology_id == ontology_id).limit(2000)
    )).scalars().all()
    today = datetime.now().strftime("%Y-%m-%d")
    best: tuple[int, dict | None] = (99, None)
    for ent in rows:
        props = json.loads(ent.properties or "{}") or {}
        if not props.get("sobt"):
            continue
        item = {"id": ent.id, "name": ent.name, "leg_no": props.get("leg_no", "")}
        is_today = str(props.get("flight_date") or "") == today
        has_delay = bool(props.get("etd")) and props.get("etd") != props.get("sobt")
        rank = 0 if (is_today and has_delay) else 1 if is_today else 2 if props.get("etd") else 3
        if rank < best[0]:
            best = (rank, item)
        if rank == 0:
            break
    return best[1]


async def _clear(db: AsyncSession, cat_id: str, leg_ontology_id: str) -> None:
    """按名称/编码删除本脚本生成的数据（计划 → 工作流 → 派生属性 → 函数/服务）。"""
    func_codes = [f["code"] for f in FUNCTIONS] + RULE_FUNC_CODES
    prop_codes = [p["code"] for p in DERIVED_PROPS]

    wf_ids = [row for row in (await db.execute(
        select(Workflow.id).where(Workflow.name.in_(
            [WF_A_NAME, WF_B_NAME, WF_G_NAME, WF_H_NAME, WF_I_NAME]))
    )).scalars().all()]
    if wf_ids:
        await db.execute(delete(Schedule).where(Schedule.workflow_id.in_(wf_ids)))
        await db.execute(delete(WorkflowRun).where(WorkflowRun.workflow_id.in_(wf_ids)))
        await db.execute(delete(Workflow).where(Workflow.id.in_(wf_ids)))

    await db.execute(delete(OntologyDerivedProperty).where(
        OntologyDerivedProperty.ontology_id == leg_ontology_id,
        OntologyDerivedProperty.code.in_(prop_codes)))
    await db.execute(delete(OntologyFunction).where(
        OntologyFunction.category_id == cat_id,
        OntologyFunction.code.in_(func_codes)))
    await db.execute(delete(OntologyService).where(
        OntologyService.owner_type == "ontology",
        OntologyService.ontology_id == leg_ontology_id,
        OntologyService.code.in_(ACTION_CODES)))
    # 规则实体的评估服务（owner_type=entity，挂「告警规则」本体）
    from models import Ontology
    rule_ont = (await db.execute(
        select(Ontology).where(Ontology.category_id == cat_id,
                               Ontology.name == RULE_ONTOLOGY_NAME)
    )).scalar_one_or_none()
    if rule_ont:
        await db.execute(delete(OntologyService).where(
            OntologyService.owner_type == "entity",
            OntologyService.ontology_id == rule_ont.id,
            OntologyService.code.in_(RULE_FUNC_CODES)))
    await db.commit()


async def _seed(base_url: str, force: bool) -> None:
    await init_db()
    async with async_session() as db:
        cat_id, leg_id = await _locate(db)

        existed = (await db.execute(
            select(OntologyFunction.id).where(
                OntologyFunction.category_id == cat_id,
                OntologyFunction.code.in_([f["code"] for f in FUNCTIONS] + RULE_FUNC_CODES))
        )).first() or (await db.execute(
            select(Workflow.id).where(Workflow.name.in_(
                [WF_A_NAME, WF_B_NAME, WF_G_NAME, WF_H_NAME, WF_I_NAME]))
        )).first()
        if existed and not force:
            print("运行时层数据已存在。如需重建请使用 --force 参数。")
            return
        if force:
            print("强制重建：清理函数/派生属性/工作流/定时计划...")
            await _clear(db, cat_id, leg_id)

        # 1. 函数
        func_id_by_code: dict[str, str] = {}
        for fdef in FUNCTIONS:
            fn = OntologyFunction(
                category_id=cat_id, ontology_id=leg_id,
                name=fdef["name"], code=fdef["code"],
                description=fdef["description"],
                params_schema=json.dumps(fdef["params_schema"], ensure_ascii=False),
                return_schema=fdef["return_schema"],
                code_text=fdef["code_text"],
                is_deterministic=0 if fdef["code"] == "leg_phase" else 1,
                sort_order=fdef["sort_order"],
            )
            db.add(fn)
            await db.flush()
            func_id_by_code[fdef["code"]] = fn.id
        print(f"已创建 {len(FUNCTIONS)} 个本体函数：{', '.join(f['code'] for f in FUNCTIONS)}")

        # 1.5 动作（编排模式，组合上面的函数，不写业务代码）
        dup_ids = [row for row in (await db.execute(
            select(OntologyService.id).where(
                OntologyService.owner_type == "ontology",
                OntologyService.ontology_id == leg_id,
                OntologyService.code.in_(ACTION_CODES))
        )).scalars().all()]
        if dup_ids:
            await db.execute(delete(OntologyService).where(OntologyService.id.in_(dup_ids)))
            await db.flush()
        for adef in build_actions(func_id_by_code):
            db.add(OntologyService(
                owner_type="ontology", ontology_id=leg_id, entity_id=None,
                name=adef["name"], code=adef["code"], description=adef["description"],
                params_schema=json.dumps(adef["params"], ensure_ascii=False),
                code_text="", language="python", timeout_seconds=30,
                is_enabled=1, sort_order=adef["sort_order"],
                execution_mode="flow",
                flow=json.dumps(adef["flow"], ensure_ascii=False),
            ))
        await db.flush()
        print(f"已创建 {len(ACTION_CODES)} 个编排动作：{', '.join(ACTION_CODES)}")

        # 1.7 告警规则函数 + 规则实体评估服务（规则资产化：阈值在实体属性，逻辑在函数）
        from models import Ontology
        rule_ont = (await db.execute(
            select(Ontology).where(Ontology.category_id == cat_id,
                                   Ontology.name == RULE_ONTOLOGY_NAME)
        )).scalar_one_or_none()
        if not rule_ont:
            raise SystemExit("类别下未找到本体「告警规则」，请先重建定义层与实体层种子"
                             "（seed_flight_ops_ontology.py / seed_flight_ops_entities.py）")
        rule_fn_ids: dict[str, str] = {}
        rule_return_schema = '{"alerts": "array", "alert_count": "number", "checked_count": "number"}'
        for rdef in RULE_FUNCTIONS:
            fn = OntologyFunction(
                category_id=cat_id, ontology_id=rule_ont.id,
                name=rdef["name"], code=rdef["code"],
                description=rdef["description"],
                params_schema=json.dumps(rdef["params_schema"], ensure_ascii=False),
                return_schema=rule_return_schema,
                code_text=rdef["code_text"],
                is_deterministic=0,
                sort_order=rdef["sort_order"],
            )
            db.add(fn)
            await db.flush()
            rule_fn_ids[rdef["code"]] = fn.id
        print(f"已创建 {len(RULE_FUNCTIONS)} 个告警规则函数：{', '.join(RULE_FUNC_CODES)}")

        rule_ents = (await db.execute(
            select(Entity).where(Entity.ontology_id == rule_ont.id)
        )).scalars().all()
        ent_by_code: dict[str, Entity] = {}
        for ent in rule_ents:
            props = json.loads(ent.properties or "{}") or {}
            if props.get("rule_code"):
                ent_by_code[str(props["rule_code"])] = ent
        rule_bindings: dict[str, dict] = {}
        for sdef in build_rule_services(rule_fn_ids):
            ent = ent_by_code.get(sdef["rule_code"])
            if not ent:
                raise SystemExit(f"未找到规则编码为「{sdef['rule_code']}」的告警规则实体，"
                                 "请先重建实体种子（seed_flight_ops_entities.py）")
            svc = OntologyService(
                owner_type="entity", ontology_id=rule_ont.id, entity_id=ent.id,
                name=sdef["name"], code=sdef["code"], description=sdef["description"],
                params_schema=json.dumps(sdef["params"], ensure_ascii=False),
                code_text="", language="python", timeout_seconds=30,
                is_enabled=1, sort_order=200,
                execution_mode="flow",
                flow=json.dumps(sdef["flow"], ensure_ascii=False),
            )
            db.add(svc)
            await db.flush()
            rule_bindings[sdef["rule_code"]] = {"service_id": svc.id, "entity_id": ent.id}
        print(f"已创建 {len(rule_bindings)} 个规则评估服务（实体级，引用规则函数）")

        # 2. 派生属性（读时模式）
        for pdef in DERIVED_PROPS:
            db.add(OntologyDerivedProperty(
                ontology_id=leg_id,
                name=pdef["name"], code=pdef["code"], data_type=pdef["data_type"],
                source_kind="function", function_id=func_id_by_code[pdef["function_code"]],
                params=json.dumps(pdef["params"], ensure_ascii=False),
                materialize_mode="virtual",
                sort_order=pdef["sort_order"],
            ))
        await db.flush()
        print(f"已创建 {len(DERIVED_PROPS)} 个派生属性（virtual 读时模式）")

        # 3. 工作流（id 动态填入 HTTP 节点）
        props = (await db.execute(
            select(OntologyDerivedProperty).where(
                OntologyDerivedProperty.ontology_id == leg_id,
                OntologyDerivedProperty.code.in_([p["code"] for p in DERIVED_PROPS]))
        )).scalars().all()
        props = sorted(props, key=lambda p: p.sort_order)
        prop_payloads = [{"id": p.id, "name": p.name, "code": p.code} for p in props]

        sample = await _pick_sample_entity(db, leg_id)

        # 3.5 附加定位：运行告警本体 / 触发告警关系定义 / 知识库（注入代码节点 params）
        from models import Ontology, OntologyRelation
        alert_ont = (await db.execute(
            select(Ontology).where(Ontology.category_id == cat_id,
                                   Ontology.name == "运行告警")
        )).scalar_one_or_none()
        if not alert_ont:
            raise SystemExit("类别下未找到本体「运行告警」，请先重建实体种子（seed_flight_ops_entities.py）")
        alert_rel = (await db.execute(
            select(OntologyRelation).where(OntologyRelation.category_id == cat_id,
                                           OntologyRelation.name == "触发告警")
        )).scalar_one_or_none()
        kb_id = (await db.execute(
            select(Entity.kb_id).where(Entity.ontology_id == leg_id).limit(1)
        )).scalar_one_or_none()
        ids = {"leg_ontology_id": leg_id,
               "alert_ontology_id": alert_ont.id,
               "kb_id": kb_id or "",
               "alert_rel_def_id": alert_rel.id if alert_rel else "",
               "rule_bindings": rule_bindings}
        scan_date = datetime.now().strftime("%Y%m%d")

        wf_a = Workflow(
            name=WF_A_NAME,
            description="把航段 4 个读时派生属性（飞行时长/起飞延误/到达延误/运行阶段）"
                        "批量物化写入实体属性，供列表与报表直读。由定时计划每 30 分钟触发。",
            definition=json.dumps(_workflow_a(base_url, prop_payloads), ensure_ascii=False),
        )
        wf_b = Workflow(
            name=WF_B_NAME,
            description="输入 entity_id，实时计算该航段全部派生属性，"
                        "并由大模型生成一段面向值班人员的运行简报。",
            definition=json.dumps(_workflow_b(base_url, sample["id"] if sample else ""),
                                  ensure_ascii=False),
        )
        wf_g = Workflow(
            name=WF_G_NAME,
            description="论文第5章分级告警：HTTP 拉当日航段 → 6 个规则评估服务并行"
                        "（规则资产化：告警规则实体+本体函数，阈值改实体属性即可生效）"
                        "→ 汇总 → 条件分流 → 智能体研判 → 高风险人工审批 → 批量入库挂关系。",
            definition=json.dumps(_workflow_g(base_url, ids, scan_date),
                                  ensure_ascii=False),
        )
        wf_h = Workflow(
            name=WF_H_NAME,
            description="规则7 持续未解除升级：拉取未解除告警 → 规则评估服务"
                        "（阈值取自规则实体属性）→ 写回实体后由智能体生成升级通报。",
            definition=json.dumps(_workflow_h(base_url, ids), ensure_ascii=False),
        )
        wf_i = Workflow(
            name=WF_I_NAME,
            description="单告警深度研判：拉告警实体+关联航段派生属性，"
                        "智能体给出误报判断/影响面/处置动作/解除条件。手动输入 alert_id 运行。",
            definition=json.dumps(_workflow_i(base_url, ids), ensure_ascii=False),
        )
        db.add_all([wf_a, wf_b, wf_g, wf_h, wf_i])
        await db.flush()
        print(f"已创建工作流：{WF_A_NAME}（id={wf_a.id}）、{WF_B_NAME}（id={wf_b.id}）、"
              f"{WF_G_NAME}（id={wf_g.id}）、{WF_H_NAME}（id={wf_h.id}）、"
              f"{WF_I_NAME}（id={wf_i.id}）")

        # 4. 定时计划（后端启动时由调度引擎加载；运行中生成需重启后端生效）
        db.add(Schedule(
            name=SCHEDULE_NAME,
            description="每 30 分钟运行「航段派生属性定时物化」工作流，"
                        "批量物化航段派生属性到实体属性。",
            workflow_id=wf_a.id,
            trigger="interval",
            trigger_config=json.dumps({"every": 30, "unit": "minutes"}),
            input_params=json.dumps({}),
            enabled=1,
            muted=0, alert_on_failure=1, max_failures_alert=3,
        ))
        db.add(Schedule(
            name=SCHEDULE_G_NAME,
            description="每 15 分钟运行「航班运行风险告警扫描」（规则引擎→条件分流→"
                        "智能体研判→高风险人工审批→入库）。",
            workflow_id=wf_g.id,
            trigger="interval",
            trigger_config=json.dumps({"every": 15, "unit": "minutes"}),
            input_params=json.dumps({}),
            enabled=1,
            muted=0, alert_on_failure=1, max_failures_alert=3,
        ))
        db.add(Schedule(
            name=SCHEDULE_H_NAME,
            description="每 15 分钟运行「告警升级巡检」（与扫描错峰触发），"
                        "持续未解除的告警自动升级并生成通报。",
            workflow_id=wf_h.id,
            trigger="interval",
            trigger_config=json.dumps({"every": 15, "unit": "minutes"}),
            input_params=json.dumps({}),
            enabled=1,
            muted=0, alert_on_failure=1, max_failures_alert=3,
        ))
        await db.commit()
        print(f"已创建定时计划：{SCHEDULE_NAME}、{SCHEDULE_G_NAME}、{SCHEDULE_H_NAME}")

        if sample:
            wf_b_def = json.loads(wf_b.definition)
            for node in wf_b_def["nodes"]:
                if node["id"] == "start":
                    node["config"]["defaults"] = {"entity_id": sample["id"]}
            wf_b.definition = json.dumps(wf_b_def, ensure_ascii=False)
            await db.commit()
            print(f"工作流 B 默认入参 entity_id={sample['id']}"
                  f"（航段 {sample.get('leg_no') or sample['name']}），运行时可修改")

        print(f"\n[完成] 运行时层生成完毕（后端 API 基地址：{base_url}）")
        print("验证建议：")
        print(f"  1. 实时计算：GET {base_url}/api/entities/<entity_id>/derived-properties?refresh=true")
        print(f"  2. 手动运行工作流 B：前端「工作流」页 → 单航段运行简报 → 运行")
        print("  3. 定时计划：重启后端后生效；也可在「定时计划」页点「立即执行」验证工作流 A")
        print(f"  4. 告警链路：手动运行「{WF_G_NAME}」→ 审批挂起后在待办里通过/驳回 →"
              f" 再运行「{WF_H_NAME}」看升级（低风险待处理超 20 分钟即触发）")


# ══════════════════════════ 验证 ══════════════════════════

async def _verify() -> None:
    """挑一个航段实体，逐个实时计算派生属性并打印（不依赖后端运行）。"""
    await init_db()
    async with async_session() as db:
        cat_id, leg_id = await _locate(db)
        sample = await _pick_sample_entity(db, leg_id)
        if not sample:
            raise SystemExit("未找到带 sobt/etd 的航段实体，请先运行 seed_flight_ops_entities.py")
        props = json.loads((await db.get(Entity, sample["id"])).properties or "{}")
        dps = (await db.execute(
            select(OntologyDerivedProperty).where(
                OntologyDerivedProperty.ontology_id == leg_id,
                OntologyDerivedProperty.is_enabled == 1)
        )).scalars().all()
        if not dps:
            raise SystemExit("未找到派生属性，请先用 --force 重新生成")

        print(f"验证实体：{sample['name']}（{sample['leg_no']}）")
        for key in ("sobt", "sibt", "etd", "eta", "leg_status"):
            print(f"  {key} = {props.get(key, '')}")
        print("-" * 56)
        entity = await db.get(Entity, sample["id"])
        for dp in sorted(dps, key=lambda d: d.sort_order):
            res = await DerivedPropertyService.resolve_value(db, dp, entity)
            mark = "OK " if res.get("ok") else "ERR"
            print(f"  [{mark}] {dp.name:<8} ({dp.code}) = {res.get('value')}"
                  + (f"  错误：{res.get('error')}" if not res.get("ok") else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed 民航航班运行监控运行时层")
    parser.add_argument("--force", action="store_true", help="删除同名函数/派生属性/工作流/计划后重建")
    parser.add_argument("--verify", action="store_true", help="挑一个航段实体试算全部派生属性")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"工作流 HTTP 节点访问的后端基地址（默认 {DEFAULT_BASE_URL}）")
    args = parser.parse_args()
    if args.verify:
        asyncio.run(_verify())
    else:
        asyncio.run(_seed(args.base_url.rstrip("/"), args.force))


if __name__ == "__main__":
    main()
