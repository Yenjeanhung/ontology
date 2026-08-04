"""阶段二A 验证脚本：实体/关系实例层 CRUD + Kùzu 同步。

运行方式（在 backend 目录下）：
    python test_phase2a.py

会使用临时 SQLite 数据库与临时 Kùzu 数据库，跑完自动清理。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# 在导入 config 之前设置环境变量，指向临时目录
_TMP_DIR = Path(tempfile.mkdtemp(prefix="knowsource_test_"))
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8765")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP_DIR / 'test.db').as_posix()}")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
os.environ.setdefault("EMBEDDING_DIMENSION", "512")
os.environ.setdefault("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
os.environ.setdefault("OPENAI_EMBEDDING_DIMENSION", "1536")
os.environ.setdefault("VECTOR_STORE_PROVIDER", "chroma")
os.environ.setdefault("CHROMA_PERSIST_DIR", str(_TMP_DIR / "chroma"))
os.environ.setdefault("MILVUS_HOST", "localhost")
os.environ.setdefault("MILVUS_PORT", "19530")
os.environ.setdefault("GRAPH_STORE_PROVIDER", "kuzu")
os.environ.setdefault("KUZU_DB_PATH", str(_TMP_DIR / "graph.kuzu"))
os.environ.setdefault("NEO4J_URI", "bolt://localhost:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "test")
os.environ.setdefault("NEO4J_DATABASE", "neo4j")
os.environ.setdefault("GRAPH_ENTITY_EXTRACTION_ENABLED", "false")
os.environ.setdefault("GRAPH_EXTRACTION_BATCH_SIZE", "6")
os.environ.setdefault("GRAPH_EXTRACTION_CONCURRENCY", "3")
os.environ.setdefault("GRAPH_MIN_CHARS_FOR_EXTRACTION", "80")
os.environ.setdefault("GRAPH_MAX_ENTITIES_PER_CHUNK", "12")
os.environ.setdefault("GRAPH_MAX_RELATIONS_PER_CHUNK", "12")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "test_key")
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:9999/v1")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("LLM_MAX_TOKENS", "2000")
os.environ.setdefault("LLM_TEMPERATURE", "0.7")
os.environ.setdefault("CHUNK_SIZE", "800")
os.environ.setdefault("CHUNK_OVERLAP", "120")
os.environ.setdefault("UPLOAD_DIR", str(_TMP_DIR / "uploads"))
os.environ.setdefault("CHUNK_DIR", str(_TMP_DIR / "chunks"))
os.environ.setdefault("MAX_FILE_SIZE", "104857600")

# 切换到 backend 目录，确保模块可导入
BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

# Stub 缺失的可选依赖，避免 providers/__init__.py 触发的 langchain_core 导入失败。
# 真实运行环境会安装这些包；此处仅用于本机语法/逻辑自测。
import types as _types  # noqa: E402

if "langchain_core" not in sys.modules:
    _lc = _types.ModuleType("langchain_core")
    _lc_emb = _types.ModuleType("langchain_core.embeddings")
    class _EmbeddingsStub:  # 最小桩，满足 type hint
        pass
    _lc_emb.Embeddings = _EmbeddingsStub
    _lc.embeddings = _lc_emb
    sys.modules["langchain_core"] = _lc
    sys.modules["langchain_core.embeddings"] = _lc_emb

from database import async_session, init_db  # noqa: E402
from services.entity_service import EntityService  # noqa: E402
from services.ontology_service import OntologyService  # noqa: E402

# Kùzu 同步是 best-effort（service 内部 try/except）。
# 若本机未装 kuzu，ensure_graph_schema 会失败，但 SQLite CRUD 不受影响。
_KUZU_AVAILABLE = False
try:
    import kuzu  # noqa: F401
    _KUZU_AVAILABLE = True
except Exception:
    pass


def _print(label: str, payload):
    print(f"\n=== {label} ===")
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:1500])
    else:
        print(payload)


async def main():
    print(f"临时目录: {_TMP_DIR}")
    print(f"Kùzu 可用: {_KUZU_AVAILABLE}（同步调用将{'执行' if _KUZU_AVAILABLE else '静默跳过'}）")
    print("初始化数据库...")
    await init_db()
    print("init_db 完成（含 schema.sql 全量建表）")

    if _KUZU_AVAILABLE:
        from providers.graph_store import ensure_graph_schema
        print("初始化 Kùzu schema...")
        ensure_graph_schema()
    else:
        print("跳过 Kùzu schema 初始化（未安装 kuzu 包）")

    async with async_session() as db:
        # 1. 先建一个本体类别 + 本体 + 关系字典 + 三元组
        cat = await OntologyService.create_category(db, "金融领域本体", "测试用")
        category_id = cat["id"]
        _print("创建本体类别", cat)

        ont = await OntologyService.create_ontology(db, category_id, "人物", "人物本体", "#ef4444")
        ontology_id = ont["id"]
        _print("创建本体[人物]", ont)

        ont_org = await OntologyService.create_ontology(db, category_id, "组织", "组织本体", "#3b82f6")
        ontology_id_org = ont_org["id"]

        rel_def = await OntologyService.create_relation(db, category_id, "任职于", "人物在某组织任职")
        relation_def_id = rel_def["id"]
        _print("创建关系定义[任职于]", rel_def)

        constraint = await OntologyService.create_constraint(
            db, category_id, ontology_id, relation_def_id, ontology_id_org, "人物→组织"
        )
        _print("创建三元组约束", constraint)

        # 2. 实体实例 CRUD
        e1 = await EntityService.create_entity(
            db,
            kb_id="kb_test_001",
            ontology_id=ontology_id,
            entity_type="人物",
            name="张三",
            description="某公司高管",
            properties={"姓名": "张三", "性别": "男", "年龄": 42},
        )
        _print("创建实体[张三]", e1)

        e2 = await EntityService.create_entity(
            db,
            kb_id="kb_test_001",
            ontology_id=ontology_id_org,
            entity_type="组织",
            name="A公司",
            description="互联网企业",
            properties={"名称": "A公司", "行业": "互联网"},
        )
        _print("创建实体[A公司]", e2)

        # upsert 语义：同名实体再次创建应更新而非报错
        e1_again = await EntityService.create_entity(
            db,
            kb_id="kb_test_001",
            ontology_id=ontology_id,
            entity_type="人物",
            name="张三",
            description="某公司高管（更新描述）",
            properties={"姓名": "张三", "性别": "男", "年龄": 43, "职务": "CEO"},
        )
        _print("upsert 实体[张三]（应同 id）", {
            "id": e1_again["id"], "same_id": e1_again["id"] == e1["id"],
            "description": e1_again["description"], "properties": e1_again["properties"],
        })

        # 列表 + 搜索 + 分页
        lst = await EntityService.list_entities(db, kb_id="kb_test_001", q="张", page=1, page_size=10)
        _print("实体列表（搜索'张'）", lst)

        lst_by_type = await EntityService.list_entities(db, entity_type="组织")
        _print("实体列表（按类型'组织'）", lst_by_type)

        # 详情（含关联关系）
        detail = await EntityService.get_entity(db, e1["id"])
        _print("实体详情[张三]", detail)

        # 更新
        updated = await EntityService.update_entity(
            db, e1["id"], description="更新后的描述", properties={"姓名": "张三", "职务": "CTO"}
        )
        _print("更新实体[张三]", updated)

        # 3. 关系实例 CRUD
        r1 = await EntityService.create_relation(
            db,
            kb_id="kb_test_001",
            relation_def_id=relation_def_id,
            relation_type="任职于",
            source_entity_id=e1["id"],
            target_entity_id=e2["id"],
            description="张三在 A公司任职",
        )
        _print("创建关系[张三→A公司]", r1)

        # upsert 语义
        r1_again = await EntityService.create_relation(
            db,
            kb_id="kb_test_001",
            relation_def_id=relation_def_id,
            relation_type="任职于",
            source_entity_id=e1["id"],
            target_entity_id=e2["id"],
            description="张三在 A公司任职（更新）",
        )
        _print("upsert 关系（应同 id）", {
            "id": r1_again["id"], "same_id": r1_again["id"] == r1["id"],
            "description": r1_again["description"],
        })

        rel_list = await EntityService.list_relations(db, kb_id="kb_test_001")
        _print("关系列表", rel_list)

        rel_detail = await EntityService.get_relation(db, r1["id"])
        _print("关系详情", rel_detail)

        # 实体详情的关联关系字段
        detail_after_rel = await EntityService.get_entity(db, e1["id"])
        _print("实体[张三]的关联关系数", len(detail_after_rel["relations"]))

        # 4. 统计
        stats = await EntityService.stats(db, kb_id="kb_test_001")
        _print("统计", stats)

        # 5. 删除关系
        ok = await EntityService.delete_relation(db, r1["id"])
        _print("删除关系", ok)
        rel_list_after = await EntityService.list_relations(db, kb_id="kb_test_001")
        _print("删除后关系列表 total", rel_list_after["total"])

        # 6. 删除实体（级联删除其参与的关系）
        # 重新建一条关系，验证删除实体时的级联
        r2 = await EntityService.create_relation(
            db,
            kb_id="kb_test_001",
            relation_def_id=relation_def_id,
            relation_type="任职于",
            source_entity_id=e1["id"],
            target_entity_id=e2["id"],
            description="再建一条用于级联测试",
        )
        _print("重建关系用于级联测试", {"id": r2["id"]})
        ok = await EntityService.delete_entity(db, e1["id"])
        _print("删除实体[张三]（应级联删除其关系）", ok)
        rel_left = await EntityService.list_relations(db, kb_id="kb_test_001")
        _print("删除实体后剩余关系 total（应为 0）", rel_left["total"])
        ent_left = await EntityService.list_entities(db, kb_id="kb_test_001")
        _print("剩余实体 total（应为 1，仅 A公司）", ent_left["total"])

        # 7. 清理：删除本体类别（级联）
        await OntologyService.delete_category(db, category_id)
        _print("清理：删除本体类别", "done")

    print("\n✅ 阶段二A 验证通过")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        try:
            shutil.rmtree(_TMP_DIR, ignore_errors=True)
            print(f"已清理临时目录: {_TMP_DIR}")
        except Exception:
            pass
