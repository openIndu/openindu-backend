"""Admin writes leave an admin_audit_logs row (AdminAuditMiddleware, require_admin stamp, add_audit_detail)."""
import logging
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.shared.core.audit import add_audit_detail, self_audited
from app.shared.core.database import SessionLocal
from app.shared.core.dependencies import require_admin, require_auth
from app.shared.middleware import admin_audit
from app.shared.middleware.admin_audit import ACTION_MAX_LENGTH, READ_ONLY_METHODS, AdminAuditMiddleware, audit_action
from app.shared.models.admin_audit_log import AdminAuditLog
from app.web_app import app as web_app

ADMIN = SimpleNamespace(id=7, role="admin")
MEMBER = SimpleNamespace(id=8, role="member")
MODULE = __name__.rsplit(".", 1)[-1]  # how audit_action names the module of a probe endpoint


class Item(BaseModel):
    name: str


def build_probe() -> FastAPI:
    probe = FastAPI()
    probe.add_middleware(AdminAuditMiddleware)

    @probe.get("/items")
    async def list_items(_=Depends(require_admin)):
        return {"ok": True}

    @probe.post("/items")
    async def create_item(_=Depends(require_admin)):
        return {"ok": True}

    @probe.put("/items/{item_id}")
    async def replace_item(item_id: int, _=Depends(require_admin)):
        return {"ok": True}

    @probe.patch("/items/{item_id}")
    async def edit_item(item_id: int, _=Depends(require_admin)):
        return {"ok": True}

    @probe.delete("/items/{item_id}")
    async def remove_item(item_id: int, _=Depends(require_admin)):
        return {"ok": True}

    @probe.post("/validated")
    async def create_validated(item: Item, _=Depends(require_admin)):
        return {"ok": True}

    @probe.post("/missing")
    async def create_missing(_=Depends(require_admin)):
        raise HTTPException(404, "gone")

    @probe.post("/explode")
    async def explode(_=Depends(require_admin)):
        raise RuntimeError("boom")

    @probe.post("/own-row")
    @self_audited
    async def own_row(_=Depends(require_admin)):
        return {"ok": True}

    @probe.post("/with-detail")
    async def with_detail(request: Request, _=Depends(require_admin)):
        add_audit_detail(request, ids=[1, 2], count=2, status="forged", path="/forged")
        return {"ok": True}

    return probe


@pytest.fixture(autouse=True)
def clean_state():
    with SessionLocal() as db:
        db.query(AdminAuditLog).delete()
        db.commit()
    yield
    web_app.dependency_overrides.clear()


def as_user(app: FastAPI, user) -> TestClient:
    app.dependency_overrides[require_auth] = lambda: user
    return TestClient(app, raise_server_exceptions=False)


def audit_rows() -> list[AdminAuditLog]:
    with SessionLocal() as db:
        return db.query(AdminAuditLog).order_by(AdminAuditLog.id).all()


@pytest.mark.parametrize(
    ("method", "url", "function"),
    [
        ("POST", "/items", "create_item"),
        ("PUT", "/items/3", "replace_item"),
        ("PATCH", "/items/3", "edit_item"),
        ("DELETE", "/items/3", "remove_item"),
    ],
)
def test_successful_admin_write_is_recorded(method, url, function):
    client = as_user(build_probe(), ADMIN)
    assert client.request(method, url).status_code == 200
    [row] = audit_rows()
    assert row.admin_id == ADMIN.id
    assert row.target_user_id is None
    assert row.action == f"{MODULE}.{function}"
    assert row.detail == {"method": method, "path": url, "status": 200}


def test_reads_are_not_recorded():
    assert as_user(build_probe(), ADMIN).get("/items").status_code == 200
    assert audit_rows() == []


@pytest.mark.parametrize(("url", "payload", "status"), [("/missing", None, 404), ("/validated", {}, 422), ("/explode", None, 500)])
def test_failed_requests_are_not_recorded(url, payload, status):
    response = as_user(build_probe(), ADMIN).post(url, json=payload)
    assert response.status_code == status
    assert audit_rows() == []


def test_non_admin_and_anonymous_requests_are_not_recorded():
    assert as_user(build_probe(), MEMBER).post("/items").status_code == 403

    probe = build_probe()

    def anonymous():
        raise HTTPException(401, "请先登录")

    probe.dependency_overrides[require_auth] = anonymous
    assert TestClient(probe).post("/items").status_code == 401
    assert audit_rows() == []


def test_self_audited_endpoint_is_skipped():
    """Its use case writes the semantic row; the middleware must not add a second one."""
    assert as_user(build_probe(), ADMIN).post("/own-row").status_code == 200
    assert audit_rows() == []


def test_query_string_is_recorded():
    assert as_user(build_probe(), ADMIN).post("/items?force=true&dry=no").status_code == 200
    [row] = audit_rows()
    assert row.detail["query"] == {"force": "true", "dry": "no"}


def test_handler_details_are_merged_but_cannot_override_the_request_facts():
    assert as_user(build_probe(), ADMIN).post("/with-detail").status_code == 200
    [row] = audit_rows()
    assert row.detail == {"method": "POST", "path": "/with-detail", "status": 200, "ids": [1, 2], "count": 2}


def test_audit_write_failure_never_fails_the_request(monkeypatch, caplog):
    def broken_write(*args, **kwargs):
        raise RuntimeError("database is down")

    monkeypatch.setattr(admin_audit, "_write", broken_write)
    with caplog.at_level(logging.WARNING, logger=admin_audit.logger.name):
        response = as_user(build_probe(), ADMIN).post("/items")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert "admin audit row not written for POST /items" in caplog.text
    assert audit_rows() == []


def test_action_is_cut_to_the_column_length():
    async def endpoint_with_a_name_that_is_far_longer_than_the_fifty_character_action_column():
        return None

    action = audit_action(endpoint_with_a_name_that_is_far_longer_than_the_fifty_character_action_column)
    assert len(action) == ACTION_MAX_LENGTH
    assert action.startswith(f"{MODULE}.endpoint_with_a_name")


def _depends_on(dependant, target) -> bool:
    return any(sub.call is target or _depends_on(sub, target) for sub in dependant.dependencies)


def _app_endpoints():
    for route in web_app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is not None and getattr(route, "methods", None):
            yield route, endpoint


EXPECTED_SELF_AUDITED = {
    ("PUT", "/api/v1/users/{user_id}/role"),
    ("POST", "/api/v1/users/{user_id}/blacklist"),
    ("POST", "/api/v1/users/{user_id}/unblacklist"),
    ("POST", "/api/v1/users/{user_id}/force-logout"),
    ("DELETE", "/api/v1/users/{user_id}"),
    ("PUT", "/api/v1/admin/member-applications/{user_id}/reject"),
    ("PUT", "/api/v1/admin/member-applications/{user_id}/approve"),
}


def test_self_audited_routes_are_exactly_the_membership_ones():
    marked = {
        (method, route.path)
        for route, endpoint in _app_endpoints()
        if getattr(endpoint, "__self_audited__", False)
        for method in route.methods
    }
    assert marked == EXPECTED_SELF_AUDITED
    for route, endpoint in _app_endpoints():
        if getattr(endpoint, "__self_audited__", False):
            assert _depends_on(route.dependant, require_admin), route.path


def test_every_admin_write_route_gets_its_own_action_that_fits_the_column():
    actions: dict[str, list[str]] = {}
    for route, endpoint in _app_endpoints():
        if route.methods <= READ_ONLY_METHODS or getattr(endpoint, "__self_audited__", False):
            continue
        if not _depends_on(route.dependant, require_admin):
            continue
        full = f"{endpoint.__module__.rsplit('.', 1)[-1]}.{endpoint.__name__}"
        assert len(full) <= ACTION_MAX_LENGTH, f"{full} would be cut"
        actions.setdefault(full, []).append(route.path)
    assert {a: p for a, p in actions.items() if len(p) > 1} == {}
    # documents and software both have a toggle_publish, which is why the module is part of the name
    assert {"software.toggle_publish", "documents.toggle_publish", "tags.create_tag"} <= set(actions)


class FakeCatalog:
    """Stands in for catalog(db): each use case is a plain function registered by name."""

    def __init__(self, **use_cases):
        self._use_cases = use_cases

    def __getattr__(self, name):
        return SimpleNamespace(execute=self._use_cases[name])


class Row:
    def __init__(self, is_published=True):
        self.is_published = is_published

    def to_dict(self, **_):
        return {"id": 1}


DOCUMENT_CATALOG = FakeCatalog(
    review_document=lambda doc_id, action, reason: (Row(), "ok"),
    toggle_document_publish=lambda doc_id: Row(is_published=False),
    bulk_publish_documents=lambda criteria, publish: 3,
)
SOFTWARE_CATALOG = FakeCatalog(
    review_submission=lambda software_id, action, reason: (Row(), "ok"),
    toggle_publish=lambda software_id: Row(is_published=True),
    toggle_version_publish=lambda software_id, version_id: Row(is_published=False),
    bulk_publish=lambda criteria, publish: 4,
    bulk_delete_versions=lambda criteria: 2,
)

ENRICHED = [
    (
        "documents.review_document_submission", "PATCH", "/api/v1/documents/5/review",
        {"action": "reject", "reason": "unclear scan"},
        {"action": "reject", "reason": "unclear scan"},
    ),
    ("documents.toggle_publish", "PATCH", "/api/v1/documents/5/publish", None, {"is_published": False}),
    (
        "documents.bulk_publish_documents", "PATCH", "/api/v1/documents/publish/bulk",
        {"ids": [1, 2, 3], "publish": False},
        {"ids": [1, 2, 3], "publish": False, "count": 3},
    ),
    (
        "software.review_software_submission", "PATCH", "/api/v1/software/9/review",
        {"action": "publish"},
        {"action": "publish", "reason": None},
    ),
    ("software.toggle_publish", "PATCH", "/api/v1/software/9/publish", None, {"is_published": True}),
    ("software.toggle_version_publish", "PATCH", "/api/v1/software/9/versions/4/publish", None, {"is_published": False}),
    (
        "software.bulk_publish_software", "PATCH", "/api/v1/software/publish/bulk",
        {"version_ids": [4, 5], "brand": "siemens", "publish": True},
        {"version_ids": [4, 5], "brand": "siemens", "publish": True, "count": 4},
    ),
    (
        "software.bulk_delete_software", "DELETE", "/api/v1/software/bulk",
        {"ids": [9], "keyword": "tia"},
        {"ids": [9], "keyword": "tia", "count": 2},
    ),
]


@pytest.mark.parametrize(("action", "method", "url", "payload", "expected"), ENRICHED, ids=[e[0] for e in ENRICHED])
def test_moderation_and_bulk_routes_record_what_the_url_cannot_show(monkeypatch, action, method, url, payload, expected):
    monkeypatch.setattr("app.api.documents.catalog", lambda db: DOCUMENT_CATALOG)
    monkeypatch.setattr("app.api.software.catalog", lambda db: SOFTWARE_CATALOG)
    response = as_user(web_app, ADMIN).request(method, url, json=payload)
    assert response.status_code == 200, response.text
    [row] = audit_rows()
    assert row.action == action
    assert row.detail == {"method": method, "path": url, "status": 200, **expected}


class TagRow:
    def to_dict(self):
        return {"id": 12}


def test_tag_lifecycle_is_audited(monkeypatch):
    monkeypatch.setattr(
        "app.api.tags.catalog",
        lambda db: FakeCatalog(
            create_tag=lambda **kwargs: TagRow(),
            update_tag=lambda *args, **kwargs: TagRow(),
            delete_tag=lambda *args, **kwargs: None,
        ),
    )
    client = as_user(web_app, ADMIN)
    assert client.post("/api/v1/tags", json={"type": "sw_brand", "value": "probe", "label_zh": "探针"}).status_code == 200
    assert client.patch("/api/v1/tags/12", json={"label_zh": "探针2"}).status_code == 200
    assert client.delete("/api/v1/tags/12").status_code == 200
    assert [(r.action, r.detail["method"], r.detail["path"]) for r in audit_rows()] == [
        ("tags.create_tag", "POST", "/api/v1/tags"),
        ("tags.update_tag", "PATCH", "/api/v1/tags/12"),
        ("tags.delete_tag", "DELETE", "/api/v1/tags/12"),
    ]
