"""验证 mem0 命名空间迁移结果：新复合键能否按线上同款过滤语法命中。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymilvus import Collection, connections  # noqa: E402

from config import settings  # noqa: E402

NEW_KEY = "user:sysadmin0001:agent:default"

connections.connect(host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
col = Collection(settings.MEM0_COLLECTION)
col.load()

# 与 mem0 _create_filter 同款表达式：metadata["user_id"] == "<值>"
expr = f'metadata["user_id"] == "{NEW_KEY}"'
n = col.query(expr=expr, output_fields=["count(*)"])[0]["count(*)"]
print(f"新键命中条数: {n}  ({NEW_KEY})")

rows = col.query(expr=expr, limit=2, output_fields=["text", "metadata"])
for r in rows:
    md = r["metadata"]
    print(f"  text={r['text'][:30]!r} session={md.get('session_id')} hash={bool(md.get('hash'))}")

# 旧值残留检查
old = col.query(expr='metadata["user_id"] == "agent_default"', output_fields=["count(*)"])[0]["count(*)"]
print(f"旧值残留: {old}")
