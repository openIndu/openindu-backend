"""Smart-consultation (RAG chat) API — adapter for the knowledge context. §4.3.12

POST /api/v1/chat        -> SSE stream (event: sources / delta / done / error)
GET  /api/v1/chat/quota  -> remaining daily quota

Distinct from the MCP server (§4.4, serves external Claude Code): this endpoint
runs an in-process LLM (DeepSeek) over the same Milvus retrieval.
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.dependencies import get_db, require_member
from app.core.utils import ok, real_client_ip
from app.models.chat_log import ChatLog
from app.models.chat_message import ChatMessage
from app.models.user import User
from app.modules.knowledge.application.consultation import persist_after_stream
from app.modules.knowledge.domain.rules import require_non_empty_question
from app.modules.knowledge.provider import knowledge

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # 关闭 nginx/ingress 缓冲，保证逐 token 流式（见 §8 NFR / §4.3.12 部署注意）
    "X-Accel-Buffering": "no",
}


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatBody(BaseModel):
    message: str
    history: list[ChatTurn] | None = None
    filters: dict | None = None


def _today_count(db: Session, user_id: int) -> int:
    return (
        db.query(ChatLog)
        .filter(ChatLog.user_id == user_id, ChatLog.created_at >= date.today())
        .count()
    )


@router.get("/chat/quota")
async def chat_quota(
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    if current_user.role == "admin":
        return ok({"limit": settings.CHAT_DAILY_LIMIT, "used": 0, "remaining": None, "unlimited": True})
    used = _today_count(db, current_user.id)
    return ok({
        "limit": settings.CHAT_DAILY_LIMIT,
        "used": used,
        "remaining": max(0, settings.CHAT_DAILY_LIMIT - used),
        "unlimited": False,
    })


@router.post("/chat")
async def chat(
    body: ChatBody,
    request: Request,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    require_non_empty_question(body.message)
    ctx = knowledge(db)

    # 1. 每日配额（admin 豁免），与下载限额同范式（§4.3.6-1）
    ctx.ensure_quota.execute(role=current_user.role, user_id=current_user.id)

    # 2. 检索（阻塞调用放线程池，避免卡事件循环）
    #    先用 LLM 将多轮对话改写为自包含搜索 query，再送入 Milvus
    history_dicts = [t.model_dump() for t in (body.history or [])]
    rewritten = await ctx.rewritten_query.execute(body.message, history_dicts)
    filters = {
        "brand": (body.filters or {}).get("brand"),
        "category": (body.filters or {}).get("category"),
    }
    chunks = await run_in_threadpool(ctx.retrieve_chunks.execute, rewritten, filters)

    # 3. 记一条 chat_log（计入当日配额；token 用量在流结束后回填）
    sources, log_id = ctx.record_consultation.execute(
        user_id=current_user.id,
        ip_address=real_client_ip(request) or "unknown",
        question=body.message,
        chunks=chunks,
    )

    # 4. SSE 流式返回（sources -> delta* -> done）
    return StreamingResponse(
        ctx.stream_answer.execute(body.message, history_dicts, chunks, sources, log_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat/sessions/{session_id}/stream")
async def chat_session_stream(
    session_id: int,
    body: ChatBody,
    request: Request,
    current_user: User = Depends(require_member),
    db: Session = Depends(get_db),
):
    require_non_empty_question(body.message)
    ctx = knowledge(db)

    # Validate session ownership
    session = ctx.ensure_own_session.execute(session_id, current_user.id)

    # Daily quota check (admin exempt)
    ctx.ensure_quota.execute(role=current_user.role, user_id=current_user.id)

    # Load history from DB (last 6 messages)
    db_msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(desc(ChatMessage.id))
        .limit(6)
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(db_msgs)]

    # Persist user message (+ auto-title from first user message)
    ctx.record_user_turn.execute(
        session_id=session_id,
        session_title=session.title,
        had_history=bool(db_msgs),
        question=body.message,
    )

    # Retrieval — LLM rewrites the query first for cross-turn context
    rewritten = await ctx.rewritten_query.execute(body.message, history)
    filters = {
        "brand": (body.filters or {}).get("brand"),
        "category": (body.filters or {}).get("category"),
    }
    chunks = await run_in_threadpool(ctx.retrieve_chunks.execute, rewritten, filters)

    # Quota log
    sources, log_id = ctx.record_consultation.execute(
        user_id=current_user.id,
        ip_address=real_client_ip(request) or "unknown",
        question=body.message,
        chunks=chunks,
    )

    # Persist assistant message after stream completes
    stream = ctx.stream_answer.execute(body.message, history, chunks, sources, log_id)
    return StreamingResponse(
        persist_after_stream(
            ctx.transcripts,
            stream,
            session_id=session_id,
            sources_json=json.dumps(sources, ensure_ascii=False) if sources else None,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
