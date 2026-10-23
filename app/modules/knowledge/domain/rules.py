"""Input rules for knowledge consultations and chat sessions."""
from app.domain.knowledge import KnowledgeCode, KnowledgeError


def require_non_empty_question(question: str) -> str:
    if not question or not question.strip():
        raise KnowledgeError(KnowledgeCode.EMPTY_QUESTION)
    return question


def require_session_title(title: str) -> str:
    clean = title.strip()[:100]
    if not clean:
        raise KnowledgeError(KnowledgeCode.TITLE_EMPTY)
    return clean


def require_feedback_value(value: int) -> None:
    if value not in (1, -1):
        raise KnowledgeError(KnowledgeCode.FEEDBACK_INVALID)
