"""Middleware that records one admin_audit_logs row for every successful admin write (non-GET)."""
import logging
from typing import Any

from fastapi import Request
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import SessionLocal
from app.models.admin_audit_log import AdminAuditLog

logger = logging.getLogger(__name__)

READ_ONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ACTION_MAX_LENGTH = 50  # admin_audit_logs.action is String(50)


def audit_action(endpoint: Any) -> str:
    """'<api module>.<endpoint function>', e.g. software.toggle_publish."""
    module = endpoint.__module__.rsplit(".", 1)[-1]
    return f"{module}.{endpoint.__name__}"[:ACTION_MAX_LENGTH]


def _write(admin_id: int, action: str, detail: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        db.add(AdminAuditLog(admin_id=admin_id, target_user_id=None, action=action, detail=detail))
        db.commit()
    finally:
        db.close()


class AdminAuditMiddleware(BaseHTTPMiddleware):
    """Audit trail for admin writes.

    require_admin stamps request.state.audit_admin_id, so only requests that really passed the admin
    check are recorded, and a new admin endpoint is covered without anyone remembering to opt in.
    Only requests that succeeded count (an audit row means the change happened). Endpoints marked
    @self_audited keep their own semantic row and are skipped. Writing the row can never fail the
    request: a failure is logged and the response goes out unchanged.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method in READ_ONLY_METHODS or response.status_code >= 400:
            return response
        admin_id = getattr(request.state, "audit_admin_id", None)
        route = request.scope.get("route")
        if admin_id is None or route is None or getattr(route.endpoint, "__self_audited__", False):
            return response

        detail: dict[str, Any] = {
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
        }
        if request.query_params:
            detail["query"] = dict(request.query_params)
        for key, value in getattr(request.state, "audit_detail", {}).items():
            detail.setdefault(key, value)
        try:
            await run_in_threadpool(_write, admin_id, audit_action(route.endpoint), detail)
        except Exception:
            logger.warning("admin audit row not written for %s %s", request.method, request.url.path, exc_info=True)
        return response
