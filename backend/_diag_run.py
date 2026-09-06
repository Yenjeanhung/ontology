import asyncio

import asyncpg


async def main():
    c = await asyncpg.connect(
        "postgresql://ontology:ontology123@localhost:5432/knowsource"
    )
    rows = await c.fetch(
        "select id, status, entity_count, relation_count, error, "
        "created_at from graph_sync_runs order by created_at desc limit 5"
    )
    for r in rows:
        print("=" * 60)
        print(r["id"], r["status"], "e:", r["entity_count"], "r:", r["relation_count"], r["created_at"])
        print(r["error"])
    await c.close()


asyncio.run(main())
