"""Middleware that updates login session activity."""
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from jwt import PyJWTError
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware

from app.modules.analytics.infrastructure.geo import resolve_ip_geo
from app.modules.identity.infrastructure.tokens import decode_token
from app.shared.core.database import SessionLocal
from app.shared.domain.auth import AuthError
from app.shared.models.login_session import LoginSession


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


def _client_ip(request: Request) -> str:
    from app.shared.core.utils import real_client_ip
    return real_client_ip(request) or "unknown"


def _client_id(request: Request) -> str | None:
    value = request.headers.get("x-openindu-client-id", "").strip()
    return value[:64] or None


class OnlineStatsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                payload = decode_token(auth.split(" ", 1)[1])
                if payload.get("type") == "access" and payload.get("sub"):
                    db = SessionLocal()
                    try:
                        user_id = int(payload["sub"])
                        ip = _client_ip(request)
                        ua = request.headers.get("user-agent", "")[:512]
                        cid = _client_id(request)
                        geo = resolve_ip_geo(ip)
                        now = _now()
                        if cid:
                            # Same browser/client switched account: retire the
                            # previous user's active session for this client id.
                            db.query(LoginSession).filter(
                                LoginSession.client_id == cid,
                                LoginSession.user_id != user_id,
                                LoginSession.is_active.is_(True),
                            ).update({"is_active": False}, synchronize_session=False)
                            session = db.query(LoginSession).filter(
                                LoginSession.user_id == user_id,
                                LoginSession.client_id == cid,
                            ).first()
                        else:
                            session = db.query(LoginSession).filter(
                                LoginSession.user_id == user_id,
                                LoginSession.ip_address == ip,
                                LoginSession.user_agent == ua,
                            ).first()
                        if session:
                            session.ip_address = ip
                            session.user_agent = ua
                            session.last_active_at = now
                            session.is_active = True
                            session.geo_location = str(geo["name"])
                            if cid:
                                session.client_id = cid
                        else:
                            db.add(LoginSession(
                                user_id=user_id,
                                client_id=cid,
                                ip_address=ip,
                                user_agent=ua,
                                geo_location=str(geo["name"]),
                                last_active_at=now,
                                is_active=True,
                            ))
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                    finally:
                        db.close()
            except (AuthError, PyJWTError, ValueError, HTTPException):
                pass
        return await call_next(request)
