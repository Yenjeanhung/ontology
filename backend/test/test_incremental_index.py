"""知识库文档增量更新单元测试（内容指纹 + 分片 diff 决策）。

运行：cd backend && python -m pytest test/test_incremental_index.py -q
不依赖数据库 / 向量库 / LLM：纯函数验证复用、新嵌入、过期清理三类决策。
"""
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.file_service import (
    build_chunk_hash_index,
    chunk_content_hash,
    make_chunk_id,
    select_stale_chunks,
    sha256_file,
)


def _row(cid: str, content_hash: str | None, embedding_id: str | None = "__auto__"):
    """模拟 Chunk 行：增量 diff 只关心 id / content_hash / embedding_id。"""
    return SimpleNamespace(
        id=cid,
        content_hash=content_hash,
        embedding_id=cid if embedding_id == "__auto__" else embedding_id,
    )


# ── 内容指纹 ──────────────────────────────────────────────


def test_chunk_content_hash_stable_and_discriminating():
    assert chunk_content_hash("同一段内容") == chunk_content_hash("同一段内容")
    assert chunk_content_hash("同一段内容") != chunk_content_hash("同一段内容 ")
    assert (
        chunk_content_hash("abc")
        == hashlib.sha256("abc".encode("utf-8")).hexdigest()
    )


def test_make_chunk_id_content_addressed():
    h = "a" * 64
    assert make_chunk_id("f1", h) == f"f1_{'a' * 16}"
    # 同文件内重复内容分片追加序号去重
    assert make_chunk_id("f1", h, 2) == f"f1_{'a' * 16}_2"
    assert make_chunk_id("f1", h, 3) != make_chunk_id("f1", h, 2)


def test_sha256_file_matches_direct_hash(tmp_path: Path):
    p = tmp_path / "doc.txt"
    p.write_bytes(b"hello incremental update")
    assert sha256_file(p) == hashlib.sha256(b"hello incremental update").hexdigest()
    # 大于读取块（1MB）的文件：流式读取结果一致
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (1024 * 1024 * 2 + 7))
    assert sha256_file(big) == hashlib.sha256(big.read_bytes()).hexdigest()


# ── diff 索引构建 ─────────────────────────────────────────


def test_build_chunk_hash_index_groups_and_skips_legacy():
    old = [
        _row("f1_aaa", "aaa"),
        _row("f1_aaa_dup", "aaa"),
        _row("f1_bbb", "bbb"),
        # 存量数据（migration_035 之前）：无 hash 不参与复用
        _row("f1_0", None),
        # embedding_id 缺失：向量 id 不明，复用有风险，同样跳过
        _row("f1_ccc", "ccc", embedding_id=None),
    ]
    idx = build_chunk_hash_index(old)
    assert sorted(idx.keys()) == ["aaa", "bbb"]
    assert [c.id for c in idx["aaa"]] == ["f1_aaa", "f1_aaa_dup"]


# ── 三类决策：复用 / 新嵌入 / 过期清理 ────────────────────


def test_incremental_diff_full_decision_flow():
    old = [
        _row("f1_aaa", "aaa"),
        _row("f1_aaa_2", "aaa"),
        _row("f1_bbb", "bbb"),
        _row("f1_0", None),  # 存量老格式分片
    ]
    idx = build_chunk_hash_index(old)

    # 新一轮分片：aaa×3（旧行只有 2 条可复用）、ccc 新增；bbb 消失
    new_hashes = ["aaa", "aaa", "aaa", "ccc"]
    reused, fresh, seen, dup = [], [], set(), {}
    for h in new_hashes:
        dup[h] = dup.get(h, 0) + 1
        bucket = idx.get(h)
        if bucket:
            row = bucket.popleft()
            seen.add(row.id)
            reused.append(row.id)
            continue
        fresh.append(make_chunk_id("f1", h, dup[h]))

    assert reused == ["f1_aaa", "f1_aaa_2"]
    assert fresh == [make_chunk_id("f1", "aaa", 3), make_chunk_id("f1", "ccc", 1)]

    stale = select_stale_chunks(old, seen)
    assert sorted(c.id for c in stale) == ["f1_0", "f1_bbb"]


def test_first_processing_no_stale():
    """首次处理：无旧分片 → 全部新嵌入，无清理。"""
    old: list = []
    idx = build_chunk_hash_index(old)
    assert idx == {}
    assert select_stale_chunks(old, set()) == []


def test_unchanged_document_full_reuse():
    """内容未变的重复重处理：全部分片复用，零嵌入零清理（策略未变时）。"""
    hashes = ["h1", "h2", "h3"]
    old = [_row(make_chunk_id("f1", h, 1), h) for h in hashes]
    idx = build_chunk_hash_index(old)

    reused, seen = [], set()
    for h in hashes:
        row = idx[h].popleft()
        seen.add(row.id)
        reused.append(row.id)
    assert reused == [c.id for c in old]
    assert select_stale_chunks(old, seen) == []
