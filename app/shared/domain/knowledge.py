"""Knowledge outcomes independent of HTTP and presentation language."""
from enum import StrEnum


class KnowledgeCode(StrEnum):
    EMPTY_QUESTION = "KNOWLEDGE_EMPTY_QUESTION"
    QUOTA_EXCEEDED = "KNOWLEDGE_QUOTA_EXCEEDED"
    RETRIEVAL_UNAVAILABLE = "KNOWLEDGE_RETRIEVAL_UNAVAILABLE"
    SESSION_NOT_FOUND = "KNOWLEDGE_SESSION_NOT_FOUND"
    TITLE_EMPTY = "KNOWLEDGE_TITLE_EMPTY"
    MESSAGE_NOT_FOUND = "KNOWLEDGE_MESSAGE_NOT_FOUND"
    FEEDBACK_INVALID = "KNOWLEDGE_FEEDBACK_INVALID"


class KnowledgeError(Exception):
    """Transport-independent knowledge-context failure."""

    def __init__(self, code: KnowledgeCode, **params: object) -> None:
        self.code = code
        self.params = params
        super().__init__(code.value)
