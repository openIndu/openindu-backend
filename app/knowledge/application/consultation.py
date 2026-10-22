"""Consultation flow use cases: quota, retrieval, quota logging, turn recording."""
import json
from typing import Protocol

from app.domain.knowledge import KnowledgeCode, KnowledgeError
from app.knowledge.application.retrieval import Retriever
from app.knowledge.domain.answering import sources_from


class ConsultationLedger(Protocol):
    def today_count(self, user_id: int) -> int: ...

    def record(self, *, user_id: int, ip_address: str, question: str, source_docs_json: str) -> int: ...

    def backfill_usage(self, log_id: int | None, usage: dict) -> None: ...


class TranscriptLedger(Protocol):
    def record_user_turn(self, session_id: int, content: str, auto_title: str | None) -> None: ...

    def save_assistant_reply(self, session_id: int, content: str, sources_json: str | None, mode: str | None) -> None: ...


class EnsureChatQuota:
    def __init__(self, ledger: ConsultationLedger, limit: int) -> None:
        self.ledger = ledger
        self.limit = limit

    def execute(self, *, role: str, user_id: int) -> None:
        if role == "admin":
            return
        if self.ledger.today_count(user_id) >= self.limit:
            raise KnowledgeError(KnowledgeCode.QUOTA_EXCEEDED, limit=self.limit)


class RetrieveChunks:
    def __init__(self, retriever: Retriever) -> None:
        self.retriever = retriever

    def execute(self, query: str, filters: dict | None = None, top_k: int | None = None) -> list[dict]:
        try:
            return self.retriever.retrieve(query, top_k, filters)
        except KnowledgeError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise KnowledgeError(KnowledgeCode.RETRIEVAL_UNAVAILABLE) from exc


class RecordConsultation:
    def __init__(self, ledger: ConsultationLedger) -> None:
        self.ledger = ledger

    def execute(self, *, user_id: int, ip_address: str, question: str, chunks: list[dict]) -> tuple[list[dict], int]:
        sources = sources_from(chunks)
        log_id = self.ledger.record(
            user_id=user_id,
            ip_address=ip_address,
            question=question[:2000],
            source_docs_json=json.dumps([s["document_name"] for s in sources], ensure_ascii=False),
        )
        return sources, log_id


class RecordUserTurn:
    def __init__(self, transcripts: TranscriptLedger) -> None:
        self.transcripts = transcripts

    def execute(self, *, session_id: int, session_title: str, had_history: bool, question: str) -> None:
        auto_title = None
        if session_title == "新会话" and not had_history:
            auto_title = question.strip()[:30]
        self.transcripts.record_user_turn(session_id, question[:4000], auto_title)


async def persist_after_stream(save: TranscriptLedger, stream, *, session_id: int, sources_json: str | None):
    """Tap the SSE answer stream, then persist the assistant reply afterwards."""
    full_content = ""
    final_mode = None
    current_event = None
    async for chunk in stream:
        text = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    d = json.loads(raw)
                    if current_event == "delta":
                        full_content += d.get("text", "")
                    elif current_event == "mode":
                        final_mode = d.get("mode")
                except Exception:  # noqa: BLE001
                    pass
        yield chunk
    save.save_assistant_reply(session_id, full_content, sources_json, final_mode)
