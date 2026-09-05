"""向 Entity / Relation 表写入民航维修领域真实实体/关系。

设计目标
========

1. **不破坏业务数据**：只清理 ``民航维修领域本体`` 类别下、本次写入专用知识库
   ``kb_aviation_seed`` 内的旧 Entity/Relation。
2. **本体严格对应**：每个 Entity 写入时 ``ontology_id`` 取自
   ``seed_aviation_ontology.ONTOLOGIES`` 中定义的 20 个本体（先确保本体已
   通过 ``seed_aviation_ontology.py`` 落库）。
3. **关系严格遵循三元组约束**：每条 Relation 写入时校验 (source_ontology,
   relation, target_ontology) 符合 ``CONSTRAINTS``，违反时丢弃并打印警告。
4. **真实术语驱动**：所有名称、属性取值均来自 ``aviation_domain``，实例层
   （机号、件号、工单号）按真实维修业务规律派生（威布尔故障间隔、NFF 占比、
   引入日期范围等），与 ``build_aviation_graph.py`` 保持一致。
5. **可复现**：固定随机种子，便于回归对比。
6. **可重入**：直接运行是“增量补缺”，加 ``--force`` 则先清再写。

用法
====

::

    cd backend
    python scripts/seed_aviation_entities.py            # 增量写入
    python scripts/seed_aviation_entities.py --force    # 清空后写入
    python scripts/seed_aviation_entities.py --fleet 400 --seed 7
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

# 允许从 backend/scripts 目录直接运行
sys.path.insert(0, str(Path(__file__).parent.parent))

import sqlalchemy as sa
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session, engine, init_db
from models import (
    Entity,
    KnowledgeBase,
    Ontology,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
    Relation,
)

# 复用已成熟的真实术语库与本体定义
from seed_aviation_ontology import CATEGORY_NAME, CONSTRAINTS, ONTOLOGIES, RELATIONS
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

# ──────────────────────────── 配置 ────────────────────────────

SEED_KB_NAME = "民航维修领域图谱（种子数据）"
SEED_KB_DESC = (
    "由 scripts/seed_aviation_entities.py 自动生成的民航维修领域真实实例数据，"
    "约 10 万节点 / 30 万关系，实体 / 关系严格遵循「民航维修领域本体」中的"
    "本体定义与三元组约束，专用于图谱推理、图计算与可视化演示。"
)

# 规模参数（≈10 万节点 / 30+ 万边）
FLEET_SIZE_DEFAULT = 300               # 机队规模（架）
COMPONENTS_PER_AIRCRAFT = 130          # 每架装机部件实例
WORK_ORDERS_PER_AIRCRAFT = 130         # 每架历史工单
HISTORY_DAYS = 1095                    # 历史跨度（天，约 3 年）
AIRWORTHINESS_DOCS = 600               # AD/SB/EO 适航文件
MEL_DEFERRALS = 800                    # MEL 保留
PERSONNEL = 300                        # 机务/工程师
VENDORS = 40                           # OEM/MRO/供应商
STATIONS = 120                         # 维修站
PARAMETERS_PER_CHAPTER = (2, 5)        # 每个 ATA 章节的监控参数随机 2~5
FAULT_CODES_PER_CHAPTER = (2, 5)       # 每个 ATA 章节的故障代码随机 2~5

# ──────────────────────────── 工具 ────────────────────────────

def _gen_id() -> str:
    return uuid.uuid4().hex[:12]


def _weibull_hours(rng: random.Random, scale: float, shape: float = 2.2) -> float:
    """威布尔分布采样——航空部件寿命的行业标准模型（shape>1 = 老化型）。"""
    u = rng.random()
    while u <= 0:
        u = rng.random()
    return scale * ((-math.log(u)) ** (1.0 / shape))


def _chunked(seq, size):
    buf = []
    for x in seq:
        buf.append(x)
        if len(buf) >= size:
            yield buf
            buf = []
    if buf:
        yield buf


# ──────────────────────────── 修复旧数据 ────────────────────────────

async def _purge_old_seed_entities(db: AsyncSession, kb_id: str) -> tuple[int, int]:
    """清理本次专用 KB 下旧 Entity/Relation。返回 (实体数, 关系数)。"""
    ent_count = (await db.execute(
        delete(Entity).where(Entity.kb_id == kb_id)
    )).rowcount
    rel_count = (await db.execute(
        delete(Relation).where(Relation.kb_id == kb_id)
    )).rowcount
    return ent_count, rel_count


# ──────────────────────────── 实体生成器 ────────────────────────────

class EntityFactory:
    """按本体类一次性构造所有「类级」与「实例级」实体。

    所有 commit 都在 ``seed()`` 之外用 ``add_all`` + 显式 flush 完成，
    这样主流程保持轻量、便于排错。
    """

    def __init__(self, db: AsyncSession, kb_id: str, ont_id_by_name: dict[str, str],
                 rel_id_by_name: dict[str, str], fleet: int, seed: int):
        self.db = db
        self.kb_id = kb_id
        self.ont_id = ont_id_by_name
        self.rel_id = rel_id_by_name
        self.rng = random.Random(seed)
        self.fleet = fleet

        # 实体行缓存（待 commit 时统一 flush）
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []
        # entity_id -> Entity 的 O(1) 索引。
        # 关键性能点：关系生成需要按 id 反查本体类型做三元组校验，
        # 若用 next(e for e in self.entities ...) 线性扫描，10 万实体 × 30 万关系
        # 会退化成数十亿次比较而跑不完，这里必须走字典。
        self._ent_by_id: dict[str, Entity] = {}
        # 已存在关系 dedup 集合（避免 (kb_id, src, rel_type, tgt) 唯一约束冲突）
        self._rel_keys: set[tuple[str, str, str]] = set()

        # 各种 id 索引，供后续关系生成使用
        self.eid_by_ont: dict[str, list[str]] = {}     # ontology_name -> [entity_id, ...]
        self.eid_by_name_ont: dict[tuple[str, str], str] = {}  # (ontology_name, name) -> id

        # 性能：每 2000 条批 flush 一次
        self._FLUSH_BATCH = 2000

        # 把本体三元组约束预编译成集合，校验从 O(n) 降为 O(1)
        self._allowed_triples: set[tuple[str, str, str]] = set(CONSTRAINTS)

        # 计数器：用于合规性总结
        self.stats = {
            "total_entities": 0,
            "total_relations": 0,
            "skipped_invalid_triples": 0,
            "skipped_dup": 0,
            "by_ontology": {},
        }

    # ────── 低层写入 ──────

    def _add_entity(self, ont_name: str, name: str, description: str = "",
                    properties: dict | None = None) -> str:
        ont_id = self.ont_id.get(ont_name)
        if not ont_id:
            raise RuntimeError(f"未知本体：{ont_name}")
        eid = _gen_id()
        ent = Entity(
            id=eid,
            kb_id=self.kb_id,
            ontology_id=ont_id,
            entity_type=ont_name,
            name=name,
            description=description or "",
            properties=json.dumps(properties or {}, ensure_ascii=False),
        )
        self.entities.append(ent)
        self._ent_by_id[eid] = ent
        self.eid_by_ont.setdefault(ont_name, []).append(eid)
        self.eid_by_name_ont[(ont_name, name)] = eid
        self.stats["total_entities"] += 1
        self.stats["by_ontology"][ont_name] = self.stats["by_ontology"].get(ont_name, 0) + 1
        return eid

    @staticmethod
    def _weibull_hours(rng: random.Random, scale: float, shape: float = 2.2) -> float:
        """威布尔分布采样——航空部件寿命的行业标准模型（shape>1 = 老化型）。"""
        u = rng.random()
        while u <= 0:
            u = rng.random()
        return scale * ((-math.log(u)) ** (1.0 / shape))

    def _add_relation(self, src_id: str, rel_name: str, tgt_id: str,
                      description: str = "", properties: dict | None = None) -> bool:
        """写入一条关系，自动校验本体三元组约束。

        Returns
        -------
        bool
            ``True`` 表示已写入；``False`` 表示违反约束被丢弃（计入跳过数）。
        """
        rel_def_id = self.rel_id.get(rel_name)
        if not rel_def_id:
            self.stats["skipped_invalid_triples"] += 1
            return False

        # 校验 (src_ontology, relation, tgt_ontology) 是否在 CONSTRAINTS 内。
        src_ent = self._ent_by_id.get(src_id)
        tgt_ent = self._ent_by_id.get(tgt_id)
        if not src_ent or not tgt_ent:
            self.stats["skipped_invalid_triples"] += 1
            return False
        src_ont, tgt_ont = src_ent.entity_type, tgt_ent.entity_type

        if not self._is_allowed_triple(src_ont, rel_name, tgt_ont):
            self.stats["skipped_invalid_triples"] += 1
            return False

        # 去重：(src, relation_type, tgt) 唯一；同一对重复就跳过
        dedup_key = (src_id, rel_name, tgt_id)
        if dedup_key in self._rel_keys:
            self.stats["skipped_dup"] += 1
            return False
        self._rel_keys.add(dedup_key)

        r = Relation(
            id=_gen_id(),
            kb_id=self.kb_id,
            relation_def_id=rel_def_id,
            relation_type=rel_name,
            source_entity_id=src_id,
            target_entity_id=tgt_id,
            description=description or "",
        )
        self.relations.append(r)
        self.stats["total_relations"] += 1
        return True

    def _is_allowed_triple(self, src_ont: str, rel_name: str, tgt_ont: str) -> bool:
        """(源本体, 关系, 目标本体) 是否在 seed 脚本定义的 CONSTRAINTS 内。"""
        return (src_ont, rel_name, tgt_ont) in self._allowed_triples

    # ────── 批量 flush ──────

    async def _flush_entities(self) -> None:
        """把缓冲中的实体写入数据库。

        注意：不清空 self.entities！
        后续 build_* 阶段会通过 id 反查并读取 properties；如果这里清空，
        那些 next() 就会 StopIteration 导致整个 seed 中断。
        ORM 会在 flush 之后仍把内存对象保留，expire_on_commit=False 也保证
        提交后属性不过期。最终 seed 结束时由 GC 释放整张列表即可。
        """
        if not self.entities:
            return
        for batch in _chunked(self.entities, self._FLUSH_BATCH):
            self.db.add_all(batch)
            await self.db.flush()

    async def _flush_relations(self) -> None:
        if not self.relations:
            return
        for batch in _chunked(self.relations, self._FLUSH_BATCH):
            self.db.add_all(batch)
            await self.db.flush()
        # relations 不需要反查 id，写完即可释放
        self.relations.clear()

    # ─────────────────── 本体填充（实体/关系） ───────────────────

    async def build_types_layer(self):
        """机型 / 发动机型号 / ATA 章节 / 系统 / 维修措施 / 征兆 / 工况 / 监控参数。"""
        # 1) 机型（AircraftType）
        aircraft_type_ids: list[str] = []
        for model, maker, category, engines in AIRCRAFT_TYPES:
            eid = self._add_entity(
                "机型", model,
                description=f"{maker} 制造，类别 {category}，可选发动机 {'/'.join(engines)}",
                properties={
                    "type_code": model,
                    "manufacturer": maker,
                    "engine_model": "/".join(engines),
                    "ata_applicability": "全机型适用",
                },
            )
            aircraft_type_ids.append(eid)

        # 2) 发动机型号（EngineType — 本体未定义此类型，复用"机型"占位不合适，故跳过。
        #    真实业务里发动机会作为整机下的子设备节点，由"航空器"直接挂在"机型"下即可。
        engine_type_ids: list[str] = []

        # 3) ATA 章节（"系统"本体，每章一个 ATA 章节节点，下面挂子系统）
        ata_ids: dict[str, str] = {}
        for code, zh, en in ATA_CHAPTERS:
            eid = self._add_entity(
                "系统", f"ATA {code} {zh}",
                description=f"{zh}（{en}）",
                properties={
                    "ata_chapter": code,
                    "system_name": zh,
                    "abbreviation": en.split("/")[0].strip()[:8],
                },
            )
            ata_ids[code] = eid

        # 4) 子系统（每个 ATA 章节拆出 2~4 个子系统，对应"系统"本体下具体子系统节点）
        self.system_ata: dict[str, str] = {}          # system_eid -> ata_code
        self.system_by_ata: dict[str, list[str]] = {}  # ata_code -> [system_eid]，反向索引
        for ata_code, ata_eid in ata_ids.items():
            parts = COMPONENTS_BY_ATA.get(ata_code, [])
            if not parts:
                # 通用章节（05/06/07/08/09/10/11/12/20）无可拆换 LRU，只保留 ATA 章节节点本身
                self.system_ata[ata_eid] = ata_code
                self.system_by_ata.setdefault(ata_code, []).append(ata_eid)
                continue
            groups: list[list[str]] = []
            chunk = 3
            for i in range(0, len(parts), chunk):
                groups.append(parts[i:i + chunk])
            for gi, grp in enumerate(groups, 1):
                system_name = f"{ATA_CHAPTER_DICT[ata_code][0]}-{grp[0]}"
                eid = self._add_entity(
                    "系统", system_name,
                    description=f"ATA {ata_code} 章 {gi} 号子系统，覆盖部件：{' / '.join(grp)}",
                    properties={
                        "ata_chapter": ata_code,
                        "system_name": system_name,
                        "abbreviation": grp[0][:8],
                        "subsystem_index": gi,
                        "typical_parts": "|".join(grp),
                    },
                )
                self.system_ata[eid] = ata_code
                self.system_by_ata.setdefault(ata_code, []).append(eid)
                # 子系统 "组成"-> ATA 章节（允许："系统"->"系统"，但本体只定义了"部件"->"系统"；
                # 避免违规：ATA 章节节点就充当"系统"层级，不另写"组成"边）
                # 关系："包含系统" = "系统" -> "系统"，但本体 CONSTRAINTS 里"包含系统"的方向是
                # ("机型", "包含系统", "系统")，所以此处不写。

        # 5) 维修措施（MaintenanceAction — 本体用"维修措施"）
        self.action_ids: list[str] = []
        for zh, en, code in MAINTENANCE_ACTIONS:
            eid = self._add_entity(
                "维修措施", zh,
                description=en,
                properties={
                    "name": zh,
                    "action_type": code,
                    "procedure": en,
                    "is_permanent_fix": code in ("RPR", "RPL", "MOD", "OHL"),
                },
            )
            self.action_ids.append(eid)

        # 6) 故障征兆（Symptom — 本体有"故障征兆"）
        self.symptom_ids: list[str] = []
        for s in SYMPTOMS:
            self.symptom_ids.append(self._add_entity(
                "故障征兆", s,
                description=f"机组/机务可观测的早期信号：{s}",
                properties={"name": s, "observation": "机组报告/驾驶舱效应"},
            ))

        # 7) 工况（Condition — 本体"工况"，按飞行阶段枚举）
        phases = ["启动", "滑行", "起飞", "爬升", "巡航", "下降", "进近", "着陆", "地面", "多阶段"]
        self.condition_ids: list[str] = []
        for ph in phases:
            self.condition_ids.append(self._add_entity(
                "工况", f"飞行阶段-{ph}",
                description=f"飞行阶段：{ph}",
                properties={"name": ph, "condition_type": "飞行阶段"},
            ))
        # 外加气象/温度/海拔/负载四个工况
        for name, ctype, value in [
            ("高温环境", "气象", "ISA+15℃ 及以上"),
            ("低温环境", "气象", "ISA-30℃ 及以下"),
            ("高湿度", "气象", "相对湿度 > 80%"),
            ("高原机场", "机场", "海拔 > 2400 m"),
            ("沿海腐蚀环境", "外部环境", "高盐雾 / 沿海运营"),
            ("沙尘环境", "外部环境", "沙尘 / 多尘跑道"),
            ("高震动工况", "震动", "浴盆曲线加速区段"),
            ("重载起落", "负载", "最大着陆重量 ≥ MTOW 90%"),
        ]:
            self.condition_ids.append(self._add_entity(
                "工况", name,
                description=f"{ctype}：{value}",
                properties={"name": name, "condition_type": ctype, "value": value},
            ))

        # 8) 监控参数（Parameter — 每个 ATA 章节随机 2~5 个监控点）
        self.parameter_ids: list[str] = []
        param_templates = [
            ("EGT", "排气温度", "℃"),
            ("N1", "风扇转速", "%"),
            ("N2", "压气机转速", "%"),
            ("VIB", "振动值", "ips"),
            ("FF", "燃油流量", "kg/h"),
            ("OIL_T", "滑油温度", "℃"),
            ("OIL_P", "滑油压力", "psi"),
            ("HYD_P", "液压压力", "psi"),
            ("HYD_QTY", "油箱量", "%"),
            ("BLEED", "引气压力", "psi"),
            ("BRAKE_T", "刹车温度", "℃"),
            ("TIRE_P", "轮胎气压", "psi"),
            ("CAB_P", "座舱高度", "ft"),
            ("CAB_T", "座舱温度", "℃"),
            ("DOOR", "舱门状态", "BOOL"),
            ("FLAP", "襟翼位置", "DEG"),
            ("SPD", "指示空速", "kt"),
            ("ALT", "气压高度", "ft"),
        ]
        for ata_code, ata_eid in ata_ids.items():
            n = self.rng.randint(*PARAMETERS_PER_CHAPTER)
            chosen = self.rng.sample(param_templates, min(n, len(param_templates)))
            for code, name, unit in chosen:
                eid = self._add_entity(
                    "监控参数", f"{code}-ATA{ata_code}",
                    description=f"{name}（{unit}），采集自 ATA {ata_code}",
                    properties={
                        "param_code": code,
                        "name": name,
                        "unit": unit,
                        "normal_range": f"{name} 正常区间",
                        "source": self.rng.choice(["ACARS", "QAR", "CMS", "人工记录"]),
                    },
                )
                self.parameter_ids.append(eid)
                # "监测于"：监控参数 -> 监控参数... 不对：本体定义是 "部件 -> 监控参数"。
                # 监控参数通常挂在 ATA 章节下而不是部件，所以此处省略边；部件 -> 监控参数
                # 留到部件生成阶段。
        await self._flush_entities()
        # 缓存常用引用
        self.aircraft_type_ids = aircraft_type_ids
        self.ata_ids = ata_ids
        self.engine_type_ids = engine_type_ids

    async def build_component_types(self):
        """件号级部件类型 + 故障代码 + 故障模式 + 故障原因。"""
        # 1) 件号级部件（本体"部件"）
        self.component_type_ids: list[str] = []
        self.component_type_by_ata: dict[str, list[str]] = {}
        suppliers = [
            "Collins Aerospace", "Honeywell", "Safran", "Parker", "Eaton",
            "Liebherr", "UTC Aerospace", "Thales", "原厂件", "Boeing Material",
        ]

        _pn_counter = {"n": 0}
        for system_eid, ata_code in self.system_ata.items():
            parts = COMPONENTS_BY_ATA.get(ata_code, [])
            if not parts:
                continue
            for part_name in parts:
                variants = self.rng.randint(2, 4)
                for v in range(variants):
                    _pn_counter["n"] += 1
                    # 用全局唯一序号替代随机数，保证 (kb_id, entity_type, name) 唯一
                    pn = f"{ata_code[:2]}{_pn_counter['n']:06d}-{v + 1:02d}"
                    # name 必须包含件号，避免 (kb_id, entity_type, name) 唯一约束冲突
                    entity_name = f"{part_name}/{pn}"
                    eid = self._add_entity(
                        "部件", entity_name,
                        description=f"{part_name}（{pn}）",
                        properties={
                            "part_number": pn,
                            "name": part_name,
                            "ata_chapter": ata_code,
                            "manufacturer": self.rng.choice(suppliers),
                            "component_type": self.rng.choice(["LRU", "SRU", "消耗件", "时寿件"]),
                            "is_life_limited": 1 if self.rng.random() < 0.3 else 0,
                            "mtbur_target": int(self._weibull_hours(self.rng, 12000, 1.8)),
                            "installed_position": self.rng.choice(["左发", "右发", "前舱", "后舱", "主起落架", "前起落架", "驾驶舱", "客舱"]),
                        },
                    )
                    self.component_type_ids.append(eid)
                    self.component_type_by_ata.setdefault(ata_code, []).append(eid)
                    # "组成"：部件 -> 系统（CONSTRAINT 允许）
                    self._add_relation(eid, "组成", system_eid,
                                       properties={"role": "primary"})

        # 2) 故障代码（本体"故障代码"）— 每个 ATA 章节 2~5 个 ECAM/CMS 报文码
        self.fault_code_ids: list[str] = []
        self.fault_code_by_ata: dict[str, list[str]] = {}
        for ata_code, ata_eid in self.ata_ids.items():
            n = self.rng.randint(*FAULT_CODES_PER_CHAPTER)
            for i in range(n):
                code = f"{ata_code}-{self.rng.randint(10000, 99999)}"
                eid = self._add_entity(
                    "故障代码", code,
                    description=f"{code} 报文",
                    properties={
                        "fault_code": code,
                        "message": f"{code} 系统告警",
                        "ata_chapter": ata_code,
                        "level": self.rng.choice(["WARNING", "CAUTION", "ADVISORY", "STATUS"]),
                        "trigger_phase": self.rng.choice([
                            "启动", "滑行", "起飞", "爬升", "巡航", "下降", "进近", "着陆", "地面", "多阶段"
                        ]),
                    },
                )
                self.fault_code_ids.append(eid)
                self.fault_code_by_ata.setdefault(ata_code, []).append(eid)

        # 3) 故障模式（本体"故障模式"）：按 ATA 章节 FAULT_MODES 派生
        self.fault_mode_ids: list[str] = []
        self.fault_mode_by_ata: dict[str, list[str]] = {}
        for fm_zh, fm_en, applicable in FAULT_MODES:
            if not applicable:
                applicable = ["21", "27", "32"]
            for ata in applicable:
                eid = self._add_entity(
                    "故障模式", f"{fm_zh}（ATA {ata}）",
                    description=f"{fm_en}（ATA {ata}）",
                    properties={
                        "name": fm_zh,
                        "ata_chapter": ata,
                        "fault_class": self.rng.choice(["A", "B", "C", "D"]),
                        "affects_dispatch": 1 if self.rng.random() < 0.55 else 0,
                        "mel_deferrable": 1 if self.rng.random() < 0.7 else 0,
                    },
                )
                self.fault_mode_ids.append(eid)
                self.fault_mode_by_ata.setdefault(ata, []).append(eid)
                # "归属系统"：故障模式 -> 系统（选该 ATA 章节的任一子系统）
                sys_candidates = self.system_by_ata.get(ata, [])
                if sys_candidates:
                    self._add_relation(eid, "归属系统", self.rng.choice(sys_candidates))
                # "上报代码"：故障模式 -> 该 ATA 章节的故障代码
                if ata in self.fault_code_by_ata:
                    self._add_relation(eid, "上报代码",
                                       self.rng.choice(self.fault_code_by_ata[ata]))
                # "触发于"：故障模式 -> 工况
                if self.condition_ids:
                    self._add_relation(eid, "触发于", self.rng.choice(self.condition_ids))
                # "表现为" / "征兆为"在故障原因阶段反向写

        # 4) 故障原因（本体"故障原因"）：从 FAULT_MODES 抽取的"机理"层描述
        cause_categories = [
            "设计", "制造", "安装", "操作", "老化磨损", "维护不当",
            "外来物FOD", "软件逻辑", "外部环境", "不明",
        ]
        mechanism_pool = [
            "材料疲劳", "应力集中", "密封圈老化", "润滑油膜失效",
            "电弧烧蚀", "振动疲劳", "热循环损伤", "腐蚀坑扩展",
            "螺栓预紧力下降", "管路卡箍松脱", "线束磨损", "传感器漂移",
            "控制律偏差", "软件时序异常", "FOD 打伤", "鸟击冲击",
            "涂层剥落", "轴承保持架断裂", "齿轮啮合不良", "作动筒内泄",
        ]
        self.cause_ids: list[str] = []
        for mc in mechanism_pool:
            for ata in self.rng.sample([c for c, _, _ in ATA_CHAPTERS], k=4):
                eid = self._add_entity(
                    "故障原因", f"{mc}（ATA {ata}）",
                    description=f"{mc}（ATA {ata}）",
                    properties={
                        "name": mc,
                        "ata_chapter": ata,
                        "cause_category": self.rng.choice(cause_categories),
                    },
                )
                self.cause_ids.append(eid)
                # "归属系统"
                sys_candidates = self.system_by_ata.get(ata, [])
                if sys_candidates:
                    self._add_relation(eid, "归属系统", self.rng.choice(sys_candidates))
                # "征兆为" -> 故障征兆
                if self.symptom_ids:
                    self._add_relation(eid, "征兆为", self.rng.choice(self.symptom_ids))
                # "表现为" -> 故障模式（取该 ATA 章节下的故障模式，走预建索引 O(1)）
                fms_for_ata = self.fault_mode_by_ata.get(ata, [])
                if fms_for_ata:
                    self._add_relation(eid, "表现为", self.rng.choice(fms_for_ata))
                # "发生于" -> 该 ATA 章节的部件
                if ata in self.component_type_by_ata:
                    self._add_relation(eid, "发生于",
                                       self.rng.choice(self.component_type_by_ata[ata]))

        await self._flush_entities()

    async def build_resources(self):
        """厂商 / 人员 / 维修站 / 航材 / 工装工具 / 适航文件 / 手册 / MEL 保留。"""
        # 1) 厂商
        vendor_names = [
            "Boeing", "Airbus", "COMAC", "CFM International", "Pratt & Whitney",
            "General Electric", "Rolls-Royce", "Collins Aerospace", "Honeywell",
            "Safran", "Parker Hannifin", "Eaton", "Liebherr-Aerospace",
            "UTC Aerospace Systems", "Thales", "GKN Aerospace", "Spirit AeroSystems",
            "Meggitt", "Zodiac Aerospace", "Lycoming", "Williams International",
            "中国商飞", "中航工业", "中航材", "国航维修", "东航技术", "南航机务",
            "海航技术", "深航维修", "山航技术", "厦航维修", "川航维修",
            "国泰航空", "长荣航太", "新加坡科技工程", "汉莎技术", "法航工业",
            "阿联酋工程", "GE Aviation", "MTU航空发动机",
        ]
        self.vendor_ids = []
        for name in vendor_names[:VENDORS]:
            self.vendor_ids.append(self._add_entity(
                "厂商", name,
                description=f"{name}（行业主要供应商 / OEM / MRO）",
                properties={
                    "name": name,
                    "vendor_type": self.rng.choice(["OEM", "部件制造商", "MRO", "航材供应商", "租赁商"]),
                },
            ))

        # 2) 人员
        surnames = ["张", "王", "李", "刘", "陈", "杨", "黄", "赵", "吴", "周",
                    "徐", "孙", "马", "朱", "胡", "林", "何", "高", "罗", "郑"]
        given = ["伟", "磊", "勇", "军", "杰", "涛", "明", "超", "辉", "建国",
                 "建华", "志强", "国强", "海涛", "鹏程", "浩", "翔", "宇", "哲", "凯"]
        self.personnel_ids = []
        for i in range(PERSONNEL):
            name = f"{self.rng.choice(surnames)}{self.rng.choice(given)}"
            emp_no = f"MX{10000 + i}"
            eid = self._add_entity(
                "人员", f"{name}({emp_no})",
                description=f"机务 {name}（{emp_no}）",
                properties={
                    "employee_no": emp_no,
                    "name": name,
                    "license_type": self.rng.choice(["TA", "PA", "TR", "PR"]),
                    "type_ratings": self.rng.choice(["B737", "A320", "A330", "B777", "B787", "多机型"]),
                    "skill_level": self.rng.choice(["机械员", "技术员", "工程师", "放行工程师", "专家"]),
                },
            )
            self.personnel_ids.append(eid)

        # 3) 维修站
        base_stations = list(MAINTENANCE_STATIONS)
        extras = ["北京大兴", "上海虹桥", "沈阳桃仙", "厦门高崎", "武汉天河", "郑州新郑",
                 "乌鲁木齐", "拉萨贡嘎", "哈尔滨太平", "长沙黄花", "青岛胶东", "三亚凤凰"]
        all_stations = base_stations + extras
        self.station_ids = []
        for i, name in enumerate(all_stations[:STATIONS]):
            code = f"{self.rng.choice(['Z', 'B', 'P', 'X', 'C']) + self.rng.choice(['A', 'B', 'C'])}{self.rng.randint(0, 9)}{self.rng.randint(0, 9)}"
            eid = self._add_entity(
                "维修站", f"{name}-{code}",
                description=f"{name}（{code}）",
                properties={
                    "iata_code": code,
                    "name": name,
                    "station_type": self.rng.choice(["主基地", "分公司基地", "航站", "外站"]),
                    "aog_capable": 1 if self.rng.random() < 0.3 else 0,
                },
            )
            self.station_ids.append(eid)

        # 4) 航材（本体"航材"）—— 从部件清单派生，但加上库存属性
        self.spare_ids = []
        spare_ata_to_ct = {}
        for ct_eid in self.component_type_ids:
            ent = self._ent_by_id[ct_eid]
            props = json.loads(ent.properties)
            sp = props.get("part_number", "") + "-SP"
            spare_name = f"{props.get('name', ent.name)}/{sp}"
            spare_eid = self._add_entity(
                "航材", spare_name,
                description=f"{ent.name} 备件 {sp}",
                properties={
                    "part_number": sp,
                    "name": props.get("name", ent.name),
                    "ata_chapter": props.get("ata_chapter"),
                    "spare_type": self.rng.choice(["周转件", "消耗件", "时寿件", "标准件"]),
                    "stock": self.rng.randint(0, 12),
                    "stock_station": self.rng.choice(self.station_ids),
                    "interchange_code": self.rng.choice(["INTERCHANGEABLE", "REPLACEABLE", "ONE-WAY", "不可互换"]),
                    "aog_stock": 1 if self.rng.random() < 0.25 else 0,
                    "lead_time_days": self.rng.randint(1, 60),
                },
            )
            self.spare_ids.append(spare_eid)
            spare_ata_to_ct[ct_eid] = spare_eid
            # "适用于"：航材 -> 部件（"航材适用于部件"，CONSTRAINT 允许）
            self._add_relation(spare_eid, "适用于", ct_eid)
            # "供应自"：航材 -> 厂商
            if self.vendor_ids:
                self._add_relation(spare_eid, "供应自", self.rng.choice(self.vendor_ids))
            # "部件供应自厂商"
            if self.vendor_ids:
                self._add_relation(ct_eid, "供应自", self.rng.choice(self.vendor_ids))

        # 5) 工装工具（本体"工装工具"）
        self.tool_ids = []
        tool_templates = ["千斤顶", "顶块", "扭矩扳手", "液压泵车", "孔探仪",
                           "示波器", "数据下载器", "校验压力表", "NDT 探头组",
                           "维修支架", "系留绳", "拉拔器", "插头清洁工具"]
        for t in tool_templates:
            eid = self._add_entity(
                "工装工具", t,
                description=f"专用工装：{t}",
                properties={
                    "name": t,
                    "is_specialized": 1 if self.rng.random() < 0.6 else 0,
                    "calibration_valid_until": (
                        date.today() + timedelta(days=self.rng.randint(30, 720))
                    ).isoformat(),
                },
            )
            self.tool_ids.append(eid)

        # 6) 适航文件（本体"适航文件"）— AD / SB / SIL / EO
        self.ad_ids = []
        issuers = ["CAAC", "FAA", "EASA", "Boeing", "Airbus", "COMAC"]
        for i in range(AIRWORTHINESS_DOCS):
            dtype = self.rng.choice(["AD", "SB", "SIL", "EO", "服务信函"])
            ata = self.rng.choice([c for c, _, _ in ATA_CHAPTERS])
            year = self.rng.randint(2018, 2026)
            num = f"{dtype} {year}-{i:04d}"
            eid = self._add_entity(
                "适航文件", num,
                description=f"{dtype} {num}（ATA {ata}）",
                properties={
                    "doc_no": num,
                    "doc_type": dtype,
                    "title": f"{dtype} 修正/检查相关要求（ATA {ata}）",
                    "issuer": self.rng.choice(issuers),
                    "effective_date": (
                        date(year, self.rng.randint(1, 12), self.rng.randint(1, 28))
                    ).isoformat(),
                    "is_repetitive": 1 if dtype in ("AD", "SB") and self.rng.random() < 0.4 else 0,
                    "repetitive_interval": self.rng.choice(["100 FH", "500 FH", "3000 FC", "12 MO", "6 MO"]),
                    "has_terminating_action": 1 if self.rng.random() < 0.3 else 0,
                    "applicability": f"ATA {ata}",
                },
            )
            self.ad_ids.append(eid)
            # "受控于"：部件 -> 适航文件（CONSTRAINT 允许）
            if ata in self.component_type_by_ata:
                self._add_relation(self.rng.choice(self.component_type_by_ata[ata]),
                                   "受控于", eid)

        # 7) 手册（本体"手册"）—— AMM/FIM/TSM/IPC 各 ATA 章节 1~3 个手册章节节点
        self.manual_ids = []
        for ata_code, ata_eid in self.ata_ids.items():
            n = self.rng.randint(1, 3)
            for k in range(n):
                mtype = self.rng.choice(["AMM", "FIM", "TSM", "IPC", "SSM", "SWPM"])
                chap = f"{ata_code}-{self.rng.randint(10, 99)}-{self.rng.randint(10, 99)}"
                eid = self._add_entity(
                    "手册", f"{mtype} {chap}",
                    description=f"{mtype} 第 {chap} 章",
                    properties={
                        "manual_code": mtype,
                        "chapter_no": chap,
                        "title": f"{mtype} {chap} 任务说明",
                        "version": f"Rev {self.rng.randint(10, 60)}",
                        "effectivity": f"All {self.rng.choice(['737', 'A320', '787', 'A330'])}",
                    },
                )
                self.manual_ids.append(eid)
                # "覆盖" 手册 -> 系统（选个 ATA 章节下的子系统）
                sys_candidates = self.system_by_ata.get(ata_code, [])
                if sys_candidates:
                    self._add_relation(eid, "覆盖", self.rng.choice(sys_candidates))
                if self.component_type_by_ata.get(ata_code):
                    self._add_relation(eid, "覆盖", self.rng.choice(self.component_type_by_ata[ata_code]))
                # "隔离步骤" 手册 -> 维修措施
                if self.action_ids:
                    self._add_relation(eid, "隔离步骤", self.rng.choice(self.action_ids))
                # "对应故障" 手册 -> 故障代码（AT 章节匹配的）
                if ata_code in self.fault_code_by_ata:
                    self._add_relation(eid, "对应故障",
                                       self.rng.choice(self.fault_code_by_ata[ata_code]))

        # 8) MEL 保留
        self.mel_ids = []
        for i in range(MEL_DEFERRALS):
            item = f"{self.rng.randint(20, 99)}-{self.rng.randint(1, 99):02d}"
            cat = self.rng.choice(["A", "B", "C", "D"])
            mel_no = f"MEL-{100000 + i}"
            opened = date.today() - timedelta(days=self.rng.randint(1, 90))
            eid = self._add_entity(
                "MEL保留", mel_no,
                description=f"MEL 项目 {item}，{cat} 类",
                properties={
                    "deferral_no": mel_no,
                    "mel_item_no": item,
                    "category": cat,
                    "opened_at": opened.isoformat(),
                    "due_date": (opened + timedelta(days=int({"A": 3, "B": 10, "C": 120, "D": 240}[cat]))).isoformat(),
                    "approved_by": self.rng.choice(self.personnel_ids),
                    "status": self.rng.choice(["有效", "有效", "有效", "已关闭", "超期"]),
                    "reason": self.rng.choice(["航材缺料", "工具故障", "工时不足", "等备件中", "夜航保留"]),
                },
            )
            self.mel_ids.append(eid)
            # "依据保留"：MEL保留 -> 手册
            if self.manual_ids:
                self._add_relation(eid, "依据保留", self.rng.choice(self.manual_ids))
            # "保留涉及"：MEL保留 -> 部件
            if self.component_type_ids:
                self._add_relation(eid, "保留涉及", self.rng.choice(self.component_type_ids))

        await self._flush_entities()

    async def build_fleet(self):
        """机队（飞机实例 + 发动机实例 + 装机部件实例）。"""
        self.aircraft_ids: list[str] = []
        self.aircraft_meta: list[dict] = []
        self.engine_ids: list[str] = []
        self.component_instance_ids: list[str] = []
        # 预计算：ATA 72（发动机）章节下的系统节点，供发动机实例挂接
        self._sys72 = self.system_by_ata.get("72", [])
        self._wo_progress: set[str] = set()

        for i in range(self.fleet):
            model, maker, category, engine_opts = self.rng.choice(AIRCRAFT_TYPES)
            # 注册号使用 fleet 索引 + 机型代码前缀，保证唯一
            reg = f"B-{self.rng.randint(1000, 9999)}{i:03d}"
            msn = f"{10000 + i}"
            entry = date(2005, 1, 1) + timedelta(days=self.rng.randint(0, 6500))
            fh = int(self.rng.uniform(3000, 60000))
            fc = int(fh / self.rng.uniform(1.2, 2.6))

            # 航空器本体
            ac_eid = self._add_entity(
                "航空器", f"{reg}/{model}",
                description=f"{model}（{reg} / MSN {msn}），{maker} 制造",
                properties={
                    "registration": reg,
                    "msn": msn,
                    "aircraft_type": model,
                    "entry_date": entry.isoformat(),
                    "flight_hours": fh,
                    "flight_cycles": fc,
                    "status": self.rng.choice(["在役", "在役", "在役", "停场维修"]),
                },
            )
            self.aircraft_ids.append(ac_eid)
            # "属于机型"
            self._add_relation(ac_eid, "属于机型",
                               self.rng.choice(self.aircraft_type_ids))

            # 发动机实例（本体"部件"类型——这里给"部件"复用）
            for pos in ("左发", "右发"):
                emodel = self.rng.choice(engine_opts)
                esn = f"{emodel.split('-')[0]}{self.rng.randint(100000, 999999)}"
                eng_eid = self._add_entity(
                    "部件", f"{reg}-{pos} {emodel}",
                    description=f"{reg} 飞机{pos}发动机 {emodel}",
                    properties={
                        "part_number": emodel,
                        "name": f"发动机{pos}",
                        "ata_chapter": "72",
                        "manufacturer": emodel.split('-')[0],
                        "component_type": "LRU",
                        "is_life_limited": 1,
                        "life_limit": "20000 FC",
                        "installed_position": pos,
                    },
                )
                self.engine_ids.append(eng_eid)
                # 组成：发动机 -> 系统 ATA 72（预计算，避免循环内重复扫描）
                if self._sys72:
                    self._add_relation(eng_eid, "组成", self._sys72[0])

            # 装机部件实例（本体"部件"）
            for _ in range(COMPONENTS_PER_AIRCRAFT):
                ct_eid = self.rng.choice(self.component_type_ids)
                ct_name = self._ent_by_id[ct_eid].name
                csn = int(self.rng.uniform(0, max(fc, 1) * self.rng.uniform(0.3, 1.0)))
                tsn = int(csn * self.rng.uniform(1.5, 2.5))
                sn = f"SN{self.rng.randint(1000000, 9999999)}"
                cmp_eid = self._add_entity(
                    "部件", f"{reg}-{ct_name}-{sn}",
                    description=f"{reg} 装机部件 {ct_name}",
                    properties={
                        "part_number": ct_eid,
                        "name": f"{ct_name} 装机件",
                        "ata_chapter": "见件号",
                        "manufacturer": "原厂件",
                        "component_type": "LRU",
                        "is_life_limited": 0,
                        "installed_position": "装机",
                    },
                )
                self.component_instance_ids.append(cmp_eid)
                # 装机件 -> 部件类型（"组成" 部件 -> 部件 允许）
                self._add_relation(cmp_eid, "组成", ct_eid)

            if (i + 1) % 25 == 0:
                print(f"      机队进度 {i + 1}/{self.fleet} 架，实体 {self.stats['total_entities']:,}，"
                      f"关系 {self.stats['total_relations']:,}", flush=True)

        await self._flush_entities()

    async def build_work_orders(self):
        """维修工单（连接飞机、故障模式、措施、部件、人员、站点、保留）。"""
        start_date = date.today() - timedelta(days=HISTORY_DAYS)
        wo_count = 0
        for ac_eid in self.aircraft_ids:
            ac_props = json.loads(self._ent_by_id[ac_eid].properties)
            total_wo = WORK_ORDERS_PER_AIRCRAFT
            for seq in range(total_wo):
                wo_count += 1
                wo_no = f"WO-{wo_count}"
                day_offset = int(self.rng.triangular(0, HISTORY_DAYS, HISTORY_DAYS))
                opened = datetime.combine(
                    start_date + timedelta(days=day_offset),
                    datetime.min.time().replace(hour=self.rng.randint(0, 23)),
                )
                # 故障模式（按 ATA 章节任选）
                fm_eid = self.rng.choice(self.fault_mode_ids) if self.fault_mode_ids else None
                # 维修措施
                ac_eid_choice = self.rng.choice(self.action_ids) if self.action_ids else None
                # 站点 + 人员
                station = self.rng.choice(self.station_ids) if self.station_ids else None
                person = self.rng.choice(self.personnel_ids) if self.personnel_ids else None
                # ATA 章节
                ata_code = None
                if fm_eid:
                    ata_code = json.loads(self._ent_by_id[fm_eid].properties).get("ata_chapter")

                is_nff = self.rng.random() < NFF_RATE
                closed = opened + timedelta(hours=self.rng.uniform(1, 24))
                wo_eid = self._add_entity(
                    "维修工单", wo_no,
                    description=f"{wo_no} 排故记录",
                    properties={
                        "workorder_no": wo_no,
                        "nrc_no": f"NRC{wo_count:08d}",
                        "opened_at": opened.isoformat(),
                        "closed_at": closed.isoformat(),
                        "ata_chapter": ata_code or "",
                        "result": "已修复" if not is_nff else "NFF无故障发现",
                        "is_repeat": 1 if self.rng.random() < 0.15 else 0,
                        "verification": self.rng.choice(["地面测试", "试车", "试飞", "目视检查", "无"]),
                        "downtime_hours": round(self.rng.uniform(0.5, 18), 1),
                        "repair_hours": round(self.rng.uniform(0.3, 6), 1),
                        "flight_hours": int(ac_props.get("flight_hours", 0) * self.rng.uniform(0.5, 1.0)),
                        "flight_cycles": int(ac_props.get("flight_cycles", 0) * self.rng.uniform(0.5, 1.0)),
                    },
                )

                # 涉及故障：工单 -> 故障模式
                if fm_eid:
                    self._add_relation(wo_eid, "涉及故障", fm_eid)
                # 采取措施：工单 -> 维修措施
                if ac_eid_choice:
                    self._add_relation(wo_eid, "采取措施", ac_eid_choice)
                # 发生于航空器
                self._add_relation(wo_eid, "发生于航空器", ac_eid)
                # 处理于（人员）
                if person:
                    self._add_relation(wo_eid, "处理于", person)
                # 执行于（站点）
                if station:
                    self._add_relation(wo_eid, "执行于", station)

                # 涉及部件：工单 -> 此飞机的某一装机部件
                if self.component_instance_ids and self.rng.random() < 0.85:
                    # 简化：用统一随机部件
                    self._add_relation(wo_eid, "涉及部件",
                                       self.rng.choice(self.component_instance_ids))

                # 引用手册：工单 -> 手册（通过"采取措施"-> 维修措施，再"引用" 手册；
                # 工单本体不在 CONSTRAINT 允许源里，所以省略直接 edge）
                # 依据适航文件：同上省略
                # 产生保留：工单 -> MEL保留（仅当 is_nff 为 False 且结果为保留时）
                if (not is_nff) and (self.rng.random() < 0.05) and self.mel_ids:
                    self._add_relation(wo_eid, "产生保留", self.rng.choice(self.mel_ids))

            ac_idx = len(self._wo_progress)
            self._wo_progress.add(ac_eid)
            if (ac_idx + 1) % 25 == 0:
                print(f"      工单进度 {ac_idx + 1}/{len(self.aircraft_ids)} 架，"
                      f"实体 {self.stats['total_entities']:,}，关系 {self.stats['total_relations']:,}",
                      flush=True)

        await self._flush_entities()

    async def build_monitor_links(self):
        """为已生成的部件类型补上 "监测于" 关系（部件 -> 监控参数）。"""
        # 缓存：ata 章节 -> 该章节下所有监控参数 id（走本体索引，避免全表扫描）
        ata_param_ids_by_ata: dict[str, list[str]] = {}
        for eid in self.eid_by_ont.get("监控参数", []):
            nm = self._ent_by_id[eid].name  # 形如 "EGT-ATA29"
            if "-ATA" in nm:
                ata = nm.split("-ATA")[-1]
                ata_param_ids_by_ata.setdefault(ata, []).append(eid)

        for ata_code, ct_ids in self.component_type_by_ata.items():
            ata_param_ids = ata_param_ids_by_ata.get(ata_code, [])
            if not ata_param_ids:
                continue
            for ct in ct_ids:
                n = self.rng.randint(1, 3)
                for _ in range(n):
                    self._add_relation(ct, "监测于", self.rng.choice(ata_param_ids))
        await self._flush_entities()

    async def finalize(self):
        # 强制把剩余缓存刷盘（虽然前几步已 flush，但保险起见）
        if self.entities:
            await self._flush_entities()
        if self.relations:
            await self._flush_relations()
        await self.db.commit()


# 全局（仅供 build_types_layer 用）— 避免在 hot loop 内反复 dict 构造
ATA_CHAPTER_DICT = {code: (zh, en) for code, zh, en in ATA_CHAPTERS}


# ──────────────────────────── 主流程 ────────────────────────────

async def ensure_seed_kb(db: AsyncSession) -> str:
    """取出（或新建）种子专用的 KnowledgeBase，返回 kb_id。"""
    row = (await db.execute(
        select(KnowledgeBase).where(KnowledgeBase.name == SEED_KB_NAME)
    )).scalar_one_or_none()
    if row:
        return row.id
    kb = KnowledgeBase(
        id=_gen_id(),
        name=SEED_KB_NAME,
        description=SEED_KB_DESC,
    )
    db.add(kb)
    await db.flush()
    await db.refresh(kb)
    return kb.id


async def load_ontology_index(db: AsyncSession) -> tuple[dict[str, str], dict[str, str]]:
    """查回 (本体名 -> ontology_id) 与 (关系名 -> relation_id) 索引。"""
    cat = (await db.execute(
        select(OntologyCategory).where(OntologyCategory.name == CATEGORY_NAME)
    )).scalar_one_or_none()
    if not cat:
        raise RuntimeError(
            f"本体类别「{CATEGORY_NAME}」不存在，请先执行：python scripts/seed_aviation_ontology.py --force"
        )

    onto_rows = (await db.execute(
        select(Ontology).where(Ontology.category_id == cat.id)
    )).scalars().all()
    ont_id_by_name: dict[str, str] = {}
    for o in onto_rows:
        ont_id_by_name[o.name] = o.id
    expected = {o["name"] for o in ONTOLOGIES}
    missing = expected - set(ont_id_by_name)
    if missing:
        raise RuntimeError(f"本体缺失（请重新执行 seed_aviation_ontology.py）：{sorted(missing)}")

    rel_rows = (await db.execute(
        select(OntologyRelation).where(OntologyRelation.category_id == cat.id)
    )).scalars().all()
    rel_id_by_name: dict[str, str] = {r.name: r.id for r in rel_rows}
    expected_rel = {r["name"] for r in RELATIONS}
    missing_rel = expected_rel - set(rel_id_by_name)
    if missing_rel:
        raise RuntimeError(f"关系缺失：{sorted(missing_rel)}")

    return ont_id_by_name, rel_id_by_name


async def seed(force: bool = False, fleet: int = FLEET_SIZE_DEFAULT, seed: int = 42) -> None:
    await init_db()

    async with async_session() as db:
        ont_id_by_name, rel_id_by_name = await load_ontology_index(db)
        kb_id = await ensure_seed_kb(db)
        await db.commit()
        print(f"[0/6] 种子 KB：{SEED_KB_NAME}（id={kb_id}）")

        # 清理
        if force:
            print("[1/6] 清理旧 Entity / Relation ...")
            ec, rc = await _purge_old_seed_entities(db, kb_id)
            await db.commit()
            print(f"      删除 Entity {ec} 条，Relation {rc} 条")

        factory = EntityFactory(db, kb_id, ont_id_by_name, rel_id_by_name, fleet, seed)

        print("[2/6] 生成类级层（机型 / 系统 / 故障 / 措施 / 征兆 / 工况 / 参数） ...")
        await factory.build_types_layer()

        print("[3/6] 生成件号层（部件 / 故障代码 / 故障模式 / 故障原因） ...")
        await factory.build_component_types()

        print("[4/6] 生成资源层（厂商 / 人员 / 维修站 / 航材 / 工装 / 手册 / 适航文件 / MEL） ...")
        await factory.build_resources()

        print(f"[5/6] 生成机队（{fleet} 架 + 装机部件 + 维修工单） ...")
        await factory.build_fleet()
        await factory.build_work_orders()
        await factory.build_monitor_links()

        print("[6/6] 提交 ...")
        await factory.finalize()

        info = factory.stats
        print()
        print("=" * 68)
        print("民航维修领域真实实体生成完毕")
        print("=" * 68)
        print(f"知识库      ：{SEED_KB_NAME}（{kb_id}）")
        print(f"实体总数    ：{info['total_entities']:,}")
        print(f"关系总数    ：{info['total_relations']:,}")
        print(f"非法三元组  ：{info['skipped_invalid_triples']:,}")
        print(f"重复边去重  ：{info.get('skipped_dup', 0):,}")
        print("-" * 68)
        print("实体按本体分布：")
        for k, v in sorted(info["by_ontology"].items(), key=lambda kv: -kv[1]):
            print(f"  {k:<24} {v:>8,}")
        print("=" * 68)


def main() -> None:
    ap = argparse.ArgumentParser(description="民航维修领域真实实体 / 关系生成")
    ap.add_argument("--force", action="store_true", help="先清空专用 KB 内的旧 Entity/Relation 再写")
    ap.add_argument("--fleet", type=int, default=FLEET_SIZE_DEFAULT, help=f"机队规模（默认 {FLEET_SIZE_DEFAULT}）")
    ap.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    args = ap.parse_args()

    asyncio.run(seed(force=args.force, fleet=args.fleet, seed=args.seed))


if __name__ == "__main__":
    main()
