"""阶段二B 验证脚本：本体约束注入 + 动态 Prompt + 后处理校验 + SQLite 持久化。

测试内容（不调用真实 LLM，使用 mock payload）：
1. OntologyService.get_kb_extraction_constraints 加载约束
2. GraphExtractionService._build_constrained_system_prompt 动态 Prompt 构建
3. GraphExtractionService._merge_payload 后处理校验（类型过滤、三元组匹配、属性规整）
4. FileService._persist_extraction_to_sqlite 实体/关系写入 SQLite + id 回填

运行方式（在 backend 目录下）：
    python test_phase2b.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

# 在导入 config 之前设置环境变量，指向临时目录
_TMP_DIR = Path(tempfile.mkdtemp(prefix="knowsource_phase2b_"))
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8765")
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP_DIR / 'test.db').as_posix()}")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
os.environ.setdefault("EMBEDDING_DIMENSION", "512")
os.environ.setdefault("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
os.environ.setdefault("OPENAI_EMBEDDING_DIMENSION", "1536")
os.environ.setdefault("VECTOR_STORE_PROVIDER", "chroma")
os.environ.setdefault("MILVUS_HOST", "localhost")
os.environ.setdefault("MILVUS_PORT", "19530")
os.environ.setdefault("CHROMA_PERSIST_DIR", str(_TMP_DIR / "chroma"))
os.environ.setdefault("GRAPH_STORE_PROVIDER", "kuzu")
os.environ.setdefault("KUZU_DB_PATH", str(_TMP_DIR / "graph.kuzu"))
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

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

# Stub 缺失的可选依赖
import types as _types  # noqa: E402

if "langchain_core" not in sys.modules:
    _lc = _types.ModuleType("langchain_core")
    _lc.__path__ = []  # 标记为 package
    _lc_msg = _types.ModuleType("langchain_core.messages")
    _lc_emb = _types.ModuleType("langchain_core.embeddings")

    class _SystemMessage:
        def __init__(self, content):
            self.content = content

    class _HumanMessage:
        def __init__(self, content):
            self.content = content

    class _EmbeddingsStub:
        pass

    _lc_msg.SystemMessage = _SystemMessage
    _lc_msg.HumanMessage = _HumanMessage
    _lc_emb.Embeddings = _EmbeddingsStub
    _lc.messages = _lc_msg
    _lc.embeddings = _lc_emb
    sys.modules["langchain_core"] = _lc
    sys.modules["langchain_core.messages"] = _lc_msg
    sys.modules["langchain_core.embeddings"] = _lc_emb

from database import async_session, init_db  # noqa: E402
from providers.graph_store import ChunkGraphData, GraphEntity, GraphRelation  # noqa: E402
from services.entity_service import EntityService  # noqa: E402
from services.graph_extraction_service import GraphExtractionService  # noqa: E402
from services.ontology_service import OntologyService  # noqa: E402

_KUZU_AVAILABLE = False
try:
    import kuzu  # noqa: F401
    _KUZU_AVAILABLE = True
except Exception:
    pass

# 测试计数器
_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = ""):
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label} {detail}")


def _print(label: str, payload):
    print(f"\n--- {label} ---")
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str)[:2000])
    else:
        print(payload)


async def main():
    print(f"临时目录: {_TMP_DIR}")
    print(f"Kùzu 可用: {_KUZU_AVAILABLE}")
    print("初始化数据库...")
    await init_db()
    print("init_db 完成")

    if _KUZU_AVAILABLE:
        from providers.graph_store import ensure_graph_schema
        ensure_graph_schema()

    async with async_session() as db:
        # ===== 1. 搭建本体定义：类别 + 本体 + 属性 + 关系 + 三元组 =====
        print("\n=== 步骤1：搭建本体定义 ===")
        cat = await OntologyService.create_category(db, "金融领域本体", "阶段二B测试")
        category_id = cat["id"]

        ont_person = await OntologyService.create_ontology(db, category_id, "人物", "人物本体", "#ef4444")
        ont_org = await OntologyService.create_ontology(db, category_id, "组织", "组织本体", "#3b82f6")
        ont_product = await OntologyService.create_ontology(db, category_id, "金融产品", "金融产品本体", "#10b981")
        person_id = ont_person["id"]
        org_id = ont_org["id"]
        product_id = ont_product["id"]

        # 人物属性
        from schemas import CreateOntologyAttributeRequest
        for attr_data in [
            {"name": "姓名", "data_type": "string", "is_required": True},
            {"name": "年龄", "data_type": "number", "is_required": False},
            {"name": "性别", "data_type": "enum", "is_required": False, "enum_values": ["男", "女"]},
        ]:
            await OntologyService.create_attribute(db, person_id, CreateOntologyAttributeRequest(**attr_data))

        # 组织属性
        for attr_data in [
            {"name": "名称", "data_type": "string", "is_required": True},
            {"name": "成立时间", "data_type": "date", "is_required": False},
        ]:
            await OntologyService.create_attribute(db, org_id, CreateOntologyAttributeRequest(**attr_data))

        # 金融产品属性
        for attr_data in [
            {"name": "名称", "data_type": "string", "is_required": True},
            {"name": "风险等级", "data_type": "enum", "is_required": False, "enum_values": ["低", "中", "高"]},
        ]:
            await OntologyService.create_attribute(db, product_id, CreateOntologyAttributeRequest(**attr_data))

        # 关系定义
        rel_works_at = await OntologyService.create_relation(db, category_id, "任职于", "人物在某组织任职")
        rel_holds = await OntologyService.create_relation(db, category_id, "持有", "持有某金融产品")
        rel_influences = await OntologyService.create_relation(db, category_id, "影响", "对另一对象产生影响")

        # 三元组约束
        await OntologyService.create_constraint(db, category_id, person_id, rel_works_at["id"], org_id)
        await OntologyService.create_constraint(db, category_id, person_id, rel_holds["id"], product_id)
        await OntologyService.create_constraint(db, category_id, org_id, rel_holds["id"], product_id)
        await OntologyService.create_constraint(db, category_id, org_id, rel_influences["id"], product_id)

        # 绑定知识库
        kb_id = "kb_phase2b_test"
        await OntologyService.bind_kb(db, kb_id, category_id)
        print("本体定义搭建完成（3本体/3关系/4三元组）")

        # ===== 2. 加载抽取约束 =====
        print("\n=== 步骤2：加载抽取约束 ===")
        constraint = await OntologyService.get_kb_extraction_constraints(db, kb_id)
        _check("约束非空", constraint is not None)
        _check("本体数=3", len(constraint["ontologies"]) == 3, f"actual={len(constraint['ontologies'])}")
        _check("关系数=3", len(constraint["relation_names"]) == 3, f"actual={len(constraint['relation_names'])}")
        _check("三元组数=4", len(constraint["constraints"]) == 4, f"actual={len(constraint['constraints'])}")
        _check("constraint_set 非空", len(constraint["constraint_set"]) == 4)
        _check("ontology_by_name 含人物", "人物" in constraint["ontology_by_name"])
        _check("relation_id_by_name 含任职于", "任职于" in constraint["relation_id_by_name"])

        # 验证人物本体的合并属性
        person_entry = constraint["ontology_by_name"]["人物"]
        attr_names = [a["name"] for a in person_entry["attributes"]]
        _check("人物属性含姓名/年龄/性别", set(attr_names) == {"姓名", "年龄", "性别"}, f"actual={attr_names}")

        # ===== 3. 测试动态 Prompt 构建 =====
        print("\n=== 步骤3：动态 Prompt 构建 ===")
        system_prompt = GraphExtractionService._build_constrained_system_prompt(constraint)
        _check("Prompt 含本体约束标记", "【本体约束" in system_prompt)
        _check("Prompt 含人物类型", "人物" in system_prompt)
        _check("Prompt 含组织类型", "组织" in system_prompt)
        _check("Prompt 含金融产品类型", "金融产品" in system_prompt)
        _check("Prompt 含三元组任职于", "任职于" in system_prompt)
        _check("Prompt 含属性姓名", "姓名" in system_prompt)
        _check("Prompt 含枚举值男/女", "男" in system_prompt and "女" in system_prompt)
        _check("Prompt 含必填标记", "必填" in system_prompt)
        _print("动态 System Prompt（前500字）", system_prompt[:500])

        # ===== 4. 测试后处理校验（_merge_payload）=====
        print("\n=== 步骤4：后处理校验 ===")
        # 模拟 LLM 返回的 payload（含合法与非法数据）
        mock_payload = {
            "chunks": [
                {
                    "chunk_id": "chunk_001",
                    "entities": [
                        # 合法实体：人物 + 属性
                        {
                            "name": "张三",
                            "entity_type": "人物",
                            "description": "某公司高管",
                            "properties": {
                                "姓名": "张三",
                                "年龄": "42",          # string→number 应规整
                                "性别": "男",           # 合法枚举
                                "非法属性": "应被剔除",   # 不在定义中，应剔除
                            },
                        },
                        # 合法实体：组织
                        {
                            "name": "A公司",
                            "entity_type": "组织",
                            "description": "互联网企业",
                            "properties": {"名称": "A公司", "成立时间": "2010-01-01"},
                        },
                        # 非法实体：类型不在本体中
                        {
                            "name": "某地点",
                            "entity_type": "地点",
                            "description": "应被过滤",
                        },
                        # 合法实体：金融产品，但 enum 值非法
                        {
                            "name": "基金X",
                            "entity_type": "金融产品",
                            "description": "高风险基金",
                            "properties": {
                                "名称": "基金X",
                                "风险等级": "极高",  # 不在枚举内，应置空剔除
                            },
                        },
                    ],
                    "relations": [
                        # 合法三元组：人物→任职于→组织
                        {
                            "source_name": "张三", "source_type": "人物",
                            "target_name": "A公司", "target_type": "组织",
                            "relation_type": "任职于",
                            "description": "张三在A公司任职",
                        },
                        # 非法三元组：类型不匹配（人物→影响→组织，不在约束中）
                        {
                            "source_name": "张三", "source_type": "人物",
                            "target_name": "A公司", "target_type": "组织",
                            "relation_type": "影响",
                            "description": "应被过滤",
                        },
                        # 非法三元组：关系不在字典中
                        {
                            "source_name": "张三", "source_type": "人物",
                            "target_name": "A公司", "target_type": "组织",
                            "relation_type": "投资",
                            "description": "应被过滤",
                        },
                        # 合法三元组：人物→持有→金融产品
                        {
                            "source_name": "张三", "source_type": "人物",
                            "target_name": "基金X", "target_type": "金融产品",
                            "relation_type": "持有",
                            "description": "张三持有基金X",
                        },
                    ],
                }
            ]
        }

        chunk = ChunkGraphData(chunk_id="chunk_001", chunk_index=0, content="模拟文本内容" * 20)
        chunk_map = {"chunk_001": chunk}
        GraphExtractionService._merge_payload(chunk_map, mock_payload, constraint)

        # 验证实体过滤
        entity_types = [e.entity_type for e in chunk.entities]
        _check("实体数=3（过滤掉地点）", len(chunk.entities) == 3, f"actual={len(chunk.entities)}, types={entity_types}")
        _check("无地点类型", "地点" not in entity_types)
        _check("含人物/组织/金融产品", set(entity_types) == {"人物", "组织", "金融产品"})

        # 验证属性规整
        person_entity = next(e for e in chunk.entities if e.entity_type == "人物")
        _check("人物 ontology_id 已回填", person_entity.ontology_id == person_id)
        props = json.loads(person_entity.properties) if person_entity.properties else {}
        _check("非法属性已剔除", "非法属性" not in props)
        _check("年龄已转 number", isinstance(props.get("年龄"), float), f"actual={type(props.get('年龄'))}")
        _check("性别保留（合法枚举）", props.get("性别") == "男")
        _check("姓名保留", props.get("姓名") == "张三")

        # 验证金融产品 enum 规整
        product_entity = next(e for e in chunk.entities if e.entity_type == "金融产品")
        product_props = json.loads(product_entity.properties) if product_entity.properties else {}
        _check("风险等级非法枚举已剔除", "风险等级" not in product_props, f"actual={product_props}")
        _check("金融产品名称保留", product_props.get("名称") == "基金X")

        # 验证关系过滤
        rel_types = [(r.source_type, r.relation_type, r.target_type) for r in chunk.relations]
        _check("关系数=2（过滤掉非法三元组）", len(chunk.relations) == 2, f"actual={len(chunk.relations)}, rels={rel_types}")
        _check("含人物→任职于→组织", ("人物", "任职于", "组织") in rel_types)
        _check("含人物→持有→金融产品", ("人物", "持有", "金融产品") in rel_types)
        _check("无人物→影响→组织", ("人物", "影响", "组织") not in rel_types)
        _check("无投资关系", "投资" not in [r.relation_type for r in chunk.relations])

        # 验证 relation_def_id 回填
        works_at_rel = next(r for r in chunk.relations if r.relation_type == "任职于")
        _check("任职于 relation_def_id 已回填", works_at_rel.relation_def_id == rel_works_at["id"])

        # ===== 5. 测试 SQLite 持久化 =====
        print("\n=== 步骤5：SQLite 持久化（_persist_extraction_to_sqlite）===")
        from services.file_service import FileService

        file_id = "file_phase2b_test"
        await FileService._persist_extraction_to_sqlite(
            db, file_id=file_id, kb_id=kb_id, batch_chunks=[chunk],
        )

        # 验证实体 id 已回填
        _check("人物实体 id 已回填", person_entity.id is not None and len(person_entity.id) > 0)
        _check("组织实体 id 已回填", chunk.entities[1].id is not None)
        _check("金融产品实体 id 已回填", product_entity.id is not None)

        # 验证关系 id 和起终点已回填
        _check("任职于关系 id 已回填", works_at_rel.id is not None)
        _check("任职于 source_entity_id 已回填", works_at_rel.source_entity_id == person_entity.id)
        _check("任职于 target_entity_id 已回填", works_at_rel.target_entity_id == chunk.entities[1].id)

        # 验证 SQLite 中确实写入了实体
        entities_page = await EntityService.list_entities(db, kb_id=kb_id, page=1, page_size=50)
        _check("SQLite 实体总数=3", entities_page["total"] == 3, f"actual={entities_page['total']}")

        # 验证 SQLite 中确实写入了关系
        from services.entity_service import EntityService as ES
        relations_page = await ES.list_relations(db, kb_id=kb_id, page=1, page_size=50)
        _check("SQLite 关系总数=2", relations_page["total"] == 2, f"actual={relations_page['total']}")

        # 验证实体详情中的属性
        person_detail = await EntityService.get_entity(db, person_entity.id)
        _check("实体详情非空", person_detail is not None)
        _check("实体详情 properties 含姓名", person_detail["properties"].get("姓名") == "张三")
        _check("实体详情 properties 含年龄(number)", person_detail["properties"].get("年龄") == 42.0)
        _check("实体详情无非法属性", "非法属性" not in person_detail["properties"])
        _check("实体详情 ontology_id 正确", person_detail["ontology_id"] == person_id)
        _print("人物实体详情", person_detail)

        # ===== 6. 测试无约束模式（向后兼容）=====
        print("\n=== 步骤6：无约束模式（向后兼容）===")
        free_payload = {
            "chunks": [{
                "chunk_id": "chunk_free",
                "entities": [
                    {"name": "自由实体", "entity_type": "概念", "description": "无约束"},
                ],
                "relations": [],
            }]
        }
        free_chunk = ChunkGraphData(chunk_id="chunk_free", chunk_index=0, content="自由文本" * 20)
        free_map = {"chunk_free": free_chunk}
        GraphExtractionService._merge_payload(free_map, free_payload, None)
        _check("无约束模式实体保留", len(free_chunk.entities) == 1)
        _check("无约束模式 ontology_id 为空", free_chunk.entities[0].ontology_id is None)

        # ===== 7. 清理 =====
        print("\n=== 步骤7：清理 ===")
        await OntologyService.unbind_kb(db, kb_id)
        await OntologyService.delete_category(db, category_id)
        print("清理完成")

    # 清理临时目录
    import shutil
    shutil.rmtree(_TMP_DIR, ignore_errors=True)

    print(f"\n{'='*60}")
    print(f"测试结果：{_passed} 通过，{_failed} 失败")
    print(f"{'='*60}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
