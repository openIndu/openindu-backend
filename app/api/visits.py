"""Visit tracking API for dashboard analytics."""
from fastapi import APIRouter, Depends, HTTPException, Request
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db
from app.core.utils import ok
from app.identity.infrastructure.tokens import decode_token
from app.models.visit_event import VisitEvent
from app.services.geo_service import resolve_ip_geo

router = APIRouter(prefix="/visits")


def client_ip(request: Request) -> str:
    from app.core.utils import real_client_ip
    return real_client_ip(request) or "unknown"


class VisitBody(BaseModel):
    path: str = "/"
    client_id: str | None = None
    event_type: str = "page_view"


def _safe_id(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    return value[:64]


@router.post("/track")
async def track_visit(body: VisitBody, request: Request, db: Session = Depends(get_db)):
    if body.event_type != "page_view":
        raise HTTPException(400, "不支持的访问事件类型")
    ip = client_ip(request)
    ua = request.headers.get("user-agent", "")[:512]
    geo = resolve_ip_geo(ip)
    user_id = None
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = decode_token(auth.split(" ", 1)[1])
            if payload.get("type") == "access" and payload.get("sub"):
                user_id = int(payload["sub"])
        except (HTTPException, JWTError, ValueError):
            user_id = None
    event = VisitEvent(
        ip_address=ip,
        client_id=_safe_id(body.client_id),
        event_type="page_view",
        user_agent=ua,
        path=body.path[:512] or "/",
        geo_location=str(geo["name"]),
        country_code=str(geo["country_code"]),
        is_authenticated=user_id is not None,
        user_id=user_id,
    )
    db.add(event)
    db.commit()
    return ok({"tracked": True})
