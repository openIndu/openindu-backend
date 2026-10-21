"""Integration tests for authentication API endpoints.

These tests use FastAPI TestClient with dependency_overrides to verify
API routing, request validation, response formatting, and error handling.
"""
from unittest.mock import MagicMock, patch

import pytest


def _make_user(id=1, phone="13800138000", role="user"):
    """Create a simple mock user object."""
    user = MagicMock()
    user.id = id
    user.phone = phone
    user.role = role
    user.nickname = None
    user.is_active = True
    user.is_blacklisted = False

    def to_dict():
        return {
            "id": id,
            "phone": phone,
            "nickname": user.nickname,
            "role": role,
            "is_active": True,
            "is_blacklisted": False,
            "blacklisted_at": None,
            "blacklisted_by": None,
            "tokens_invalidated_at": None,
            "created_at": "2024-01-01T00:00:00",
            "last_login": "2024-01-01T00:00:00",
        }

    user.to_dict.side_effect = to_dict
    return user


def _make_tokens():
    return {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_type": "bearer",
        "expires_in": 900,
    }


def _make_client(app, auth_user=None):
    """Create a TestClient with dependency overrides set up.

    Clears any existing overrides on the app first to ensure test isolation.
    """
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_db, require_auth, require_admin, require_member

    # Clear previous overrides for test isolation
    app.dependency_overrides.clear()

    mock_db = MagicMock()
    mock_db.query.return_value = MagicMock()
    mock_db.add = MagicMock()
    mock_db.commit = MagicMock()
    mock_db.refresh = MagicMock()

    def _get_db():
        yield mock_db
    app.dependency_overrides[get_db] = _get_db

    if auth_user is not None:
        async def _require_auth():
            return auth_user
        app.dependency_overrides[require_auth] = _require_auth
        app.dependency_overrides[require_admin] = _require_auth
        app.dependency_overrides[require_member] = _require_auth

    client = TestClient(app)
    client._mock_db = mock_db
    return client


class TestSendCode:
    """Test POST /api/v1/auth/send-code."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_send_code_success(self):
        """Sending code to a valid phone should return 200."""
        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.send_code") as mock_send:
            response = client.post("/api/v1/auth/send-code", json={"phone": "13800138000"})
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert "验证码" in data["message"]
            mock_send.assert_called_once()

    def test_send_code_invalid_phone(self):
        """Invalid phone format should return 400."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.send_code", side_effect=HTTPException(status_code=400, detail="手机号格式不正确")):
            response = client.post("/api/v1/auth/send-code", json={"phone": "12345"})
            assert response.status_code == 400


class TestRegisterAndLogin:
    """Test the register -> login -> me -> refresh -> logout flow."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app
        self.user = _make_user()
        self.tokens = _make_tokens()

    def test_register_success(self):
        """First user registration should succeed."""
        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.register", return_value={"user": self.user.to_dict(), "tokens": self.tokens}):
            response = client.post("/api/v1/auth/register", json={"phone": "13800138001", "code": "888888"})
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert data["data"]["tokens"]["access_token"] == "test-access-token"
            assert data["data"]["user"]["phone"] == "13800138000"

    def test_register_duplicate(self):
        """Registering same phone twice should fail."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.register", side_effect=HTTPException(status_code=409, detail="用户已存在")):
            response = client.post("/api/v1/auth/register", json={"phone": "13800138002", "code": "888888"})
            assert response.status_code == 409

    def test_login_success(self):
        """Login should succeed."""
        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.login", return_value={"user": self.user.to_dict(), "tokens": self.tokens}):
            response = client.post("/api/v1/auth/login", json={"phone": "13800138003", "code": "888888"})
            assert response.status_code == 200
            assert response.json()["data"]["user"]["phone"] == "13800138000"

    def test_login_unregistered(self):
        """Login with unregistered phone should fail."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.login", side_effect=HTTPException(status_code=404, detail="用户不存在，请先注册")):
            response = client.post("/api/v1/auth/login", json={"phone": "13900000000", "code": "888888"})
            assert response.status_code == 404

    def test_me_authenticated(self):
        """GET /me with valid token should return user info."""
        user = _make_user(phone="13800138004")
        client = _make_client(self.app, auth_user=user)
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["phone"] == "13800138004"

    def test_me_unauthenticated(self):
        """GET /me without token should return 401."""
        client = _make_client(self.app)
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401

    def test_update_me(self):
        """PATCH /me should update current user's nickname."""
        user = _make_user(phone="13800138004")
        client = _make_client(self.app, auth_user=user)
        response = client.patch("/api/v1/auth/me", json={"nickname": "Tom"})
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["nickname"] == "Tom"
        assert user.nickname == "Tom"

    def test_update_me_unauthenticated(self):
        """PATCH /me without token should return 401."""
        client = _make_client(self.app)
        response = client.patch("/api/v1/auth/me", json={"nickname": "Tom"})
        assert response.status_code == 401

    def test_refresh_token(self):
        """POST /refresh with valid refresh token should return new tokens."""
        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.refresh_token", return_value={"user": self.user.to_dict(), "tokens": self.tokens}):
            response = client.post("/api/v1/auth/refresh", json={"refresh_token": "valid-refresh-token"})
            assert response.status_code == 200
            assert "access_token" in response.json()["data"]["tokens"]

    def test_refresh_with_access_token_fails(self):
        """POST /refresh with access token should fail."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        with patch("app.api.auth.auth_service.refresh_token", side_effect=HTTPException(status_code=401, detail="无效 refresh token")):
            response = client.post("/api/v1/auth/refresh", json={"refresh_token": "access-token-not-refresh"})
            assert response.status_code == 401

    def test_logout(self):
        """POST /logout should succeed."""
        user = _make_user(phone="13800138007")
        client = _make_client(self.app, auth_user=user)
        # Generate a valid JWT token for the middleware to decode
        from jose import jwt
        from app.core.config import settings
        import time
        payload = {
            "sub": "1",
            "phone": "13800138007",
            "role": "user",
            "type": "access",
            "jti": "test-jti",
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600
        }
        valid_token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

        # conftest.py creates all tables on the shared file-based SQLite database,
        # so the middleware's SessionLocal can access them directly.
        with patch("app.api.auth.auth_service.logout"):
            response = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {valid_token}"})
            assert response.status_code == 200
            assert response.json()["code"] == 200


class TestCaptcha:
    """GET /auth/captcha + the captcha gate on POST /auth/send-code.

    Unlike the classes above, these run against the real SQLite database
    (conftest creates every table) because the captcha challenge has to
    survive between the two HTTP calls.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.core.database import SessionLocal
        from app.models.captcha_challenge import CaptchaChallenge

        from app.web_app import app
        from app.core.dependencies import get_db
        from fastapi.testclient import TestClient

        db = SessionLocal()
        db.query(CaptchaChallenge).delete()
        db.commit()

        def _real_db():
            session = SessionLocal()
            try:
                yield session
            finally:
                session.close()

        app.dependency_overrides.clear()
        app.dependency_overrides[get_db] = _real_db
        self.client = TestClient(app)
        self.db = db
        yield
        app.dependency_overrides.clear()
        db.query(CaptchaChallenge).delete()
        db.commit()
        db.close()

    def test_get_captcha_returns_image(self):
        """GET /auth/captcha returns a challenge id and a PNG data URI."""
        response = self.client.get("/api/v1/auth/captcha")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["captcha_id"] > 0
        assert data["image"].startswith("data:image/png;base64,")

    def test_send_code_without_captcha_rejected(self):
        """POST /auth/send-code without captcha fields fails with 400."""
        response = self.client.post("/api/v1/auth/send-code", json={"phone": "13800138000"})
        assert response.status_code == 400
        assert "图形验证码" in response.json()["detail"]

    def test_send_code_with_wrong_captcha_rejected(self):
        """A wrong captcha answer fails before any SMS-side validation."""
        captcha = self.client.get("/api/v1/auth/captcha").json()["data"]
        response = self.client.post("/api/v1/auth/send-code", json={
            "phone": "13800138000",
            "captcha_id": captcha["captcha_id"],
            "captcha_text": "ZZZZ",
        })
        assert response.status_code == 400
        assert "图形验证码" in response.json()["detail"]

    def test_send_code_with_valid_captcha_passes_gate(self):
        """A correct captcha lets the request through to phone validation —
        proven by failing on an invalid phone instead of the captcha error."""
        from app.models.captcha_challenge import CaptchaChallenge

        captcha = self.client.get("/api/v1/auth/captcha").json()["data"]
        row = self.db.query(CaptchaChallenge).filter(
            CaptchaChallenge.id == captcha["captcha_id"]).one()
        response = self.client.post("/api/v1/auth/send-code", json={
            "phone": "123",
            "captcha_id": captcha["captcha_id"],
            "captcha_text": row.answer,
        })
        assert response.status_code == 400
        assert "手机号格式" in response.json()["detail"]
