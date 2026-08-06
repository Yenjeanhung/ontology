"""Kùzu 表结构查看工具

用法（需先停止后端服务，因为 Kùzu 不支持并发访问）:
  python kuzu_inspect.py              # 查看所有表 + Entity/Relation 列
  python kuzu_inspect.py Entity       # 查看指定表的列
  python kuzu_inspect.py Relation     # 查看 Relation 表的列
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kuzu
from config import settings
from pathlib import Path

db_path = Path(settings.KUZU_DB_PATH)

try:
    db = kuzu.Database(str(db_path), read_only=True)
except Exception:
    try:
        db = kuzu.Database(str(db_path))
    except Exception as e:
        print(f"无法打开数据库（可能被后端锁定）: {e}")
        print("请先停止后端服务再运行此工具。")
        sys.exit(1)

conn = kuzu.Connection(db)
target = sys.argv[1] if len(sys.argv) > 1 else None

if target:
    print(f"\n=== 表 {target} 的列 ===")
    try:
        result = conn.execute(f"CALL table_info('{target}') RETURN *")
        while result.has_next():
            row = result.get_next()
            print(f"  {row}")
    except Exception as e:
        print(f"  失败: {e}")
else:
    print("\n=== Kùzu 所有表 ===")
    try:
        result = conn.execute("CALL show_tables() RETURN *")
        cols = result.get_column_names()
        print(f"  返回列: {cols}\n")
        while result.has_next():
            row = result.get_next()
            print(f"  {row}")
    except Exception as e:
        print(f"  失败: {e}")

    for table in ['Entity', 'Relation', 'Chunk', 'Document']:
        print(f"\n--- {table} 列 ---")
        try:
            result = conn.execute(f"CALL table_info('{table}') RETURN *")
            while result.has_next():
                row = result.get_next()
                print(f"  {row}")
        except Exception as e:
            print(f"  {e}")

    # 统计各表行数
    print("\n=== 各表行数 ===")
    for table in ['KnowledgeBase', 'Document', 'Chunk', 'Entity', 'Relation']:
        try:
            result = conn.execute(f"MATCH (n:{table}) RETURN count(n) AS cnt")
            if result.has_next():
                cnt = result.get_next()[0]
                print(f"  {table}: {cnt} 行")
        except Exception as e:
            print(f"  {table}: 查询失败 ({e})")
