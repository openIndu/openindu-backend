"""Composition root for the knowledge context (RAG consultation, chat sessions)."""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.knowledge.application.answering import StreamAnswer
from app.knowledge.application.consultation import (
    EnsureChatQuota,
    RecordConsultation,
    RecordUserTurn,
    RetrieveChunks,
    TranscriptLedger,
)
from app.knowledge.application.retrieval import Retriever
from app.knowledge.application.rewriter import RewriteQuery
from app.knowledge.application.sessions import (
    CreateChatSession,
    DeleteChatSession,
    EnsureOwnSession,
    RenameChatSession,
    SetMessageFeedback,
)
from app.knowledge.infrastructure.llm import OpenAiCompatibleLlm
from app.knowledge.infrastructure.repositories import (
    MilvusSearchGateway,
    SqlBrandDirectory,
    SqlChatTranscripts,
    SqlConsultationLedger,
    SqlSessionDirectory,
)


@dataclass
class KnowledgeContext:
    ensure_quota: EnsureChatQuota
    rewritten_query: RewriteQuery
    retrieve_chunks: RetrieveChunks
    record_consultation: RecordConsultation
    record_user_turn: RecordUserTurn
    stream_answer: StreamAnswer
    ensure_own_session: EnsureOwnSession
    create_session: CreateChatSession
    rename_session: RenameChatSession
    delete_session: DeleteChatSession
    set_message_feedback: SetMessageFeedback
    transcripts: TranscriptLedger


def knowledge(db: Session) -> KnowledgeContext:
    llm = OpenAiCompatibleLlm()
    ledger = SqlConsultationLedger(db)
    sessions = SqlSessionDirectory(db)
    retriever = Retriever(MilvusSearchGateway(), SqlBrandDirectory(db), default_top_k=settings.RAG_TOP_K)
    return KnowledgeContext(
        ensure_quota=EnsureChatQuota(ledger, settings.CHAT_DAILY_LIMIT),
        rewritten_query=RewriteQuery(llm, model=settings.LLM_MODEL, api_key=settings.LLM_API_KEY),
        retrieve_chunks=RetrieveChunks(retriever),
        record_consultation=RecordConsultation(ledger),
        record_user_turn=RecordUserTurn(SqlChatTranscripts(db)),
        stream_answer=StreamAnswer(
            llm,
            ledger,
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
        ),
        ensure_own_session=EnsureOwnSession(sessions),
        create_session=CreateChatSession(sessions),
        rename_session=RenameChatSession(sessions),
        delete_session=DeleteChatSession(sessions),
        set_message_feedback=SetMessageFeedback(sessions),
        transcripts=SqlChatTranscripts(db),
    )
