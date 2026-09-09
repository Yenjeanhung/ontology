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
                "url": f"{base_url}/api/derived-properties/{prop['id']}/materialize",
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
    """按名称/编码删除本脚本生成的数据（计划 → 工作流 → 派生属性 → 函数）。"""
    func_codes = [f["code"] for f in FUNCTIONS]
    prop_codes = [p["code"] for p in DERIVED_PROPS]

    wf_ids = [row for row in (await db.execute(
        select(Workflow.id).where(Workflow.name.in_([WF_A_NAME, WF_B_NAME]))
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
    await db.commit()


async def _seed(base_url: str, force: bool) -> None:
    await init_db()
    async with async_session() as db:
        cat_id, leg_id = await _locate(db)

        existed = (await db.execute(
            select(OntologyFunction.id).where(
                OntologyFunction.category_id == cat_id,
                OntologyFunction.code.in_([f["code"] for f in FUNCTIONS]))
        )).first() or (await db.execute(
            select(Workflow.id).where(Workflow.name.in_([WF_A_NAME, WF_B_NAME]))
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
        db.add_all([wf_a, wf_b])
        await db.flush()
        print(f"已创建工作流：{WF_A_NAME}（id={wf_a.id}）、{WF_B_NAME}（id={wf_b.id}）")

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
        await db.commit()
        print(f"已创建定时计划：{SCHEDULE_NAME}")

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
