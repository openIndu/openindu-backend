"""Invalid or expired Bearer tokens must degrade to anonymous at the edge
instead of surfacing as 500 (regression: the identity codec raises the
domain AuthError, which the edge except-clauses must treat like JWTError)."""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.visits import _bearer_user_id
from app.middleware.online_stats import OnlineStatsMiddleware
from app.web_app import app as web_app


def test_online_stats_middleware_swallows_invalid_bearer():
    """An undecodable token in the tracking middleware means anonymous, not 500."""
    probe = FastAPI()
    probe.add_middleware(OnlineStatsMiddleware)

    @probe.get("/ping")
    def ping():
        return {"ok": True}

    resp = TestClient(probe).get("/ping", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def _request_with_bearer(value: str) -> Request:
    return Request(scope={"type": "http", "headers": [(b"authorization", value.encode())]})


def test_visits_bearer_user_id_treats_invalid_token_as_anonymous():
    assert _bearer_user_id(_request_with_bearer("Bearer not-a-jwt")) is None


def test_protected_route_with_invalid_bearer_is_401_not_500():
    """In-request auth treats an undecodable token as missing credentials."""
    resp = TestClient(web_app).get(
        "/api/v1/chat/sessions",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "请先登录"

