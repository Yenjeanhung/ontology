"""一次性回填：存量会话归属到指定用户（默认系统管理员 admin）。

背景：chat_sessions.user_id 字段早期"预留多用户"未写入，改造后问答接口
已从登录态取 user_id 落库；存量会话该列为空，全部归属 admin（sysadmin0001）。

用法（backend 目录下）：
    python scripts/backfill_chat_sessions_user.py --dry-run   # 只统计不写
    python scripts/backfill_chat_sessions_user.py             # 实际回填
    python scripts/backfill_chat_sessions_user.py --user-id 某用户id
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from database import async_session  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(description="chat_sessions.user_id 存量回填")
    ap.add_argument("--user-id", default="sysadmin0001", help="归属用户 id")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写")
    args = ap.parse_args()

    async with async_session() as db:
        cnt = (await db.execute(
            text("SELECT COUNT(*) FROM chat_sessions WHERE COALESCE(user_id, '') = ''"),
        )).scalar_one()
        print(f"待回填会话数: {cnt} -> user_id={args.user_id} dry_run={args.dry_run}")
        if args.dry_run or cnt == 0:
            return
        r = await db.execute(
            text("UPDATE chat_sessions SET user_id = :u WHERE COALESCE(user_id, '') = ''"),
            {"u": args.user_id},
        )
        await db.commit()
        print(f"done: updated={r.rowcount}")


if __name__ == "__main__":
    asyncio.run(main())
