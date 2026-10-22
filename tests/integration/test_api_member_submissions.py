"""Integration tests for member submissions (个人中心投稿).

Uses the real SQLite test database (created in conftest) so the full
pending -> published / rejected state flow is exercised end to end.
Storage (OSS/local) is mocked — no files are written anywhere.
"""
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import SessionLocal, engine
from app.core.dependencies import require_admin, require_auth, require_member
from app.models import Base
from app.models.resource_tag import ResourceTag
from app.models.user import User

# conftest's create_all predates the resource_tags table (every other test
# mocks the DB away, so nothing else creates it). create_all is idempotent,
# so this only fills the gap.
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _tags():
    """Ensure the brand/category tags the upload endpoints validate against."""
    db = SessionLocal()
    # ResourceTag's PK is a plain BigInteger, which SQLite does not treat as a
    # rowid alias — supply ids explicitly in the test database.
    next_id = db.query(ResourceTag.id).order_by(ResourceTag.id.desc()).first()
    next_id = (next_id[0] if next_id else 90000) + 1
    for ttype, value in [
        ("sw_brand", "testbrand"), ("sw_category", "testcat"),
        ("doc_brand", "testbrand"), ("doc_category", "testcat"),
    ]:
        exists = db.query(ResourceTag).filter(ResourceTag.type == ttype, ResourceTag.value == value).first()
        if not exists:
            db.add(ResourceTag(id=next_id, type=ttype, value=value, label_zh=value, is_active=True))
            next_id += 1
    db.commit()
    db.close()
    yield


@pytest.fixture(autouse=True)
def _clear_overrides():
    # Earlier modules (test_api_auth) leave their dependency_overrides on the
    # shared app — a leftover get_db mock would poison the real-DB tests here.
    from app.web_app import app
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _get_or_create_user(phone: str, role: str) -> User:
    db = SessionLocal()
    user = db.query(User).filter(User.phone == phone).first()
    if not user:
        user = User(phone=phone, role=role)
        db.add(user)
        db.commit()
        db.refresh(user)
    db.close()
    return user


def _client_as(user: User) -> TestClient:
    """Bind auth overrides on the shared app and return a client.

    The overrides are app-global: creating a client for another user silently
    re-authenticates every existing client. Rebind with this helper before
    acting as a previous user again.
    """
    from app.web_app import app

    async def _auth():
        return user

    app.dependency_overrides[require_auth] = _auth
    app.dependency_overrides[require_member] = _auth
    app.dependency_overrides[require_admin] = _auth
    return TestClient(app)


@pytest.fixture()
def member():
    return _get_or_create_user("13900001001", "member")


@pytest.fixture()
def member2():
    return _get_or_create_user("13900001002", "member")


@pytest.fixture()
def admin():
    return _get_or_create_user("13900001000", "admin")


_sw_seq = 0


def _sw_meta(filename="pkg.zip"):
    global _sw_seq
    _sw_seq += 1
    return {"filename": "stored-" + filename, "file_size": 123, "file_hash": "a" * 64,
            "oss_key": f"software/test/{_sw_seq}/{filename}"}


def _upload_software(client, version="1.0"):
    with patch("app.api.software.storage_service.upload_file", return_value=_sw_meta()), \
         patch("app.api.software.storage_service.delete_file"):
        return client.post(
            "/api/v1/software/upload",
            files={"file": ("pkg.zip", io.BytesIO(b"zipdata"), "application/zip")},
            data={"brand": "testbrand", "category": "testcat", "version": version, "description": "d"},
        )


class TestSoftwareSubmissions:
    def test_member_upload_creates_pending_submission(self, member):
        client = _client_as(member)
        resp = _upload_software(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == member.id
        assert data["is_published"] is False

    def test_admin_upload_not_pending(self, admin):
        client = _client_as(admin)
        resp = _upload_software(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "published"
        assert data["submitted_by"] is None

    def test_pending_submission_invisible_in_published_list(self, member):
        client = _client_as(member)
        sw_id = _upload_software(client).json()["data"]["id"]
        resp = client.get("/api/v1/software", params={"published_only": "true", "expand_versions": "true"})
        assert resp.status_code == 200
        assert all(item["id"] != sw_id for item in resp.json()["data"]["items"])

    def test_list_filter_by_submission_status(self, member, admin):
        member_client = _client_as(member)
        pending_id = _upload_software(member_client).json()["data"]["id"]
        admin_client = _client_as(admin)
        resp = admin_client.get("/api/v1/software", params={"submission_status": "pending", "expand_versions": "true"})
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert items and all(i["id"] == pending_id for i in items if i["id"] == pending_id)
        assert any(i["id"] == pending_id for i in items)
        # Expanded version rows must carry the review state for the admin UI.
        assert all(i["submission_status"] == "pending" for i in items)
        resp = admin_client.get("/api/v1/software", params={"submission_status": "pending", "expand_versions": "false"})
        assert all(i["id"] != 0 and i["submission_status"] == "pending" for i in resp.json()["data"]["items"])
        resp = admin_client.get("/api/v1/software", params={"submission_status": "rejected"})
        assert all(i["submission_status"] == "rejected" for i in resp.json()["data"]["items"])

    def test_review_publish_makes_visible(self, member, admin):
        member_client = _client_as(member)
        sw_id = _upload_software(member_client).json()["data"]["id"]
        admin_client = _client_as(admin)
        resp = admin_client.patch(f"/api/v1/software/{sw_id}/review", json={"action": "publish"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "published"
        assert data["is_published"] is True
        assert all(v["is_published"] for v in data["versions"])
        resp = member_client.get("/api/v1/software", params={"published_only": "true", "expand_versions": "true"})
        assert any(item["id"] == sw_id for item in resp.json()["data"]["items"])

    def test_review_reject_requires_reason(self, member, admin):
        client = _client_as(member)
        sw_id = _upload_software(client).json()["data"]["id"]
        admin_client = _client_as(admin)
        resp = admin_client.patch(f"/api/v1/software/{sw_id}/review", json={"action": "reject"})
        assert resp.status_code == 400

    def test_review_reject_sets_reason_visible_to_member(self, member, admin):
        member_client = _client_as(member)
        sw_id = _upload_software(member_client).json()["data"]["id"]
        admin_client = _client_as(admin)
        resp = admin_client.patch(f"/api/v1/software/{sw_id}/review", json={"action": "reject", "reason": "描述太简单"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["submission_status"] == "rejected"
        items = _client_as(member).get("/api/v1/software/my-submissions").json()["data"]["items"]
        mine = next(i for i in items if i["id"] == sw_id)
        assert mine["submission_status"] == "rejected"
        assert mine["rejected_reason"] == "描述太简单"

    def test_reject_published_blocked(self, member, admin):
        member_client = _client_as(member)
        sw_id = _upload_software(member_client).json()["data"]["id"]
        admin_client = _client_as(admin)
        assert admin_client.patch(f"/api/v1/software/{sw_id}/review", json={"action": "publish"}).status_code == 200
        resp = admin_client.patch(f"/api/v1/software/{sw_id}/review", json={"action": "reject", "reason": "x"})
        assert resp.status_code == 400

    def test_review_invalid_action_and_unknown_id(self, admin):
        client = _client_as(admin)
        resp = client.patch("/api/v1/software/999999/review", json={"action": "publish"})
        assert resp.status_code == 404
        resp = client.patch("/api/v1/software/1/review", json={"action": "nonsense"})
        assert resp.status_code == 400

    def test_my_submissions_lists_own_only(self, member, member2):
        client = _client_as(member)
        sw_id = _upload_software(client).json()["data"]["id"]
        other = _client_as(member2)
        items = other.get("/api/v1/software/my-submissions").json()["data"]["items"]
        assert all(i["id"] != sw_id for i in items)
        mine = _client_as(member).get("/api/v1/software/my-submissions").json()["data"]["items"]
        assert any(i["id"] == sw_id for i in mine)

    def test_member_cannot_delete_others_submission(self, member, member2):
        client = _client_as(member)
        sw_id = _upload_software(client).json()["data"]["id"]
        resp = _client_as(member2).delete(f"/api/v1/software/my-submissions/{sw_id}")
        assert resp.status_code == 404

    def test_member_deletes_own_submission(self, member):
        client = _client_as(member)
        sw_id = _upload_software(client).json()["data"]["id"]
        with patch("app.api.software.storage_service.delete_file") as del_file:
            resp = client.delete(f"/api/v1/software/my-submissions/{sw_id}")
        assert resp.status_code == 200, resp.text
        items = client.get("/api/v1/software/my-submissions").json()["data"]["items"]
        assert all(i["id"] != sw_id for i in items)
        assert del_file.called

    def test_member_cannot_init_for_existing_software(self, member, admin):
        admin_client = _client_as(admin)
        sw_id = _upload_software(admin_client).json()["data"]["id"]
        resp = _client_as(member).post("/api/v1/software/upload/init", json={
            "filename": "pkg.zip", "brand": "testbrand", "category": "testcat",
            "version": "9.9", "size": 100, "software_id": sw_id,
        })
        assert resp.status_code == 403


_doc_seq = 0


def _doc_meta(filename="manual.pdf"):
    global _doc_seq
    _doc_seq += 1
    return {"filename": "stored-" + filename, "file_size": 55, "file_hash": "b" * 64,
            "oss_key": f"docs/test/{_doc_seq}/{filename}"}


def _upload_document(client):
    with patch("app.api.documents.storage_service.upload_file", return_value=_doc_meta()), \
         patch("app.api.documents.storage_service.delete_file"):
        return client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"brand": "testbrand", "category": "testcat", "description": "d"},
        )


class TestDocumentSubmissions:
    def test_member_upload_creates_pending_submission(self, member):
        client = _client_as(member)
        resp = _upload_document(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == member.id

    def test_admin_upload_not_pending(self, admin):
        client = _client_as(admin)
        resp = _upload_document(client)
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["submission_status"] == "published"

    def test_review_publish_and_reject_flow(self, member, admin):
        member_client = _client_as(member)
        doc_id = _upload_document(member_client).json()["data"]["id"]
        admin_client = _client_as(admin)
        listed = admin_client.get("/api/v1/documents", params={"submission_status": "pending"}).json()["data"]["items"]
        assert any(i["id"] == doc_id for i in listed)
        resp = admin_client.patch(f"/api/v1/documents/{doc_id}/review", json={"action": "reject", "reason": "缺系列信息"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["submission_status"] == "rejected"
        resp = admin_client.patch(f"/api/v1/documents/{doc_id}/review", json={"action": "publish"})
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "published"
        assert data["is_published"] is True
        assert data["rejected_reason"] is None

    def test_document_review_requires_reason(self, member, admin):
        client = _client_as(member)
        doc_id = _upload_document(client).json()["data"]["id"]
        resp = _client_as(admin).patch(f"/api/v1/documents/{doc_id}/review", json={"action": "reject"})
        assert resp.status_code == 400

    def test_my_submissions_and_delete(self, member, member2):
        client = _client_as(member)
        doc_id = _upload_document(client).json()["data"]["id"]
        resp = _client_as(member2).delete(f"/api/v1/documents/my-submissions/{doc_id}")
        assert resp.status_code == 404
        client = _client_as(member)
        with patch("app.api.documents.storage_service.delete_file") as del_file:
            resp = client.delete(f"/api/v1/documents/my-submissions/{doc_id}")
        assert resp.status_code == 200, resp.text
        assert del_file.called
        items = client.get("/api/v1/documents/my-submissions").json()["data"]["items"]
        assert all(i["id"] != doc_id for i in items)
