"""schema_store：本体驱动的 Schema 图谱装载与子图检索（NL2SQL 第一级取数链）。

方案：doc/智能体/多智能体/多智能体DataAgent·本体驱动NL2SQL.md §2~§3。

建模约定（复用本体四层模型，migration_042 扩展列）：
- 数据源   = OntologyCategory（datasource_dialect 非空 = 数据源类别；dsn=独立业务实例连接串）
- 数据表   = Ontology（code=物理表名，alias=检索别名）
- 字段     = OntologyAttribute（code=物理列名，enum_values=值白名单，is_required=1 → 非空）
- 表关系   = OntologyRelation + OntologyRelationConstraint（join_condition=字段映射 JSON）

检索链：NL → 词典种子命中（match_seeds）→ BFS 子图扩展（expand_subgraph）→
prompt 上下文（render_context）。词典 = 表/字段/关系的中文名 + 别名 + code +
枚举值，全量常驻内存按词长优先子串扫描（表数量小，无需向量检索）。

失败语义：无数据源类别 / 检索 0 命中 → 返回 None，调用方回落 NL2Filter 老路
（与 OTel/图表 MCP 的「缺失自动降级」同风格，绝不抛异常阻塞流水线）。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Ontology,
    OntologyAttribute,
    OntologyCategory,
    OntologyRelation,
    OntologyRelationConstraint,
)

logger = logging.getLogger(__name__)


def _edge_cardinality(source_max: object, target_max: object) -> str:
    """约束端点上限 → 边基数展示（source→target 视角）。

    两端限 1 → 1:1；target 限 1 → N:1；其余（含未配置 0=不限）→ 1:N（保守翻倍防护）。
    """
    smax = int(source_max or 0)
    tmax = int(target_max or 0)
    if smax == 1 and tmax == 1:
        return "1:1"
    return "N:1" if tmax == 1 else "1:N"


MAX_EXPAND_TABLES = 6     # 单跳扇出超过该值时按相关度剪枝（方案 §3.2）
MAX_HOPS = 2              # 子图扩展跳数
DIALECT_LABEL = {         # render_context 的方言标注
    "postgres": "PostgreSQL",
    "sqlite": "SQLite",
    "mysql": "MySQL",
}

# data_type（本体属性类型）→ render 展示的 SQL 类型风格
_TYPE_LABEL = {
    "string": "TEXT", "text": "TEXT", "date": "DATE",
    "datetime": "TIMESTAMP", "timestamp": "TIMESTAMP",
    "number": "DECIMAL", "decimal": "DECIMAL", "float": "DECIMAL",
    "integer": "INTEGER", "int": "INTEGER", "boolean": "INTEGER", "bool": "INTEGER",
}


# ────────────────────────── 数据结构（方案 §3.1） ──────────────────────────

@dataclass
class SchemaColumn:
    code: str
    name: str
    data_type: str
    alias: str = ""
    description: str = ""
    enum_values: list = field(default_factory=list)
    unit: str = ""
    format: str = ""
    required: bool = False          # is_required=1 → 非空列


@dataclass
class SchemaTable:
    code: str                       # 物理表名
    name: str                       # 中文表名
    display_name: str = ""
    alias: str = ""                 # 逗号分隔检索别名
    description: str = ""
    columns: list = field(default_factory=list)   # list[SchemaColumn]

    def col(self, code: str) -> Optional[SchemaColumn]:
        for c in self.columns:
            if c.code == code:
                return c
        return None


@dataclass
class SchemaEdge:
    relation_name: str
    relation_alias: str = ""
    inverse_name: str = ""
    source_table: str = ""          # 本体 code
    target_table: str = ""          # 本体 code
    join: list = field(default_factory=list)      # [{"left": "flight_no", "right": "flight_no"}]
    cardinality: str = "1:N"        # source→target 视角

    def other(self, table: str) -> str:
        return self.target_table if table == self.source_table else self.source_table


@dataclass
class SchemaGraph:
    category_id: str
    category_name: str
    datasource_dialect: str
    datasource_dsn: str
    tables: dict = field(default_factory=dict)    # code → SchemaTable
    edges: list = field(default_factory=list)     # list[SchemaEdge]


@dataclass
class SchemaSubGraph:
    graph: SchemaGraph
    tables: list                    # list[SchemaTable]（种子在前，低置信在尾）
    edges: list                     # list[SchemaEdge]
    seed_tables: list               # 种子表 code
    matched_terms: list             # 命中词
    low_confidence: list            # 仅 description 弱命中的表 code


# ────────────────────────── 图谱装载 ──────────────────────────

def _split_alias(raw: str) -> list:
    """逗号分隔别名串 → 词列表（去空、小写）。"""
    if not raw:
        return []
    return [w.strip().lower() for w in re.split(r"[,，、;；]", raw) if w.strip()]


def _parse_enum(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw if str(v).strip()]
    try:
        val = json.loads(raw)
        return [str(v) for v in val] if isinstance(val, list) else []
    except Exception:
        return []


def _parse_join(raw: str) -> list:
    if not raw:
        return []
    try:
        val = json.loads(raw)
        if isinstance(val, list):
            return [item for item in val
                    if isinstance(item, dict) and item.get("left") and item.get("right")]
    except Exception:
        pass
    return []


async def load_schema_graph(db: AsyncSession, cat: OntologyCategory) -> SchemaGraph:
    """装载一个数据源类别的完整 schema 图（表级批量查询，量小常驻一轮）。"""
    graph = SchemaGraph(
        category_id=cat.id, category_name=cat.name,
        datasource_dialect=(cat.datasource_dialect or "").strip(),
        datasource_dsn=(cat.datasource_dsn or "").strip(),
    )

    ont_rows = (await db.execute(
        select(Ontology).where(Ontology.category_id == cat.id)
    )).scalars().all()
    if not ont_rows:
        return graph
    id2table = {o.id: o for o in ont_rows}

    # 字段：单次查询内存分组
    attr_rows = (await db.execute(
        select(OntologyAttribute).where(OntologyAttribute.ontology_id.in_(id2table.keys()))
    )).scalars().all()
    attrs_by_ont: dict = {}
    for a in attr_rows:
        attrs_by_ont.setdefault(a.ontology_id, []).append(a)

    for o in ont_rows:
        cols = [
            SchemaColumn(
                code=(a.code or "").strip(), name=a.name,
                data_type=(a.data_type or "string").strip(),
                alias=a.alias or "", description=a.description or "",
                enum_values=_parse_enum(a.enum_values),
                unit=a.unit or "", format=a.format or "",
                required=bool(a.is_required),
            )
            for a in sorted(attrs_by_ont.get(o.id, []), key=lambda x: x.sort_order)
            if (a.code or "").strip()
        ]
        graph.tables[(o.code or "").strip()] = SchemaTable(
            code=(o.code or "").strip(), name=o.name,
            display_name=o.display_name or o.name,
            alias=o.alias or "", description=o.description or "",
            columns=cols,
        )

    # 关系边：constraint 决定具体两表，relation 提供业务命名
    rel_rows = (await db.execute(
        select(OntologyRelation).where(OntologyRelation.category_id == cat.id)
    )).scalars().all()
    rel_by_id = {r.id: r for r in rel_rows}
    cons = (await db.execute(
        select(OntologyRelationConstraint)
        .where(OntologyRelationConstraint.category_id == cat.id)
    )).scalars().all()

    for c in cons:
        rel = rel_by_id.get(c.relation_id)
        src = id2table.get(c.source_ontology_id)
        tgt = id2table.get(c.target_ontology_id)
        if not rel or not src or not tgt:
            continue
        src_code, tgt_code = (src.code or "").strip(), (tgt.code or "").strip()
        if src_code not in graph.tables or tgt_code not in graph.tables:
            continue
        join = _parse_join(c.join_condition)
        if not join:      # 无 join 映射的边不可用于 SQL 拼装，跳过
            continue
        graph.edges.append(SchemaEdge(
            relation_name=rel.name,
            relation_alias=rel.alias or "",
            inverse_name=rel.inverse_name or "",
            source_table=src_code, target_table=tgt_code,
            join=join,
            # 基数判读（方案 §2.2，与约束层 source_max/target_max 对齐）：
            # 两端限 1 → 1:1；target 限 1 → N:1；其余（含未配置）→ 1:N（保守翻倍防护）
            cardinality=_edge_cardinality(c.source_max, c.target_max),
        ))
    return graph


async def find_datasource_graphs(db: AsyncSession) -> list:
    """全部启用 NL2SQL 的数据源类别（dialect 非空），各装载成 SchemaGraph。"""
    cats = (await db.execute(
        select(OntologyCategory).where(OntologyCategory.datasource_dialect != "")
        .where(OntologyCategory.datasource_dialect.isnot(None))
    )).scalars().all()
    graphs = []
    for cat in cats:
        try:
            graphs.append(await load_schema_graph(db, cat))
        except Exception:
            logger.exception("[NL2SQL][schema_store] 类别 %s 装载失败，跳过", cat.name)
    return graphs


# ────────────────────────── 种子命中与子图扩展（方案 §3.2） ──────────────────────────

@dataclass
class _Vocab:
    """检索词典：词 → 命中目标。词源 = 表名/显示名/别名/code + 字段名/别名/code +
    枚举值 + 关系名/别名/反向名（全小写）。"""

    tables: dict = field(default_factory=dict)     # 词 → set[table_code]（强命中：名/别名/code）
    weak_tables: dict = field(default_factory=dict)  # 词 → set[table_code]（弱命中：仅 description）
    attrs: dict = field(default_factory=dict)      # 词 → set[(table_code, attr_code)]
    edges: dict = field(default_factory=dict)      # 词 → set[edge_index]

    def add(self, vocab: dict, word: str, target):
        word = word.strip().lower()
        if len(word) >= 2:
            vocab.setdefault(word, set()).add(target)


def _build_vocab(graph: SchemaGraph) -> _Vocab:
    v = _Vocab()
    for t in graph.tables.values():
        v.add(v.tables, t.code, t.code)
        v.add(v.tables, t.name, t.code)
        v.add(v.tables, t.display_name, t.code)
        for w in _split_alias(t.alias):
            v.add(v.tables, w, t.code)
        for w in set(re.findall(r"[\u4e00-\u9fa5A-Za-z]{2,}", t.description or "")):
            v.weak_tables.setdefault(w.lower(), set()).add(t.code)
        for c in t.columns:
            key = (t.code, c.code)
            v.add(v.attrs, c.code, key)
            v.add(v.attrs, c.name, key)
            for w in _split_alias(c.alias):
                v.add(v.attrs, w, key)
            for ev in c.enum_values:            # 枚举值入典：「机长」→ crew 域
                v.add(v.attrs, ev, key)
    for i, e in enumerate(graph.edges):
        v.add(v.edges, e.relation_name, i)
        for w in _split_alias(e.relation_alias):
            v.add(v.edges, w, i)
        v.add(v.edges, e.inverse_name, i)
    return v


def _match_seeds(graph: SchemaGraph, task: str) -> tuple:
    """词典子串扫描（词长优先）：返回 (seed_codes, seed_edge_idx, matched_terms)。"""
    v = _build_vocab(graph)
    task_l = task.lower()

    def _hits(vocab: dict) -> set:
        out = set()
        for word in sorted(vocab, key=len, reverse=True):
            if word in task_l:
                out |= vocab[word]
        return out

    seeds = {t for t in (_hits(v.tables) | {tc for tc, _ in _hits(v.attrs)})}
    seed_edges = _hits(v.edges)
    attr_hits = _hits(v.attrs)
    # 关系边命中 → 两端表都是种子
    for ei in seed_edges:
        e = graph.edges[ei]
        seeds.add(e.source_table)
        seeds.add(e.target_table)
    # 字段枚举/别名命中 → 所在表是种子（跨 2 跳拿到「会员等级」→ members）
    weak = {tc for w, tcs in v.weak_tables.items() if w in task_l for tc in tcs}

    terms = sorted(
        {w for w in v.tables if w in task_l}
        | {w for w in v.attrs if w in task_l}
        | {w for w in v.edges if w in task_l}
    )
    return seeds, seed_edges, terms, attr_hits, weak


def _relevance(table: SchemaTable, terms: list) -> int:
    """表与命中词的相关度：description/列名再命中计数（剪枝排序用）。"""
    if not terms:
        return 0
    text = (table.description or "").lower() + " " + table.name.lower() \
        + " " + " ".join(c.name.lower() + " " + c.code.lower() for c in table.columns)
    return sum(1 for w in terms if w in text)


def expand_subgraph(graph: SchemaGraph, task: str) -> Optional[SchemaSubGraph]:
    """种子命中 + BFS 子图扩展 → 最小连通子图（单表命中也放行：单表问题免 join）。"""
    seeds, seed_edges, terms, _attr_hits, weak = _match_seeds(graph, task)
    if not seeds:
        return None

    keep_tables = set(seeds)
    keep_edges = set(seed_edges)
    # 种子边强制纳入：沿边把对端表拉进子图（保证检索到的 join 关系可落地）
    frontier = set(seeds)
    for _hop in range(MAX_HOPS):
        nxt: set = set()
        for tc in frontier:
            for i, e in enumerate(graph.edges):
                if e.source_table not in graph.tables or e.target_table not in graph.tables:
                    continue
                if tc in (e.source_table, e.target_table):
                    other = e.other(tc)
                    if other not in keep_tables:
                        # 扇出剪枝：该表一跳邻居超限时按相关度取舍
                        nbrs = [x.other(tc) for x in graph.edges
                                if tc in (x.source_table, x.target_table)]
                        if len(nbrs) > MAX_EXPAND_TABLES:
                            ranked = sorted(
                                set(nbrs),
                                key=lambda c: _relevance(graph.tables[c], terms),
                                reverse=True)[:MAX_EXPAND_TABLES]
                            if other not in ranked:
                                continue
                        keep_tables.add(other)
                        nxt.add(other)
                    keep_edges.add(i)
        frontier = nxt
        if not frontier:
            break

    # 种子边若因剪枝丢失对端表，回补（检索语义优先于规模）
    for ei in list(keep_edges):
        e = graph.edges[ei]
        keep_tables.add(e.source_table)
        keep_tables.add(e.target_table)

    low = sorted(seeds & weak - set()) if weak else []
    # 孤表裁剪：与命中词零关联的中间表放低置信尾区（不淘汰）
    core = [c for c in keep_tables if c in seeds or c in weak]
    order = core + sorted(keep_tables - set(core), key=lambda c: _relevance(graph.tables[c], terms))
    return SchemaSubGraph(
        graph=graph,
        tables=[graph.tables[c] for c in order if c in graph.tables],
        edges=[graph.edges[i] for i in sorted(keep_edges)],
        seed_tables=sorted(seeds),
        matched_terms=terms,
        low_confidence=[c for c in order if c not in core and c in weak] or low,
    )


# ────────────────────────── render_context（方案 §3.3） ──────────────────────────

def _col_text(c: SchemaColumn) -> str:
    parts = [_TYPE_LABEL.get(c.data_type.lower(), "TEXT")]
    note = c.name
    if c.enum_values:
        note += f"，取值: {'/'.join(c.enum_values)}"
    if c.description and c.description not in note:
        note += f"({c.description})"
    if not c.required:
        note += "，可空"
    if c.unit:
        note += f"，单位: {c.unit}"
    return f"{parts[0]}({note})"


def render_context(sub: SchemaSubGraph) -> str:
    """子图 → 注入 prompt 的 schema 上下文（DDL 片段 + ON 条件 + 基数 + 红线）。"""
    g = sub.graph
    label = DIALECT_LABEL.get(g.datasource_dialect, g.datasource_dialect or "SQL")
    lines = [f"【可用表】（{label} 方言）"]
    for t in sub.tables:
        cols = ",\n  ".join(f"{c.code} {_col_text(c)}" for c in t.columns)
        lines.append(f"TABLE {t.code} {t.display_name}: {cols}" if t.columns
                     else f"TABLE {t.code} {t.display_name}")
    if sub.low_confidence:
        lines.append(f"【低置信候选表】仅描述弱命中，无必要勿用：{', '.join(sub.low_confidence)}")

    lines.append("")
    lines.append("【表关系】（join 时必须使用以下 ON 条件）")
    for e in sub.edges:
        ons = " AND ".join(
            f"{e.source_table}.{j['left']} = {e.target_table}.{j['right']}" for j in e.join)
        lines.append(f"- {e.source_table} →[{e.relation_name}] {e.target_table}，基数 {e.cardinality}")
        lines.append(f"  ON {ons}")

    lines.append("【注意】① 「可空」标注的外键列所在边取「全部主表行」必须 LEFT JOIN，"
                 "INNER JOIN 会静默丢行；② 1:N 边 join 后对主表字段 SUM/COUNT 必须先按主键"
                 "去重或先子查询聚合，否则统计翻倍；③ 多路径可达时以问题主语命中的种子表为起点"
                 "选唯一边链，禁止同链重复 join 同一条边；④ 过滤值必须来自「取值」枚举或问题原词，"
                 "禁止编造。")
    return "\n".join(lines)


# ────────────────────────── 顶层入口 ──────────────────────────

async def prepare_nl2sql(task: str) -> Optional[dict]:
    """NL2SQL 检索总入口：找数据源类别 → 种子命中 → 子图 → prompt 上下文。

    返回 {"graph", "sub", "ctx", "dialect", "dsn"}；无类别/0 命中返回 None
    （调用方回落 NL2Filter 老路）。多个数据源类别时取命中种子最多的一个。
    """
    if not task or not task.strip():
        return None
    try:
        from database import async_session
        async with async_session() as db:
            graphs: list[Any] = await find_datasource_graphs(db)
    except Exception:
        logger.exception("[NL2SQL][schema_store] 图谱装载失败，回落老链")
        return None
    if not graphs:
        return None

    best: Optional[SchemaSubGraph] = None
    for g in graphs:
        sub = expand_subgraph(g, task)
        if sub is None:
            continue
        score = len(sub.seed_tables) * 2 + len(sub.matched_terms)
        if best is None or score > best._score:
            best = sub
            best._score = score          # type: ignore[attr-defined]
    if best is None:
        return None

    return {
        "graph": best.graph,
        "sub": best,
        "ctx": render_context(best),
        "dialect": best.graph.datasource_dialect,
        "dsn": best.graph.datasource_dsn,
        "tables": [t.code for t in best.tables],
    }
