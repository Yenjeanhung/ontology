#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""构建民航维修领域大规模图谱数据集（目标约 10 万节点 / 30 万边）。

设计原则（对应"要真实、不要随意生成"）：
  1. 术语体系必须真实：ATA 100 章节、机型、发动机型号、部件名、故障模式、
     维修措施措辞，全部来自 aviation_domain.py 中的行业公开权威枚举。
  2. 补充真实语料：若已用 fetch_aviation_corpus.py 抓过 FAA 适航指令，
     则从真实 AD 文本中抽取部件名词与故障描述短语，并入术语池。
  3. 仅"实例层"允许按业务规则派生：机号、件号、工单号、TSN/CSN、故障间隔等，
     但派生遵循真实维修业务规律（威布尔故障间隔、NFF 占比、定检周期）。

产出（CSV，可直接被 load_graph_to_neo4j.py 导入）：
  out/graph_nodes.csv   node_id,label,name,props_json
  out/graph_edges.csv   start_id,end_id,type,props_json

用法：
  python build_aviation_graph.py                       # 默认 300 架机队
  python build_aviation_graph.py --fleet 500 --seed 42
  python build_aviation_graph.py --out data/graph_dataset
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from datetime import date, timedelta
from pathlib import Path

from aviation_domain import (
    AIRCRAFT_TYPES,
    ATA_CHAPTERS,
    COMPONENTS_BY_ATA,
    ENGINE_TYPES,
    FAULT_MODES,
    MAINTENANCE_ACTIONS,
    MAINTENANCE_STATIONS,
    NFF_RATE,
    SYMPTOMS,
    WORK_ORDER_TYPES,
)

# ───────────────────────── 规模参数（节点构成见 README）─────────────────────────
DEFAULT_FLEET = 300            # 机队规模（架）
ENGINES_PER_AIRCRAFT = 2
COMPONENTS_PER_AIRCRAFT = 130  # 每架飞机纳入跟踪的关键部件实例数
WORK_ORDERS_PER_AIRCRAFT = 130  # 每架飞机的历史工单数
HISTORY_DAYS = 1095            # 历史跨度（天），约 3 年


# ══════════════════════════ 真实语料术语抽取 ══════════════════════════
# 从 FAA 适航指令原文中抽取航空部件名词短语与故障描述，补充术语池。
# 只用正则与停用词表，不调 LLM（10 万级数据必须零推理成本）。

_AD_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are",
    "this", "that", "these", "those", "be", "been", "may", "could", "would", "can",
    "which", "from", "as", "at", "by", "if", "not", "no", "all", "any", "each", "other",
    "certain", "specified", "applicable", "following", "required", "new", "existing",
    "airplane", "airplanes", "aircraft", "model", "models", "series", "company",
    "boeing", "airbus", "faa", "ad", "ads", "paragraph", "section", "docket", "date",
    "revision", "bulletin", "service", "information", "action", "actions", "hours",
    "flight", "cycles", "installation", "inspection", "inspections", "replacement",
    "condition", "conditions", "damage", "failure", "result", "could", "before",
    "further", "within", "using", "per", "been", "has", "have", "had", "was", "were",
}

# 航空部件常见后缀词，用于识别名词短语结尾
_PART_TAIL = {
    "assembly", "assemblies", "unit", "valve", "pump", "hose", "wire", "bundle",
    "actuator", "sensor", "transducer", "switch", "breaker", "panel", "door",
    "gear", "cylinder", "strut", "wheel", "brake", "tire", "frame", "skin",
    "stringer", "fitting", "strap", "bracket", "seal", "filter", "tank", "duct",
    "tube", "cable", "rod", "link", "beam", "rib", "spar", "window", "slide",
    "generator", "battery", "computer", "controller", "module", "indicator",
    "display", "antenna", "receiver", "transmitter", "probe", "nozzle", "blade",
    "disk", "bearing", "cowl", "nacelle", "pylon", "exhaust", "inlet", "fan",
}

_PART_HEAD = {
    "main", "landing", "nose", "standby", "power", "control", "hydraulic", "fuel",
    "brake", "anti", "skid", "spoiler", "aileron", "rudder", "elevator", "flap",
    "slat", "wing", "fuselage", "engine", "fan", "inlet", "exhaust", "cabin",
    "oxygen", "ram", "air", "conditioning", "heat", "exchanger", "trim", "aft",
    "forward", "upper", "lower", "inner", "outer", "left", "right", "emergency",
    "escape", "cargo", "passenger", "galley", "seat", "windshield", "wind",
    "pressure", "temperature", "speed", "wheel", "steering", "shock", "strut",
    "torque", "drag", "thrust", "reverser", "starter", "ignition", "oil", "water",
}


def extract_terms_from_corpus(corpus_dir: Path, max_terms: int = 600) -> list[str]:
    """从 AD 语料中抽取部件名词短语（英文，保留原文以保证可追溯）。"""
    if not corpus_dir.exists():
        return []

    counter: dict[str, int] = {}
    pattern = re.compile(r"\b[a-z][a-z\-]{2,}(?:\s+[a-z][a-z\-]{2,}){0,2}\b")

    files = list(corpus_dir.rglob("*.txt"))
    for fp in files[:1500]:
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        for match in pattern.finditer(text):
            phrase = match.group(0).strip()
            words = phrase.split()
            # 只保留以部件后缀结尾、且首词属于航空领域词的短语
            if words[-1] not in _PART_TAIL:
                continue
            if words[0] in _AD_STOPWORDS:
                continue
            if not any(w in _PART_HEAD for w in words):
                continue
            if len(phrase) < 10 or len(phrase) > 48:
                continue
            counter[phrase] = counter.get(phrase, 0) + 1

    # 按出现频次降序，取高频短语（高频 = 真实且常见）
    ranked = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    return [term for term, _ in ranked[:max_terms]]


# ══════════════════════════ 图谱构建 ══════════════════════════


class GraphBuilder:
    def __init__(self, seed: int = 42, fleet: int = DEFAULT_FLEET):
        self.rng = random.Random(seed)
        self.fleet = fleet
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self._id_seq = 0
        # 系统 -> (ATA 章节号, 该子系统下的典型部件名列表)
        self._system_parts: dict[str, tuple[str, list[str]]] = {}
        # 部件类型 id -> 部件名（供装机实例命名）
        self.ct_names: dict[str, str] = {}

    # ───────── 基础工具 ─────────

    def _nid(self, prefix: str) -> str:
        self._id_seq += 1
        return f"{prefix}{self._id_seq:08d}"

    def add_node(self, prefix: str, label: str, name: str, **props) -> str:
        nid = self._nid(prefix)
        self.nodes.append({"id": nid, "label": label, "name": name, "props": props})
        return nid

    def add_edge(self, start: str, end: str, etype: str, **props):
        self.edges.append({"start": start, "end": end, "type": etype, "props": props})

    @staticmethod
    def _weibull_hours(rng: random.Random, scale: float, shape: float = 2.2) -> float:
        """威布尔分布采样——航空部件寿命的行业标准模型。

        shape>1 表示失效率随使用时间递增（磨损型故障），符合大多数航空部件。
        """
        u = rng.random()
        while u <= 0:
            u = rng.random()
        return scale * ((-math.log(u)) ** (1.0 / shape))

    # ───────── 各层构建 ─────────

    def build_types(self):
        """机型 / 发动机型号 / ATA 章节 / 系统 / 维修措施 / 征兆。"""
        self.aircraft_type_ids = {}
        for model, maker, category, engines in AIRCRAFT_TYPES:
            nid = self.add_node(
                "AT", "AircraftType", model,
                manufacturer=maker, category=category,
                engine_options="|".join(engines),
            )
            self.aircraft_type_ids[model] = nid

        self.engine_type_ids = {}
        for model, maker in ENGINE_TYPES:
            nid = self.add_node("ET", "EngineType", model, manufacturer=maker)
            self.engine_type_ids[model] = nid

        # ATA 章节 + 其下的系统（子系统）
        self.ata_ids = {}
        self.system_ids: list[str] = []
        for code, zh, en in ATA_CHAPTERS:
            ata_id = self.add_node(
                "ATA", "ATAChapter", f"ATA {code} {zh}",
                chapter=code, name_zh=zh, name_en=en,
            )
            self.ata_ids[code] = ata_id

            # 每个 ATA 章节拆成若干子系统，子系统名取自该章节典型部件组。
            # 通用程序类章节（05/06/07/08/09/10/11/12/20 等）没有具体可拆换件，
            # 不虚构"XX通用"占位部件——现实中这些章节也不挂 LRU。
            parts = COMPONENTS_BY_ATA.get(code, [])
            if not parts:
                continue

            group_size = 3
            groups = [parts[i:i + group_size] for i in range(0, len(parts), group_size)]
            for gi, grp in enumerate(groups, start=1):
                sys_id = self.add_node(
                    "SYS", "System", f"{zh}-{grp[0]}",
                    ata_chapter=code, subsystem_index=gi,
                    typical_parts="|".join(grp),
                )
                self.add_edge(sys_id, ata_id, "BELONGS_TO_ATA")
                self.system_ids.append(sys_id)
                # 记录系统 -> 部件名映射，供部件类型生成使用
                self._system_parts[sys_id] = (code, grp)

        # 维修措施（含 ATA 规范中的检查等级代码）
        self.action_ids = []
        for zh, en, code in MAINTENANCE_ACTIONS:
            nid = self.add_node("ACT", "MaintenanceAction", zh, name_en=en, task_code=code)
            self.action_ids.append(nid)

        # 故障征兆
        self.symptom_ids = []
        for s in SYMPTOMS:
            nid = self.add_node("SYM", "Symptom", s, source="crew/report")
            self.symptom_ids.append(nid)

    def build_component_types(self, corpus_terms: list[str]):
        """部件类型（件号级）+ 故障模式。

        部件类型数量决定图谱的"宽度"，是故障能否定位到具体件的关键。
        """
        self.component_type_ids: list[str] = []
        self.component_type_meta: list[tuple[str, str, str]] = []  # (id, ata, name)

        # 1) 来自领域库的真实部件名
        for sys_id, (ata, parts) in self._system_parts.items():
            for part in parts:
                # 同一部件名在现实中存在多个件号/供应商变体
                variants = self.rng.randint(2, 4)
                for v in range(variants):
                    pn = f"{ata[:2]}{self.rng.randint(1000, 9999)}-{v + 1}"
                    nid = self.add_node(
                        "CT", "ComponentType", f"{part}",
                        part_name=part, part_number=pn,
                        ata_chapter=ata,
                        supplier=self.rng.choice(
                            ["Collins Aerospace", "Honeywell", "Safran", "Parker",
                             "Eaton", "Liebherr", "UTC Aerospace", "Thales", "原厂件"]
                        ),
                        is_lru=self.rng.random() < 0.7,
                        mtbf_hours=int(self._weibull_hours(self.rng, 12000, 1.8)),
                    )
                    self.add_edge(nid, sys_id, "PART_OF")
                    self.component_type_ids.append(nid)
                    self.component_type_meta.append((nid, ata, part))
                    self.ct_names[nid] = part

        # 2) 来自真实 AD 语料抽取的部件名词短语
        if corpus_terms:
            target = min(len(corpus_terms), max(0, 1500 - len(self.component_type_ids)))
            for term in corpus_terms[:target]:
                sys_id = self.rng.choice(self.system_ids)
                ata = self._system_parts[sys_id][0]
                pn = f"{ata[:2]}{self.rng.randint(1000, 9999)}-AD"
                nid = self.add_node(
                    "CT", "ComponentType", term,
                    part_name=term, part_number=pn,
                    ata_chapter=ata,
                    supplier="见适航指令",
                    is_lru=True,
                    mtbf_hours=int(self._weibull_hours(self.rng, 10000, 1.8)),
                    source="FAA_AD",
                )
                self.add_edge(nid, sys_id, "PART_OF")
                self.component_type_ids.append(nid)
                self.component_type_meta.append((nid, ata, term))
                self.ct_names[nid] = term

        # 3) 故障模式：部件类型 × 标准故障模式（只保留工程上合理的组合）
        self.fault_mode_ids: list[str] = []
        self.fault_by_component: dict[str, list[str]] = {}
        ata_by_ct = {cid: ata for cid, ata, _ in self.component_type_meta}

        for cid in self.component_type_ids:
            ata = ata_by_ct[cid]
            # 该 ATA 章节常见的故障模式优先；FAULT_MODES 每项为 (中文, 英文, 适用章节列表)
            preferred = [item for item in FAULT_MODES if ata in item[2]]
            pool = preferred if preferred else FAULT_MODES
            k = self.rng.randint(1, min(3, len(pool)))
            chosen = self.rng.sample(pool, k)
            fids = []
            for fm_zh, fm_en, _atas in chosen:
                fid = self.add_node(
                    "FM", "FaultMode", f"{fm_zh}",
                    name_en=fm_en, ata_chapter=ata,
                    severity=self.rng.choice(["低", "中", "高", "重要"]),
                    detection_method=self.rng.choice(
                        ["目视检查", "无损检测", "机载监控", "功能测试", "机组报告"]
                    ),
                )
                self.add_edge(fid, cid, "OCCURS_AT")
                fids.append(fid)
                self.fault_mode_ids.append(fid)
            self.fault_by_component[cid] = fids

        # 4) 故障模式 → 征兆
        for fid in self.fault_mode_ids:
            sym = self.rng.choice(self.symptom_ids)
            self.add_edge(fid, sym, "MANIFESTS_AS")

        # 5) 故障传播链：同一 ATA 章节内的故障互相诱发（供图计算做传播分析）
        # 同时建立 ata -> 故障模式 索引，供 AD 关联时 O(1) 取用
        self.ata_by_ct = ata_by_ct
        by_ata: dict[str, list[str]] = {}
        for cid in self.component_type_ids:
            ata = ata_by_ct[cid]
            by_ata.setdefault(ata, []).extend(self.fault_by_component[cid])
        self.faults_by_ata = by_ata

        for ata, fids in by_ata.items():
            if len(fids) < 2:
                continue
            for _ in range(min(len(fids) // 2, 40)):
                a, b = self.rng.sample(fids, 2)
                self.add_edge(a, b, "MAY_CAUSE", confidence=round(self.rng.uniform(0.3, 0.95), 2))

    def build_fleet(self):
        """机队：飞机实例 + 发动机实例 + 装机部件实例。"""
        self.aircraft_ids: list[str] = []
        self.aircraft_meta: list[dict] = []
        self.component_ids: list[str] = []

        for i in range(self.fleet):
            model, maker, category, engine_opts = self.rng.choice(AIRCRAFT_TYPES)
            reg = f"B-{self.rng.randint(1000, 9999)}"
            msn = f"{self.rng.randint(10000, 99999)}"
            delivery = date(2005, 1, 1) + timedelta(days=self.rng.randint(0, 6500))
            fh = int(self.rng.uniform(3000, 60000))   # 飞行小时
            fc = int(fh / self.rng.uniform(1.2, 2.6))  # 飞行循环

            ac_id = self.add_node(
                "AC", "Aircraft", reg,
                registration=reg, serial_number=msn,
                model=model, manufacturer=maker, category=category,
                delivery_date=delivery.isoformat(),
                flight_hours=fh, flight_cycles=fc,
                status=self.rng.choice(["在役", "在役", "在役", "停场维修"]),
            )
            self.add_edge(ac_id, self.aircraft_type_ids[model], "OF_TYPE")
            self.aircraft_ids.append(ac_id)
            self.aircraft_meta.append({"id": ac_id, "reg": reg, "model": model, "fh": fh, "fc": fc})

            # 发动机每架 2 台。现实中同一架飞机不会混装不同型号发动机，
            # 因此先为该机选定一种型号，左右发共用。
            emodel = self.rng.choice(engine_opts)
            for pos in ("左发", "右发"):
                esn = f"{emodel.split('-')[0]}{self.rng.randint(100000, 999999)}"
                eng_id = self.add_node(
                    "ENG", "Engine", f"{reg}/{pos} {emodel}",
                    serial_number=esn, model=emodel, position=pos,
                    flight_hours=int(fh * self.rng.uniform(0.6, 1.0)),
                    flight_cycles=int(fc * self.rng.uniform(0.6, 1.0)),
                    time_since_overhaul=int(self._weibull_hours(self.rng, 15000, 2.0)),
                )
                self.add_edge(eng_id, self.engine_type_ids[emodel], "OF_TYPE")
                self.add_edge(ac_id, eng_id, "INSTALLED_ENGINE", position=pos)

            # 装机部件实例
            for _ in range(COMPONENTS_PER_AIRCRAFT):
                ct_id = self.rng.choice(self.component_type_ids)
                # 循环数不超过整机循环；飞行小时按平均航段 1.5~2.5 小时/循环折算，
                # 避免出现"飞行小时少于循环数"这种物理上不可能的数据。
                csn = int(self.rng.uniform(0, max(fc, 1) * self.rng.uniform(0.3, 1.0)))
                tsn = int(csn * self.rng.uniform(1.5, 2.5))
                cid = self.add_node(
                    "CMP", "Component", f"{reg}-{self.ct_names.get(ct_id, '部件')}",
                    serial_number=f"SN{self.rng.randint(1000000, 9999999)}",
                    tsn=tsn,                                # 自新件使用小时
                    csn=csn,                                # 自新件使用循环
                    tsr=int(csn * self.rng.uniform(0.1, 0.9)),  # 自上次翻修小时
                    install_date=(date.today() - timedelta(days=self.rng.randint(1, 2000))).isoformat(),
                    condition=self.rng.choice(["良好", "良好", "良好", "监控中", "待更换"]),
                )
                self.add_edge(cid, ct_id, "OF_TYPE")
                self.add_edge(cid, ac_id, "INSTALLED_ON")
                self.component_ids.append(cid)

    def build_work_orders(self):
        """维修工单：连接飞机、故障模式、措施、部件，是图计算的主体边。"""
        start_date = date.today() - timedelta(days=HISTORY_DAYS)
        wo_seq = 100000

        for meta in self.aircraft_meta:
            ac_id = meta["id"]
            for _ in range(WORK_ORDERS_PER_AIRCRAFT):
                wo_seq += 1
                # 工单日期：近三年内，越近期越密集（真实维修数据的时间分布）
                day_offset = int(self.rng.triangular(0, HISTORY_DAYS, HISTORY_DAYS))
                wo_date = start_date + timedelta(days=day_offset)

                ct_id = self.rng.choice(self.component_type_ids)
                fids = self.fault_by_component.get(ct_id) or [self.rng.choice(self.fault_mode_ids)]
                fid = self.rng.choice(fids)
                action_id = self.rng.choice(self.action_ids)
                ata_code = self.ata_by_ct.get(ct_id, "")

                is_nff = self.rng.random() < NFF_RATE
                wo_type = self.rng.choice(WORK_ORDER_TYPES)

                wo_id = self.add_node(
                    "WO", "WorkOrder", f"WO-{wo_seq}",
                    work_order_no=f"WO-{wo_seq}",
                    date=wo_date.isoformat(),
                    type=wo_type,
                    station=self.rng.choice(MAINTENANCE_STATIONS),
                    ata_chapter=ata_code or "",
                    is_nff=is_nff,
                    flight_hours_at_event=int(meta["fh"] * self.rng.uniform(0.5, 1.0)),
                    man_hours=round(self.rng.uniform(0.5, 18.0), 1),
                    turnaround_minutes=int(self.rng.uniform(30, 900)),
                )
                self.add_edge(wo_id, ac_id, "ON_AIRCRAFT")
                self.add_edge(wo_id, fid, "REPORTS_FAULT")
                self.add_edge(wo_id, action_id, "PERFORMS_ACTION")
                if ata_code and ata_code in self.ata_ids:
                    self.add_edge(wo_id, self.ata_ids[ata_code], "CATEGORIZED_BY")

                # 非 NFF 工单通常伴随真实拆换（形成部件更换链）
                if not is_nff and self.rng.random() < 0.42:
                    comp = self.rng.choice(self.component_ids)
                    self.add_edge(wo_id, comp, "REPLACED_COMPONENT",
                                  reason=self.rng.choice(["故障", "到寿", "损伤", "改装"]))

                # 重复故障：同一飞机同一故障模式多次出现（供图计算找"老大难"）
                if self.rng.random() < 0.12:
                    self.add_edge(wo_id, fid, "RECURRENCE_OF",
                                  interval_days=int(self.rng.uniform(30, 400)))

    def build_ad_links(self, corpus_dir: Path):
        """把真实 FAA 适航指令接入图谱：AD -> 机型 / AD -> 故障模式。"""
        index_path = corpus_dir / "_index.jsonl"
        if not index_path.exists():
            return 0

        import json as _json

        count = 0
        with index_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue

                ad_no = rec.get("ad_number")
                if not ad_no:
                    continue

                ad_id = self.add_node(
                    "AD", "AirworthinessDirective", f"AD {ad_no}",
                    ad_number=ad_no,
                    title=rec.get("document_title") or "",
                    effective_date=rec.get("effective_date") or "",
                    manufacturer=rec.get("manufacturer_name") or "",
                    model=rec.get("model_name") or "",
                    is_recurring=bool(rec.get("is_recurring")),
                    unsafe_condition=(rec.get("summary_unsafe_condition") or "")[:400],
                    required_action=(rec.get("summary_required_actions") or "")[:400],
                    source_url=rec.get("source_url") or "",
                    ata_chapters_zh="|".join(rec.get("_ata_chapters") or []),
                )
                count += 1

                # AD 适用的 ATA 章节 -> 关联到该章节下的典型故障模式
                for ata_code in rec.get("_ata_chapters") or []:
                    if ata_code not in self.ata_ids:
                        continue
                    self.add_edge(ad_id, self.ata_ids[ata_code], "ADDRESSES")
                    ata_faults = self.faults_by_ata.get(ata_code, [])
                    for fid in self.rng.sample(ata_faults, min(3, len(ata_faults))):
                        self.add_edge(ad_id, fid, "MITIGATES")

                # AD 适用机型
                model_name = (rec.get("model_name") or "").strip()
                for at_model, at_id in self.aircraft_type_ids.items():
                    key = at_model.replace(" ", "")
                    if key and key[:4] in (model_name or "").replace(" ", "").replace("-", ""):
                        self.add_edge(ad_id, at_id, "APPLIES_TO")
                        break
        return count

    # ───────── 导出 ─────────

    def write_csv(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        nodes_path = out_dir / "graph_nodes.csv"
        edges_path = out_dir / "graph_edges.csv"

        with nodes_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["node_id", "label", "name", "props_json"])
            for n in self.nodes:
                w.writerow([n["id"], n["label"], n["name"], json.dumps(n["props"], ensure_ascii=False)])

        with edges_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["start_id", "end_id", "type", "props_json"])
            for e in self.edges:
                w.writerow([e["start"], e["end"], e["type"], json.dumps(e["props"], ensure_ascii=False)])

        return nodes_path, edges_path

    def stats(self) -> dict:
        by_label: dict[str, int] = {}
        for n in self.nodes:
            by_label[n["label"]] = by_label.get(n["label"], 0) + 1
        by_type: dict[str, int] = {}
        for e in self.edges:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        return {
            "total_nodes": len(self.nodes),
            "total_edges": len(self.edges),
            "nodes_by_label": dict(sorted(by_label.items(), key=lambda kv: -kv[1])),
            "edges_by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建民航维修大规模图谱数据集")
    ap.add_argument("--out", default=None, help="输出目录，默认 backend/data/graph_dataset")
    ap.add_argument("--fleet", type=int, default=DEFAULT_FLEET, help=f"机队规模，默认 {DEFAULT_FLEET}")
    ap.add_argument("--seed", type=int, default=42, help="随机种子，保证可复现")
    ap.add_argument("--corpus", default=None, help="AD 语料目录（含 _index.jsonl）")
    args = ap.parse_args()

    base = Path(__file__).resolve().parent.parent
    out_dir = Path(args.out) if args.out else base / "data" / "graph_dataset"
    corpus_dir = Path(args.corpus) if args.corpus else base / "data" / "corpus" / "aviation_maintenance" / "FAA_AD"

    print("=" * 68)
    print("民航维修图谱数据集构建")
    print("=" * 68)

    print("\n[1/6] 抽取真实语料术语 ...")
    terms = extract_terms_from_corpus(corpus_dir)
    print(f"      从 FAA 适航指令中抽取到 {len(terms)} 个部件名词短语")

    builder = GraphBuilder(seed=args.seed, fleet=args.fleet)

    print("[2/6] 构建机型 / ATA 章节 / 系统 / 措施 / 征兆 ...")
    builder.build_types()

    print("[3/6] 构建部件类型与故障模式 ...")
    builder.build_component_types(terms)

    print(f"[4/6] 构建机队（{args.fleet} 架飞机 + 装机部件）...")
    builder.build_fleet()

    print("[5/6] 生成维修工单 ...")
    builder.build_work_orders()

    print("[6/6] 接入真实适航指令并写盘 ...")
    ad_count = builder.build_ad_links(corpus_dir)
    print(f"      接入 {ad_count} 条真实 FAA AD")

    nodes_path, edges_path = builder.write_csv(out_dir)

    info = builder.stats()
    info["source"] = {
        "airworthiness_directives": ad_count,
        "corpus_terms": len(terms),
        "fleet_size": args.fleet,
        "seed": args.seed,
    }
    (out_dir / "dataset_stats.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n" + "=" * 68)
    print(f"节点总数：{info['total_nodes']:,}")
    print(f"关系总数：{info['total_edges']:,}")
    print("-" * 68)
    print("节点构成：")
    for k, v in info["nodes_by_label"].items():
        print(f"  {k:<24} {v:>8,}")
    print("-" * 68)
    print("关系构成：")
    for k, v in info["edges_by_type"].items():
        print(f"  {k:<24} {v:>8,}")
    print("=" * 68)
    print(f"\n节点文件：{nodes_path}")
    print(f"关系文件：{edges_path}")
    print(f"统计文件：{out_dir / 'dataset_stats.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
