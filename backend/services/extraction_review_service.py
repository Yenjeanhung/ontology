"""实体抽取复核队列服务。

存放未通过抽取规则、待人工审核的实体；审核通过即入库，驳回则丢弃并记录。
设计文档：doc/知识库/实体抽取属性级规则与人工复核设计.md §5
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import ExtractionReview

logger = logging.getLogger(__name__)

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
EXPIRED = "expired"

ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"


def _now() -> str:
    return datetime.now().isoformat()


def _load_json(raw: str | None) -> Any:
    """解析 JSON 文本；非法或空返回 None。"""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value is not None else ""


def _serialize(row: ExtractionReview) -> dict:
    return {
        "id": row.id,
        "kb_id": row.kb_id,
        "file_id": row.file_id or "",
        "chunk_id": row.chunk_id or "",
        "ontology_id": row.ontology_id or "",
        "entity_type": row.entity_type,
        "entity_name": row.entity_name,
        "description": row.description or "",
        "properties": _load_json(row.properties) or {},
        "raw_properties": _load_json(row.raw_properties) or {},
        "confidence": float(row.confidence or 1.0),
        "violations": _load_json(row.violations) or [],
        "primary_rule": row.primary_rule or "",
        "status": row.status,
        "reviewer": row.reviewer or "",
        "review_notes": row.review_notes or "",
        "reviewed_at": row.reviewed_at or "",
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class ExtractionReviewService:
    """复核队列 CRUD + 审核入库。"""

    # ===== 写入 =====

    @staticmethod
    async def add_from_sink(
        db: AsyncSession,
        *,
        kb_id: str,
        file_id: str,
        sink: list,
    ) -> int:
        """把抽取阶段收集的待复核实体写入队列。

        sink 元素为 (entity, verdict, chunk_id)；返回写入条数。
        """
        if not sink:
            return 0
        count = 0
        for entity, verdict, chunk_id in sink:
            name = (getattr(entity, "name", "") or "").strip()
            if not name:
                continue
            db.add(ExtractionReview(
                kb_id=kb_id,
                file_id=file_id or "",
                chunk_id=chunk_id or "",
                ontology_id=getattr(entity, "ontology_id", "") or "",
                entity_type=getattr(entity, "entity_type", "") or "UNKNOWN",
                entity_name=name,
                description=getattr(entity, "description", "") or "",
                properties=getattr(entity, "properties", "") or "",
                raw_properties=getattr(verdict, "raw_properties", "") or "",
                confidence=float(getattr(verdict, "confidence", 1.0) or 1.0),
                violations=json.dumps(
                    [v.to_dict() for v in getattr(verdict, "violations", [])],
                    ensure_ascii=False,
                ),
                primary_rule=getattr(verdict, "primary_rule", "") or "",
                status=PENDING,
            ))
            count += 1
        if count:
            await db.commit()
            logger.info(
                "Extraction reviews queued: kb_id=%s file_id=%s count=%s",
                kb_id, file_id, count,
            )
        return count

    # ===== 查询 =====

    @staticmethod
    async def list_reviews(
        db: AsyncSession,
        *,
        kb_id: str,
        status: str | None = None,
        file_id: str | None = None,
        rule: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        query = select(ExtractionReview).where(ExtractionReview.kb_id == kb_id)
        if status:
            query = query.where(ExtractionReview.status == status)
        if file_id:
            query = query.where(ExtractionReview.file_id == file_id)
        if rule:
            query = query.where(ExtractionReview.primary_rule == rule)

        total = (
            await db.execute(select(func.count()).select_from(query.subquery()))
        ).scalar() or 0
        rows = (
            await db.execute(
                query.order_by(ExtractionReview.created_at.desc())
                .offset(max(0, (page - 1) * page_size))
                .limit(max(1, page_size))
            )
        ).scalars().all()
        return {
            "items": [_serialize(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    @staticmethod
    async def get_review(db: AsyncSession, review_id: str) -> dict | None:
        row = await db.get(ExtractionReview, review_id)
        return _serialize(row) if row else None

    @staticmethod
    async def stats(db: AsyncSession, *, kb_id: str, file_id: str | None = None) -> dict:
        def _grouped_query(column, *, pending_only: bool = False):
            query = select(column, func.count()).where(ExtractionReview.kb_id == kb_id)
            if pending_only:
                query = query.where(ExtractionReview.status == PENDING)
            if file_id:
                query = query.where(ExtractionReview.file_id == file_id)
            return query.group_by(column)

        status_rows = (await db.execute(_grouped_query(ExtractionReview.status))).all()
        by_status = {str(s): int(c) for s, c in status_rows if s}

        rule_rows = (
            await db.execute(_grouped_query(ExtractionReview.primary_rule, pending_only=True))
        ).all()
        pending_by_rule = {str(r or "other"): int(c) for r, c in rule_rows}

        return {
            "by_status": by_status,
            "pending_by_rule": pending_by_rule,
            "total": sum(by_status.values()),
        }

    # ===== 审核 =====

    @staticmethod
    async def update_review(
        db: AsyncSession,
        review_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        properties: dict | None = None,
        review_notes: str | None = None,
    ) -> dict | None:
        """修改待审核内容（仅 pending 可改）。"""
        row = await db.get(ExtractionReview, review_id)
        if not row:
            return None
        if row.status != PENDING:
            raise ValueError(f"仅待审核（pending）记录可修改，当前状态：{row.status}")
        if name is not None:
            row.entity_name = name.strip() or row.entity_name
        if description is not None:
            row.description = description
        if properties is not None:
            row.properties = _dump_json(properties)
        if review_notes is not None:
            row.review_notes = review_notes
        row.updated_at = _now()
        await db.commit()
        return _serialize(row)

    @staticmethod
    async def approve(
        db: AsyncSession,
        review_id: str,
        *,
        reviewer: str = "",
        name: str | None = None,
        description: str | None = None,
        properties: dict | None = None,
        review_notes: str = "",
    ) -> dict:
        """审核通过并入库。

        复用 EntityService.create_entity 的 upsert 语义，与自动入库路径完全一致，
        因此重复操作不会产出重复实体（设计文档 §5.3）。
        """
        row = await db.get(ExtractionReview, review_id)
        if not row:
            raise ValueError("复核记录不存在")
        if row.status != PENDING:
            raise ValueError(f"仅待审核（pending）记录可审核，当前状态：{row.status}")

        final_name = (name or row.entity_name or "").strip()
        if not final_name:
            raise ValueError("实体名称不能为空")
        final_props = properties if properties is not None else (_load_json(row.properties) or None)
        final_desc = description if description is not None else (row.description or "")

        # 延迟导入：entity_service 依赖面较广，避免模块级循环导入
        from services.entity_service import EntityService

        try:
            await EntityService.create_entity(
                db,
                kb_id=row.kb_id,
                ontology_id=row.ontology_id,
                entity_type=row.entity_type,
                name=final_name,
                description=final_desc,
                properties=final_props,
                source_file_id=row.file_id or None,
                source_chunk_id=row.chunk_id or None,
            )
        except Exception as exc:
            # 入库失败保持 pending，避免静默丢失（设计文档 §5.3）
            logger.exception("Approve review failed, keep pending: id=%s", review_id)
            raise ValueError(f"入库失败，已保留为待审核：{exc}") from exc

        row.entity_name = final_name
        if properties is not None:
            row.properties = _dump_json(final_props)
        if description is not None:
            row.description = final_desc
        row.status = APPROVED
        row.reviewer = reviewer
        row.review_notes = review_notes
        row.reviewed_at = _now()
        row.updated_at = _now()
        await db.commit()
        logger.info("Extraction review approved: id=%s entity=%s", review_id, final_name)
        return _serialize(row)

    @staticmethod
    async def reject(
        db: AsyncSession,
        review_id: str,
        *,
        reviewer: str = "",
        review_notes: str = "",
    ) -> dict | None:
        row = await db.get(ExtractionReview, review_id)
        if not row:
            return None
        if row.status != PENDING:
            raise ValueError(f"仅待审核（pending）记录可审核，当前状态：{row.status}")
        row.status = REJECTED
        row.reviewer = reviewer
        row.review_notes = review_notes
        row.reviewed_at = _now()
        row.updated_at = _now()
        await db.commit()
        return _serialize(row)

    @staticmethod
    async def batch_action(
        db: AsyncSession,
        ids: list[str],
        action: str,
        *,
        reviewer: str = "",
    ) -> dict:
        results: list[dict] = []
        for review_id in ids:
            try:
                if action == ACTION_APPROVE:
                    await ExtractionReviewService.approve(db, review_id, reviewer=reviewer)
                elif action == ACTION_REJECT:
                    res = await ExtractionReviewService.reject(db, review_id, reviewer=reviewer)
                    if res is None:
                        raise ValueError("复核记录不存在")
                else:
                    raise ValueError(f"不支持的批量操作：{action}")
                results.append({"id": review_id, "ok": True})
            except Exception as exc:  # noqa: BLE001 - 批量操作需汇总每条结果
                results.append({"id": review_id, "ok": False, "error": str(exc)})
        return {
            "results": results,
            "succeeded": sum(1 for r in results if r["ok"]),
            "failed": sum(1 for r in results if not r["ok"]),
        }

    @staticmethod
    async def expire_by_file(db: AsyncSession, file_id: str) -> int:
        """文件重新处理前，把该文件下旧 pending 记录置为 expired（不清数据）。"""
        if not file_id:
            return 0
        rows = (
            await db.execute(
                select(ExtractionReview).where(
                    ExtractionReview.file_id == file_id,
                    ExtractionReview.status == PENDING,
                )
            )
        ).scalars().all()
        if not rows:
            return 0
        now = _now()
        for row in rows:
            row.status = EXPIRED
            row.updated_at = now
        await db.commit()
        logger.info("Extraction reviews expired: file_id=%s count=%s", file_id, len(rows))
        return len(rows)
