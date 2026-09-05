# -*- coding: utf-8 -*-
"""临时检查：Neo4j 中 kb_id 分布 vs PG 知识库。用完即删。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase
from sqlalchemy import select

from config import settings
from database import async_session
from models import KnowledgeBase


async def main():
    async with async_session() as s:
        kbs = (await s.execute(select(KnowledgeBase.id, KnowledgeBase.name))).all()
    print('== PG 中的知识库 ==')
    for k in kbs:
        print('  %s  %s' % (k.id, k.name))

    drv = GraphDatabase.driver(
        settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    with drv.session(database=settings.NEO4J_DATABASE) as s:
        print()
        print('== Neo4j 中 Entity 节点的 kb_id 分布 ==')
        for rec in s.run('MATCH (n:Entity) RETURN n.kb_id AS kb, count(*) AS c ORDER BY c DESC'):
            print('  %-40s %8d' % (str(rec['kb']), rec['c']))
        print('== Neo4j 中 Relation 节点的 kb_id 分布 ==')
        for rec in s.run('MATCH (n:Relation) RETURN n.kb_id AS kb, count(*) AS c ORDER BY c DESC'):
            print('  %-40s %8d' % (str(rec['kb']), rec['c']))
        print('== 无 kb_id 的节点（旧英文图）==')
        for rec in s.run(
            'MATCH (n) WHERE n.kb_id IS NULL '
            'RETURN labels(n)[0] AS lbl, count(*) AS c ORDER BY c DESC LIMIT 25'
        ):
            print('  %-24s %8d' % (rec['lbl'], rec['c']))
    drv.close()


asyncio.run(main())
