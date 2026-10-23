"""Unit tests for the member-approval SMS notification."""
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User


def _make_pending_user(db, phone="13900000001", uid=910001):
    # Explicit id: sqlite (unit tests) cannot auto-number BigInteger PKs, and
    # this branch deliberately does not carry the BigIntPK model change.
    user = User(
        id=uid,
        phone=phone,
        role="user",
        is_active=True,
        is_blacklisted=False,
        member_apply_status="pending",
        member_apply_at=datetime.now(UTC).replace(tzinfo=None),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture()
def client():
    """TestClient on the real SQLite db with an admin identity injected."""
    from fastapi.testclient import TestClient

    from app.core.database import SessionLocal
    from app.core.dependencies import get_db, require_admin
    from app.web_app import app

    db = SessionLocal()
    db.query(AdminAuditLog).delete()
    db.query(User).filter(User.phone == "13900000001").delete()
    db.commit()

    def _real_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    async def _admin():
        return User(id=1, phone="13800000000", role="admin", is_active=True, is_blacklisted=False)

    app.dependency_overrides.clear()
    app.dependency_overrides[get_db] = _real_db
    app.dependency_overrides[require_admin] = _admin
    yield TestClient(app)
    app.dependency_overrides.clear()
    db.query(AdminAuditLog).delete()
    db.query(User).filter(User.phone == "13900000001").delete()
    db.commit()
    db.close()


def _approve(client, user_id):
    return client.put(f"/api/v1/admin/member-applications/{user_id}/approve")


class TestApproveSendsSms:
    def test_approve_notifies_and_upgrades(self, client):
        from app.core.database import SessionLocal

        db = SessionLocal()
        user = _make_pending_user(db)

        with patch("app.modules.membership.infrastructure.repositories.sms_service.send_member_approved", return_value=True) as mock_send:
            response = _approve(client, user.id)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["status"] == "approved"
        assert data["current_role"] == "member"
        assert data["sms_notified"] is True
        assert "短信通知已发送" in response.json()["message"]
        mock_send.assert_called_once_with(user.phone)
        db.refresh(user)
        assert user.role == "member"

    def test_approve_survives_sms_failure(self, client):
        """A failed SMS must not roll back the committed approval."""
        from app.core.database import SessionLocal

        db = SessionLocal()
        user = _make_pending_user(db)

        with patch(
            "app.modules.membership.infrastructure.repositories.sms_service.send_member_approved",
            side_effect=RuntimeError("aliyun down"),
        ):
            response = _approve(client, user.id)

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["sms_notified"] is False
        db.refresh(user)
        assert user.role == "member"
        assert user.member_apply_status == "approved"

    def test_approve_reports_unconfigured_template(self, client):
        """Real mode without SMS_APPROVAL_TEMPLATE_ID: approval fine, notice skipped."""
        from app.core.database import SessionLocal

        db = SessionLocal()
        user = _make_pending_user(db)

        with patch.object(settings, "SMS_MOCK_ENABLED", False), patch.object(settings, "SMS_APPROVAL_TEMPLATE_ID", ""):
            response = _approve(client, user.id)

        assert response.status_code == 200
        assert response.json()["data"]["sms_notified"] is False

    def test_mock_mode_does_not_claim_delivery(self, client):
        from app.core.database import SessionLocal

        db = SessionLocal()
        user = _make_pending_user(db)

        with patch.object(settings, "SMS_MOCK_ENABLED", True):
            with patch("app.services.sms_service.SmsService.send") as mock_send:
                response = _approve(client, user.id)

        assert response.status_code == 200
        assert response.json()["data"]["sms_notified"] is False
        mock_send.assert_not_called()


class TestSmsService:
    def test_send_requires_template_id(self):
        from app.services.sms_service import SmsService

        with pytest.raises(RuntimeError, match="模板"):
            SmsService.send("13800138000", "")

    def test_verification_code_uses_code_template(self):
        from app.services.sms_service import SmsService

        with patch.object(SmsService, "send") as mock_send:
            SmsService.send_verification_code("13800138000", "123456")
        mock_send.assert_called_once_with("13800138000", settings.SMS_TEMPLATE_ID, '{"code":"123456"}')
