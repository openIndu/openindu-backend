"""Integration tests for member submissions (个人中心投稿).

Uses the real SQLite test database (created in conftest) so the full
pending -> published / rejected state flow is exercised end to end.
Storage (OSS/local) is mocked — no files are written anywhere.
"""
import io
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.shared.core.database import SessionLocal, engine
from app.shared.core.dependencies import get_current_user, require_admin, require_auth, require_member
from app.shared.models import Base
from app.shared.models.resource_tag import ResourceTag
from app.shared.models.submission_event import SubmissionEvent
from app.shared.models.user import User

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
    app.dependency_overrides[get_current_user] = _auth
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
    with patch("app.modules.catalog.infrastructure.storage.storage_service.upload_file", return_value=_sw_meta()), \
         patch("app.modules.catalog.infrastructure.storage.storage_service.delete_file"):
        return client.post(
            "/api/v1/software/upload",
            files={"file": ("pkg.zip", io.BytesIO(b"zipdata"), "application/zip")},
            data={"brand": "testbrand", "category": "testcat", "version": version, "description": "d"},
        )


class TestSoftwareSubmissions:
    def test_unpublished_package_requires_owner_or_admin(self, member, member2, admin):
        sw_id = _upload_software(_client_as(member)).json()["data"]["id"]
        other = _client_as(member2)
        assert all(item["id"] != sw_id for item in other.get("/api/v1/software").json()["data"]["items"])
        assert other.get(f"/api/v1/software/{sw_id}").status_code == 404
        assert other.get(f"/api/v1/software/{sw_id}/download-link").status_code == 404
        assert _client_as(member).get(f"/api/v1/software/{sw_id}").status_code == 200
        assert _client_as(admin).get(f"/api/v1/software/{sw_id}").status_code == 200

    def test_member_upload_creates_pending_submission(self, member):
        client = _client_as(member)
        resp = _upload_software(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == member.id
        assert data["is_published"] is False

    def test_admin_upload_enters_review_with_owner(self, admin):
        client = _client_as(admin)
        resp = _upload_software(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == admin.id
        # Admin uploads used to be ownerless (submitted_by NULL) and therefore
        # never showed up in the uploader's own 我的投稿 list.
        listed = client.get("/api/v1/software/my-submissions")
        assert listed.status_code == 200, listed.text
        assert any(item["id"] == data["id"] for item in listed.json()["data"]["items"])

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
        with patch("app.modules.catalog.infrastructure.storage.storage_service.delete_file") as del_file:
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

    def test_my_submissions_pagination(self, member):
        client = _client_as(member)
        ids = [_upload_software(client, version=f"1.{i}").json()["data"]["id"] for i in range(3)]
        data = client.get("/api/v1/software/my-submissions", params={"limit": 2}).json()["data"]
        assert [i["id"] for i in data["items"]] == [ids[2], ids[1]]  # newest first
        assert data["total"] >= 3
        assert data["truncated"] is True
        page2 = client.get("/api/v1/software/my-submissions", params={"limit": 2, "offset": 1}).json()["data"]
        assert [i["id"] for i in page2["items"]] == [ids[1], ids[0]]
        assert client.get("/api/v1/software/my-submissions", params={"limit": 501}).status_code == 422
        assert client.get("/api/v1/software/my-submissions", params={"offset": -1}).status_code == 422


_doc_seq = 0


def _doc_meta(filename="manual.pdf"):
    global _doc_seq
    _doc_seq += 1
    return {"filename": "stored-" + filename, "file_size": 55, "file_hash": "b" * 64,
            "oss_key": f"docs/test/{_doc_seq}/{filename}"}


def _upload_document(client):
    with patch("app.modules.catalog.infrastructure.storage.storage_service.upload_file", return_value=_doc_meta()), \
         patch("app.modules.catalog.infrastructure.storage.storage_service.delete_file"):
        return client.post(
            "/api/v1/documents/upload",
            files={"file": ("manual.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
            data={"brand": "testbrand", "category": "testcat", "description": "d"},
        )


class TestDocumentSubmissions:
    def test_unpublished_document_requires_owner_or_admin(self, member, member2, admin):
        doc_id = _upload_document(_client_as(member)).json()["data"]["id"]
        other = _client_as(member2)
        assert all(item["id"] != doc_id for item in other.get("/api/v1/documents").json()["data"]["items"])
        assert other.get(f"/api/v1/documents/{doc_id}").status_code == 404
        assert other.get(f"/api/v1/documents/{doc_id}/download-link").status_code == 404
        assert other.get(f"/api/v1/documents/{doc_id}/preview-link").status_code == 404
        assert _client_as(member).get(f"/api/v1/documents/{doc_id}").status_code == 200
        assert _client_as(admin).get(f"/api/v1/documents/{doc_id}").status_code == 200

    def test_member_upload_creates_pending_submission(self, member):
        client = _client_as(member)
        resp = _upload_document(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == member.id

    def test_admin_upload_enters_review_with_owner(self, admin):
        client = _client_as(admin)
        resp = _upload_document(client)
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["submission_status"] == "pending"
        assert data["submitted_by"] == admin.id

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
        with patch("app.modules.catalog.infrastructure.storage.storage_service.delete_file") as del_file:
            resp = client.delete(f"/api/v1/documents/my-submissions/{doc_id}")
        assert resp.status_code == 200, resp.text
        assert del_file.called
        items = client.get("/api/v1/documents/my-submissions").json()["data"]["items"]
        assert all(i["id"] != doc_id for i in items)

    def test_my_submissions_pagination(self, member):
        client = _client_as(member)
        ids = [_upload_document(client).json()["data"]["id"] for _ in range(3)]
        data = client.get("/api/v1/documents/my-submissions", params={"limit": 2}).json()["data"]
        assert [i["id"] for i in data["items"]] == [ids[2], ids[1]]  # newest first
        assert data["total"] >= 3
        assert data["truncated"] is True
        assert client.get("/api/v1/documents/my-submissions", params={"limit": 501}).status_code == 422


def test_submission_events_survive_software_and_document_withdrawal(member, admin):
    with SessionLocal() as db:
        last_event_id = db.query(SubmissionEvent.id).order_by(SubmissionEvent.id.desc()).first()
    last_event_id = last_event_id[0] if last_event_id else 0
    member_client = _client_as(member)
    sw_id = _upload_software(member_client).json()["data"]["id"]
    doc_id = _upload_document(member_client).json()["data"]["id"]

    with patch("app.modules.catalog.infrastructure.storage.storage_service.delete_file"):
        assert member_client.delete(f"/api/v1/software/my-submissions/{sw_id}").status_code == 200
        assert member_client.delete(f"/api/v1/documents/my-submissions/{doc_id}").status_code == 200

    with SessionLocal() as db:
        events = db.query(SubmissionEvent).filter(
            SubmissionEvent.actor_id == member.id,
            SubmissionEvent.id > last_event_id,
        ).order_by(SubmissionEvent.id).all()
        assert [(e.resource_type, e.action, e.resource_name) for e in events] == [
            ("software", "submitted", "pkg.zip"),
            ("document", "submitted", "manual.pdf"),
            ("software", "withdrawn", "pkg.zip"),
            ("document", "withdrawn", "manual.pdf"),
        ]

    # Use the real require_admin dependency for access control, not _client_as's override.
    from app.web_app import app

    app.dependency_overrides.clear()
    app.dependency_overrides[require_auth] = lambda: member
    assert TestClient(app).get("/api/v1/admin/submission-events").status_code == 403
    app.dependency_overrides[require_auth] = lambda: admin
    response = TestClient(app).get("/api/v1/admin/submission-events", params={
        "resource_type": "document", "resource_id": doc_id,
    })
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == 2
    assert [item["action"] for item in data["items"]] == ["withdrawn", "submitted"]
    assert all(item["actor"] != member.phone for item in data["items"])


def test_admin_document_upload_has_submission_event(admin):
    with SessionLocal() as db:
        last_event_id = db.query(SubmissionEvent.id).order_by(SubmissionEvent.id.desc()).first()
    last_event_id = last_event_id[0] if last_event_id else 0
    doc_id = _upload_document(_client_as(admin)).json()["data"]["id"]
    with SessionLocal() as db:
        [event] = db.query(SubmissionEvent).filter(
            SubmissionEvent.id > last_event_id,
            SubmissionEvent.resource_type == "document", SubmissionEvent.resource_id == doc_id,
        ).all()
        assert (event.actor_id, event.action) == (admin.id, "submitted")


def test_admin_software_version_upload_has_submission_event(admin):
    client = _client_as(admin)
    sw_id = _upload_software(client).json()["data"]["id"]
    with patch("app.modules.catalog.infrastructure.storage.storage_service.upload_file", return_value=_sw_meta("update.zip")):
        response = client.post(
            f"/api/v1/software/{sw_id}/versions",
            files={"file": ("update.zip", io.BytesIO(b"update"), "application/zip")},
            data={"version": "2.0"},
        )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        events = db.query(SubmissionEvent).filter(
            SubmissionEvent.resource_type == "software",
            SubmissionEvent.resource_id == sw_id,
            SubmissionEvent.actor_id == admin.id,
        ).order_by(SubmissionEvent.id).all()
        assert [(event.action, event.version) for event in events] == [
            ("submitted", "1.0"), ("version_added", "2.0"),
        ]
