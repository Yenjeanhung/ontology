from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Chunk, File, KnowledgeBase
from providers.embedding import create_embeddings
from providers.vector_store import (
    create_vector_store,
    enrich_vector_index_records,
    get_vector_store_provider_name,
)


class VectorDataService:

    @staticmethod
    def _source_tables(provider: str) -> dict:
        vector_tables: list[str] = []
        if provider == "chroma":
            vector_tables = [
                "collections",
                "embeddings",
                "embedding_metadata",
            ]

        return {
            "business": ["knowledge_bases", "files", "chunks"],
            "vector_store": vector_tables,
        }

    @staticmethod
    def _build_filters(kb_id: str | None = None, query: str | None = None) -> list:
        filters = []
        if kb_id:
            filters.append(File.kb_id == kb_id)
        if query:
            like = f"%{query.strip()}%"
            filters.append(
                or_(
                    File.name.like(like),
                    Chunk.content.like(like),
                    Chunk.embedding_id.like(like),
                )
            )
        return filters

    @staticmethod
    async def _fetch_rows(
        db: AsyncSession,
        filters: list,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict]:
        stmt = (
            select(
                Chunk.id,
                Chunk.file_id,
                Chunk.chunk_index,
                Chunk.embedding_id,
                Chunk.content,
                Chunk.created_at,
                File.kb_id,
                File.name,
                KnowledgeBase.name,
            )
            .join(File, Chunk.file_id == File.id)
            .join(KnowledgeBase, File.kb_id == KnowledgeBase.id)
            .order_by(File.created_at.desc(), File.id.asc(), Chunk.chunk_index.asc())
        )
        if filters:
            stmt = stmt.where(*filters)
        if limit is not None:
            stmt = stmt.limit(limit)
        if offset is not None:
            stmt = stmt.offset(offset)

        rows = (await db.execute(stmt)).all()
        return [
            {
                "chunk_id": row[0],
                "file_id": row[1],
                "chunk_index": row[2],
                "embedding_id": row[3],
                "content_preview": (row[4] or "")[:240],
                "content_full": row[4] or "",
                "content_length": len(row[4] or ""),
                "created_at": row[5],
                "kb_id": row[6],
                "file_name": row[7],
                "kb_name": row[8],
                "store_found": None,
                "store_document_preview": "",
                "store_metadata": {},
            }
            for row in rows
        ]

    @staticmethod
    def _enrich_items(items: list[dict]) -> list[dict]:
        grouped_items = defaultdict(list)
        for item in items:
            grouped_items[item["kb_id"]].append(item)

        enriched_items = []
        for current_kb_id, group in grouped_items.items():
            enriched_items.extend(enrich_vector_index_records(current_kb_id, group))
        return enriched_items

    @staticmethod
    async def list_records(
        db: AsyncSession,
        kb_id: str | None = None,
        query: str | None = None,
        unsynced_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        provider = get_vector_store_provider_name()
        page_size = max(1, min(limit, 200))
        page_offset = max(0, offset)
        filters = VectorDataService._build_filters(kb_id=kb_id, query=query)

        total_stmt = (
            select(func.count())
            .select_from(Chunk)
            .join(File, Chunk.file_id == File.id)
            .join(KnowledgeBase, File.kb_id == KnowledgeBase.id)
        )
        if filters:
            total_stmt = total_stmt.where(*filters)
        source_total = (await db.execute(total_stmt)).scalar_one()

        if unsynced_only:
            raw_items = await VectorDataService._fetch_rows(db, filters)
            enriched_items = VectorDataService._enrich_items(raw_items)
            filtered_items = [
                item
                for item in enriched_items
                if item.get("store_found") is False or not item.get("embedding_id")
            ]
            total = len(filtered_items)
            items = filtered_items[page_offset: page_offset + page_size]
        else:
            raw_items = await VectorDataService._fetch_rows(
                db,
                filters,
                limit=page_size,
                offset=page_offset,
            )
            items = VectorDataService._enrich_items(raw_items)
            total = source_total

        page = (page_offset // page_size) + 1
        return {
            "provider": provider,
            "kb_id": kb_id,
            "total": total,
            "source_total": source_total,
            "limit": page_size,
            "offset": page_offset,
            "page": page,
            "has_prev": page_offset > 0,
            "has_next": page_offset + page_size < total,
            "source_tables": VectorDataService._source_tables(provider),
            "items": items,
        }

    @staticmethod
    async def similarity_test(kb_id: str, query: str, top_k: int = 10) -> dict:
        embeddings = create_embeddings()
        vectorstore = create_vector_store(kb_id, embeddings)
        docs_with_scores = vectorstore.similarity_search_with_score(
            query,
            k=max(1, min(top_k, 20)),
        )

        items = []
        for idx, (doc, score) in enumerate(docs_with_scores, start=1):
            items.append(
                {
                    "rank": idx,
                    "score": round(1 - float(score), 4),
                    "file_id": doc.metadata.get("file_id", ""),
                    "file_name": doc.metadata.get("file_name", ""),
                    "page_number": doc.metadata.get("page_number"),
                    "start_offset": doc.metadata.get("start_offset"),
                    "end_offset": doc.metadata.get("end_offset"),
                    "file_ext": doc.metadata.get("file_ext", ""),
                    "chunk_text": doc.page_content,
                }
            )

        return {
            "provider": get_vector_store_provider_name(),
            "kb_id": kb_id,
            "query": query,
            "top_k": top_k,
            "items": items,
        }

    @staticmethod
    async def export_summary(
        db: AsyncSession,
        kb_id: str | None = None,
        fmt: str = "json",
    ):
        records = await VectorDataService.list_records(
            db,
            kb_id=kb_id,
            limit=200,
            offset=0,
        )
        items = records["items"]

        kb_groups = defaultdict(
            lambda: {
                "kb_name": "",
                "files": defaultdict(int),
                "synced": 0,
                "unsynced": 0,
            }
        )
        for item in items:
            group = kb_groups[item["kb_id"]]
            group["kb_name"] = item["kb_name"]
            group["files"][item["file_name"]] += 1
            if item.get("store_found") is False or not item.get("embedding_id"):
                group["unsynced"] += 1
            else:
                group["synced"] += 1

        summary = {
            "provider": records["provider"],
            "kb_id": kb_id,
            "record_count": len(items),
            "source_tables": records["source_tables"],
            "knowledge_bases": [
                {
                    "kb_id": current_kb_id,
                    "kb_name": data["kb_name"],
                    "file_count": len(data["files"]),
                    "chunk_count": sum(data["files"].values()),
                    "synced_count": data["synced"],
                    "unsynced_count": data["unsynced"],
                    "files": [
                        {"file_name": file_name, "chunk_count": chunk_count}
                        for file_name, chunk_count in data["files"].items()
                    ],
                }
                for current_kb_id, data in kb_groups.items()
            ],
        }

        if fmt == "md":
            lines = [
                "# Vector Summary",
                "",
                f"- Provider: {summary['provider']}",
                f"- Record Count: {summary['record_count']}",
                f"- Business Tables: {', '.join(summary['source_tables']['business'])}",
                f"- Vector Tables: {', '.join(summary['source_tables']['vector_store']) or '--'}",
                "",
            ]
            for kb in summary["knowledge_bases"]:
                lines.extend(
                    [
                        f"## {kb['kb_name']} ({kb['kb_id']})",
                        f"- Files: {kb['file_count']}",
                        f"- Chunks: {kb['chunk_count']}",
                        f"- Synced: {kb['synced_count']}",
                        f"- Unsynced: {kb['unsynced_count']}",
                        "",
                    ]
                )
                for file_info in kb["files"]:
                    lines.append(
                        f"- {file_info['file_name']}: {file_info['chunk_count']} chunks"
                    )
                lines.append("")
            return "\n".join(lines)

        return summary
