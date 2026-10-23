"""SQLAlchemy / Milvus adapters for the knowledge context."""
import sqlalchemy
from sqlalchemy.orm import Session

from app.modules.knowledge.infrastructure.milvus import milvus_service
from app.shared.core.database import SessionLocal
from app.shared.models.chat_log import ChatLog
from app.shared.models.chat_message import ChatMessage
from app.shared.models.chat_session import ChatSession
from app.shared.models.resource_tag import ResourceTag


class SqlConsultationLedger:
    def __init__(self, db: Session) -> None:
        self.db = db

    def today_count(self, user_id: int) -> int:
        from datetime import date

        return self.db.query(ChatLog).filter(ChatLog.user_id == user_id, ChatLog.created_at >= date.today()).count()

    def record(self, *, user_id: int, ip_address: str, question: str, source_docs_json: str) -> int:
        log = ChatLog(user_id=user_id, ip_address=ip_address, question=question, source_docs=source_docs_json)
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return log.id

    def backfill_usage(self, log_id: int | None, usage: dict) -> None:
        """Backfill token usage onto the chat_logs row (best-effort, own session)."""
        if not log_id or not usage:
            return
        db = SessionLocal()
        try:
            row = db.query(ChatLog).filter(ChatLog.id == log_id).first()
            if row:
                row.prompt_tokens = usage.get("prompt_tokens")
                row.completion_tokens = usage.get("completion_tokens")
                db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


class SqlSessionDirectory:
    def __init__(self, db: Session) -> None:
        self.db = db

    def find_owned(self, session_id: int, user_id: int) -> ChatSession | None:
        return self.db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == user_id).first()

    def create(self, user_id: int) -> ChatSession:
        s = ChatSession(user_id=user_id, title="新会话")
        self.db.add(s)
        self.db.commit()
        self.db.refresh(s)
        return s

    def rename(self, session: ChatSession, title: str) -> ChatSession:
        session.title = title
        self.db.commit()
        self.db.refresh(session)
        return session

    def delete(self, session: ChatSession) -> None:
        self.db.delete(session)
        self.db.commit()

    def set_feedback(self, session_id: int, message_id: int, value: int) -> bool:
        msg = (
            self.db.query(ChatMessage)
            .filter(
                ChatMessage.id == message_id,
                ChatMessage.session_id == session_id,
                ChatMessage.role == "assistant",
            )
            .first()
        )
        if not msg:
            return False
        msg.feedback = value
        self.db.commit()
        return True


class SqlChatTranscripts:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_user_turn(self, session_id: int, content: str, auto_title: str | None) -> None:
        self.db.add(ChatMessage(session_id=session_id, role="user", content=content))
        if auto_title:
            self.db.query(ChatSession).filter(ChatSession.id == session_id).update({"title": auto_title})
        self.db.commit()

    def save_assistant_reply(self, session_id: int, content: str, sources_json: str | None, mode: str | None) -> None:
        # The request-scoped session is already closed by the time a
        # StreamingResponse body runs — use a fresh one (best-effort).
        save_db = SessionLocal()
        try:
            save_db.add(
                ChatMessage(session_id=session_id, role="assistant", content=content, sources=sources_json, mode=mode)
            )
            save_db.execute(
                sqlalchemy.text("UPDATE chat_sessions SET updated_at = NOW() WHERE id = :sid"),
                {"sid": session_id},
            )
            save_db.commit()
        except Exception:
            save_db.rollback()
        finally:
            save_db.close()


class SqlBrandDirectory:
    def __init__(self, db: Session) -> None:
        self.db = db

    def registered_brands(self) -> list[tuple[str, str | None]]:
        rows = (
            self.db.query(ResourceTag.value, ResourceTag.label_zh)
            .filter(ResourceTag.type == "doc_brand", ResourceTag.is_active.is_(True))
            .all()
        )
        return [(value, label_zh) for value, label_zh in rows]


class MilvusSearchGateway:
    def search(self, query: str, top_k: int, where_filter: dict | None) -> list[dict]:
        return milvus_service.search(query, top_k=top_k, where_filter=where_filter)
