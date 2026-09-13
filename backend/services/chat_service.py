"""智能体会话服务：短期记忆的落库、装填与滚动摘要。

按 doc/智能体/智能体会话_功能设计.md：
- P1 会话 / 消息 CRUD + 近 N 轮装填（窗口轮数 + 字符预算双重约束）
- P2 滚动摘要：累计轮数超阈值时，把窗口外的旧轮次压缩进 session.summary，
  通过 summary_until_id 幂等推进，不重复摘要。
"""

import json
import logging
import uuid
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models import ChatMessage, ChatSession

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now().isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class ChatService:
    # ───────────────────────── 会话 CRUD ─────────────────────────

    @staticmethod
    async def create_session(db: AsyncSession, agent_id: str = "", kb_id: str = "",
                             user_id: str = "", title: str = "") -> ChatSession:
        session = ChatSession(
            id=_new_id(), agent_id=agent_id or "", kb_id=kb_id or "",
            user_id=user_id or "", title=(title or "").strip()[:200],
            created_at=_now(), updated_at=_now(),
        )
        db.add(session)
        await db.commit()
        return session

    @staticmethod
    async def get(db: AsyncSession, session_id: str) -> ChatSession | None:
        return await db.get(ChatSession, session_id)

    @staticmethod
    async def list_sessions(db: AsyncSession, agent_id: str | None = None,
                            kb_id: str | None = None, limit: int = 50) -> list[ChatSession]:
        stmt = select(ChatSession)
        if agent_id is not None:
            stmt = stmt.where(ChatSession.agent_id == agent_id)
        if kb_id:
            stmt = stmt.where(ChatSession.kb_id == kb_id)
        stmt = stmt.order_by(ChatSession.updated_at.desc()).limit(max(1, min(limit, 200)))
        rows = await db.execute(stmt)
        return list(rows.scalars().all())

    @staticmethod
    async def rename(db: AsyncSession, session_id: str, title: str) -> ChatSession | None:
        session = await db.get(ChatSession, session_id)
        if not session:
            return None
        new_title = (title or "").strip()[:200]
        if new_title:
            session.title = new_title
        session.updated_at = _now()
        await db.commit()
        return session

    @staticmethod
    async def delete(db: AsyncSession, session_id: str) -> bool:
        session = await db.get(ChatSession, session_id)
        if not session:
            return False
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
        await db.delete(session)
        await db.commit()
        return True

    # ───────────────────────── 消息 ─────────────────────────

    @staticmethod
    async def get_messages(db: AsyncSession, session_id: str) -> list[ChatMessage]:
        rows = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
        return list(rows.scalars().all())

    @staticmethod
    async def append_message(db: AsyncSession, session_id: str, role: str,
                             content: str, meta: dict | None = None) -> ChatMessage:
        msg = ChatMessage(
            session_id=session_id, role=role, content=content or "",
            meta=json.dumps(meta, ensure_ascii=False) if meta else "",
            created_at=_now(),
        )
        db.add(msg)
        session = await db.get(ChatSession, session_id)
        if session:
            # 首条用户消息作为会话标题
            if role == "user" and not (session.title or "").strip():
                session.title = (content or "").strip()[:50]
            session.updated_at = _now()
        await db.commit()
        return msg

    # ───────────────────────── 历史装填（P1 核心） ─────────────────────────

    @staticmethod
    async def load_history(db: AsyncSession, session_id: str) -> dict:
        """装填注入 prompt 的历史，返回 {history, summary}。

        - 只取最近 CHAT_SESSION_WINDOW_TURNS 轮（一轮 = 用户一问 + 助手一答）；
        - 总字符超过 CHAT_SESSION_CHAR_BUDGET 时从最旧开始丢弃；
          最新一条本身超预算时截尾保留（宁可短也不丢最近上下文）；
        - history 顺序为「从旧到新」，可直接转 LangChain 消息序列。
        """
        session = await db.get(ChatSession, session_id)
        summary = ""
        if session and settings.CHAT_SUMMARY_ENABLED:
            summary = (session.summary or "").strip()

        messages = await ChatService.get_messages(db, session_id)
        window = max(0, settings.CHAT_SESSION_WINDOW_TURNS) * 2
        candidates = messages[-window:] if window > 0 else []

        budget = max(0, settings.CHAT_SESSION_CHAR_BUDGET)
        picked: list[dict] = []
        used = 0
        for msg in reversed(candidates):
            cost = len(msg.content or "")
            if used + cost > budget and picked:
                break  # 预算满且已有内容：从这条起（更旧的）全部丢弃
            picked.append({"role": msg.role, "content": msg.content or ""})
            used += cost
        picked.reverse()

        if picked and 0 < budget < len(picked[-1]["content"]):
            picked[-1]["content"] = "…" + picked[-1]["content"][-budget:]

        return {"history": picked, "summary": summary}

    # ───────────────────────── 滚动摘要（P2） ─────────────────────────

    @staticmethod
    async def maybe_summarize(db: AsyncSession, session_id: str) -> None:
        """累计轮数超阈值时，把窗口外的旧轮次滚动压缩进 session.summary。

        - 幂等：summary_until_id 记录已计入摘要的最后一条消息，重复调用不重复摘要；
        - 新摘要 = LLM(旧摘要 + 出窗轮次)；
        - LLM 未配置或调用失败时静默跳过，不影响主链路。
        """
        if not settings.CHAT_SUMMARY_ENABLED:
            return
        session = await db.get(ChatSession, session_id)
        if not session:
            return
        messages = await ChatService.get_messages(db, session_id)
        trigger = max(1, settings.CHAT_SUMMARY_TRIGGER_TURNS) * 2  # 一轮两条
        if len(messages) < trigger:
            return

        window = max(0, settings.CHAT_SESSION_WINDOW_TURNS) * 2
        if session.summary_until_id:
            idx = next((i for i, m in enumerate(messages) if m.id == session.summary_until_id), -1)
            older = messages[idx + 1: len(messages) - window] if idx >= 0 else messages[: len(messages) - window]
        else:
            older = messages[: len(messages) - window]
        if not older:
            return

        transcript = "\n".join(
            f"{'用户' if m.role == 'user' else '助手'}：{m.content}" for m in older
        )
        if len(transcript) > 8000:
            transcript = transcript[:4000] + "\n…（中间省略）…\n" + transcript[-4000:]

        prompt = (
            "请把下面这段多轮对话压缩成一份要点摘要，作为后续对话的背景知识。\n"
            "要求：只保留事实、结论、关键实体与数字；不评价、不复述原句；"
            f"总长不超过 {settings.CHAT_SUMMARY_MAX_CHARS} 字。\n\n"
            f"已有摘要（若有，请在其基础上合并更新）：\n{(session.summary or '（无）')}\n\n"
            f"新增对话：\n{transcript}"
        )
        try:
            from providers.llm import create_llm

            llm = create_llm()
            if llm is None:
                return
            resp = await llm.ainvoke(prompt)
            text = (getattr(resp, "content", "") or "").strip()
            if not text:
                return
            session.summary = text[: settings.CHAT_SUMMARY_MAX_CHARS]
            session.summary_until_id = older[-1].id
            session.updated_at = _now()
            await db.commit()
            logger.info("Chat summary updated: session=%s summarized=%d messages",
                        session_id, len(older))
        except Exception:
            logger.exception("Chat summary failed: session=%s", session_id)
