import asyncio
from database import engine, init_db


async def main():
    await init_db()
    async with engine.begin() as c:
        rows = await c.execute(__import__("sqlalchemy").text("PRAGMA table_info(workflow_runs)"))
        cols = [r[1] for r in rows]
        print("workflow_runs cols:", cols)
        r2 = await c.execute(__import__("sqlalchemy").text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schedules'"))
        print("schedules exists:", bool(r2.fetchall()))
        r3 = await c.execute(__import__("sqlalchemy").text(
            "SELECT version FROM _migrations WHERE version='migration_014'"))
        print("migration_014 applied:", bool(r3.fetchall()))


asyncio.run(main())
