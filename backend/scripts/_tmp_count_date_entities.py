# -*- coding: utf-8 -*-
"""临时脚本：清理 PG 中的日期形态实体及其关联关系（带前后对照）。"""
import asyncio
import sys

sys.path.insert(0, ".")

from sqlalchemy import text

from database import async_session

DATE_RE = r"^\d{4}[-/年.]\d{1,2}[-/月.]\d{1,2}"


async def main():
    async with async_session() as s:
        ent_before = (await s.execute(text(
            "SELECT count(*) FROM entities WHERE name ~ :p"
        ), {"p": DATE_RE})).scalar()
        rel_before = (await s.execute(text(
            "SELECT count(*) FROM relations r "
            "JOIN entities se ON r.source_entity_id = se.id "
            "JOIN entities te ON r.target_entity_id = te.id "
            "WHERE se.name ~ :p OR te.name ~ :p"
        ), {"p": DATE_RE})).scalar()
        print(f"删除前: 日期实体 {ent_before}, 关联关系 {rel_before}")

        del_rel = (await s.execute(text(
            "DELETE FROM relations r WHERE r.id IN ("
            "  SELECT r2.id FROM relations r2 "
            "  JOIN entities se ON r2.source_entity_id = se.id "
            "  JOIN entities te ON r2.target_entity_id = te.id "
            "  WHERE se.name ~ :p OR te.name ~ :p)"
        ), {"p": DATE_RE})).rowcount
        del_ent = (await s.execute(text(
            "DELETE FROM entities WHERE name ~ :p"
        ), {"p": DATE_RE})).rowcount
        await s.commit()
        print(f"已删除: 实体 {del_ent}, 关系 {del_rel}")

        ent_after = (await s.execute(text(
            "SELECT count(*) FROM entities WHERE name ~ :p"
        ), {"p": DATE_RE})).scalar()
        total = (await s.execute(text("SELECT count(*) FROM entities"))).scalar()
        print(f"删除后: 日期实体 {ent_after}, entities 总数 {total}")


asyncio.run(main())
