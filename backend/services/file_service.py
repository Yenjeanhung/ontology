from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import shutil
from time import perf_counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.chunker import iter_text_chunks
from database import async_session
from models import Chunk, File, KnowledgeBase
from providers.embedding import create_embeddings
from providers.graph_store import (
    ChunkGraphData,
    delete_document_graph,
    fetch_graph_view,
    get_graph_store_provider_name,
    upsert_document_graph,
)
from providers.parser import get_parser
from providers.vector_store import create_vector_store, get_vector_store_provider_name
from services.entity_service import EntityService
from services.graph_extraction_service import GraphExtractionService
from services.ontology_service import OntologyService, OntologySuggestionService

logger = logging.getLogger(__name__)

UPLOAD_DIR = Path(settings.UPLOAD_DIR)
CHUNK_DIR = Path(settings.CHUNK_DIR)
LOG_TAIL_LIMIT = 120


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _visual_stage_name(stage: str | None) -> str | None:
    mapping = {
        "chunking": "chunking",
        "vectorizing": "vectorizing",
        "extract_prepare": "extraction",
        "extracting": "extraction",
        "graph_writing": "graph",
    }
    return mapping.get(stage or "")


class FileService:
    _status_subscribers: dict[str, set[asyncio.Queue]] = {}
    _cancel_events: dict[str, asyncio.Event] = {}
    _running_tasks: dict[str, asyncio.Task] = {}

    @staticmethod
    def _build_status_payload(file: File) -> dict:
        return {
            "status": file.status,
            "progress": file.progress,
            "message": file.message,
            "detail": FileService._read_detail(file),
            "logs": FileService._read_logs(file),
        }

    @staticmethod
    def subscribe_status(file_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=16)
        FileService._status_subscribers.setdefault(file_id, set()).add(queue)
        return queue

    @staticmethod
    def unsubscribe_status(file_id: str, queue: asyncio.Queue):
        subscribers = FileService._status_subscribers.get(file_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            FileService._status_subscribers.pop(file_id, None)

    @staticmethod
    def _publish_status(file_id: str, payload: dict):
        subscribers = FileService._status_subscribers.get(file_id)
        if not subscribers:
            return
        stale: list[asyncio.Queue] = []
        for queue in list(subscribers):
            try:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                queue.put_nowait(payload)
            except Exception:
                stale.append(queue)
        for queue in stale:
            subscribers.discard(queue)
        if not subscribers:
            FileService._status_subscribers.pop(file_id, None)

    @staticmethod
    def _create_cancel_event(file_id: str) -> asyncio.Event:
        event = asyncio.Event()
        FileService._cancel_events[file_id] = event
        return event

    @staticmethod
    def _get_cancel_event(file_id: str) -> asyncio.Event | None:
        return FileService._cancel_events.get(file_id)

    @staticmethod
    def _clear_cancel_event(file_id: str):
        FileService._cancel_events.pop(file_id, None)

    @staticmethod
    def _check_cancelled(file_id: str):
        event = FileService._get_cancel_event(file_id)
        if event and event.is_set():
            raise asyncio.CancelledError(f"Processing cancelled: {file_id}")

    @staticmethod
    def _register_running_task(file_id: str, task: asyncio.Task):
        FileService._running_tasks[file_id] = task

        def _cleanup(_task: asyncio.Task):
            current = FileService._running_tasks.get(file_id)
            if current is _task:
                FileService._running_tasks.pop(file_id, None)
                FileService._clear_cancel_event(file_id)

        task.add_done_callback(_cleanup)

    @staticmethod
    def _cancel_running_task(file_id: str) -> asyncio.Task | None:
        task = FileService._running_tasks.get(file_id)
        event = FileService._get_cancel_event(file_id)
        if event:
            event.set()
        if task and not task.done():
            task.cancel()
        return task

    @staticmethod
    async def _delete_index_artifacts(
        db: AsyncSession,
        file: File,
        *,
        remove_source_file: bool,
    ):
        embeddings = create_embeddings()
        chunks = (await db.execute(select(Chunk).where(Chunk.file_id == file.id))).scalars().all()
        vector_provider_name = get_vector_store_provider_name()
        graph_provider_name = get_graph_store_provider_name()
        logger.info(
            "Deleting file index artifacts: file_id=%s kb_id=%s chunk_rows=%s vector_provider=%s graph_provider=%s remove_source_file=%s",
            file.id,
            file.kb_id,
            len(chunks),
            vector_provider_name,
            graph_provider_name,
            remove_source_file,
        )

        if chunks:
            try:
                vectorstore = create_vector_store(file.kb_id, embeddings)
                ids_to_delete = [chunk.embedding_id for chunk in chunks if chunk.embedding_id]
                if ids_to_delete:
                    await asyncio.to_thread(vectorstore.delete, ids=ids_to_delete)
            except Exception:
                logger.exception("Vector delete failed: file_id=%s kb_id=%s", file.id, file.kb_id)

            for chunk in chunks:
                await db.delete(chunk)
            await db.flush()

        try:
            await asyncio.to_thread(delete_document_graph, file.id)
        except Exception:
            logger.exception("Graph delete failed: file_id=%s kb_id=%s", file.id, file.kb_id)

        if remove_source_file and file.path and not file.asset_id:
            file_path = Path(file.path)
            if file_path.exists():
                file_path.unlink()

        for chunk_path in CHUNK_DIR.glob(f"{file.id}_*"):
            chunk_path.unlink()

    @staticmethod
    def _empty_detail() -> dict:
        return {
            "started_at": None,
            "finished_at": None,
            "elapsed_ms": 0,
            "stage": "idle",
            "summary": {
                "chunk_count": 0,
                "entity_count": 0,
                "relation_count": 0,
            },
            "stages": {
                "total": {"progress": 0, "label": "等待开始"},
                "chunking": {"progress": 0, "current": 0, "total": 0, "label": "等待开始"},
                "vectorizing": {"progress": 0, "current": 0, "total": 0, "label": "等待开始"},
                "extraction": {
                    "progress": 0,
                    "processed_batches": 0,
                    "total_batches": 0,
                    "started_batches": 0,
                    "running_batches": 0,
                    "processed_chunks": 0,
                    "total_candidate_chunks": 0,
                    "entity_count": 0,
                    "relation_count": 0,
                    "label": "等待开始",
                },
                "graph": {"progress": 0, "label": "等待开始"},
            },
        }

    @staticmethod
    def _read_detail(file: File) -> dict:
        if not file.detail:
            return FileService._empty_detail()
        try:
            return json.loads(file.detail)
        except Exception:
            return FileService._empty_detail()

    @staticmethod
    def _write_detail(file: File, detail: dict):
        file.detail = json.dumps(detail, ensure_ascii=False)

    @staticmethod
    def _read_logs(file: File) -> list[dict]:
        if not file.logs:
            return []
        try:
            value = json.loads(file.logs)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _write_logs(file: File, logs: list[dict]):
        file.logs = json.dumps(logs[-LOG_TAIL_LIMIT:], ensure_ascii=False)

    @staticmethod
    def _append_log(file: File, message: str, level: str = "info"):
        logs = FileService._read_logs(file)
        logs.append({
            "time": _utc_now_iso(),
            "level": level,
            "message": message[:500],
        })
        FileService._write_logs(file, logs)

    @staticmethod
    async def _commit_runtime_state(
        db: AsyncSession,
        file: File,
        *,
        progress: int | None = None,
        message: str | None = None,
        stage: str | None = None,
        chunk_progress: dict | None = None,
        vector_progress: dict | None = None,
        extraction_progress: dict | None = None,
        graph_progress: dict | None = None,
        summary: dict | None = None,
        log_message: str | None = None,
        log_level: str = "info",
        status: str | None = None,
        finished: bool = False,
    ):
        detail = FileService._read_detail(file)
        now_iso = _utc_now_iso()
        old_stage = detail.get("stage")
        old_visual_stage = _visual_stage_name(old_stage)
        new_visual_stage = _visual_stage_name(stage if stage is not None else old_stage)
        if detail.get("started_at") is None:
            detail["started_at"] = now_iso
        # 当阶段发生视觉变化时，管理各阶段的计时器
        if stage is not None and old_visual_stage != new_visual_stage:
            # 开始新的视觉阶段的计时
            if new_visual_stage:
                current = detail["stages"].get(new_visual_stage)
                if current and not current.get("started_at"):
                    current["started_at"] = now_iso
            # 结束上一个视觉阶段的计时 - 但如果是从 chunking 切换到 vectorizing，不结束 chunking
            # 因为这两个阶段在前端是合并显示的"分片与向量化"
            if old_visual_stage and not (old_visual_stage == "chunking" and new_visual_stage == "vectorizing"):
                previous = detail["stages"].get(old_visual_stage)
                if previous and previous.get("started_at") and not previous.get("finished_at"):
                    previous["finished_at"] = now_iso
                    started = datetime.fromisoformat(previous["started_at"])
                    ended = datetime.fromisoformat(previous["finished_at"])
                    previous["elapsed_ms"] = max(0, int((ended - started).total_seconds() * 1000))
        if progress is not None:
            file.progress = max(0, min(100, progress))
            detail["stages"]["total"]["progress"] = file.progress
        if message is not None:
            file.message = message[:200]
            detail["stages"]["total"]["label"] = message[:200]
        if stage is not None:
            detail["stage"] = stage
        # 确保 chunking 阶段在有进度时总是有开始时间戳
        if chunk_progress:
            detail["stages"]["chunking"].update(chunk_progress)
            if detail["stages"]["chunking"].get("progress", 0) > 0 and not detail["stages"]["chunking"].get("started_at"):
                detail["stages"]["chunking"]["started_at"] = now_iso
                logger.info(f"[TIMER-FIX] Set chunking started_at because progress > 0")
        if vector_progress:
            detail["stages"]["vectorizing"].update(vector_progress)
            if detail["stages"]["vectorizing"].get("progress", 0) > 0 and not detail["stages"]["vectorizing"].get("started_at"):
                detail["stages"]["vectorizing"]["started_at"] = now_iso
                logger.info(f"[TIMER-FIX] Set vectorizing started_at because progress > 0")
        if extraction_progress:
            detail["stages"]["extraction"].update(extraction_progress)
        if graph_progress:
            detail["stages"]["graph"].update(graph_progress)
        if summary:
            detail["summary"].update(summary)
        if finished:
            detail["finished_at"] = now_iso
        for stage_name in ("chunking", "vectorizing", "extraction", "graph"):
            stage_detail = detail["stages"].get(stage_name)
            if not stage_detail or not stage_detail.get("started_at"):
                continue
            if stage_detail.get("progress", 0) >= 100 and not stage_detail.get("finished_at"):
                stage_detail["finished_at"] = now_iso
            ended = (
                datetime.fromisoformat(stage_detail["finished_at"])
                if stage_detail.get("finished_at")
                else datetime.now(timezone.utc)
            )
            started = datetime.fromisoformat(stage_detail["started_at"])
            stage_detail["elapsed_ms"] = max(0, int((ended - started).total_seconds() * 1000))
        if detail.get("started_at"):
            started = datetime.fromisoformat(detail["started_at"])
            ended = datetime.fromisoformat(detail["finished_at"]) if detail.get("finished_at") else datetime.now(timezone.utc)
            detail["elapsed_ms"] = max(0, int((ended - started).total_seconds() * 1000))
        
        # 确保所有有进度的阶段都有 started_at
        for stage_name in ["chunking", "vectorizing", "extraction", "graph"]:
            stage = detail["stages"].get(stage_name)
            if stage and stage.get("progress", 0) > 0 and not stage.get("started_at"):
                stage["started_at"] = now_iso
                logger.info(f"[TIMER-FIX] Auto-set {stage_name} started_at because progress={stage['progress']}")
        
        FileService._write_detail(file, detail)
        if log_message:
            FileService._append_log(file, log_message, log_level)
        if status is not None:
            file.status = status
        await db.commit()
        FileService._publish_status(file.id, FileService._build_status_payload(file))

    @staticmethod
    async def upload_chunk(
        db: AsyncSession,
        file_id: str,
        file_name: str,
        file_size: int,
        kb_id: str,
        chunk_index: int,
        total_chunks: int,
        chunk_data,
    ) -> dict:
        file = await db.get(File, file_id)
        if not file:
            file = File(
                id=file_id,
                kb_id=kb_id,
                name=file_name,
                size=file_size,
                total_chunks=total_chunks,
            )
            FileService._write_detail(file, FileService._empty_detail())
            FileService._write_logs(file, [])
            db.add(file)
            await db.commit()

        chunk_path = CHUNK_DIR / f"{file_id}_{chunk_index:06d}"
        with open(chunk_path, "wb") as f:
            shutil.copyfileobj(chunk_data, f)

        received = len(list(CHUNK_DIR.glob(f"{file_id}_*")))
        logger.info(
            "Upload chunk received: file_id=%s kb_id=%s chunk_index=%s/%s received=%s file_name=%s",
            file_id,
            kb_id,
            chunk_index + 1,
            total_chunks,
            received,
            file_name,
        )

        if received == total_chunks:
            await FileService._reassemble(file_id, file, db)

        return {"status": "ok", "chunk_index": chunk_index, "received": received}

    @staticmethod
    async def _reassemble(file_id: str, file: File, db: AsyncSession):
        kb_dir = UPLOAD_DIR / file.kb_id
        kb_dir.mkdir(exist_ok=True)
        target_path = kb_dir / f"{file_id}{Path(file.name).suffix.lower()}"

        chunk_files = sorted(CHUNK_DIR.glob(f"{file_id}_*"))
        logger.info(
            "Reassembling upload: file_id=%s kb_id=%s chunks=%s target=%s",
            file_id,
            file.kb_id,
            len(chunk_files),
            target_path,
        )
        with open(target_path, "wb") as out:
            for chunk_path in chunk_files:
                with open(chunk_path, "rb") as chunk_in:
                    out.write(chunk_in.read())
                chunk_path.unlink()

        from services.library_service import LibraryService

        directory = await LibraryService.default_kb_directory(db, file.kb_id)
        asset = await LibraryService.create_asset_from_path(
            db,
            target_path,
            name=file.name,
            directory_id=directory.id,
            source_type="kb_upload",
            move=True,
        )
        file.asset_id = asset.id
        file.path = asset.path
        await FileService._commit_runtime_state(
            db,
            file,
            status="uploaded",
            progress=0,
            message="上传完成，等待开始处理",
            stage="uploaded",
            log_message="上传完成，文件已重组",
        )
        logger.info(
            "Upload reassembled: file_id=%s kb_id=%s path=%s size=%s",
            file_id,
            file.kb_id,
            file.path,
            file.size,
        )

    @staticmethod
    async def start_processing(file_id: str, db: AsyncSession, extract_graph: bool = True) -> bool:
        file = await db.get(File, file_id)
        if not file or file.status != "uploaded":
            logger.warning(
                "Start processing skipped: file_id=%s status=%s",
                file_id,
                getattr(file, "status", None),
            )
            return False

        detail = FileService._empty_detail()
        detail["started_at"] = _utc_now_iso()
        detail["stage"] = "preparing"
        FileService._write_detail(file, detail)
        FileService._write_logs(file, [])
        await FileService._commit_runtime_state(
            db,
            file,
            status="processing",
            progress=0,
            message="准备开始处理",
            stage="preparing",
            log_message="处理任务已启动",
        )
        logger.info(
            "Start processing: file_id=%s kb_id=%s file_name=%s extract_graph=%s",
            file.id, file.kb_id, file.name, extract_graph,
        )
        cancel_event = FileService._create_cancel_event(file_id)
        task = asyncio.create_task(
            FileService._process_file_bg(file_id, extract_graph=extract_graph, cancel_event=cancel_event)
        )
        FileService._register_running_task(file_id, task)
        return True

    @staticmethod
    async def restart_processing(file_id: str, db: AsyncSession, extract_graph: bool = True) -> bool:
        file = await db.get(File, file_id)
        if not file or not file.path:
            logger.warning(
                "Restart processing skipped: file_id=%s status=%s path=%s",
                file_id,
                getattr(file, "status", None),
                getattr(file, "path", None),
            )
            return False

        if file.status == "processing":
            logger.warning("Restart processing skipped: file_id=%s already processing", file_id)
            return False

        await FileService._delete_index_artifacts(db, file, remove_source_file=False)

        detail = FileService._empty_detail()
        detail["started_at"] = _utc_now_iso()
        detail["stage"] = "preparing"
        FileService._write_detail(file, detail)
        FileService._write_logs(file, [])
        await FileService._commit_runtime_state(
            db,
            file,
            status="processing",
            progress=0,
            message="准备重新处理",
            stage="preparing",
            log_message="已清理旧分片、向量和图谱，开始重新处理",
        )
        logger.info(
            "Restart processing: file_id=%s kb_id=%s file_name=%s extract_graph=%s",
            file.id, file.kb_id, file.name, extract_graph,
        )
        cancel_event = FileService._create_cancel_event(file_id)
        task = asyncio.create_task(
            FileService._process_file_bg(file_id, extract_graph=extract_graph, cancel_event=cancel_event)
        )
        FileService._register_running_task(file_id, task)
        return True

    @staticmethod
    async def get_status(file_id: str, db: AsyncSession) -> dict | None:
        file = await db.get(File, file_id)
        if not file:
            return None
        return FileService._build_status_payload(file)

    @staticmethod
    async def _process_file_bg(file_id: str, extract_graph: bool = True, cancel_event: asyncio.Event | None = None):
        async with async_session() as db:
            try:
                FileService._check_cancelled(file_id)
                file = await db.get(File, file_id)
                if not file or not file.path:
                    logger.warning("Background processing aborted: file_id=%s missing file or path", file_id)
                    return

                kb = await db.get(KnowledgeBase, file.kb_id)
                if not kb:
                    raise ValueError(f"Knowledge base not found: {file.kb_id}")

                file_path = Path(file.path)
                vector_provider_name = get_vector_store_provider_name()
                graph_provider_name = get_graph_store_provider_name()
                pipeline_started = perf_counter()

                logger.info(
                    "Processing pipeline started: file_id=%s kb_id=%s file_name=%s vector_provider=%s graph_provider=%s path=%s",
                    file.id,
                    file.kb_id,
                    file.name,
                    vector_provider_name,
                    graph_provider_name,
                    file.path,
                )
                await FileService._commit_runtime_state(
                    db,
                    file,
                    progress=5,
                    message="正在解析文档",
                    stage="parsing",
                    vector_progress={"label": "等待写入", "progress": 0, "current": 0, "total": 0},
                    chunk_progress={"label": "等待分片", "progress": 0, "current": 0, "total": 0},
                    extraction_progress={"label": "等待抽取", "progress": 0} if extract_graph else {"label": "已跳过", "progress": 100},
                    graph_progress={"label": "等待写图", "progress": 0} if extract_graph else {"label": "已跳过", "progress": 100},
                    log_message="开始解析文档",
                )

                parse_started = perf_counter()
                parser = get_parser(file_path)
                result = parser.parse(file_path)
                FileService._check_cancelled(file_id)
                parse_ms = (perf_counter() - parse_started) * 1000
                logger.info(
                    "Parsing completed: file_id=%s content_chars=%s metadata_keys=%s duration_ms=%.0f",
                    file.id,
                    len(result.content or ""),
                    sorted((result.metadata or {}).keys()),
                    parse_ms,
                )
                await FileService._commit_runtime_state(
                    db,
                    file,
                    progress=15,
                    message="正在切分文本",
                    stage="chunking",
                    log_message=f"文档解析完成，用时 {parse_ms / 1000:.1f}s",
                )

                chunk_started = perf_counter()
                embeddings = create_embeddings()
                vectorstore = create_vector_store(file.kb_id, embeddings)
                vector_batch_size = max(1, settings.VECTOR_WRITE_BATCH_SIZE)
                content_length = max(len(result.content or ""), 1)

                await FileService._commit_runtime_state(
                    db,
                    file,
                    progress=18,
                    message="Streaming chunks into vector store",
                    stage="chunking",
                    vector_progress={
                        "progress": 0,
                        "current": 0,
                        "total": 0,
                        "label": "Waiting for first vector batch",
                    },
                    log_message=f"Streaming chunk/vector pipeline started, provider={vector_provider_name}",
                )

                from langchain_core.documents import Document

                text_chunks: list[dict] = []
                graph_chunks: list[ChunkGraphData] = []
                chunk_ids: list[str] = []
                pending_chunk_rows: list[dict] = []
                pending_docs: list[Document] = []
                pending_ids: list[str] = []
                generated_chunks = 0
                written_docs = 0
                last_generated_offset = 0
                last_written_offset = 0
                vector_started = perf_counter()

                async def flush_vector_batch(*, final: bool) -> None:
                    nonlocal written_docs, last_written_offset
                    FileService._check_cancelled(file_id)
                    if not pending_docs:
                        return

                    batch_docs = list(pending_docs)
                    batch_ids = list(pending_ids)
                    batch_rows = list(pending_chunk_rows)
                    await asyncio.to_thread(vectorstore.add_documents, batch_docs, ids=batch_ids)

                    for chunk_row, chunk_id in zip(batch_rows, batch_ids):
                        text_chunks.append(chunk_row)
                        chunk_ids.append(chunk_id)
                        graph_chunks.append(
                            ChunkGraphData(
                                chunk_id=chunk_id,
                                chunk_index=chunk_row["index"],
                                content=chunk_row["content"],
                            )
                        )
                        db.add(
                            Chunk(
                                id=chunk_id,
                                file_id=file_id,
                                content=chunk_row["content"],
                                chunk_index=chunk_row["index"],
                                embedding_id=chunk_id,
                            )
                        )

                    written_docs += len(batch_docs)
                    last_written_offset = batch_rows[-1]["end_offset"]
                    chunk_ratio = min(1.0, last_generated_offset / content_length)
                    vector_ratio = min(1.0, last_written_offset / content_length)
                    display_total = generated_chunks

                    # 在仅分片模式下，向量写入阶段贡献更多进度（因为没有抽取阶段）
                    vector_progress_weight = 70 if not extract_graph else 35
                    await FileService._commit_runtime_state(
                        db,
                        file,
                        progress=20 + int(vector_ratio * vector_progress_weight),
                        message=f"Streaming chunks: generated {generated_chunks}, vectorized {written_docs}",
                        stage="vectorizing",
                        chunk_progress={
                            "progress": 100 if final else max(1, int(chunk_ratio * 99)),
                            "current": generated_chunks,
                            "total": display_total,
                            "label": (
                                f"Chunked {generated_chunks}/{generated_chunks}"
                                if final else f"Chunking in progress, generated {generated_chunks}"
                            ),
                        },
                        vector_progress={
                            "progress": 100 if final else max(1, int(vector_ratio * 99)),
                            "current": written_docs,
                            "total": display_total,
                            "label": (
                                f"Vectorized {written_docs}/{written_docs}"
                                if final else f"Vectorizing in progress, written {written_docs}"
                            ),
                        },
                        summary={"chunk_count": generated_chunks},
                    )
                    pending_docs.clear()
                    pending_ids.clear()
                    pending_chunk_rows.clear()

                for chunk in iter_text_chunks(result.content, result.metadata):
                    FileService._check_cancelled(file_id)
                    generated_chunks += 1
                    last_generated_offset = chunk["end_offset"]
                    chunk_id = f"{file_id}_{chunk['index']}"
                    pending_chunk_rows.append(chunk)
                    pending_ids.append(chunk_id)
                    pending_docs.append(
                        Document(
                            page_content=chunk["content"],
                            metadata={
                                "file_id": file_id,
                                "file_name": file.name,
                                "chunk_index": chunk["index"],
                                "start_offset": chunk["start_offset"],
                                "end_offset": chunk["end_offset"],
                                "page_number": chunk.get("page_number"),
                                "file_ext": file_path.suffix.lower(),
                            },
                        )
                    )
                    if len(pending_docs) >= vector_batch_size:
                        await flush_vector_batch(final=False)

                if generated_chunks == 0:
                    await FileService._commit_runtime_state(
                        db,
                        file,
                        status="failed",
                        progress=file.progress,
                        message="Document content is empty after parsing",
                        stage="failed",
                        log_message="Chunking failed: parsed content is empty",
                        log_level="error",
                        finished=True,
                    )
                    logger.warning("Document content empty after parsing: file_id=%s", file_id)
                    return

                await flush_vector_batch(final=True)
                FileService._check_cancelled(file_id)
                chunk_ms = (perf_counter() - chunk_started) * 1000
                vector_ms = (perf_counter() - vector_started) * 1000
                logger.info(
                    "Vector write completed: file_id=%s kb_id=%s provider=%s count=%s duration_ms=%.0f",
                    file.id,
                    file.kb_id,
                    vector_provider_name,
                    len(chunk_ids),
                    vector_ms,
                )
                # 在仅分片模式下，向量写入完成后跳到90%（因为没有抽取阶段）
                final_progress = 90 if not extract_graph else 55
                await FileService._commit_runtime_state(
                    db,
                    file,
                    progress=final_progress,
                    message="Vector write complete, preparing graph extraction" if extract_graph else "Vector write complete, saving chunk records",
                    stage="extract_prepare" if extract_graph else "saving",
                    vector_progress={
                        "progress": 100,
                        "current": len(chunk_ids),
                        "total": len(chunk_ids),
                        "label": f"Vectorized {len(chunk_ids)}/{len(chunk_ids)}",
                    },
                    chunk_progress={
                        "progress": 100,
                        "current": len(text_chunks),
                        "total": len(text_chunks),
                        "label": f"Chunked {len(text_chunks)}/{len(text_chunks)}",
                    },
                    summary={"chunk_count": len(text_chunks)},
                    log_message=(
                        f"Streaming chunk/vector pipeline finished: {len(text_chunks)} chunks, "
                        f"chunking {chunk_ms / 1000:.1f}s, vector write {vector_ms / 1000:.1f}s"
                    ),
                )

                entity_count = 0
                relation_count = 0

                if extract_graph:
                    # 加载知识库绑定的本体约束（无绑定时返回 None，抽取服务回退到自由抽取模式）
                    ontology_constraint = await OntologyService.get_kb_extraction_constraints(
                        db, file.kb_id
                    )
                    has_constraint = bool(
                        ontology_constraint and ontology_constraint.get("ontologies")
                    )
                    if has_constraint:
                        logger.info(
                            "Ontology constraint loaded: file_id=%s kb_id=%s category=%s ontologies=%s constraints=%s",
                            file.id,
                            file.kb_id,
                            ontology_constraint.get("category_name"),
                            len(ontology_constraint.get("ontologies", [])),
                            len(ontology_constraint.get("constraints", [])),
                        )

                    # Set up KB + Document metadata and clear old graph data first
                    await asyncio.to_thread(
                        upsert_document_graph,
                        file.kb_id,
                        kb.name,
                        file.id,
                        file.name,
                        file.path or "",
                        [],
                        True,  # clear_existing
                    )
                    extraction_started = perf_counter()
                    last_ui_update = {"batch": 0, "ts": perf_counter()}
                    last_log_flush = {"ts": 0.0}
                    unique_entities: set[tuple[str, str]] = set()
                    unique_relations: set[tuple[str, str, str, str, str]] = set()

                    async def batch_result_callback(batch_chunks: list):
                        FileService._check_cancelled(file_id)
                        # 本体约束模式：先将实体/关系写入 SQLite（权威存储），回填实例 id
                        # 再写 Kùzu（upsert_document_graph 使用回填的 id，保证双库一致）
                        if has_constraint:
                            await FileService._persist_extraction_to_sqlite(
                                db,
                                file_id=file.id,
                                kb_id=file.kb_id,
                                batch_chunks=batch_chunks,
                            )
                        await asyncio.to_thread(
                            upsert_document_graph,
                            file.kb_id,
                            kb.name,
                            file.id,
                            file.name,
                            file.path or "",
                            batch_chunks,
                            False,
                        )
                        for chunk in batch_chunks:
                            for entity in chunk.entities:
                                unique_entities.add((entity.name.lower(), entity.entity_type.lower()))
                            for relation in chunk.relations:
                                unique_relations.add((
                                    relation.source_name.lower(),
                                    relation.source_type.lower(),
                                    relation.relation_type.lower(),
                                    relation.target_name.lower(),
                                    relation.target_type.lower(),
                                ))
                        await FileService._commit_runtime_state(
                            db,
                            file,
                            extraction_progress={
                                "entity_count": len(unique_entities),
                                "relation_count": len(unique_relations),
                            },
                        )

                    async def extraction_progress_callback(
                        processed_batches: int,
                        total_batches: int,
                        processed_chunks: int,
                        total_candidate_chunks: int,
                        started_batches: int,
                        running_batches: int,
                    ):
                        FileService._check_cancelled(file_id)
                        ratio = processed_batches / max(total_batches, 1)
                        dispatch_ratio = started_batches / max(total_batches, 1)
                        visual_ratio = max(ratio, dispatch_ratio * 0.35)
                        progress = 56 + int(visual_ratio * 24)
                        now = perf_counter()
                        if (
                            processed_batches != total_batches
                            and processed_batches != 1
                            and started_batches != 1
                            and now - last_ui_update["ts"] < 0.5
                        ):
                            return
                        last_ui_update["batch"] = max(processed_batches, started_batches)
                        last_ui_update["ts"] = now
                        if processed_batches > 0:
                            extraction_message = (
                                f"正在抽取实体与关系：已完成批次 {processed_batches}/{total_batches}"
                            )
                            extraction_label = f"已完成批次 {processed_batches}/{total_batches}"
                        elif started_batches > 0:
                            extraction_message = (
                                f"已发起抽取请求：{started_batches}/{total_batches}，进行中 {running_batches}"
                            )
                            extraction_label = f"已发起 {started_batches}/{total_batches}，进行中 {running_batches}"
                        else:
                            extraction_message = f"正在准备抽取批次 0/{total_batches}"
                            extraction_label = f"准备中 0/{total_batches}"
                        await FileService._commit_runtime_state(
                            db,
                            file,
                            progress=progress,
                            message=extraction_message,
                            stage="extracting",
                            extraction_progress={
                                "progress": max(
                                    1 if started_batches > 0 else 0,
                                    int(visual_ratio * 100),
                                ),
                                "processed_batches": processed_batches,
                                "total_batches": total_batches,
                                "processed_chunks": processed_chunks,
                                "total_candidate_chunks": total_candidate_chunks,
                                "entity_count": len(unique_entities),
                                "relation_count": len(unique_relations),
                                "started_batches": started_batches,
                                "running_batches": running_batches,
                                "label": extraction_label,
                            },
                        )

                    async def extraction_log_callback(message: str):
                        FileService._check_cancelled(file_id)
                        now = perf_counter()
                        should_flush = (
                            "开始请求大模型抽取" in message
                            or "抽取完成" in message
                            or "抽取失败" in message
                            or "阶段完成" in message
                            or (now - last_log_flush["ts"]) >= 0.5
                        )
                        if not should_flush:
                            return
                        last_log_flush["ts"] = now
                        log_level = "error" if "失败" in message or "错误" in message else "info"
                        await FileService._commit_runtime_state(
                            db,
                            file,
                            log_message=message,
                            log_level=log_level,
                            message=file.message,
                        )

                    graph_chunks = await GraphExtractionService.extract(
                        file.name,
                        graph_chunks,
                        progress_callback=extraction_progress_callback,
                        log_callback=extraction_log_callback,
                        batch_result_callback=batch_result_callback,
                        cancel_check=lambda: FileService._check_cancelled(file_id),
                        ontology_constraint=ontology_constraint if has_constraint else None,
                    )
                    FileService._check_cancelled(file_id)
                    entity_count = sum(len(chunk.entities) for chunk in graph_chunks)
                    relation_count = sum(len(chunk.relations) for chunk in graph_chunks)
                    extraction_ms = (perf_counter() - extraction_started) * 1000
                    graph_view = await asyncio.to_thread(
                        fetch_graph_view,
                        file.kb_id,
                        file.id,
                        "",
                        "",
                    )
                    graph_summary = graph_view.get("summary", {}) if isinstance(graph_view, dict) else {}
                    entity_count = int(graph_summary.get("entity_total", entity_count) or 0)
                    relation_count = int(graph_summary.get("relation_total", relation_count) or 0)
                    logger.info(
                        "Graph extraction completed: file_id=%s kb_id=%s chunks=%s entities=%s relations=%s duration_ms=%.0f",
                        file.id,
                        file.kb_id,
                        len(graph_chunks),
                        entity_count,
                        relation_count,
                        extraction_ms,
                    )
                    # 自由模式：基于抽取结果自动生成候选本体建议（后台异步，不阻塞主流程）
                    if not has_constraint and graph_chunks and entity_count > 0:
                        try:
                            suggestion_data = await GraphExtractionService.generate_ontology_suggestion(
                                file.name, graph_chunks, kb_name=kb.name,
                            )
                            if suggestion_data:
                                score = float(
                                    (suggestion_data.get("stats") or {}).get("confidence", 0.7) or 0.7
                                )
                                await OntologySuggestionService.create_suggestion(
                                    db, kb_id=file.kb_id, file_id=file.id,
                                    suggestion_data=suggestion_data,
                                    source_mode="free_extraction",
                                    score=score,
                                )
                                logger.info(
                                    "Ontology suggestion created: kb_id=%s file_id=%s ont=%s rels=%s score=%.2f",
                                    file.kb_id, file.id,
                                    len(suggestion_data.get("ontologies") or []),
                                    len(suggestion_data.get("relations") or []),
                                    score,
                                )
                        except Exception:
                            logger.exception("Auto ontology suggestion creation failed (non-critical)")

                    await FileService._commit_runtime_state(
                        db,
                        file,
                        progress=90,
                        message=f"抽取完成：实体 {entity_count}，关系 {relation_count}",
                        stage="saving",
                        extraction_progress={
                            "progress": 100,
                            "entity_count": entity_count,
                            "relation_count": relation_count,
                            "label": f"已抽取实体 {entity_count}，关系 {relation_count}",
                        },
                        graph_progress={"progress": 100, "label": "图谱已逐批写入"},
                        summary={"entity_count": entity_count, "relation_count": relation_count},
                        log_message=(
                            f"实体与关系抽取完成，用时 {extraction_ms / 1000:.1f}s，"
                            f"实体 {entity_count}，关系 {relation_count}"
                        ),
                    )
                else:
                    # 仅分片模式下，向量写入完成后已经设置了进度为90%，这里不再重复设置
                    await FileService._commit_runtime_state(
                        db,
                        file,
                        message="已跳过图谱抽取，正在保存分片记录",
                        stage="saving",
                        extraction_progress={"progress": 100, "label": "已跳过图谱抽取"},
                        graph_progress={"progress": 100, "label": "已跳过"},
                        summary={"entity_count": 0, "relation_count": 0},
                        log_message="已跳过实体与关系抽取（extract_graph=false）",
                    )

                total_ms = (perf_counter() - pipeline_started) * 1000
                FileService._check_cancelled(file_id)
                await FileService._commit_runtime_state(
                    db,
                    file,
                    status="indexed",
                    progress=100,
                    message=(
                        f"Processing complete: {len(text_chunks)} chunks, "
                        f"{entity_count} entities, {relation_count} relations"
                    ),
                    stage="completed",
                    graph_progress={"progress": 100, "label": "Chunk records saved"},
                    summary={
                        "chunk_count": len(text_chunks),
                        "entity_count": entity_count,
                        "relation_count": relation_count,
                    },
                    log_message=(
                        f"Processing complete in {total_ms / 1000:.1f}s: "
                        f"chunks={len(text_chunks)}, entities={entity_count}, relations={relation_count}"
                    ),
                    finished=True,
                )
                logger.info(
                    "Chunk row persistence completed incrementally: file_id=%s kb_id=%s count=%s",
                    file.id,
                    file.kb_id,
                    len(text_chunks),
                )
                logger.info(
                    "Processing pipeline finished: file_id=%s kb_id=%s status=%s progress=%s total_duration_ms=%.0f",
                    file.id,
                    file.kb_id,
                    file.status,
                    file.progress,
                    total_ms,
                )
            except asyncio.CancelledError:
                logger.info("Processing task cancelled: file_id=%s", file_id)
                return
            except Exception as exc:
                logger.exception("Processing pipeline failed: file_id=%s error=%s", file_id, exc)
                try:
                    file = await db.get(File, file_id)
                    if file:
                        await FileService._delete_index_artifacts(db, file, remove_source_file=False)
                        await FileService._commit_runtime_state(
                            db,
                            file,
                            status="failed",
                            message=str(exc)[:200],
                            stage="failed",
                            log_message=f"处理失败：{exc}",
                            log_level="error",
                            finished=True,
                        )
                        await db.commit()
                except Exception as inner_exc:
                    logger.exception("Failed to update file status in original session: file_id=%s error=%s", file_id, inner_exc)
                    try:
                        async with async_session() as new_db:
                            file = await new_db.get(File, file_id)
                            if file:
                                await FileService._delete_index_artifacts(new_db, file, remove_source_file=False)
                                await FileService._commit_runtime_state(
                                    new_db,
                                    file,
                                    status="failed",
                                    message=str(exc)[:200],
                                    stage="failed",
                                    log_message=f"处理失败：{exc}",
                                    log_level="error",
                                    finished=True,
                                )
                                await new_db.commit()
                    except Exception as new_exc:
                        logger.exception("Failed to update file status in new session: file_id=%s error=%s", file_id, new_exc)

    @staticmethod
    async def _persist_extraction_to_sqlite(
        db: AsyncSession,
        *,
        file_id: str,
        kb_id: str,
        batch_chunks: list,
    ):
        """将抽取校验后的实体/关系实例写入 SQLite（权威存储），并回填实例 id 到 GraphEntity/GraphRelation。

        本方法在 batch_result_callback 中、写 Kùzu 之前调用，保证：
        1. SQLite 先写入实体实例（upsert by kb_id+entity_type+name），拿到实例 id
        2. 回填 GraphEntity.id，使后续 upsert_document_graph 在 Kùzu 中使用同一 id
        3. SQLite 写入关系实例（需要起终点实体 id），回填 GraphRelation.id / source_entity_id / target_entity_id

        EntityService.create_entity / create_relation 内部已 best-effort 同步 Kùzu（upsert_entity/upsert_relation），
        与 upsert_document_graph 的 MERGE 语义一致，不会冲突。
        """
        entity_key_to_id: dict[tuple[str, str], str] = {}
        for chunk in batch_chunks:
            # 1. 写实体实例，回填 id
            for entity in chunk.entities:
                if entity.id:
                    entity_key_to_id[(entity.name.strip().lower(), (entity.entity_type or "UNKNOWN").strip().lower())] = entity.id
                    continue
                if not entity.ontology_id:
                    continue  # 无本体归属（不应发生，后处理已过滤）
                key = (entity.name.strip().lower(), (entity.entity_type or "UNKNOWN").strip().lower())
                if key in entity_key_to_id:
                    entity.id = entity_key_to_id[key]
                    continue
                # 解析 properties JSON → dict
                props_dict = None
                if entity.properties:
                    try:
                        props_dict = json.loads(entity.properties)
                    except (json.JSONDecodeError, TypeError):
                        props_dict = None
                try:
                    result = await EntityService.create_entity(
                        db,
                        kb_id=kb_id,
                        ontology_id=entity.ontology_id,
                        entity_type=entity.entity_type,
                        name=entity.name,
                        description=entity.description or "",
                        properties=props_dict,
                        source_file_id=file_id,
                        source_chunk_id=chunk.chunk_id,
                    )
                    entity.id = result.get("id")
                    if entity.id:
                        entity_key_to_id[key] = entity.id
                except Exception:
                    logger.exception(
                        "Persist entity to SQLite failed: kb_id=%s entity_type=%s name=%s",
                        kb_id, entity.entity_type, entity.name,
                    )

            # 2. 写关系实例，回填 id + 起终点实体 id
            #    构建本 chunk 内 (name, entity_type) → entity_id 的查找表
            entity_id_lookup: dict[tuple[str, str], str] = {}
            for entity in chunk.entities:
                if entity.id:
                    entity_id_lookup[(entity.name, entity.entity_type)] = entity.id

            for relation in chunk.relations:
                if relation.id:
                    continue  # 已回填
                source_id = entity_id_lookup.get(
                    (relation.source_name, relation.source_type)
                )
                target_id = entity_id_lookup.get(
                    (relation.target_name, relation.target_type)
                )
                if not source_id or not target_id:
                    logger.warning(
                        "Skip relation (endpoint entity not found in chunk): "
                        "source=%s(%s) target=%s(%s) relation=%s",
                        relation.source_name, relation.source_type,
                        relation.target_name, relation.target_type,
                        relation.relation_type,
                    )
                    continue
                if not relation.relation_def_id:
                    continue  # 无关系定义归属（不应发生，后处理已过滤）
                try:
                    result = await EntityService.create_relation(
                        db,
                        kb_id=kb_id,
                        relation_def_id=relation.relation_def_id,
                        relation_type=relation.relation_type,
                        source_entity_id=source_id,
                        target_entity_id=target_id,
                        description=relation.description or "",
                        source_file_id=file_id,
                        source_chunk_id=chunk.chunk_id,
                    )
                    relation.id = result.get("id")
                    relation.source_entity_id = source_id
                    relation.target_entity_id = target_id
                except Exception:
                    logger.exception(
                        "Persist relation to SQLite failed: kb_id=%s relation=%s source=%s target=%s",
                        kb_id, relation.relation_type, relation.source_name, relation.target_name,
                    )

    @staticmethod
    async def list_all(db: AsyncSession) -> list[dict]:
        result = await db.execute(select(File))
        files = result.scalars().all()
        return [
            {
                "id": file.id,
                "name": file.name,
                "size": file.size,
                "asset_id": file.asset_id,
                "kb_id": file.kb_id,
                "status": file.status,
                "progress": file.progress,
                "message": file.message,
                "detail": FileService._read_detail(file),
                "logs": FileService._read_logs(file),
            }
            for file in files
        ]

    @staticmethod
    async def cancel_processing(db: AsyncSession, file_id: str) -> bool:
        """取消文件处理，删除已入库的数据（分片、向量、图谱），保持原文件不变"""
        file = await db.get(File, file_id)
        if not file:
            logger.warning("Cancel processing skipped: file_id=%s not found", file_id)
            return False
        
        # 允许在 processing 和 indexed 状态下取消处理（删除已入库的数据）
        if file.status not in ("processing", "indexed"):
            logger.warning("Cancel processing skipped: file_id=%s invalid status=%s", file_id, file.status)
            return False

        # 首先取消正在运行的处理任务，等待其结束（无论是正常结束还是被取消），确保不会有并发的数据库操作冲突
        running_task = FileService._cancel_running_task(file_id)
        if running_task:
            try:
                await asyncio.wait_for(asyncio.shield(running_task), timeout=1.5)
            except asyncio.TimeoutError:
                logger.warning("Processing task did not stop within timeout: file_id=%s", file_id)
            except asyncio.CancelledError:
                logger.info("Processing task acknowledged cancellation: file_id=%s", file_id)
            except Exception:
                logger.exception("Processing task ended with error while cancelling: file_id=%s", file_id)

        # 删除已入库的数据（分片、向量、图谱），但保留原文件
        await FileService._delete_index_artifacts(db, file, remove_source_file=False)
        
        # 重置文件状态为 uploaded（等待处理）
        file.status = "uploaded"
        file.progress = 0
        file.message = "处理已取消"
        FileService._write_detail(file, FileService._empty_detail())
        FileService._write_logs(file, [])
        
        await db.commit()
        FileService._publish_status(file.id, FileService._build_status_payload(file))
        logger.info("Processing cancelled: file_id=%s kb_id=%s", file.id, file.kb_id)
        return True

    @staticmethod
    async def delete(db: AsyncSession, file_id: str) -> bool:
        file = await db.get(File, file_id)
        if not file:
            logger.warning("Delete file skipped: file_id=%s not found", file_id)
            return False
        await FileService._delete_index_artifacts(db, file, remove_source_file=True)

        await db.delete(file)
        await db.commit()
        logger.info("File delete completed: file_id=%s kb_id=%s", file.id, file.kb_id)
        return True

    @staticmethod
    async def batch_delete(db: AsyncSession, file_ids: list[str]) -> dict:
        deleted_count = 0
        not_found_count = 0
        
        for file_id in file_ids:
            file = await db.get(File, file_id)
            if not file:
                not_found_count += 1
                logger.warning("Batch delete: file_id=%s not found", file_id)
                continue
            
            try:
                await FileService._delete_index_artifacts(db, file, remove_source_file=True)
                await db.delete(file)
                deleted_count += 1
            except Exception as e:
                logger.exception("Batch delete failed for file_id=%s: %s", file_id, e)
        
        if deleted_count > 0:
            await db.commit()
        
        logger.info("Batch delete completed: deleted=%d not_found=%d", deleted_count, not_found_count)
        return {"deleted": deleted_count, "not_found": not_found_count}

    @staticmethod
    async def cleanup_zombie_tasks(db: AsyncSession):
        """清理僵尸任务：后端重启后，状态仍为 processing 的文件实际上没有在处理"""
        result = await db.execute(select(File).where(File.status == "processing"))
        zombie_files = result.scalars().all()
        
        if not zombie_files:
            logger.info("No zombie processing tasks found")
            return
        
        logger.info("Found %d zombie processing tasks, cleaning up...", len(zombie_files))
        
        for file in zombie_files:
            try:
                # 删除可能不完整的入库数据
                await FileService._delete_index_artifacts(db, file, remove_source_file=False)
                
                # 重置文件状态为 uploaded（等待处理）
                file.status = "uploaded"
                file.progress = 0
                file.message = "服务已重启，请重新处理"
                FileService._write_detail(file, FileService._empty_detail())
                FileService._write_logs(file, [])
                
                logger.info("Cleaned up zombie task: file_id=%s file_name=%s", file.id, file.name)
            except Exception as exc:
                logger.exception("Failed to cleanup zombie task: file_id=%s error=%s", file.id, exc)
        
        await db.commit()
        logger.info("Zombie tasks cleanup completed")
