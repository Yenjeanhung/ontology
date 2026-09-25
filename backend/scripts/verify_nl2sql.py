"""verify_nl2sql：本体驱动 NL2SQL 验收脚本（文档 §11，可复跑）。

Part A 纯逻辑（无 LLM）：validate_sql 攻击用例与纠偏 + 独立实例数据对账；
Part B 全链路（LLM）：九个典型问题走 nl2sql_query（sqlite 独立实例档）；
Part C 端到端：_data_facts_nl2sql 事实卡产出。

用法（backend 目录，conda 环境 ontology）：
    python scripts/verify_nl2sql.py            # 全部
    python scripts/verify_nl2sql.py --fast     # 只跑 Part A（无 LLM 依赖）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CATEGORY_NAME = "数据源本体·国航旅客运输"
TABLES = ["airports", "flights", "members", "passengers", "tickets", "baggage",
          "crew_members", "crew_assignments", "service_requests",
          "mileage_records", "compensations"]

NINE_Q = [
    "CA1501 的机长是谁",
    "CA1501 航班的所有旅客及会员等级",
    "统计每个航班的旅客数，没有旅客的航班也要列出",
    "哪些航班没有旅客托运行李",
    "平均票价超 1000 元的舱位及其客票量",
    "金卡会员乘坐过的航班",
    "按延误分档（≤15/15-60/>60）统计各档航班数",
    "本月净增里程为正的金卡会员",
    "投诉未办结且满意度 ≤2 分的旅客名单",
]


async def part_a() -> bool:
    from services.multi_agent.nl2sql_service import validate_sql

    ok_all = True

    def check(name, cond):
        nonlocal ok_all
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok_all &= bool(cond)

    print("── Part A1 validate_sql（攻击用例与纠偏，sqlite 档）──")
    check("DROP 拦截", not validate_sql("DROP TABLE flights", "sqlite", TABLES)[0])
    check("双语句拦截", not validate_sql("SELECT 1; DELETE FROM t", "sqlite", TABLES)[0])
    check("越权表拦截", not validate_sql(
        "SELECT * FROM pg_catalog.pg_tables", "sqlite", TABLES)[0])
    check("NOT IN(子查询) 拦截", not validate_sql(
        "SELECT a FROM flights WHERE x NOT IN (SELECT y FROM tickets)",
        "sqlite", TABLES)[0])
    check("隐式 join 拦截", not validate_sql(
        "SELECT a FROM flights f, tickets t WHERE f.flight_no = t.flight_no",
        "sqlite", TABLES)[0])
    ok, s, notes = validate_sql(
        "SELECT flight_no FROM flights LIMIT 99999", "sqlite", TABLES)
    check("LIMIT 99999 纠偏为 500", ok and "LIMIT 500" in s)
    ok, s, notes = validate_sql("SELECT flight_no FROM flights", "sqlite", TABLES)
    check("无 LIMIT 自动追加 500", ok and "LIMIT 500" in s)
    ok, s, _ = validate_sql(
        "SELECT f.flight_no FROM tickets t RIGHT JOIN flights f "
        "ON t.flight_no = f.flight_no", "sqlite", TABLES)
    check("RIGHT JOIN 归一化为反向 LEFT JOIN",
          ok and "LEFT JOIN" in s and "RIGHT JOIN" not in s.upper())
    ok, _, _ = validate_sql(
        "SELECT f.flight_no FROM tickets t RIGHT JOIN flights f "
        "ON t.flight_no = f.flight_no", "postgres", TABLES)
    check("PG 档 RIGHT JOIN 原生放行", ok)
    check("多表 SELECT * 拒绝", not validate_sql(
        "SELECT * FROM flights f JOIN tickets t ON f.flight_no = t.flight_no "
        "JOIN passengers p ON t.passenger_id = p.id JOIN members m "
        "ON p.member_id = m.member_id", "sqlite", TABLES)[0])

    print("── Part A2 独立实例数据对账（按类别 DSN 动态连）──")
    from sqlalchemy import select
    from database import async_session
    from models import OntologyCategory
    async with async_session() as db:
        cat = (await db.execute(select(OntologyCategory).where(
            OntologyCategory.name == CATEGORY_NAME))).scalars().first()
        from services.datasource_service import resolve_category_datasource
        dialect, dsn = (await resolve_category_datasource(db, cat)) if cat else ("", "")
    if cat is None or not dsn.strip():
        print("  [FAIL] 数据源类别缺失或 DSN 未配置")
        return False
    print(f"  类别 dialect={dialect} dsn={dsn[:66]}")
    from models import OntologyRelation
    rels = (await db.execute(select(OntologyRelation).where(
        OntologyRelation.category_id == cat.id))).scalars().all()
    no_code = [r.name for r in rels if not (r.code or "").strip()]
    check(f"{len(rels)} 条关系编码齐备" + (f"（缺：{no_code}）" if no_code else ""),
          len(rels) == 15 and not no_code)

    if dialect == "sqlite":
        import aiosqlite
        conn = await aiosqlite.connect(dsn, uri=True)

        async def val(sql):
            cur = await conn.execute(sql)
            row = await cur.fetchone()
            await cur.close()
            return row[0]
    else:
        import asyncpg
        conn = await asyncpg.connect(dsn)

        async def val(sql):
            return await conn.fetchval(sql)

    try:
        total_rows = 0
        for t in TABLES:
            n = await val(f"SELECT COUNT(*) FROM {t}")
            total_rows += n
            print(f"  {t}: {n} 行")
        check("十一表共 8345 行", total_rows == 8345)
        sum_members = await val("SELECT SUM(miles) FROM members")
        sum_flow = await val("SELECT COALESCE(SUM(miles), 0) FROM mileage_records")
        check(f"里程对账 SUM(members.miles)={sum_members} == "
              f"30000*300+SUM(flow)={30000 * 300 + sum_flow}",
              sum_members == 30000 * 300 + sum_flow)
        sr_total = await val("SELECT COUNT(*) FROM service_requests")
        sr_no_pax = await val(
            "SELECT COUNT(*) FROM service_requests s LEFT JOIN passengers p "
            "ON s.passenger_id = p.id WHERE p.id IS NULL")
        check(f"服务请求总数 {sr_total}（LEFT JOIN 口径 {sr_total - sr_no_pax}+匿名 {sr_no_pax}，"
              f"可空 FK 未丢行）", sr_total == 450)
        captain_rows = await val(
            "SELECT COUNT(*) FROM flights f JOIN crew_assignments ca "
            "ON f.flight_no = ca.flight_no JOIN crew_members c "
            "ON ca.crew_id = c.crew_id "
            "WHERE f.flight_no='CA1501' AND ca.duty_role='机长'")
        check(f"CA1501 机长确定性唯一（{captain_rows} 条）", captain_rows == 1)
    finally:
        await conn.close()
    return ok_all


async def part_b(asks: list[int] = None) -> bool:
    from services.multi_agent.nl2sql_service import nl2sql_query

    # 脚本进程无 server 启动钩子：先从库加载生效 LLM 配置（等价 load_active_into_settings）
    from database import async_session
    from services.config_service import load_active_into_settings
    async with async_session() as db:
        await load_active_into_settings(db)

    pick = set(asks or range(1, len(NINE_Q) + 1))
    print(f"── Part B 九问全链路（sqlite 独立实例档，走 LLM，本批 {sorted(pick)}）──")
    ok_all = True
    for i, q in enumerate(NINE_Q, 1):
        if i not in pick:
            continue
        res = await nl2sql_query(q)
        if res is None:
            print(f"  [{i}] FAIL（回落老链）：{q}")
            ok_all = False
            continue
        first = res["rows"][0] if res["rows"] else {}
        preview = " | ".join(f"{v}" for v in list(first.values())[:3])
        print(f"  [{i}] ok rows={res['rowcount']} retries={res['retries']} "
              f"{res['elapsed_ms']}ms 表={res['tables'][:3]} 「{q}」→ {preview[:80]}")
    return ok_all


async def part_c() -> bool:
    from services.multi_agent.scenarios.universal import _data_facts_nl2sql

    print("── Part C 端到端事实卡 ──")
    facts = await _data_facts_nl2sql("CA1501 的机长是谁")
    if not facts or facts[0].get("id") != "fact-data-sql":
        print("  [FAIL] 事实卡未产出")
        return False
    print(f"  [PASS] fact-data-sql + {len(facts) - 1} 明细：{facts[0]['detail'][:120]}")
    return True


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="只跑 Part A（无 LLM）")
    ap.add_argument("--ask", default="",
                    help="只跑指定问号（逗号分隔，如 1,3,5）；省略跑全部九问")
    args = ap.parse_args()

    a_ok = await part_a()
    if not a_ok:
        print("\nPart A 存在 FAIL，终止")
        sys.exit(1)
    print("\nPart A 全部通过")
    if args.fast:
        return

    asks = [int(x) for x in args.ask.split(",") if x.strip()] if args.ask else None
    b_ok = await part_b(asks)
    c_ok = await part_c() if (asks is None or 1 in asks) else True
    from database import engine as _engine
    await _engine.dispose()
    if asks is None or 1 in asks:
        print(f"\n验收结论：B={'PASS' if b_ok else 'FAIL'} C={'PASS' if c_ok else 'FAIL'}")
    else:
        print(f"\n本批结论：B={'PASS' if b_ok else 'FAIL'}")
    sys.exit(0 if (b_ok and c_ok) else 1)


if __name__ == "__main__":
    asyncio.run(main())
