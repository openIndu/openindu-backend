"""Integration tests for document API endpoints.

These tests use FastAPI TestClient with dependency_overrides to verify
API routing, request validation, response formatting, and error handling.
"""
import io
from unittest.mock import MagicMock, patch

import pytest


def _query_chain(items=None, first=None, count=0):
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.offset.return_value = q
    q.limit.return_value = q
    q.all.return_value = items or []
    q.first.return_value = first
    q.count.return_value = count
    return q


def _tag(value):
    tag = MagicMock()
    tag.value = value
    return tag


def _make_user(id=1, phone="13800138000", role="user"):
    user = MagicMock()
    user.id = id
    user.phone = phone
    user.role = role
    user.is_active = True
    user.is_blacklisted = False
    return user


def _make_client(app, auth_user=None):
    """Create a TestClient with dependency overrides set up.

    Clears any existing overrides on the app first to ensure test isolation.
    """
    from fastapi.testclient import TestClient

    from app.shared.core.dependencies import get_db, require_admin, require_auth, require_member

    # Clear previous overrides for test isolation
    app.dependency_overrides.clear()

    mock_db = MagicMock()
    mock_db.query.return_value = _query_chain()
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


class TestDocumentList:
    """Test GET /api/v1/documents."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_list_empty(self):
        """Listing documents with no data should return empty list."""
        user = _make_user()
        client = _make_client(self.app, auth_user=user)

        response = client.get("/api/v1/documents")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data["data"]
        assert "total" in data["data"]

    def test_list_unauthenticated(self):
        """Listing without auth should stay public."""
        client = _make_client(self.app)
        response = client.get("/api/v1/documents")
        assert response.status_code == 200
        assert "items" in response.json()["data"]

    def test_list_with_pagination(self):
        """Pagination parameters should be accepted."""
        user = _make_user()
        client = _make_client(self.app, auth_user=user)

        response = client.get("/api/v1/documents?page=1&size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["data"]["page"] == 1
        assert data["data"]["size"] == 10


class TestDocumentUpload:
    """Test POST /api/v1/documents/upload."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_upload_success(self):
        """Uploading a valid PDF should succeed."""
        from app.shared.services.storage_service import storage_service

        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)
        client._mock_db.query.side_effect = [
            _query_chain(items=[_tag("siemens")]),
            _query_chain(items=[_tag("plc-manual")]),
        ]

        mock_meta = {
            "oss_key": "documents/siemens/abc123.pdf",
            "filename": "abc123.pdf",
            "file_hash": "abc123hash",
            "file_size": 100,
        }

        with patch.object(storage_service, "upload_file", return_value=mock_meta):
            pdf_content = b"%PDF-1.4 fake pdf content"
            response = client.post(
                "/api/v1/documents/upload",
                data={
                    "brand": "siemens",
                    "category": "plc-manual",
                    "description": "Test document",
                },
                files={"file": ("test.pdf", io.BytesIO(pdf_content), "application/pdf")},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert data["data"]["original_name"] == "test.pdf"
            assert data["data"]["brand"] == "siemens"
            assert data["data"]["category"] == "plc-manual"
            assert "sync_status" not in data["data"]
            assert "sync_time" not in data["data"]

    def test_upload_not_pdf(self):
        """Uploading a non-PDF file should fail."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)
        client._mock_db.query.side_effect = [
            _query_chain(items=[_tag("siemens")]),
            _query_chain(items=[_tag("plc-manual")]),
        ]

        response = client.post(
            "/api/v1/documents/upload",
            data={"brand": "siemens", "category": "plc-manual"},
            files={"file": ("test.txt", io.BytesIO(b"not a pdf"), "text/plain")},
        )
        assert response.status_code == 415

    def test_upload_invalid_brand(self):
        """Uploading with an invalid brand should fail."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)

        response = client.post(
            "/api/v1/documents/upload",
            data={"brand": "invalid_brand", "category": "plc-manual"},
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert response.status_code == 400

    def test_upload_invalid_category(self):
        """Uploading with an invalid category should fail."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)

        response = client.post(
            "/api/v1/documents/upload",
            data={"brand": "siemens", "category": "invalid_category"},
            files={"file": ("test.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )
        assert response.status_code == 400


class TestBrandsAndCategories:
    """Test GET /api/v1/documents/brands/list and categories/list."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_brands_list(self):
        """Brands list should return the predefined brands."""
        user = _make_user()
        client = _make_client(self.app, auth_user=user)
        client._mock_db.query.return_value = _query_chain(items=[_tag("siemens"), _tag("mitsubishi")])

        response = client.get("/api/v1/documents/brands/list")
        assert response.status_code == 200
        brands = response.json()["data"]
        assert "siemens" in brands
        assert "mitsubishi" in brands

    def test_categories_list(self):
        """Categories list should return the predefined categories."""
        user = _make_user()
        client = _make_client(self.app, auth_user=user)
        client._mock_db.query.return_value = _query_chain(items=[_tag("plc-manual"), _tag("best-practice")])

        response = client.get("/api/v1/documents/categories/list")
        assert response.status_code == 200
        categories = response.json()["data"]
        assert "plc-manual" in categories
        assert "best-practice" in categories


def test_document_sync_routes_are_not_registered():
    """Website exposes document storage operations, not indexing controls."""
    from app.web_app import app

    paths = {route.path for route in app.routes}
    assert "/api/v1/sync/trigger" not in paths
    assert "/api/v1/sync/status" not in paths
    assert "/api/v1/sync/logs" not in paths
    assert "/api/v1/documents/{doc_id}/sync" not in paths


class TestDocumentPublish:
    """Test document publish endpoints."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_bulk_publish_selected_documents(self):
        """Bulk publish should publish selected unpublished documents."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)
        doc1 = MagicMock()
        doc1.is_published = False
        doc2 = MagicMock()
        doc2.is_published = False
        client._mock_db.query.return_value = _query_chain(items=[doc1, doc2])

        response = client.patch("/api/v1/documents/publish/bulk", json={"ids": [1, 2]})

        assert response.status_code == 200
        assert response.json()["data"]["count"] == 2
        assert response.json()["data"]["publish"] is True
        assert doc1.is_published is True
        assert doc2.is_published is True
        client._mock_db.commit.assert_called_once()

    def test_bulk_unpublish_selected_documents(self):
        """Bulk unpublish (publish=false) should unpublish selected documents."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)
        doc1 = MagicMock()
        doc1.is_published = True
        client._mock_db.query.return_value = _query_chain(items=[doc1])

        response = client.patch("/api/v1/documents/publish/bulk", json={"ids": [1], "publish": False})

        assert response.status_code == 200
        assert response.json()["data"]["count"] == 1
        assert response.json()["data"]["publish"] is False
        assert doc1.is_published is False
        client._mock_db.commit.assert_called_once()

    def test_bulk_publish_requires_selected_ids_when_ids_empty(self):
        """Empty ids should fail to avoid accidental no-op confusion."""
        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)

        response = client.patch("/api/v1/documents/publish/bulk", json={"ids": []})

        assert response.status_code == 400


class TestDocumentDetail:
    """Test GET /api/v1/documents/{id}."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_get_nonexistent_document(self):
        """Getting a nonexistent document should return 404."""
        user = _make_user()
        client = _make_client(self.app, auth_user=user)

        # The mock DB query chain must return None for the document
        client._mock_db.query.return_value.filter.return_value.first.return_value = None

        response = client.get("/api/v1/documents/99999")
        assert response.status_code == 404


def _make_doc(id=1):
    doc = MagicMock()
    doc.id = id
    doc.oss_key = "documents/siemens/x.pdf"
    doc.original_name = "x.pdf"
    doc.download_count = 7
    return doc


class TestDocumentPreviewLimit:
    """Preview uses a SEPARATE daily quota (PREVIEW_DAILY_LIMIT) — it must not
    consume the download budget nor bump download_count."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_preview_separate_quota_and_no_download_count_bump(self):
        """Under the preview quota: 200, logs document_preview, leaves download_count untouched."""
        from app.shared.services.storage_service import storage_service

        member = _make_user(role="member")
        client = _make_client(self.app, auth_user=member)
        doc = _make_doc()
        # Document.first() -> doc ; DownloadLog.count() -> 0 (under preview quota)
        client._mock_db.query.return_value = _query_chain(first=doc, count=0)

        with patch.object(storage_service, "get_preview_url",
                          return_value={"url": "http://x/preview", "expires_in": 300}):
            response = client.get("/api/v1/documents/1/preview-link")

        assert response.status_code == 200
        assert response.json()["data"]["preview_url"] == "http://x/preview"
        # preview is not a download — counter must stay put
        assert doc.download_count == 7
        # the log must use the distinct preview resource_type
        added = client._mock_db.add.call_args[0][0]
        assert added.resource_type == "document_preview"

    def test_preview_429_when_preview_quota_exhausted(self):
        """At/over the preview quota: 429, and nothing is logged."""
        from app.shared.core.config import settings

        member = _make_user(role="member")
        client = _make_client(self.app, auth_user=member)
        doc = _make_doc()
        client._mock_db.query.return_value = _query_chain(first=doc, count=settings.PREVIEW_DAILY_LIMIT)

        response = client.get("/api/v1/documents/1/preview-link")

        assert response.status_code == 429
        client._mock_db.add.assert_not_called()

    def test_preview_admin_bypasses_quota(self):
        """Admins bypass the preview quota even when far over it."""
        from app.shared.services.storage_service import storage_service

        admin = _make_user(role="admin")
        client = _make_client(self.app, auth_user=admin)
        doc = _make_doc()
        client._mock_db.query.return_value = _query_chain(first=doc, count=9999)

        with patch.object(storage_service, "get_preview_url",
                          return_value={"url": "http://x/preview", "expires_in": 300}):
            response = client.get("/api/v1/documents/1/preview-link")

        assert response.status_code == 200

    def test_preview_nonexistent_document_404(self):
        """Preview of a missing document returns 404 before any quota check."""
        member = _make_user(role="member")
        client = _make_client(self.app, auth_user=member)
        client._mock_db.query.return_value = _query_chain(first=None, count=0)

        response = client.get("/api/v1/documents/99999/preview-link")
        assert response.status_code == 404


class TestDocumentDownloadLimit:
    """Guard: download-link keeps using the download quota (resource_type=document)
    and still bumps download_count — preview changes must not regress it."""

    @pytest.fixture(autouse=True)
    def setup(self):
        from app.web_app import app
        self.app = app

    def test_download_uses_document_quota_and_bumps_count(self):
        from app.shared.services.storage_service import storage_service

        member = _make_user(role="member")
        client = _make_client(self.app, auth_user=member)
        doc = _make_doc()
        client._mock_db.query.return_value = _query_chain(first=doc, count=0)

        with patch.object(storage_service, "get_download_url",
                          return_value={"url": "http://x/dl", "expires_in": 300}):
            response = client.get("/api/v1/documents/1/download-link")

        assert response.status_code == 200
        assert doc.download_count == 8  # 7 -> 8
        added = client._mock_db.add.call_args[0][0]
        assert added.resource_type == "document"

    def test_download_429_when_download_quota_exhausted(self):
        from app.shared.core.config import settings

        member = _make_user(role="member")
        client = _make_client(self.app, auth_user=member)
        doc = _make_doc()
        client._mock_db.query.return_value = _query_chain(first=doc, count=settings.DOWNLOAD_DAILY_LIMIT)

        response = client.get("/api/v1/documents/1/download-link")
        assert response.status_code == 429
