"""检索融合共享工具。

被 RAG 知识问答（rag_service）与 OAG 智能体（oag_service）共同复用，
统一分片 id 重建与 RRF（Reciprocal Rank Fusion）重排逻辑。
"""
from __future__ import annotations

from config import settings


def chunk_id_from_vector_metadata(metadata: dict) -> str | None:
    """从向量分片元数据重建与 Chroma doc id / Kùzu Chunk.id 一致的 chunk_id。

    写入侧 chunk_id = f"{file_id}_{chunk_index}"（file_service.py），向量库 id 同源。
    """
    if not isinstance(metadata, dict):
        return None
    file_id = metadata.get("file_id")
    chunk_index = metadata.get("chunk_index")
    if file_id is None or chunk_index is None:
        return None
    return f"{file_id}_{chunk_index}"


def rrf_fuse(rank_lists: list[list[str]], k: int | None = None) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion：多个有序 id 列表 → 按 RRF 分数降序。

    分数 = Σ 1/(k + rank + 1)，rank 为该 id 在各列表中的位置（0 起）。
    """
    k = k or settings.HYBRID_RRF_K
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, chunk_id in enumerate(ranks):
            if not chunk_id:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
