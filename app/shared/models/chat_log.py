"""Smart-consultation (RAG chat) audit log and daily-limit counter.

One row per `/api/v1/chat` question. Daily quota is COUNT(*) for the user on
the current date; token usage is backfilled after the answer streams. Mirrors
the download_logs design (audit + per-day counter).
"""
from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text, func

from app.shared.models import Base


class ChatLog(Base):
    __tablename__ = "chat_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False, index=True)
    ip_address = Column(String(64), nullable=True)
    question = Column(Text, nullable=True)
    source_docs = Column(Text, nullable=True)  # JSON: 命中文档名列表
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)
