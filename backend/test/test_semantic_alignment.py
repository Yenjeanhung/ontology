"""语义实体对齐（graph_cleanup_service._semantic_merge_suggestions）单元测试。

不依赖真实嵌入模型 / 主库：aiosqlite 内存库 + 假 Embeddings（预设向量表）。
覆盖：首轮编码聚簇、缓存幂等（增量不重编码）、全量比对（建议可重复出现）、
阈值拦截、防误合（享界S9/享界G9）、内容变更触发过期重算、suggest 集成去重。

运行：python test/test_semantic_alignment.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("EMBEDDING_PROVIDER", "local")
os.environ.setdefault("EMBEDDING_MODEL", "fake-embed")
os.environ.setdefault("EMBEDDING_DIMENSION", "2")
os.environ.setdefault("EMBEDDING_PROVIDER", "local")
_TMP_DIR = Path(tempfile.mkdtemp(prefix="ont_semantic_"))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP_DIR / 'test.db').as_posix()}")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from config import settings  # noqa: E402
from models import Base, Entity, EntityVector  # noqa: E402
from services import graph_cleanup_service as gcs  # noqa: E402


class FakeEmbeddings:
    """按精确文本返回预设向量的假嵌入模型；记录调用，未命中文本返回零向量。"""

    def __init__(self, table: dict[str, list[float]], dim: int = 2):
        self.table = table
        self.dim = dim
        self.encode_calls = 0
        self.encoded: list[str] = []

    def embed_documents(self, texts):
        self.encode_calls += 1
        self.encoded.extend(texts)
        return [list(self.table.get(t, [0.0] * self.dim)) for t in texts]

    def embed_query(self, text):
        return list(self.table.get(text, [0.0] * self.dim))


def _text(name: str, desc: str = "") -> str:
    return gcs._entity_embed_text(name, desc)


def _ent(eid: str, name: str, desc: str = "", etype: str = "产品", deg: int = 0) -> Entity:
    return Entity(
        id=eid, kb_id="kb1", ontology_id="ont1", entity_type=etype,
        name=name, description=desc,
    )


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync: Base.metadata.create_all(sync, tables=[EntityVector.__table__])
        )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _suggest_once(db, ents, degree, fake: FakeEmbeddings) -> list[dict]:
    orig = gcs.create_embeddings
    gcs.create_embeddings = lambda: fake
    try:
        return await gcs.GraphCleanupService._semantic_merge_suggestions(db, ents, degree)
    finally:
        gcs.create_embeddings = orig


async def case_first_run_clusters():
    """首轮：全量编码，同向量聚簇出语义建议，source=semantic。"""
    fake = FakeEmbeddings({
        _text("中国国际航空"): [1.0, 0.0],
        _text("国航"): [1.0, 0.0],
        _text("首都机场"): [0.0, 1.0],
        _text("大兴机场"): [0.92, 0.39],   # 与首都机场 cos≈0.39 < 0.90
    })
    engine, maker = await _make_db()
    async with maker() as db:
        ents = [
            _ent("e1", "中国国际航空", deg=3),
            _ent("e2", "国航", deg=1),
            _ent("e3", "首都机场", etype="地点"),
            _ent("e4", "大兴机场", etype="地点"),
        ]
        groups = await _suggest_once(db, ents, {"e1": 3, "e2": 1}, fake)
        assert len(groups) == 1, f"期望 1 组，实际 {groups}"
        g = groups[0]
        assert g["source"] == "semantic"
        assert {m["id"] for m in g["members"]} == {"e1", "e2"}, f"成员错误: {g}"
        assert g["canonical_id"] == "e1", "canonical 应取度数更高者"
        rows = (await db.execute(gcs.select(EntityVector))).scalars().all()
        assert len(rows) == 4, f"应回写 4 条缓存，实际 {len(rows)}"
    await engine.dispose()
    print("  ok: case_first_run_clusters")


async def case_cache_idempotent_and_repeated_suggestions():
    """二轮无变更：不再编码，但全量比对仍给出建议（用户未处理也不消失）。"""
    fake = FakeEmbeddings({
        _text("中国国际航空"): [1.0, 0.0],
        _text("国航"): [1.0, 0.0],
    })
    engine, maker = await _make_db()
    async with maker() as db:
        ents = [_ent("e1", "中国国际航空", deg=2), _ent("e2", "国航")]
        degree = {"e1": 2, "e2": 0}
        await _suggest_once(db, ents, degree, fake)
        assert fake.encode_calls == 1
        groups2 = await _suggest_once(db, ents, degree, fake)
        assert fake.encode_calls == 1, "二轮不应重复编码"
        assert len(groups2) == 1, f"二轮建议应仍在（全量比对），实际 {groups2}"
    await engine.dispose()
    print("  ok: case_cache_idempotent_and_repeated_suggestions")


async def case_threshold_blocks():
    """低于阈值不产生建议。"""
    fake = FakeEmbeddings({
        _text("甲"): [1.0, 0.0],
        _text("乙"): [0.0, 1.0],   # cos=0
    })
    engine, maker = await _make_db()
    async with maker() as db:
        groups = await _suggest_once(
            db, [_ent("a", "甲"), _ent("b", "乙")], {}, fake
        )
        assert groups == [], f"低相似不应出建议，实际 {groups}"
    await engine.dispose()
    print("  ok: case_threshold_blocks")


async def case_distinct_products_guard():
    """品牌同、型号编码不同：即使向量极相似也不建议合并。"""
    fake = FakeEmbeddings({
        _text("享界S9"): [1.0, 0.0],
        _text("享界G9"): [1.0, 0.0],
    })
    engine, maker = await _make_db()
    async with maker() as db:
        groups = await _suggest_once(
            db, [_ent("p1", "享界S9"), _ent("p2", "享界G9")], {}, fake
        )
        assert groups == [], f"不同产品不应合并，实际 {groups}"
    await engine.dispose()
    print("  ok: case_distinct_products_guard")


async def case_stale_recompute():
    """描述变更 → content_hash 过期 → 该实体重编码，其余走缓存。"""
    fake = FakeEmbeddings({
        _text("国航", "老描述"): [1.0, 0.0],
        _text("中国国际航空"): [1.0, 0.0],
    })
    engine, maker = await _make_db()
    async with maker() as db:
        e2 = _ent("e2", "国航", desc="老描述")
        ents = [_ent("e1", "中国国际航空", deg=1), e2]
        await _suggest_once(db, ents, {"e1": 1}, fake)
        assert fake.encode_calls == 1
        # e2 描述变更：hash 变化 → 仅 e2 重编码（新文本默认零向量，不再出建议）
        e2.description = "新描述"
        groups2 = await _suggest_once(db, ents, {"e1": 1}, fake)
        assert fake.encode_calls == 2, f"应仅重编码过期实体，encode 次数 {fake.encode_calls}"
        assert groups2 == []
    await engine.dispose()
    print("  ok: case_stale_recompute")


async def case_suggest_cleanup_integration():
    """suggest_cleanup 集成：语义组与字面组有交集时整组去重，summary 计数正确。"""
    fake = FakeEmbeddings({
        # 「问界M9」与「问界M9（尊界版）」字面相似(SequenceMatcher>0.72) → 字面通道出组
        _text("问界M9"): [1.0, 0.0],
        _text("问界M9（尊界版）"): [1.0, 0.0],
    })
    orig = gcs.create_embeddings
    gcs.create_embeddings = lambda: fake
    engine, maker = await _make_db()
    async with maker() as db:
        ents = [
            _ent("m1", "问界M9"),
            _ent("m2", "问界M9（尊界版）"),
        ]
        db.add_all(ents)
        await db.commit()
        res = await gcs.GraphCleanupService.suggest_cleanup(db, kb_id="kb1")
        # 字面组已覆盖 → 语义组应被去重，不重复出现
        semantic_sources = [g for g in res["merge_groups"] if g.get("source") == "semantic"]
        assert res["merge_groups"], "应至少有字面建议"
        assert not semantic_sources, f"语义组与字面组重叠应去重，实际 {semantic_sources}"
        assert res["summary"]["semantic_merge_group_count"] == 0
    await engine.dispose()
    gcs.create_embeddings = orig
    print("  ok: case_suggest_cleanup_integration")


async def main() -> int:
    assert settings.GRAPH_CLEANUP_SEMANTIC_ENABLED, "语义通道应默认开启"
    for name, coro in [
        ("first_run_clusters", case_first_run_clusters()),
        ("cache_idempotent", case_cache_idempotent_and_repeated_suggestions()),
        ("threshold_blocks", case_threshold_blocks()),
        ("distinct_products", case_distinct_products_guard()),
        ("stale_recompute", case_stale_recompute()),
        ("suggest_integration", case_suggest_cleanup_integration()),
    ]:
        print(f"[case] {name} ...", flush=True)
        await coro
    print("ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    _code = asyncio.run(main())
    # 异常时必须打印并走 os._exit：否则 aiosqlite 工作线程会挂住进程
    sys.stdout.flush()
    os._exit(_code)
