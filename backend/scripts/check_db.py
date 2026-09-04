import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

url = 'sqlite+aiosqlite:///./data/knowsource.db'
engine = create_async_engine(url)

async def main():
    async with engine.connect() as c:
        cats = await c.execute(text('SELECT id,name FROM ontology_categories'))
        print('--- categories ---')
        for r in cats: print(r)
        onts = await c.execute(text('SELECT id,category_id,name FROM ontologies ORDER BY category_id'))
        print('--- ontologies ---')
        for r in onts: print(r)
        ents = await c.execute(text('SELECT COUNT(*) FROM entities'))
        print('--- entity count ---')
        for r in ents: print(r)

asyncio.run(main())
