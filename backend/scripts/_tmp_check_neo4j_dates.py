#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时检查：Neo4j 中是否还有日期实体。用完即删。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from neo4j import GraphDatabase
from config import settings


def main():
    drv = GraphDatabase.driver(
        settings.NEO4J_URI, auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
    )
    with drv.session(database=settings.NEO4J_DATABASE) as s:
        cnt = s.run(
            "MATCH (n:Entity) WHERE n.name =~ '^\d{4}-\d{2}-\d{2}$' RETURN count(n) AS c"
        ).single()["c"]
        print(f"Neo4j 中日期实体数量：{cnt}")

        if cnt:
            for rec in s.run(
                "MATCH (n:Entity) WHERE n.name =~ '^\d{4}-\d{2}-\d{2}$' RETURN n.name LIMIT 10"
            ):
                print(f"  {rec['n.name']}")
    drv.close()


if __name__ == "__main__":
    main()