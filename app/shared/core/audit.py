"""Helpers for the request-level admin audit trail (see app/shared/middleware/admin_audit.py)."""
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import Request

Endpoint = TypeVar("Endpoint", bound=Callable[..., Any])


def self_audited(endpoint: Endpoint) -> Endpoint:
    """Mark an endpoint whose use case already writes its own admin_audit_logs row.

    The audit middleware skips such endpoints, so one action never leaves two rows.
    """
    endpoint.__self_audited__ = True  # type: ignore[attr-defined]
    return endpoint


def add_audit_detail(request: Request, **detail: Any) -> None:
    """Attach facts the URL cannot show (a review verdict, bulk criteria, the new publish state).

    They end up in the detail of the row the middleware writes; method, path and status always win
    over a handler key of the same name.
    """
    request.state.audit_detail = {**getattr(request.state, "audit_detail", {}), **detail}
