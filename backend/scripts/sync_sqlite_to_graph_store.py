"""把 SQLite 中的实体/关系批量同步到后端运行时图库（Kùzu / Neo4j）。

适用场景：
- 通过 gen_data.py / import_neo4j.py / sync_entities_to_sqlite.py 导入的民航维修领域实体，
  并非从文件抽取，因此不会自动进入运行时图库；
- 运行本脚本后，这些实体/关系即可在「图谱浏览/清洗」中按本体类别查看。

用法（从 backend 目录）：
    python scripts/sync_sqlite_to_graph_store.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from contextlib import contextmanager
from typing import Generator

# 确保能导入 backend 包
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _BACKEND_DIR)

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from config import settings
from models import Entity, Relation
from providers.graph_store import _get_adapter as get_adapter

BATCH_SIZE = 500


@contextmanager
def sync_session() -> Generator:
    url = getattr(settings, "DATABASE_URL", "sqlite:///./data/app.db")
    # 提取 sqlite 文件路径，支持 sqlite:///abs, sqlite:///./relative, sqlite:////abs
    m = re.match(r"sqlite\+?.*?:///(.+)$", url)
    if not m:
        raise RuntimeError(f"不支持的数据库 URL: {url}")
    db_path = m.group(1)
    if db_path.startswith("./"):
        db_path = os.path.join(_BACKEND_DIR, db_path[2:])
    db_path = os.path.abspath(db_path)
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Session = sessionmaker(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _safe(value: str | None) -> str:
    return value or ""


def _props(props: dict | str | None) -> str:
    if isinstance(props, str):
        return props or ""
    try:
        return json.dumps(props or {}, ensure_ascii=False)
    except Exception:
        return ""


def main() -> None:
    adapter = get_adapter()
    print(f"图库适配器: {adapter.provider_name}")

    with sync_session() as db:
        total_entities = db.scalar(select(func.count(Entity.id)))
        print(f"SQLite 实体总数: {total_entities}")

        # 同步实体
        entity_count = 0
        last_id = ""
        while True:
            rows = db.execute(
                select(Entity)
                .where(Entity.id > last_id)
                .order_by(Entity.id)
                .limit(BATCH_SIZE)
            ).scalars().all()
            if not rows:
                break
            for ent in rows:
                adapter.upsert_entity(
                    entity_id=ent.id,
                    kb_id=_safe(ent.kb_id),
                    ontology_id=_safe(ent.ontology_id),
                    entity_type=_safe(ent.entity_type),
                    name=_safe(ent.name),
                    description=_safe(ent.description),
                    properties=_props(ent.properties),
                )
                entity_count += 1
                last_id = ent.id
            print(f"  已同步实体: {entity_count}")

        # 同步关系
        relation_count = 0
        last_rel_id = ""
        while True:
            rows = db.execute(
                select(Relation)
                .where(Relation.id > last_rel_id)
                .order_by(Relation.id)
                .limit(BATCH_SIZE)
            ).scalars().all()
            if not rows:
                break
            for rel in rows:
                adapter.upsert_relation(
                    relation_id=rel.id,
                    kb_id=_safe(rel.kb_id),
                    relation_type=_safe(rel.relation_type),
                    description=_safe(rel.description),
                    source_entity_id=rel.source_entity_id,
                    target_entity_id=rel.target_entity_id,
                )
                relation_count += 1
                last_rel_id = rel.id
            print(f"  已同步关系: {relation_count}")

    print(f"完成：实体 {entity_count}，关系 {relation_count}")


if __name__ == "__main__":
    main()
