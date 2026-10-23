"""Chat session CRUD — list / create / rename / delete user sessions.

GET    /api/v1/chat/sessions              -> list sessions (newest first)
POST   /api/v1/chat/sessions              -> create session
PATCH  /api/v1/chat/sessions/{id}         -> rename session
DELETE /api/v1/chat/sessions/{id}         -> delete session + messages
GET    /api/v1/chat/sessions/{id}/messages -> load messages
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.modules.knowledge.provider import knowledge
from app.shared.core.dependencies import get_db, require_member
from app.shared.core.utils import ok
from app.shared.models.chat_message import ChatMessage
from app.shared.models.chat_session import ChatSession
from app.shared.models.user import User

router = APIRouter()


@router.get("/chat/sessions")
def list_sessions(
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == current_user.id)
        .order_by(desc(ChatSession.updated_at))
        .all()
    )
    return ok([s.to_dict() for s in sessions])


@router.post("/chat/sessions")
def create_session(
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    s = knowledge(db).create_session.execute(user_id=current_user.id)
    return ok(s.to_dict())


class RenameBody(BaseModel):
    title: str


@router.patch("/chat/sessions/{session_id}")
def rename_session(
    session_id: int,
    body: RenameBody,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    s = knowledge(db).rename_session.execute(session_id=session_id, user_id=current_user.id, title=body.title)
    return ok(s.to_dict())


@router.delete("/chat/sessions/{session_id}")
def delete_session(
    session_id: int,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    knowledge(db).delete_session.execute(session_id=session_id, user_id=current_user.id)
    return ok({"id": session_id})


@router.get("/chat/sessions/{session_id}/messages")
def get_messages(
    session_id: int,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    knowledge(db).ensure_own_session.execute(session_id, current_user.id)
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.id)
        .all()
    )
    return ok([m.to_dict() for m in msgs])


class FeedbackBody(BaseModel):
    value: int  # 1 = thumbs-up, -1 = thumbs-down


@router.post("/chat/sessions/{session_id}/messages/{message_id}/feedback")
def set_message_feedback(
    session_id: int,
    message_id: int,
    body: FeedbackBody,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    knowledge(db).set_message_feedback.execute(
        session_id=session_id, user_id=current_user.id, message_id=message_id, value=body.value
    )
    return ok({"id": message_id, "feedback": body.value})
