#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""临时检查：Kùzu 数据库中是否还有日期实体。用完即删。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kuzu import Database
from config import settings


def main():
    db = Database(settings.KUZU_DB_PATH)
    db.init_database()
    conn = db._database

    # 查找名称像日期的实体（YYYY-MM-DD 格式）
    cnt = conn.execute_query(
        "MATCH (n:Entity) WHERE n.name =~ '^\\\\d{4}-\\\\d{2}-\\\\d{2}$' RETURN count(n) AS c"
    ).fetch_one()["c"]
    print(f"Kùzu 中日期实体数量：{cnt}")

    if cnt:
        rows = conn.execute(
            "MATCH (n:Entity) WHERE n.name =~ '^\\\\d{4}-\\\\d{2}-\\\\d{2}$' RETURN n.name LIMIT 10"
        ).fetchall()
        print("示例日期实体：")
        for row in rows:
            print(f"  {row[0]}")

    conn.close()
    db.close()


if __name__ == "__main__":
    main()