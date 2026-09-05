# -*- coding: utf-8 -*-
"""清理历史低价值实体（与业务过滤规则同口径），同步清理 PG 与 Neo4j。

用法：
  python -X utf8 scripts/cleanup_low_value_entities.py          # 只读统计
  python -X utf8 scripts/cleanup_low_value_entities.py --purge  # 执行清理
"""
import asyncio
import re
import sys

sys.path.insert(0, ".")

from sqlalchemy import text

from database import async_session

# 与 services/graph_extraction_service._LOW_VALUE_ENTITY_PATTERNS 同口径
_PATTERNS = [
    ("日期", re.compile(r"^\d{4}\s*年(\s*$|\s*\d)")),
    ("日期", re.compile(r"^\d{1,2}\s*月(\s*\d{1,2}\s*日?)?\s*$")),
    ("日期", re.compile(r"^\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}")),
    ("日期", re.compile(r"^\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日?$")),
    ("数值", re.compile(r"^[\d.,]+\s*(辆|个|人|万|亿|%|元|台|次|岁|分|秒|美元|公里|千米|米)?\s*$")),
    ("URL", re.compile(r"^https?://", re.IGNORECASE)),
    ("版本号", re.compile(r"^v?\d+(\.\d+)+$", re.IGNORECASE)),
]


def hit_reason(name: str) -> str | None:
    name = (name or "").strip()
    if not name:
        return "空名"
    if len(name) > 24:
        return "过长"
    if re.search(r"[，。；,;！!?？]", name):
        return "含断句标点"
    for label, pat in _PATTERNS:
        if pat.search(name):
            return label
    return None


async def collect_ids() -> tuple[list[str], dict]:
    async with async_session() as s:
        rows = (await s.execute(text(
            "SELECT id, name, entity_type, COALESCE(NULLIF(kb_id, ''), '<空>') AS kb FROM entities"
        ))).fetchall()
        ids, reasons, by_type, by_kb, samples = [], {}, {}, {}, {}
        for r in rows:
            why = hit_reason(r.name)
            if not why:
                continue
            # "过长/含断句标点"多为语义完整的任务标题（种子图主体），仅统计不删；
            # 只清理无争议硬噪声：日期/数值/URL/版本号/空名
            if why in ("数值", "日期", "URL", "版本号", "空名"):
                ids.append(r.id)
            reasons[why] = reasons.get(why, 0) + 1
            by_type[r.entity_type] = by_type.get(r.entity_type, 0) + 1
            by_kb[r.kb] = by_kb.get(r.kb, 0) + 1
            samples.setdefault(why, []).append(r.name)
        return ids, {"reasons": reasons, "by_type": by_type, "by_kb": by_kb, "samples": samples}


async def purge(ids: list[str]):
    from providers.graph_store import _get_adapter

    # 1) PG：先删关联关系，再删实体
    async with async_session() as s:
        del_rel = 0
        for i in range(0, len(ids), 5000):
            batch = ids[i:i + 5000]
            del_rel += (await s.execute(text(
                "DELETE FROM relations WHERE source_entity_id = ANY(:ids) "
                "OR target_entity_id = ANY(:ids)"
            ), {"ids": batch})).rowcount
        del_ent = 0
        for i in range(0, len(ids), 5000):
            batch = ids[i:i + 5000]
            del_ent += (await s.execute(text(
                "DELETE FROM entities WHERE id = ANY(:ids)"
            ), {"ids": batch})).rowcount
        await s.commit()
    print(f"PG 已删除: 实体 {del_ent}, 关系 {del_rel}")

    # 2) Neo4j：按 PG id 分批 DETACH DELETE（种子图双标签节点同样带该 id）
    adapter = _get_adapter()
    node_del, rel_del = 0, 0
    for i in range(0, len(ids), 1000):
        batch = ids[i:i + 1000]
        result = adapter._execute(
            "UNWIND $ids AS eid MATCH (e:Entity {id: eid}) "
            "DETACH DELETE e RETURN count(e) AS c",
            {"ids": batch},
        )
        records = getattr(result, "records", result)
        node_del += sum(r["c"] for r in records)
        if (i // 1000) % 20 == 0:
            print(f"  Neo4j 已处理 {min(i + 1000, len(ids)):,}/{len(ids):,}", flush=True)
    print(f"Neo4j 已删除节点(Entity): {node_del}（含其全部关系）")


async def main():
    purge_mode = "--purge" in sys.argv
    ids, stats = await collect_ids()
    print(f"命中低价值规则实体: {len(ids):,} 个\n")
    print("按命中原因:", stats["reasons"])
    print("按类型(top10):", dict(sorted(stats["by_type"].items(), key=lambda x: -x[1])[:10]))
    print("按kb:", stats["by_kb"])
    for why, names in stats["samples"].items():
        print(f"样例[{why}]: {names[:5]}")

    if not purge_mode:
        print("\n[只读统计] 加 --purge 执行清理")
        return
    if not ids:
        print("无可清理数据")
        return
    await purge(ids)

    # 验证
    async with async_session() as s:
        total = (await s.execute(text("SELECT count(*) FROM entities"))).scalar()
        remain = 0
        rows = (await s.execute(text("SELECT name FROM entities"))).fetchall()
        remain = sum(1 for r in rows if hit_reason(r.name))
        print(f"\n验证: entities 总数 {total:,}, 残留低价值 {remain}")
    from providers.graph_store import _get_adapter
    adapter_result = _get_adapter()._execute("MATCH (e:Entity) RETURN count(e) AS c", {})
    rec = list(getattr(adapter_result, "records", adapter_result))
    print(f"验证: Neo4j Entity 节点剩余 {rec[0]['c']:,}")


asyncio.run(main())
