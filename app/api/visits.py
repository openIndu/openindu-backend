"""Visit tracking API for dashboard analytics (adapter for the analytics context)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from jwt import PyJWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.analytics.provider import analytics
from app.modules.identity.infrastructure.tokens import decode_token
from app.shared.core.dependencies import get_db
from app.shared.core.utils import ok
from app.shared.domain.auth import AuthError

router = APIRouter(prefix="/visits")


def client_ip(request: Request) -> str:
    from app.shared.core.utils import real_client_ip
    return real_client_ip(request) or "unknown"


class VisitBody(BaseModel):
    path: str = "/"
    client_id: str | None = None
    event_type: str = "page_view"


def _bearer_user_id(request: Request) -> int | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = decode_token(auth.split(" ", 1)[1])
        if payload.get("type") == "access" and payload.get("sub"):
            return int(payload["sub"])
    except (AuthError, HTTPException, PyJWTError, ValueError):
        pass
    return None


@router.post("/track")
async def track_visit(body: VisitBody, request: Request, db: Session = Depends(get_db)):
    if body.event_type != "page_view":
        raise HTTPException(400, "不支持的访问事件类型")
    analytics(db).track_visit.execute(
        path=body.path,
        client_id=body.client_id,
        user_id=_bearer_user_id(request),
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent", ""),
    )
    return ok({"tracked": True})
