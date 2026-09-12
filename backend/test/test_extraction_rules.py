"""实体抽取规则端到端验证：规则注入 → 违规处置 → 报告 → 复核队列 → 审核入库。

与 test_phase2b.py 的定位区别：
- test_phase2b.py 验证「本体约束注入 + 后处理校验」的基础能力；
- 本脚本验证设计文档 doc/知识库/实体抽取属性级规则与人工复核设计.md 的完整链路：
  属性级规则（枚举/正则/范围）→ 证据驱动置信度 → 实体级判决 → 抽取报告 →
  复核队列 → 审核入库。

不调用真实 LLM：用 mock payload 驱动 GraphExtractionService.extract，
数据库为临时 SQLite（init_db 会真实执行 migrations.sql，顺带验证迁移可跑通）。

运行方式（在 backend 目录下）：
    python test/test_extraction_rules.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import types as _types
from pathlib import Path

# 在导入 config 之前设置环境变量，指向临时目录。
# 存储路径用强制覆盖（而非 setdefault）：测试必须隔离，避免外部 shell 残留的
# DATABASE_URL 等变量把测试指到真实库上。
_TMP_DIR = Path(tempfile.mkdtemp(prefix="knowsource_rules_"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["CHROMA_PERSIST_DIR"] = str(_TMP_DIR / "chroma")
os.environ["KUZU_DB_PATH"] = str(_TMP_DIR / "graph.kuzu")
os.environ["UPLOAD_DIR"] = str(_TMP_DIR / "uploads")
os.environ["CHUNK_DIR"] = str(_TMP_DIR / "chunks")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8766")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
os.environ.setdefault("EMBEDDING_DIMENSION", "512")
os.environ.setdefault("VECTOR_STORE_PROVIDER", "chroma")
os.environ.setdefault("GRAPH_STORE_PROVIDER", "kuzu")
# 抽取必须开启，否则 extract() 直接返回
os.environ.setdefault("GRAPH_ENTITY_EXTRACTION_ENABLED", "true")
os.environ.setdefault("GRAPH_EXTRACTION_BATCH_SIZE", "6")
os.environ.setdefault("GRAPH_EXTRACTION_CONCURRENCY", "1")
os.environ.setdefault("GRAPH_MIN_CHARS_FOR_EXTRACTION", "1")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("OPENAI_API_KEY", "test_key")
os.environ.setdefault("OPENAI_BASE_URL", "http://localhost:9999/v1")
os.environ.setdefault("LLM_MODEL", "test-model")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# Stub 缺失的可选依赖（langchain_core）
if "langchain_core" not in sys.modules:
    _lc = _types.ModuleType("langchain_core")
    _lc.__path__ = []
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
from providers.graph_store import ChunkGraphData  # noqa: E402
from schemas import CreateOntologyAttributeRequest  # noqa: E402
from services.extraction_review_service import ExtractionReviewService  # noqa: E402
from services.extraction_rule_service import ExtractionReport  # noqa: E402
import services.graph_extraction_service as ges  # noqa: E402
from services.ontology_service import OntologyService  # noqa: E402

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


# 原文：用于证据驱动的置信度打分（值是否在原文精确出现）
CHUNK_CONTENT = (
    "张伟，工号AB1234，2019年入职，现任技术部负责人。"
    "李娜，工号XY9900，2021年入职。"
    "王强，工号Z12，2018年入职，年龄已超过六十岁。"
)

# 模拟 LLM 返回的 payload：故意混入各类型违规数据
MOCK_PAYLOAD = {
    "chunks": [
        {
            "chunk_id": "chunk-1",
            "entities": [
                # ① 完全合规
                {"name": "张伟", "entity_type": "人物", "description": "技术部负责人",
                 "properties": {"姓名": "张伟", "工号": "AB1234", "职级": "正式", "年龄": "35"}},
                # ② 枚举违规（职级=待定）→ drop_attribute，实体保留
                {"name": "李娜", "entity_type": "人物", "description": "2021年入职",
                 "properties": {"姓名": "李娜", "工号": "XY9900", "职级": "待定", "年龄": "28"}},
                # ③ 正则违规（工号=Z12）→ on_violation=review，实体进复核队列
                {"name": "王强", "entity_type": "人物", "description": "2018年入职",
                 "properties": {"姓名": "王强", "工号": "Z12", "职级": "试用", "年龄": "61"}},
                # ④ 必填缺失（无姓名）→ 实体进复核队列
                {"name": "赵四", "entity_type": "人物", "description": "缺少姓名",
                 "properties": {"职级": "正式"}},
            ],
            "relations": [],
        }
    ]
}


class _MockResponse:
    def __init__(self, content):
        self.content = content


class _MockLLM:
    async def ainvoke(self, messages):
        return _MockResponse(
            "```json\n" + json.dumps(MOCK_PAYLOAD, ensure_ascii=False) + "\n```"
        )


async def main():
    print(f"临时目录: {_TMP_DIR}")
    print("初始化数据库（会真实执行 migrations.sql）...")
    await init_db()
    print("init_db 完成")

    async with async_session() as db:
        # ===== 1. 搭建带规则的本体 =====
        print("\n=== 步骤1：搭建本体并配置抽取规则 ===")
        cat = await OntologyService.create_category(db, "规则验证本体", "抽取规则端到端测试")
        category_id = cat["id"]
        ont = await OntologyService.create_ontology(db, category_id, "人物", "人员对象类型")
        ontology_id = ont["id"]

        for attr_data in [
            # 必填属性
            {"name": "姓名", "data_type": "string", "is_required": True},
            # 枚举：不合规值 → 默认 drop_attribute
            {"name": "职级", "data_type": "string", "is_required": False,
             "enum_values": ["正式", "试用", "离职"],
             "on_violation": "drop_attribute"},
            # 正则：不合规值 → 整实体进人工复核
            {"name": "工号", "data_type": "string", "is_required": False,
             "value_pattern": r"^[A-Z]{2}\d{4}$",
             "on_violation": "review"},
            # 范围：61 超出上界 60 → drop_attribute
            {"name": "年龄", "data_type": "number", "is_required": False,
             "min_value": "18", "max_value": "60",
             "extraction_hint": "以周岁计", "extraction_examples": ["35"],
             "negative_examples": ["未知"]},
        ]:
            await OntologyService.create_attribute(
                db, ontology_id, CreateOntologyAttributeRequest(**attr_data)
            )

        kb_id = "kb_rules_test"
        await OntologyService.bind_kb(db, kb_id, category_id)
        print("本体搭建完成：人物（姓名必填 / 职级枚举 / 工号正则→复核 / 年龄范围）")

        # ===== 2. 约束装载：规则字段必须被带出 =====
        print("\n=== 步骤2：加载抽取约束 ===")
        constraint = await OntologyService.get_kb_extraction_constraints(db, kb_id)
        _check("约束非空", constraint is not None)
        person = constraint["ontology_by_name"]["人物"]
        attrs = {a["name"]: a for a in person["attributes"]}
        _check("枚举规则已带出", attrs["职级"]["enum_values"] == ["正式", "试用", "离职"],
               attrs["职级"].get("enum_values"))
        _check("正则规则已带出", attrs["工号"]["value_pattern"] == r"^[A-Z]{2}\d{4}$")
        _check("正则已预编译（性能：装载期编译一次）",
               attrs["工号"].get("_compiled_pattern") is not None)
        _check("范围规则已带出",
               attrs["年龄"]["min_value"] == "18" and attrs["年龄"]["max_value"] == "60")
        _check("违规处置策略已带出", attrs["工号"]["on_violation"] == "review")
        _check("提示/示例已带出",
               attrs["年龄"]["extraction_hint"] == "以周岁计"
               and attrs["年龄"]["extraction_examples"] == ["35"])

        # ===== 3. Prompt 注入 =====
        print("\n=== 步骤3：构建约束 Prompt ===")
        prompt = ges.GraphExtractionService._build_constrained_system_prompt(constraint)
        _check("Prompt 含抽取要求块", "抽取要求" in prompt)
        _check("Prompt 含枚举提示", "取值仅限 [正式|试用|离职]" in prompt)
        _check("Prompt 含正则提示", r"^[A-Z]{2}\d{4}$" in prompt)
        _check("Prompt 含范围提示", "取值范围 18 ~ 60" in prompt)
        _check("Prompt 含提示词", "以周岁计" in prompt)
        _check("Prompt 含反例", "不要抽取：未知" in prompt)
        _check("Prompt 不暴露内部处置策略（避免模型少抽）",
               "丢弃" not in prompt and "drop" not in prompt.lower())

        # ===== 4. 抽取 + 规则校验 + 报告 + 复核队列 =====
        print("\n=== 步骤4：抽取（mock LLM）→ 规则校验 → 报告 → 队列 ===")
        chunks = [ChunkGraphData(chunk_id="chunk-1", chunk_index=0, content=CHUNK_CONTENT)]
        review_sink: list = []
        report = ExtractionReport()
        # 用 mock LLM 替换真实 LLM 工厂
        ges.create_llm = lambda: _MockLLM()

        await ges.GraphExtractionService.extract(
            "测试文档.txt",
            chunks,
            ontology_constraint=constraint,
            review_sink=review_sink,
            report=report,
        )

        entities = chunks[0].entities
        names = {e.name for e in entities}
        print(f"  通过规则直接入库的实体：{sorted(names)}")
        _check("合规实体 张伟 入库", "张伟" in names)
        _check("枚举违规实体 李娜 仍入库（只丢属性值）", "李娜" in names)
        _check("正则违规实体 王强 被送复核（未入库）", "王强" not in names)
        _check("必填缺失实体 赵四 被送复核（未入库）", "赵四" not in names)

        # 属性值降级验证
        li = next((e for e in entities if e.name == "李娜"), None)
        if li:
            props = json.loads(li.properties or "{}")
            _check("李娜 的违规职级被剔除", "职级" not in props, props)
            _check("李娜 的合规属性保留", props.get("工号") == "XY9900", props)

        # ===== 5. 抽取报告 =====
        print("\n=== 步骤5：抽取报告（写入 File.detail.extraction_report 的内容） ===")
        report_dict = report.to_dict()
        print(json.dumps(report_dict, ensure_ascii=False, indent=2))
        _check("报告统计到 4 个原始实体", report_dict["total_entities_raw"] == 4,
               report_dict["total_entities_raw"])
        _check("报告统计到 2 个通过", report_dict["passed"] == 2, report_dict["passed"])
        _check("报告统计到 2 个待复核", report_dict["reviewed"] == 2, report_dict["reviewed"])
        _check("报告统计到属性降级", report_dict["downgraded_attributes"] >= 1,
               report_dict["downgraded_attributes"])
        _check("报告含置信度分布", sum(report_dict["confidence_histogram"].values()) == 4)
        _check("报告含样例", len(report_dict["samples"]) >= 1)

        # ===== 6. 复核队列 =====
        print("\n=== 步骤6：复核队列 ===")
        queued = await ExtractionReviewService.add_from_sink(
            db, kb_id=kb_id, file_id="file-1", sink=review_sink
        )
        _check("2 条进入复核队列", queued == 2, queued)

        listed = await ExtractionReviewService.list_reviews(db, kb_id=kb_id)
        _check("队列查询返回 2 条", listed["total"] == 2, listed["total"])
        _check("队列状态为 pending", all(i["status"] == "pending" for i in listed["items"]))

        stats = await ExtractionReviewService.stats(db, kb_id=kb_id)
        print(f"  队列统计：{json.dumps(stats, ensure_ascii=False)}")
        _check("统计 pending=2", stats["by_status"].get("pending") == 2, stats["by_status"])

        # 待复核条目应能看到违背了哪条规则、原始值
        for item in listed["items"]:
            print(f"  · {item['entity_type']}/{item['entity_name']} "
                  f"主因={item['primary_rule']} 置信度={item['confidence']} "
                  f"违规={[v['reason'] for v in item['violations']]}")
        _check("记录了具体违规原因",
               all(i["violations"] for i in listed["items"]))
        _check("保留了原始抽取结果供人工对照",
               any(i["raw_properties"] for i in listed["items"]))

        # ===== 7. 审核入库 =====
        print("\n=== 步骤7：人工审核 → 通过入库 ===")
        from services.entity_service import EntityService

        before = await EntityService.list_entities(db, kb_id=kb_id, page=1, page_size=100)
        before_total = before.get("total", 0) if isinstance(before, dict) else 0

        target = next(i for i in listed["items"] if i["entity_name"] == "王强")
        approved = await ExtractionReviewService.approve(
            db,
            target["id"],
            reviewer="tester",
            properties={"姓名": "王强", "工号": "AB9999", "职级": "试用"},  # 人工补全合规值
            review_notes="人工修正工号后入库",
        )
        _check("审核后状态 approved", approved["status"] == "approved", approved["status"])
        _check("记录审核人", approved["reviewer"] == "tester")

        after = await EntityService.list_entities(db, kb_id=kb_id, page=1, page_size=100)
        after_total = after.get("total", 0) if isinstance(after, dict) else 0
        _check("实体已入库（总数 +1）", after_total == before_total + 1,
               f"{before_total} -> {after_total}")

        # 重复审核应被拒绝
        try:
            await ExtractionReviewService.approve(db, target["id"], reviewer="tester")
            _check("重复审核被拒绝", False, "未抛异常")
        except ValueError:
            _check("重复审核被拒绝", True)

        # 驳回不入库
        other = next(i for i in listed["items"] if i["entity_name"] == "赵四")
        rejected = await ExtractionReviewService.reject(
            db, other["id"], reviewer="tester", review_notes="信息不足"
        )
        _check("驳回后状态 rejected", rejected["status"] == "rejected")
        after2 = await EntityService.list_entities(db, kb_id=kb_id, page=1, page_size=100)
        after2_total = after2.get("total", 0) if isinstance(after2, dict) else 0
        _check("驳回不入库", after2_total == after_total, f"{after_total} -> {after2_total}")

        # ===== 8. 重处理：旧 pending 过期 =====
        print("\n=== 步骤8：文件重新处理 → 旧 pending 过期 ===")
        await ExtractionReviewService.add_from_sink(
            db, kb_id=kb_id, file_id="file-1",
            sink=[(_types.SimpleNamespace(
                name="新一批", entity_type="人物", ontology_id=ontology_id,
                properties="{}", description=""),
                   _types.SimpleNamespace(
                       violations=[], confidence=0.4, raw_properties="{}",
                       primary_rule="required"),
                   "chunk-1")],
        )
        pending_before = (await ExtractionReviewService.stats(db, kb_id=kb_id))["by_status"].get("pending", 0)
        expired = await ExtractionReviewService.expire_by_file(db, "file-1")
        pending_after = (await ExtractionReviewService.stats(db, kb_id=kb_id))["by_status"].get("pending", 0)
        _check("过期 1 条", expired == 1, expired)
        _check("pending 减少", pending_after == pending_before - 1,
               f"{pending_before} -> {pending_after}")

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    import traceback

    try:
        _code = asyncio.run(main())
    except BaseException:
        # 异常时必须打印并走 os._exit：否则 aiosqlite 工作线程会挂住进程，
        # 输出与退出码都无法回传
        traceback.print_exc()
        _code = 1
    # Windows 下 aiosqlite 的工作线程（非 daemon）会阻塞 threading._shutdown，
    # 导致脚本逻辑完成后进程挂起不退出；此处强制结束进程并回传退出码。
    os._exit(_code)
