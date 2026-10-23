"""Integration tests for authentication API endpoints.

These tests use FastAPI TestClient with dependency_overrides to verify
API routing, request validation, response formatting, and error handling.
The identity context (``app.api.auth.identity``) is patched with fake use
cases so the HTTP contract is tested in isolation.
"""
from datetime import datetime
from types import SimpleNamespace
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


def _make_token_pair():
    """A concrete TokenPair so the adapter can serialise it with asdict()."""
    from app.modules.identity.application.sessions import TokenPair

    return TokenPair(
        access_token="test-access-token",
        refresh_token="test-refresh-token",
        token_type="bearer",
        expires_in=900,
        access_jti="access-jti",
        refresh_jti="refresh-jti",
        access_expires_at=datetime(2026, 1, 1),
        refresh_expires_at=datetime(2026, 1, 8),
    )


def _fake_context(account=None, outcome=None, is_new_user=False, pair=None):
    """A patched identity context with configured fake use cases."""
    from app.domain.auth import BizCode

    pair = pair or _make_token_pair()
    ctx = MagicMock()
    ctx.issue_tokens.execute.return_value = pair
    ctx.authenticate.execute.return_value = SimpleNamespace(
        account=account, outcome=outcome or BizCode.OK, is_new_user=is_new_user
    )
    ctx.refresh.execute.return_value = (account, pair)
    return ctx


def _make_client(app, auth_user=None):
    """Create a TestClient with dependency overrides set up.

    Clears any existing overrides on the app first to ensure test isolation.
    """
    from fastapi.testclient import TestClient

    from app.core.dependencies import get_db, require_admin, require_auth, require_member

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
        ctx = _fake_context()
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/send-code", json={"phone": "13800138000"})
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert data["biz_code"] == "AUTH_SMS_SENT"
            assert "验证码" in data["message"]
            ctx.send_code.execute.assert_called_once_with("13800138000", None)

    def test_send_code_invalid_phone(self):
        """Invalid phone format should return 400."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        ctx = _fake_context()
        ctx.send_code.execute.side_effect = HTTPException(status_code=400, detail="手机号格式不正确")
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/send-code", json={"phone": "12345"})
            assert response.status_code == 400


class TestRegisterAndLogin:
    """Test the register -> login -> me -> refresh -> logout flow."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.domain.auth import BizCode
        from app.web_app import app

        self.app = app
        self.biz = BizCode
        self.user = _make_user()
        self.tokens = _make_token_pair()

    def test_register_success(self):
        """First user registration should succeed."""
        client = _make_client(self.app)
        ctx = _fake_context(account=self.user, outcome=self.biz.AUTH_REGISTER_SUCCESS)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/register", json={"phone": "13800138001", "code": "888888"})
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert data["biz_code"] == "AUTH_REGISTER_SUCCESS"
            assert data["data"]["tokens"]["access_token"] == "test-access-token"
            assert data["data"]["user"]["phone"] == "13800138000"

    def test_register_duplicate(self):
        """Registering same phone twice should fail."""
        from app.domain.auth import AuthError, BizCode

        client = _make_client(self.app)
        ctx = _fake_context()
        ctx.authenticate.execute.side_effect = AuthError(BizCode.AUTH_USER_EXISTS)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/register", json={"phone": "13800138002", "code": "888888"})
            assert response.status_code == 409
            assert response.json()["biz_code"] == "AUTH_USER_EXISTS"

    def test_login_success(self):
        """Login should succeed."""
        client = _make_client(self.app)
        ctx = _fake_context(account=self.user, outcome=self.biz.AUTH_LOGIN_SUCCESS)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/login", json={"phone": "13800138003", "code": "888888"})
            assert response.status_code == 200
            assert response.json()["biz_code"] == "AUTH_LOGIN_SUCCESS"
            assert response.json()["data"]["user"]["phone"] == "13800138000"

    def test_login_unregistered(self):
        """Login with unregistered phone should fail."""
        from app.domain.auth import AuthError, BizCode

        client = _make_client(self.app)
        ctx = _fake_context()
        ctx.authenticate.execute.side_effect = AuthError(BizCode.AUTH_USER_NOT_FOUND)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/login", json={"phone": "13900000000", "code": "888888"})
            assert response.status_code == 400
            assert response.json()["biz_code"] == "AUTH_USER_NOT_FOUND"

    def test_sign_in_returns_domain_outcome(self):
        client = _make_client(self.app)
        ctx = _fake_context(account=self.user, outcome=self.biz.AUTH_ACCOUNT_CREATED, is_new_user=True)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/sign-in", json={"phone": "13800138000", "code": "888888"})
        assert response.status_code == 200
        assert response.json()["biz_code"] == "AUTH_ACCOUNT_CREATED"
        assert response.json()["data"]["is_new_user"] is True

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
        ctx = _fake_context(account=self.user)
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/refresh", json={"refresh_token": "valid-refresh-token"})
            assert response.status_code == 200
            assert "access_token" in response.json()["data"]["tokens"]

    def test_refresh_with_access_token_fails(self):
        """POST /refresh with access token should fail."""
        from fastapi import HTTPException

        client = _make_client(self.app)
        ctx = _fake_context()
        ctx.refresh.execute.side_effect = HTTPException(status_code=401, detail="无效 refresh token")
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/refresh", json={"refresh_token": "access-token-not-refresh"})
            assert response.status_code == 401

    def test_logout(self):
        """POST /logout should succeed."""
        user = _make_user(phone="13800138007")
        client = _make_client(self.app, auth_user=user)
        # Generate a valid JWT token for the middleware to decode
        import time

        import jwt

        from app.core.config import settings
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
        ctx = _fake_context()
        with patch("app.api.auth.identity", return_value=ctx):
            response = client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {valid_token}"})
            assert response.status_code == 200
            assert response.json()["code"] == 200
            ctx.logout.execute.assert_called_once()


class TestSliderCaptcha:
    """GET /auth/slider-captcha, POST /auth/slider-captcha/verify and the
    slider gate on POST /auth/send-code.

    Unlike the classes above, these run against the real SQLite database
    (conftest creates every table) because the challenge and its ticket have
    to survive between HTTP calls.
    """

    @pytest.fixture(autouse=True)
    def setup(self):
        from fastapi.testclient import TestClient

        from app.core.database import SessionLocal
        from app.core.dependencies import get_db
        from app.models.slider_challenge import SliderChallenge
        from app.models.sms_code import SmsCode
        from app.web_app import app

        db = SessionLocal()
        db.query(SliderChallenge).delete()
        db.query(SmsCode).filter(SmsCode.phone == "13800138000").delete()
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
        db.query(SliderChallenge).delete()
        db.query(SmsCode).filter(SmsCode.phone == "13800138000").delete()
        db.commit()
        db.close()

    def _issue(self):
        from app.models.slider_challenge import SliderChallenge

        data = self.client.get("/api/v1/auth/slider-captcha").json()["data"]
        row = self.db.query(SliderChallenge).filter(
            SliderChallenge.id == data["challenge_id"]).one()
        return data, row

    def test_get_slider_captcha_returns_puzzle(self):
        """GET /auth/slider-captcha returns an id, both PNG data URIs and piece_y."""
        response = self.client.get("/api/v1/auth/slider-captcha")
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["challenge_id"] > 0
        assert data["background"].startswith("data:image/png;base64,")
        assert data["piece"].startswith("data:image/png;base64,")
        assert 0 <= data["piece_y"] < 160

    def test_slider_verify_passes_and_returns_ticket(self):
        """The true offset (read from the DB) passes and yields a ticket."""
        data, row = self._issue()
        response = self.client.post("/api/v1/auth/slider-captcha/verify", json={
            "challenge_id": data["challenge_id"], "x": row.x})
        assert response.status_code == 200
        assert response.json()["data"]["ticket"]

    def test_slider_verify_wrong_offset_rejected(self):
        data, row = self._issue()
        response = self.client.post("/api/v1/auth/slider-captcha/verify", json={
            "challenge_id": data["challenge_id"], "x": row.x + row.tolerance + 1})
        assert response.status_code == 400
        assert "滑块验证未通过" in response.json()["detail"]

    def test_send_code_without_ticket_rejected(self):
        """POST /auth/send-code without a slider ticket fails with 400."""
        response = self.client.post("/api/v1/auth/send-code", json={"phone": "13800138000"})
        assert response.status_code == 400
        assert "请先完成滑块验证" in response.json()["detail"]

    def test_send_code_with_bogus_ticket_rejected(self):
        response = self.client.post("/api/v1/auth/send-code", json={
            "phone": "13800138000", "slider_ticket": "not-a-ticket"})
        assert response.status_code == 400
        assert "请先完成滑块验证" in response.json()["detail"]

    def test_send_code_with_verified_ticket_passes_gate(self):
        """A passed slider lets the request through to phone validation —
        proven by failing on an invalid phone instead of the slider error."""
        data, row = self._issue()
        verify = self.client.post("/api/v1/auth/slider-captcha/verify", json={
            "challenge_id": data["challenge_id"], "x": row.x})
        ticket = verify.json()["data"]["ticket"]
        response = self.client.post("/api/v1/auth/send-code", json={
            "phone": "123", "slider_ticket": ticket})
        assert response.status_code == 400
        assert "手机号格式" in response.json()["detail"]
