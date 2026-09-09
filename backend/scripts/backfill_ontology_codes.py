"""存量数据编码回填：Ontology.code / OntologyRelation.code + 分析图全量重迁。

背景：图库节点标签 / 关系类型已改为使用稳定编码（Ontology.code、OntologyRelation.code），
存量本体/关系定义大多没有编码，本脚本补齐后重建 Neo4j 分析图，使标签全部为编码。

策略：
  1. 映射表直接取自两个种子域模块（flight_ops_domain.ONTOLOGIES/RELATIONS、
     seed_aviation_ontology.ONTOLOGIES/RELATIONS）——按 name 匹配回填；
  2. 映射未命中的：本体回填 ``ONT_<ID大写>``、关系定义回填 ``REL_<ID大写>``（保证唯一）；
  3. 已有编码的一律不动；
  4. ``--resync``：对全部本体类别执行一次全量图迁入（迁入自带按类别清图重建）。

用法（在 backend 目录下）：
    python scripts/backfill_ontology_codes.py            # 只回填 PG 编码
    python scripts/backfill_ontology_codes.py --resync   # 回填后全量重建 Neo4j 分析图
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from database import async_session, init_db  # noqa: E402
from models import Ontology, OntologyCategory, OntologyRelation  # noqa: E402
from services.graph_sync_service import GraphSyncService  # noqa: E402

# ── 域映射表：直接复用种子域数据（name → code） ─────────────────────────────
from scripts.flight_ops_domain import ONTOLOGIES as OPS_ONTS  # noqa: E402
from scripts.flight_ops_domain import RELATIONS as OPS_RELS  # noqa: E402
from scripts.seed_aviation_ontology import ONTOLOGIES as AVI_ONTS  # noqa: E402
from scripts.seed_aviation_ontology import RELATIONS as AVI_RELS  # noqa: E402

NAME_TO_CODE: dict[str, str] = {}
for _o in (*OPS_ONTS, *AVI_ONTS):
    if _o.get("code"):
        NAME_TO_CODE[_o["name"]] = _o["code"]
for _r in (*OPS_RELS, *AVI_RELS):
    if _r.get("code"):
        NAME_TO_CODE.setdefault(_r["name"], _r["code"])


def _unique_code(code: str, taken: set[str]) -> str:
    """兜底编码撞车保护（id 全局唯一，理论上不会发生）。"""
    while code in taken:
        code = f"{code}_X"
    return code


async def backfill_codes() -> tuple[int, int]:
    """回填本体与关系定义编码，返回 (本体回填数, 关系回填数)。"""
    ont_fixed = rel_fixed = 0
    async with async_session() as db:
        onts = (await db.execute(select(Ontology))).scalars().all()
        taken = {o.code for o in onts if (o.code or "").strip()}
        for ont in onts:
            if (ont.code or "").strip():
                continue
            code = _unique_code(NAME_TO_CODE.get(ont.name) or f"ONT_{ont.id.upper()}", taken)
            ont.code = code
            taken.add(code)
            ont_fixed += 1
            print(f"  [本体] {ont.name} → {code}")

        rels = (await db.execute(select(OntologyRelation))).scalars().all()
        rel_taken = {r.code for r in rels if (r.code or "").strip()}
        for rel in rels:
            if (rel.code or "").strip():
                continue
            code = _unique_code(
                NAME_TO_CODE.get(rel.name) or f"REL_{rel.id.upper()}", rel_taken
            )
            rel.code = code
            rel_taken.add(code)
            rel_fixed += 1
            print(f"  [关系] {rel.name} → {code}")

        if ont_fixed or rel_fixed:
            await db.commit()
    return ont_fixed, rel_fixed


async def resync_graphs() -> None:
    """全量重建分析图：迁入自带按类别清图，标签/关系类型将全部为编码。"""
    async with async_session() as db:
        cats = (await db.execute(select(OntologyCategory))).scalars().all()
        for cat in cats:
            stats = await GraphSyncService.precheck(db, cat.id)
            total = int(stats.get("total_entities") or 0)
            if total == 0:
                print(f"跳过类别 {cat.name}({cat.id})：无实体")
                continue
            print(f"重建类别 {cat.name}({cat.id})：{total} 实体 …")
            run = await GraphSyncService.start_run(db, cat.id, "full")
            await GraphSyncService.execute_run(run["id"], cat.id)
            print("  完成")


async def main() -> None:
    parser = argparse.ArgumentParser(description="存量本体/关系编码回填 + 分析图重建")
    parser.add_argument("--resync", action="store_true", help="回填后全量重建 Neo4j 分析图")
    args = parser.parse_args()

    await init_db()

    ont_fixed, rel_fixed = await backfill_codes()
    print(f"编码回填完成：本体 {ont_fixed} 个，关系定义 {rel_fixed} 条")

    if not args.resync:
        return

    await resync_graphs()
    print("分析图全量重建完成")


if __name__ == "__main__":
    asyncio.run(main())
