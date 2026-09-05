"""临时诊断：两模式在真实前端参数下的返回规模。用完即删。"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import desc, func, select

from database import async_session
from models import Entity, Ontology
from providers.graph_store import fetch_graph_view, get_graph_store_provider_name
from services.graph_data_service import GraphDataService


async def main() -> None:
    # 1) 本体模式：前端传 limit=200
    async with async_session() as db:
        row = (
            await db.execute(
                select(Ontology.category_id, func.count(Entity.id))
                .join(Entity, Entity.ontology_id == Ontology.id)
                .group_by(Ontology.category_id)
                .order_by(desc(func.count(Entity.id)))
                .limit(1)
            )
        ).first()
        cid = row[0]
        t0 = time.perf_counter()
        view = await GraphDataService.get_ontology_view(db, category_id=cid, limit=200)
        ms = (time.perf_counter() - t0) * 1000
        s = view["summary"]
        print(f"[本体模式 limit=200] {ms:.0f}ms nodes={len(view['nodes'])} edges={len(view['edges'])} "
              f"rel_shown={s['relation_shown']}/{s['relation_total']}")

    # 2) KB 模式：图存储全量拉取耗时与规模
    from database import async_session as _s
    async with _s() as db:
        from models import KnowledgeBase
        kbs = (await db.execute(select(KnowledgeBase.id).limit(3))).scalars().all()
    print(f"graph provider: {get_graph_store_provider_name()} KBs: {kbs}")
    for kb_id in kbs[:1]:
        t0 = time.perf_counter()
        data = await asyncio.to_thread(fetch_graph_view, kb_id, None, None, None)
        ms = (time.perf_counter() - t0) * 1000
        n_all = len(data.get("nodes", []))
        e_all = len(data.get("edges", []))
        n_rec = len(data.get("records", []))
        print(f"[KB {kb_id} 全量拉取] {ms:.0f}ms nodes={n_all} edges={e_all} records={n_rec}")


if __name__ == "__main__":
    asyncio.run(main())
