"""Chat session use cases: create, rename, delete, message feedback."""
from typing import Protocol

from app.domain.knowledge import KnowledgeCode, KnowledgeError
from app.modules.knowledge.domain import rules as session_rules


class ChatSessionEntry(Protocol):
    id: int
    title: str

    def to_dict(self) -> dict: ...


class SessionDirectory(Protocol):
    def find_owned(self, session_id: int, user_id: int) -> ChatSessionEntry | None: ...

    def create(self, user_id: int) -> ChatSessionEntry: ...

    def rename(self, session: ChatSessionEntry, title: str) -> ChatSessionEntry: ...

    def delete(self, session: ChatSessionEntry) -> None: ...

    def set_feedback(self, session_id: int, message_id: int, value: int) -> bool: ...


class EnsureOwnSession:
    def __init__(self, sessions: SessionDirectory) -> None:
        self.sessions = sessions

    def execute(self, session_id: int, user_id: int) -> ChatSessionEntry:
        session = self.sessions.find_owned(session_id, user_id)
        if not session:
            raise KnowledgeError(KnowledgeCode.SESSION_NOT_FOUND)
        return session


class CreateChatSession:
    def __init__(self, sessions: SessionDirectory) -> None:
        self.sessions = sessions

    def execute(self, *, user_id: int) -> ChatSessionEntry:
        return self.sessions.create(user_id)


class RenameChatSession:
    def __init__(self, sessions: SessionDirectory) -> None:
        self.sessions = sessions

    def execute(self, *, session_id: int, user_id: int, title: str) -> ChatSessionEntry:
        clean = session_rules.require_session_title(title)
        session = self.sessions.find_owned(session_id, user_id)
        if not session:
            raise KnowledgeError(KnowledgeCode.SESSION_NOT_FOUND)
        return self.sessions.rename(session, clean)


class DeleteChatSession:
    def __init__(self, sessions: SessionDirectory) -> None:
        self.sessions = sessions

    def execute(self, *, session_id: int, user_id: int) -> None:
        session = self.sessions.find_owned(session_id, user_id)
        if not session:
            raise KnowledgeError(KnowledgeCode.SESSION_NOT_FOUND)
        self.sessions.delete(session)


class SetMessageFeedback:
    def __init__(self, sessions: SessionDirectory) -> None:
        self.sessions = sessions

    def execute(self, *, session_id: int, user_id: int, message_id: int, value: int) -> None:
        session_rules.require_feedback_value(value)
        if not self.sessions.find_owned(session_id, user_id):
            raise KnowledgeError(KnowledgeCode.SESSION_NOT_FOUND)
        if not self.sessions.set_feedback(session_id, message_id, value):
            raise KnowledgeError(KnowledgeCode.MESSAGE_NOT_FOUND)
