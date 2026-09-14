"""一次性迁移：mem0 长期记忆命名空间从单层 → (user_id, agent_id) 复合键。

背景：早期长期记忆的隔离键直接用 agent_id（存量里为 "agent_default" 这类字面值），
多用户下记忆互相可见。现已改为 "user:{uid}:agent:{aid}" 复合键
（见 services/memory_store.py::_namespace），mem0 检索时原样匹配
metadata["user_id"]（vector_stores/milvus.py::_create_filter），无前缀加工。

注意：本版 mem0 的 Milvus schema 无 user_id 标量字段（字段为 id/vectors/metadata/
text/sparse），隔离键存在 metadata JSON 里。迁移 = 读全量行 → 改写 metadata.user_id
→ upsert 回写（主键/向量原样保留，不走 LLM）。
存量数据全部归属系统管理员 admin（user_id=sysadmin0001）。

用法（backend 目录下）：
    python scripts/migrate_mem0_namespace.py --dry-run   # 先预览影响面
    python scripts/migrate_mem0_namespace.py             # 实际执行
    python scripts/migrate_mem0_namespace.py --admin-id 某用户id
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from pymilvus import Collection, connections  # noqa: E402


def _new_scope(old: str, admin_id: str) -> str:
    """旧隔离值 → 新复合键。兼容历史可能的 'agent_xxx' 字面值形式。"""
    inner = old[len("agent_"):] if old.startswith("agent_") else old
    return f"user:{admin_id}:agent:{inner or 'default'}"


def main() -> None:
    ap = argparse.ArgumentParser(description="mem0 命名空间迁移：agent_id → user:{uid}:agent:{aid}")
    ap.add_argument("--admin-id", default="sysadmin0001", help="存量数据归属的用户 id")
    ap.add_argument("--collection", default=settings.MEM0_COLLECTION, help="mem0 向量 collection 名")
    ap.add_argument("--dry-run", action="store_true", help="只统计与预览，不写回")
    args = ap.parse_args()

    connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
    col = Collection(args.collection)
    col.load()

    migrated, skipped = 0, 0
    it = col.query_iterator(batch_size=200, expr='id != ""', output_fields=["*"])
    while True:
        batch = it.next()
        if not batch:
            it.close()
            break
        todo = []
        for row in batch:
            md = row.get("metadata") or {}
            old = (md.get("user_id") or "").strip()
            if old.startswith("user:"):
                skipped += 1  # 已是新格式，幂等重跑安全
                continue
            md["user_id"] = _new_scope(old, args.admin_id)
            row["metadata"] = md
            todo.append(row)
            if migrated < 5:
                print(f"  示例: {old!r} -> {md['user_id']!r}  text={str(row.get('text'))[:40]!r}")
        if todo and not args.dry_run:
            col.upsert(todo)
        migrated += len(todo)
        print(f"progress: migrated={migrated} skipped={skipped}")

    if not args.dry_run:
        col.flush()
    print(f"done: migrated={migrated} skipped={skipped} dry_run={args.dry_run} "
          f"collection={args.collection} admin={args.admin_id}")


if __name__ == "__main__":
    main()
