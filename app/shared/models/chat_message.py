"""Chat message — one turn (user or assistant) within a session."""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, SmallInteger, String, Text, func

from app.shared.models import Base


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(BigInteger, ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)       # "user" | "assistant"
    content = Column(Text, nullable=False, default="")
    sources = Column(Text, nullable=True)           # JSON: [{document_name, page}]
    mode = Column(String(20), nullable=True)        # "grounded" | "fallback"
    feedback = Column(SmallInteger, nullable=True)  # 1=thumbs-up  -1=thumbs-down
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "sources": json.loads(self.sources) if self.sources else None,
            "mode": self.mode,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
