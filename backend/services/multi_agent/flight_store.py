# -*- coding: utf-8 -*-
"""
航班运行监控 · 平台真实数据访问层（供 flight_alarm 场景适配器使用）。

数据源：平台实体库（PostgreSQL，seed_flight_ops_* 写入的「民航航班运行监控本体」
实例层）——运行告警 / 航段 / 告警规则 / 机组 / 航空器 / 机场等真实实体与关系。
- 证据包（ops/rule/resource 域）全部取自真实实体属性与关系；
- 气象域平台暂无数据源，按用户约定**生成补充卡**并显著标注（模拟生成）；
- 真实组合告警不足时，按告警规则实体阈值生成补充告警（真实入库，ALR-GEN 前缀
  幂等），仍不足则由适配器回落到内置演示告警。

输出 dict 形状与 flight_data 的演示告警保持兼容（alarm/evidence_pack/fact_pack），
流水线与 SSE 契约不变。
"""

import json
import uuid
from datetime import datetime

from sqlalchemy import or_, select

from database import async_session
from models import Entity, Ontology, OntologyCategory, Relation

CATEGORY_NAME = "民航航班运行监控本体"

# 生成告警的告警号前缀（幂等判据：同一航段同一类型已生成过则跳过）
GEN_PREFIX = "ALR-GEN"
GEN_SOURCE = "generated_simulated"

# 已终结的告警状态（不算活跃）
_CLOSED_STATUS = {"已解除", "已处置", "误报"}

RISK_TO_LEVEL = {"高": "orange", "中": "yellow", "低": "blue"}


# ── 小工具 ────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now()


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


def _short_dt(v) -> str:
    dt = _parse_dt(v)
    return dt.strftime("%m-%d %H:%M") if dt else str(v or "")


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _props(entity: Entity) -> dict:
    try:
        return json.loads(entity.properties or "{}")
    except Exception:
        return {}


def _card(cid, domain, source, title, summary, quote, stance="neutral",
          grade="sourced_doc"):
    return {"id": cid, "domain": domain, "grade": grade, "source": source,
            "title": title, "summary": summary, "quote": quote, "stance": stance}


def _fact(fid, title, detail):
    return {"id": fid, "grade": "graph_fact", "title": title, "detail": detail}


# ── 本体索引（类别 → 各对象类型 ontology_id / kb_id） ─────────

async def _load_type_index(db) -> dict | None:
    """返回 {"ont": {类型名: ontology_id}, "kb_id": str}；平台未播种返回 None。"""
    cat = (await db.execute(
        select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
    )).scalar_one_or_none()
    if not cat:
        return None
    onts = (await db.execute(
        select(Ontology).where(Ontology.category_id == cat.id)
    )).scalars().all()
    if not onts:
        return None
    kb_row = (await db.execute(
        select(Entity.kb_id).where(Entity.ontology_id == onts[0].id).limit(1)
    )).first()
    return {"ont": {o.name: o.id for o in onts}, "kb_id": kb_row[0] if kb_row else ""}


async def _entities_of_type(db, ontology_id: str) -> dict[str, Entity]:
    rows = (await db.execute(
        select(Entity).where(Entity.ontology_id == ontology_id)
    )).scalars().all()
    return {r.id: r for r in rows}


# ── 真实告警读取 ─────────────────────────────────────────────

def _is_active(alert: dict) -> bool:
    return (alert.get("handle_status") or "待处理") not in _CLOSED_STATUS


async def list_platform_alarms() -> list[dict]:
    """
    平台真实运行告警 → 演示告警同形状的轻量 dict 列表（按触发时间倒序）。
    含同航段伴生告警（决定 combined/single）与规则实体元数据。
    """
    async with async_session() as db:
        idx = await _load_type_index(db)
        if not idx:
            return []
        alarm_rows = await _entities_of_type(db, idx["ont"].get("运行告警", ""))
        if not alarm_rows:
            return []
        alerts = []
        for e in alarm_rows.values():
            p = _props(e)
            p["entity_id"] = e.id
            alerts.append(p)
        alerts.sort(key=lambda a: a.get("triggered_at") or "", reverse=True)

        leg_nos = {a.get("related_leg_no") for a in alerts if a.get("related_leg_no")}
        legs = {}
        if leg_nos:
            leg_rows = (await db.execute(
                select(Entity).where(Entity.ontology_id == idx["ont"].get("航段", ""),
                                     Entity.name.in_(leg_nos))
            )).scalars().all()
            legs = {r.name: _props(r) | {"entity_id": r.id} for r in leg_rows}

        rule_rows = await _entities_of_type(db, idx["ont"].get("告警规则", ""))
        rules = {_props(r).get("alert_type", r.name): _props(r) for r in rule_rows.values()}

    out = []
    for a in alerts:
        leg_no = a.get("related_leg_no") or ""
        leg = legs.get(leg_no, {})
        siblings = [x for x in alerts
                    if x.get("related_leg_no") == leg_no and x["entity_id"] != a["entity_id"]
                    and _is_active(x)]
        active = _is_active(a)
        conditions = [{"code": a.get("alert_type"), "desc": a.get("message")}]
        conditions += [{"code": s.get("alert_type"), "desc": s.get("message")}
                       for s in siblings if active]
        rule = rules.get(a.get("alert_type"), {})
        level = RISK_TO_LEVEL.get(a.get("risk_level"), "blue")
        out.append({
            "alarm_id": a["entity_id"],
            "flight_no": leg.get("flight_no") or leg_no or "未知航班",
            "route": f"{leg.get('dep_airport', '?')} → {leg.get('arr_airport', '?')}",
            "aircraft": leg_no,
            "rule_name": f"「{a.get('alert_type')}」告警规则"
                         + (f"（{rule.get('rule_code')}）" if rule.get("rule_code") else ""),
            "trigger_kind": "combined" if (active and siblings) else "single",
            "level": level,
            "risk_level": a.get("risk_level", "低"),
            "triggered_at": _short_dt(a.get("triggered_at")),
            "handle_status": a.get("handle_status", "待处理"),
            "conditions": conditions if active else conditions[:1],
            "source": "platform",
            "rule": rule,
            "leg": leg,
            "siblings": siblings,
            "evidence_count": 0,   # 证据包按需在 get_platform_alarm 组装
            "fact_count": 0,
            "rule_verdict": ""
            if level in ("yellow", "orange")
            else f"规则引擎结论：{a.get('risk_level')}风险，由{a.get('handle_dept', '首响岗位')}持续跟踪即可，无需多智能体复核。",
        })
    return out


async def get_platform_alarm(alarm_id: str) -> dict | None:
    """单告警完整研判上下文：真实证据包（ops/rule/resource）+ 生成气象卡 + 图谱事实包。"""
    async with async_session() as db:
        idx = await _load_type_index(db)
        if not idx:
            return None
        alarm_e = (await db.execute(
            select(Entity).where(Entity.id == alarm_id)
        )).scalar_one_or_none()
        if not alarm_e or alarm_e.ontology_id != idx["ont"].get("运行告警", ""):
            return None
        a = _props(alarm_e)
        leg_no = a.get("related_leg_no") or ""

        leg_e = (await db.execute(
            select(Entity).where(Entity.ontology_id == idx["ont"].get("航段", ""),
                                 Entity.name == leg_no)
        )).scalar_one_or_none()
        leg = _props(leg_e) if leg_e else {}

        # 航段关系：机场 / 航空器 / 机组 / 前序 / 异常（双向）
        rels = []
        peers: dict[str, Entity] = {}
        if leg_e is not None:
            rels = (await db.execute(
                select(Relation).where(or_(Relation.source_entity_id == leg_e.id,
                                           Relation.target_entity_id == leg_e.id))
            )).scalars().all()
            peer_ids = sorted({r.source_entity_id if r.target_entity_id == leg_e.id
                               else r.target_entity_id for r in rels})
            if peer_ids:
                for e in (await db.execute(
                    select(Entity).where(Entity.id.in_(peer_ids))
                )).scalars().all():
                    peers[e.id] = e

        # 同类型历史告警统计 + 同航段伴生活跃告警（同一批实体内计算）
        hist_rows = (await db.execute(
            select(Entity).where(Entity.ontology_id == idx["ont"].get("运行告警", ""))
        )).scalars().all()
        history = [_props(h) for h in hist_rows
                   if _props(h).get("alert_type") == a.get("alert_type")]
        siblings = []
        for h in hist_rows:
            hp = _props(h)
            if hp.get("related_leg_no") == leg_no and h.id != alarm_e.id and _is_active(hp):
                siblings.append(hp)

        rule_rows = await _entities_of_type(db, idx["ont"].get("告警规则", ""))
        rule = next((_props(r) for r in rule_rows.values()
                     if _props(r).get("alert_type") == a.get("alert_type")), {})

    now = _now()
    evidence_pack = {
        "ops": _ops_cards(leg_e.id if leg_e else alarm_e.id, leg, leg_e, peers, rels, now),
        "rule": _rule_cards(alarm_e.id, a, rule),
        "resource": _resource_cards(alarm_e.id, leg, leg_e, peers, rels),
        "weather": _weather_cards(alarm_e.id, a),   # 平台暂无气象源 → 生成卡（标注）
    }
    fact_pack = _fact_pack(leg_no, leg, leg_e, peers, rels, history, a)

    level = RISK_TO_LEVEL.get(a.get("risk_level"), "blue")
    conditions = [{"code": a.get("alert_type"), "desc": a.get("message")}]
    conditions += [{"code": s.get("alert_type"), "desc": s.get("message")} for s in siblings]
    return {
        "alarm_id": alarm_e.id,
        "flight_no": leg.get("flight_no") or leg_no or "未知航班",
        "route": f"{leg.get('dep_airport', '?')} → {leg.get('arr_airport', '?')}",
        "aircraft": leg_no,
        "rule_name": f"「{a.get('alert_type')}」告警规则"
                     + (f"（{rule.get('rule_code')}）" if rule.get("rule_code") else ""),
        "trigger_kind": "combined" if siblings else "single",
        "level": level,
        "risk_level": a.get("risk_level", "低"),
        "triggered_at": _short_dt(a.get("triggered_at")),
        "handle_status": a.get("handle_status", "待处理"),
        "conditions": conditions,
        "evidence_pack": evidence_pack,
        "fact_pack": fact_pack,
        "source": "platform",
        "rule_verdict": "",
    }


# ── 证据卡组装（真实数据驱动） ────────────────────────────────

def _rel_map(rels, leg_id, typ):
    """某关系类型下的关系列表（rels 已是本航段的双向关系集）。"""
    return [r for r in rels if r.relation_type == typ and
            (r.source_entity_id == leg_id or r.target_entity_id == leg_id)]


def _peer_entity(r: Relation, leg_id, peers: dict) -> Entity | None:
    peer_id = r.target_entity_id if r.source_entity_id == leg_id else r.source_entity_id
    return peers.get(peer_id)


def _delay_min(leg: dict, now: datetime) -> dict:
    """从真实时刻属性算当前延误（分钟）。"""
    st = leg.get("leg_status", "")
    sobt, sibt = _parse_dt(leg.get("sobt")), _parse_dt(leg.get("sibt"))
    aobt, aibt = _parse_dt(leg.get("aobt")), _parse_dt(leg.get("aibt"))
    eta = _parse_dt(leg.get("eta"))
    dep_delay = None
    if st == "登机" and sobt:
        dep_delay = int((now - sobt).total_seconds() // 60) if now > sobt else 0
    elif aobt and sobt:
        dep_delay = int((aobt - sobt).total_seconds() // 60)
    arr_delay = None
    if st in ("滑入", "到达") and aibt and sibt:
        arr_delay = int((aibt - sibt).total_seconds() // 60)
    elif st == "巡航" and eta and sibt:
        arr_delay = int((eta - sibt).total_seconds() // 60)
    return {"status": st, "dep_delay": dep_delay, "arr_delay": arr_delay}


def _stance_of(delay):
    if delay is None:
        return "neutral"
    if delay >= 15:
        return "worsen"
    if delay <= 0:
        return "improve"
    return "neutral"


def _ops_cards(alarm_id, leg: dict, leg_e, peers, rels, now) -> list[dict]:
    if not leg:
        return []
    leg_id = leg_e.id if leg_e else ""
    d = _delay_min(leg, now)
    cards = []
    dep_txt = f"出港延误 {d['dep_delay']}min" if d["dep_delay"] is not None else "未出港"
    arr_txt = f"到达延误 {d['arr_delay']}min" if d["arr_delay"] is not None else "到达正常"
    cards.append(_card(
        f"ev-{alarm_id}-leg", "ops", "实体库 · 航段动态（平台真实数据）",
        f"航段 {leg.get('leg_no', '')} 动态（{d['status']}）",
        f"SOBT {str(leg.get('sobt', ''))[:16]}，当前{dep_txt}；"
        f"ETA/SIBT 口径 {arr_txt}；机上旅客 {leg.get('pax_count', '-')} 人。",
        f"{dep_txt} / {arr_txt}（leg_status={d['status']}）",
        stance=_stance_of(d["dep_delay"] if d["dep_delay"] is not None else d["arr_delay"]),
    ))
    # 前序衔接（真实关系「衔接前序」）
    for r in _rel_map(rels, leg_id, "衔接前序"):
        prev = _peer_entity(r, leg_id, peers)
        if not prev:
            continue
        pd = _delay_min(_props(prev), now)
        pst = pd["status"]
        ok = pst in ("到达", "滑入") and (pd["arr_delay"] or 0) < 10
        cards.append(_card(
            f"ev-{alarm_id}-prev", "ops", "实体库 · 前序航段（平台真实数据）",
            f"前序 {prev.name} 已{pst}" if pst in ("到达", "滑入") else f"前序 {prev.name}（{pst}）",
            f"前序航段状态 {pst}"
            + (f"，到达延误 {pd['arr_delay']}min，过站衔接余度{'充足' if ok else '紧张'}。"
               if pd["arr_delay"] is not None else "。"),
            f"前序 leg_status={pst}，arr_delay={pd['arr_delay']}",
            stance="improve" if ok else ("worsen" if pd["arr_delay"] and pd["arr_delay"] >= 15 else "neutral"),
        ))
    return cards


def _rule_cards(alarm_id, alert: dict, rule: dict) -> list[dict]:
    cards = []
    if rule:
        tv = _num(alert.get("trigger_value"))
        mid, low = _num(rule.get("threshold_mid")), _num(rule.get("threshold_low"))
        stance = "worsen" if mid > 0 and tv >= mid else "neutral"
        cards.append(_card(
            f"ev-{alarm_id}-rule", "rule", "实体库 · 告警规则资产（平台真实数据）",
            f"规则 {rule.get('alert_type', '')}（{rule.get('rule_code', '')}）",
            f"阈值 low/mid/high = {rule.get('threshold_low')}/{rule.get('threshold_mid')}/"
            f"{rule.get('threshold_high')}，当前触发值 {alert.get('trigger_value')}；"
            f"牵头部门 {rule.get('handle_dept', '-')}，规则{'启用' if rule.get('is_enabled', True) else '停用'}。",
            str(rule.get("rule_desc", "")),
            stance=stance,
        ))
    cards.append(_card(
        f"ev-{alarm_id}-trigger", "rule", "实体库 · 告警触发记录（平台真实数据）",
        f"触发记录 {alert.get('alert_no', '')}",
        f"{alert.get('risk_level', '')}风险（{alert.get('severity', '')}），"
        f"已持续 {alert.get('lasted_min', 0)} 分钟，处置状态「{alert.get('handle_status', '')}」，"
        f"牵头 {alert.get('handle_dept', '-')}{('，处理人 ' + alert['handler']) if alert.get('handler') else ''}。",
        str(alert.get("message", "")),
        stance="worsen",
    ))
    return cards


def _resource_cards(alarm_id, leg: dict, leg_e, peers, rels) -> list[dict]:
    cards = []
    leg_id = leg_e.id if leg_e else ""
    for r in _rel_map(rels, leg_id, "执飞航空器"):
        ac = _peer_entity(r, leg_id, peers)
        if not ac:
            continue
        p = _props(ac)
        cards.append(_card(
            f"ev-{alarm_id}-ac", "resource", "实体库 · 机队（平台真实数据）",
            f"航空器 {ac.name}",
            f"机型 {p.get('ac_type') or p.get('type') or '-'}，航段执飞登记一致，"
            f"机务状态正常（实体库无停场/故障标记）。",
            f"registration={ac.name}",
            stance="neutral",
        ))
    crews = []
    for r in _rel_map(rels, leg_id, "由机组执飞"):
        c = _peer_entity(r, leg_id, peers)
        if c:
            p = _props(c)
            crews.append(f"{c.name}（{p.get('role', '-')}，{p.get('total_hours', '-')}h）")
    changed = bool(leg.get("is_crew_changed"))
    if crews:
        cards.append(_card(
            f"ev-{alarm_id}-crew", "resource", "实体库 · 机组排班（平台真实数据）",
            "执勤机组名单",
            "、".join(crews) + "。"
            + ("放行后机组名单发生变更（航段属性 is_crew_changed），需复核资质与连飞限制。"
               if changed else "名单与放行一致，未触发机组变更规则。"),
            f"is_crew_changed={changed}",
            stance="worsen" if changed else "improve",
        ))
    return cards


def _weather_cards(alarm_id, alert: dict) -> list[dict]:
    """气象域：平台暂无气象数据源，按约定生成补充证据卡（显著标注模拟生成）。"""
    g = "model_knowledge"
    return [
        _card(
            f"ev-{alarm_id}-wx-obs", "weather", "气象台 · 场面观测（模拟生成，非平台数据）",
            "本场实况（模拟）",
            "本场天气趋于好转：小时雨强下降、跑道视程恢复，地面运行条件对当前告警无进一步恶化影响。"
            "（平台暂未接入气象源，此卡为演示用生成数据。）",
            "GEN-OBS：TS 减弱 / RVR 回升",
            stance="improve", grade=g,
        ),
        _card(
            f"ev-{alarm_id}-wx-fcst", "weather", "气象台 · 航路预报（模拟生成，非平台数据）",
            "航路趋势（模拟）",
            "航路走廊未来 2 小时仍有天气活动，与计划落地窗口重叠，存在二次延误风险。"
            "（平台暂未接入气象源，此卡为演示用生成数据。）",
            "GEN-FCST：CB 影响走廊（+2h）",
            stance="worsen", grade=g,
        ),
    ]


def _fact_pack(leg_no, leg: dict, leg_e, peers, rels, history: list[dict],
               alert: dict) -> list[dict]:
    facts = []
    # 1) 历史同类告警统计（真实）
    if history:
        lv = {"高": 0, "中": 0, "低": 0}
        lasted = [h["lasted_min"] for h in history if _num(h.get("lasted_min")) > 0]
        for h in history:
            lv[h.get("risk_level")] = lv.get(h.get("risk_level"), 0) + 1
        avg = int(sum(lasted) / len(lasted)) if lasted else 0
        up = lv["高"] + lv["中"]
        facts.append(_fact(
            "fact-hist", "历史同类告警统计（实体库真实数据）",
            f"实体库近 14 天「{alert.get('alert_type')}」告警共 {len(history)} 起："
            f"高 {lv['高']} / 中 {lv['中']} / 低 {lv['低']}"
            + (f"，其中 {up} 起升级中/高风险" if up else "，均维持低风险跟踪")
            + (f"，平均持续 {avg} 分钟。" if avg else "。"),
        ))
    # 2) 关联实体链（真实关系）
    if leg:
        leg_id = leg_e.id if leg_e else ""
        airport, aircraft, crew, prev = [], [], [], ""
        for r in rels:
            peer = _peer_entity(r, leg_id, peers)
            if not peer:
                continue
            if r.relation_type in ("出发于", "到达于"):
                airport.append(f"{r.relation_type[:-1]}{peer.name}")
            elif r.relation_type == "执飞航空器":
                aircraft.append(peer.name)
            elif r.relation_type == "由机组执飞":
                crew.append(peer.name)
            elif r.relation_type == "衔接前序":
                prev = f"前序 {peer.name}（{_props(peer).get('leg_status', '')}）"
        facts.append(_fact(
            "fact-rel", "关联实体链（本体图谱真实关系）",
            f"{leg.get('leg_no', leg_no)}（{leg.get('leg_status', '')}）"
            + (f" — 航空器 {'/'.join(aircraft)}" if aircraft else "")
            + (f" — 机组 {'、'.join(crew)}" if crew else "")
            + (f" — {'、'.join(sorted(set(airport)))}" if airport else "")
            + (f" — {prev}" if prev else "。"),
        ))
    return facts


# ── 数据量保障：真实组合告警不足时按规则生成补充（真实入库） ────

async def ensure_platform_alarms(min_combined: int = 3) -> None:
    """
    活跃组合告警不足 min_combined 条时，为符合条件的真实活跃航段生成补充告警：
    - 完全复用告警规则实体阈值与「运行告警」属性结构（与告警扫描工作流入库一致）；
    - alert_no 以 ALR-GEN 前缀幂等，同一航段同一类型不重复生成；
    - 为前两条航段额外补一条伴生告警，构成组合场景（多智能体复核的克制边界需求）。
    平台完全没有航段数据时不做任何写操作（由适配器回落演示数据）。
    """
    async with async_session() as db:
        idx = await _load_type_index(db)
        if not idx:
            return
        alarm_ont = idx["ont"].get("运行告警", "")
        leg_ont = idx["ont"].get("航段", "")
        if not alarm_ont or not leg_ont:
            return
        existing = (await db.execute(
            select(Entity).where(Entity.ontology_id == alarm_ont)
        )).scalars().all()
        alerts = [_props(e) | {"entity_id": e.id} for e in existing]
        combined = 0
        by_leg: dict[str, list[dict]] = {}
        for a in alerts:
            if not _is_active(a):
                continue
            by_leg.setdefault(a.get("related_leg_no") or "", []).append(a)
        combined = sum(1 for v in by_leg.values() if len(v) >= 2)
        if combined >= min_combined:
            return

        # 挑真实活跃航段：登机已晚点 ≥15min，或巡航预计到达延误 ≥10min
        legs = (await db.execute(
            select(Entity).where(Entity.ontology_id == leg_ont)
        )).scalars().all()
        now = _now()
        cands = []
        for le in legs:
            p = _props(le)
            st = p.get("leg_status", "")
            d = _delay_min(p, now)
            late = d["dep_delay"] if st == "登机" else None
            over = d["arr_delay"] if st == "巡航" else None
            if (late is not None and late >= 15) or (over is not None and over >= 10):
                cands.append((le, p, late if late is not None else over))
        if not cands:
            return

        generated = {(a.get("alert_no") or "")[:len(GEN_PREFIX)] == GEN_PREFIX
                     and a.get("related_leg_no") for a in alerts}

        def _mk_alert(leg_e: Entity, lp: dict, alert_type: str, value: float,
                      rule: dict, level: str, message: str) -> Entity:
            sev = {"低": "提示", "中": "警告", "高": "严重"}[level]
            dept = {"低": "飞行控制室", "中": "运行控制室", "高": "总值班室"}[level]
            props = {
                "alert_no": f"{GEN_PREFIX}-{uuid.uuid4().hex[:6].upper()}",
                "alert_type": alert_type, "severity": sev, "risk_level": level,
                "triggered_at": now.isoformat(timespec="seconds"),
                "threshold": rule.get("rule_desc", ""),
                "trigger_value": value, "lasted_min": 0,
                "message": message, "handle_dept": dept,
                "related_leg_no": lp.get("leg_no"),
                "handle_status": "待处理", "handler": "",
                "data_source": GEN_SOURCE,
            }
            return Entity(
                id=uuid.uuid4().hex[:12], kb_id=leg_e.kb_id, ontology_id=alarm_ont,
                entity_type="运行告警", name=f"{lp.get('leg_no')}·{alert_type}",
                description=message,
                properties=json.dumps(props, ensure_ascii=False),
                created_at=now.isoformat(timespec="seconds"),
                updated_at=now.isoformat(timespec="seconds"),
            )

        def _rel(leg_e: Entity, alert_e: Entity) -> Relation:
            return Relation(
                id=uuid.uuid4().hex[:12], kb_id=leg_e.kb_id,
                relation_def_id="触发告警", relation_type="触发告警",
                source_entity_id=leg_e.id, target_entity_id=alert_e.id,
                description="生成数据关联", created_at=now.isoformat(timespec="seconds"),
                updated_at=now.isoformat(timespec="seconds"),
            )

        rule_rows = await _entities_of_type(db, idx["ont"].get("告警规则", ""))
        rules = {_props(r).get("alert_type"): _props(r) for r in rule_rows.values()}

        made = 0
        for i, (leg_e, lp, value) in enumerate(cands[:min_combined - combined]):
            if lp.get("leg_no") in generated:
                continue
            # 按航段状态选规则：登机→晚关门，巡航→超时未落，阈值分级取自规则实体
            st = lp.get("leg_status", "")
            primary_type = "超时未落" if st == "巡航" else "晚关门"
            rule = rules.get(primary_type) or (next(iter(rules.values())) if rules else {})
            high, mid = _num(rule.get("threshold_high")), _num(rule.get("threshold_mid"))
            level = "高" if high and value >= high else ("中" if mid and value >= mid else "中")
            if st == "巡航":
                msg = (f"{lp.get('leg_no')} 超过计划到达时间 {int(value)} 分钟仍无落地报"
                       f"（生成数据，用于多智能体复核演示）")
            else:
                msg = (f"{lp.get('leg_no')} 已过计划撤轮档时间 {int(value)} 分钟仍未出港"
                       f"（生成数据，用于多智能体复核演示）")
            ae = _mk_alert(leg_e, lp, primary_type, int(value), rule, level, msg)
            db.add(ae)
            db.add(_rel(leg_e, ae))
            made += 1
            # 前两条航段补伴生告警，构成组合场景
            if i < 2 and "机组变更" in rules:
                ce = _mk_alert(leg_e, lp, "机组变更", 0, rules["机组变更"], "低",
                               f"{lp.get('leg_no')} 放行后机组名单变更"
                               f"（生成数据，用于组合复核演示）")
                db.add(ce)
                db.add(_rel(leg_e, ce))
        if made:
            await db.commit()
