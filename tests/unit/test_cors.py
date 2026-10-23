"""CORS wiring: the web app answers credentialed CORS requests for the configured allow-list only."""
from fastapi.testclient import TestClient

from app.core.config import settings
from app.web_app import app

client = TestClient(app)

UNLISTED_ORIGIN = "https://unlisted.example"


def test_listed_origins_pass_preflight_with_credentials():
    """Every origin in the allow-list gets a credentialed preflight answer."""
    assert settings.cors_origins  # an empty list would make the loop below vacuous
    for origin in settings.cors_origins:
        response = client.options(
            "/api/v1/health",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert response.headers["access-control-allow-credentials"] == "true"


def test_unlisted_origin_is_rejected_at_preflight():
    """A preflight from an origin outside the allow-list is refused and not echoed back."""
    response = client.options(
        "/api/v1/health",
        headers={"Origin": UNLISTED_ORIGIN, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_simple_request_echoes_only_listed_origins():
    """Normal responses carry the allow-origin header for listed origins and omit it otherwise."""
    listed = settings.cors_origins[0]
    allowed = client.get("/api/v1/health", headers={"Origin": listed})
    assert allowed.headers["access-control-allow-origin"] == listed

    # The server still answers; it is the browser that withholds the response without this header.
    denied = client.get("/api/v1/health", headers={"Origin": UNLISTED_ORIGIN})
    assert denied.status_code == 200
    assert "access-control-allow-origin" not in denied.headers
