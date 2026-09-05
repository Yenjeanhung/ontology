#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时检查：PG Entity 表中是否还有日期实体。用完即删。"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func

from database import async_session
from models import Entity


async def main():
    async with async_session() as s:
        # 查找名称像日期的实体（YYYY-MM-DD 格式）
        date_cnt = await s.scalar(
            select(func.count())
            .where(Entity.name.op("similar to")(r"^\d{4}-\d{2}-\d{2}$"))
        )
        print(f"PG 中日期实体数量：{date_cnt}")

        if date_cnt:
            ents = await s.scalars(
                select(Entity)
                .where(Entity.name.op("regexp")(r"^\d{4}-\d{2}-\d{2}$"))
                .limit(20)
            )
            print("示例日期实体：")
            for e in ents:
                print(f"  {e.name}")


asyncio.run(main())