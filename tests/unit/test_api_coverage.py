"""Focused API/helper coverage for coverage gate."""
import asyncio
import io
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _chain(items=None, first=None, count=0):
    q = MagicMock()
    q.filter.return_value = q
    q.join.return_value = q
    q.order_by.return_value = q
    q.offset.return_value = q
    q.limit.return_value = q
    q.all.return_value = items or []
    q.first.return_value = first
    q.count.return_value = count
    return q


class _Request:
    def __init__(self, forwarded=None):
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = SimpleNamespace(host="127.0.0.1")


def _user(role="admin"):
    return SimpleNamespace(id=1, role=role, is_active=True, is_blacklisted=False, tokens_invalidated_at=None)


def test_brand_mapping_helpers():
    from app.api import brand_mapping

    assert brand_mapping.ok({"x": 1})["data"] == {"x": 1}
    assert asyncio.run(brand_mapping.overview(user=_user()))["data"]["siemens"]
    mapped = asyncio.run(brand_mapping.address("siemens", "omron", "D0", user=_user(), db=MagicMock()))
    assert mapped["data"]["item"] == "D0"


def test_documents_helpers_and_edges(monkeypatch):
    from app.api import documents
    from app.shared.services.storage_service import storage_service

    assert documents.ok()["data"] == {}
    assert documents._client_ip(_Request("1.1.1.1, 2.2.2.2")) == "1.1.1.1"
    assert documents._client_ip(_Request()) == "127.0.0.1"

    db = MagicMock()
    doc = SimpleNamespace(id=1, oss_key="docs/a.pdf", original_name="a.pdf", download_count=None, is_published=True, submitted_by=None)
    db.query.side_effect = [_chain(first=doc), _chain(count=0)]
    monkeypatch.setattr(storage_service, "get_download_url", lambda key: {"url": f"/files/{key}", "expires_in": None})
    result = asyncio.run(documents.get_document_download_link(1, _Request(), db, _user("member")))
    assert result["data"]["download_url"] == "/files/docs/a.pdf"
    assert doc.download_count == 1

    db = MagicMock()
    db.query.return_value = _chain(first=doc)
    monkeypatch.setattr(storage_service, "delete_file", lambda key: None)
    assert asyncio.run(documents.delete_document(1, db, _user()))["message"] == "删除成功"


def test_documents_list_upload_get(monkeypatch):
    from app.api import documents
    from app.shared.services.storage_service import storage_service

    db = MagicMock()
    doc = SimpleNamespace(id=1, oss_key="docs/a.pdf", original_name="a.pdf", is_published=True)
    doc.to_dict = lambda: {"id": doc.id}
    db.query.return_value = _chain(items=[doc], count=1)
    result = asyncio.run(documents.list_documents(brand="siemens", keyword="a", page=1, size=20, sort_by="upload_time", sort_order="desc", db=db, viewer=_user()))
    assert result["data"]["total"] == 1

    db = MagicMock()
    db.query.side_effect = [
        _chain(items=[SimpleNamespace(value="siemens")]),
        _chain(items=[SimpleNamespace(value="plc-manual")]),
    ]
    db.refresh = lambda entry: None
    monkeypatch.setattr(storage_service, "upload_file", lambda c, n, p, t: {"oss_key": "docs/x.pdf", "filename": "x.pdf", "file_size": 100, "file_hash": "abc"})
    file = MagicMock()
    file.filename = "test.pdf"
    file.size = 100
    file.content_type = "application/pdf"

    async def read():
        return b"%PDF"
    file.read = read
    result = asyncio.run(documents.upload_document(file=file, brand="siemens", category="plc-manual", series="", description="desc", db=db, user=_user()))
    assert "投稿成功，等待管理员审核" in result["message"]

    db = MagicMock()
    db.query.return_value = _chain(first=doc)
    result = asyncio.run(documents.get_document(1, db=db, viewer=_user()))
    assert result["data"]["id"] == 1


def test_documents_brands_categories():
    from app.api import documents

    db = MagicMock()
    db.query.return_value = _chain(items=[SimpleNamespace(value="siemens"), SimpleNamespace(value="mitsubishi")])
    assert asyncio.run(documents.brands(db))["data"] == ["siemens", "mitsubishi"]

    db = MagicMock()
    db.query.return_value = _chain(items=[SimpleNamespace(value="plc-manual"), SimpleNamespace(value="best-practice")])
    assert asyncio.run(documents.categories(db))["data"] == ["plc-manual", "best-practice"]


def test_document_series_validation():
    from app.modules.catalog.domain.rules import require_valid_series
    from app.shared.core.err_code import ErrorCode
    from app.shared.domain.catalog import CatalogCode, CatalogError

    require_valid_series("s7-1200", True)
    require_valid_series(None, False)
    with pytest.raises(CatalogError) as exc:
        require_valid_series("bad-series", False)
    assert exc.value.code == CatalogCode.INVALID_SERIES
    assert ErrorCode.status(exc.value.code) == 400
    assert ErrorCode.message(exc.value.code) == "无效系列"


def test_series_tags_require_brand_and_global_unique_value():
    from app.api import tags
    from app.shared.core.err_code import ErrorCode
    from app.shared.domain.catalog import CatalogCode, CatalogError

    db = MagicMock()
    body = tags.CreateTagBody(type="doc_series", value="s7-1200", label_zh="S7-1200", parent_value="plc-manual")
    with pytest.raises(CatalogError) as exc:
        asyncio.run(tags.create_tag(body, db, _user()))
    assert exc.value.code == CatalogCode.TAG_SERIES_PARENT_REQUIRED
    assert ErrorCode.status(exc.value.code) == 400

    db = MagicMock()
    db.query.return_value = _chain(first=SimpleNamespace(value="s7-1200"))
    body = tags.CreateTagBody(type="doc_series", value="s7-1200", label_zh="S7-1200", parent_value="hardware-manual", brand_value="siemens")
    with pytest.raises(CatalogError) as exc:
        asyncio.run(tags.create_tag(body, db, _user()))
    assert exc.value.code == CatalogCode.TAG_ALREADY_EXISTS
    assert ErrorCode.status(exc.value.code) == 409
    assert exc.value.params == {"tag_type": "doc_series", "value": "s7-1200"}


def test_software_responses_do_not_expose_legacy_series():
    from app.api import software
    from app.shared.models.software import Software

    sw = Software(filename="tool.zip", original_name="Tool.zip", brand="siemens", category="utility")
    sw.id = 1
    sw.versions = []
    data = sw.to_dict()
    assert "series" not in data

    version = SimpleNamespace(
        id=10,
        software_id=1,
        software=SimpleNamespace(
            id=1,
            brand="siemens",
            category="utility",
            description="desc",
            created_at=datetime(2026, 1, 1),
            latest_version="1.0",
            submission_status="published",
            rejected_reason=None,
        ),
        oss_key="soft/tool.zip",
        original_name="Tool.zip",
        version="1.0",
        file_size=100,
        file_hash="abc",
        upload_time=datetime(2026, 1, 1),
        download_count=0,
        is_published=True,
    )
    db = MagicMock()
    db.query.return_value = _chain(items=[version], count=1)
    result = asyncio.run(software.list_software(expand_versions=True, page=1, size=20, sort_by="upload_time", sort_order="desc", db=db, viewer=_user()))
    assert "series" not in result["data"]["items"][0]


def test_sw_series_tag_type_is_rejected():
    from app.api import tags
    from app.shared.core.err_code import ErrorCode
    from app.shared.domain.catalog import CatalogCode, CatalogError

    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(tags.list_tags(type="sw_series", db=db))
    assert exc.value.status_code == 400

    body = tags.CreateTagBody(type="sw_series", value="obsolete", label_zh="Obsolete")
    with pytest.raises(CatalogError) as exc:
        asyncio.run(tags.create_tag(body, db, _user()))
    assert exc.value.code == CatalogCode.TAG_TYPE_INVALID
    assert ErrorCode.status(exc.value.code) == 400


@pytest.mark.parametrize("filename,expected", [("setup.exe", ".exe"), ("archive", "")])
def test_software_ext(filename, expected):
    from app.modules.catalog.domain.rules import file_ext

    assert file_ext(filename) == expected


def test_software_download_and_delete(monkeypatch):
    from datetime import datetime

    from app.api import software
    from app.modules.catalog.application.downloads import IssueSoftwareDownloadLink
    from app.shared.services.storage_service import storage_service as storage

    assert software.ok()["data"] == {}
    assert software._ip(_Request("2.2.2.2, 3.3.3.3")) == "2.2.2.2"

    sw = SimpleNamespace(id=3, original_name="tool.zip", download_count=0, submitted_by=None)
    ver = SimpleNamespace(oss_key="software/tool.zip", version="1.0", download_count=0, is_published=True)

    class _Catalog:
        def find(self, software_id):
            return sw

        def latest_active_version(self, software_id, published_only):
            return ver

    class _Ledger:
        def count_today(self, *a):
            return 0

        def record_download(self, software, version, user_id, ip):
            software.download_count += 1
            version.download_count += 1

    class _Signer:
        def download_url(self, key):
            return {"url": f"/files/{key}", "expires_in": 60}

    use_case = IssueSoftwareDownloadLink(_Catalog(), _Ledger(), _Signer(), now=lambda: datetime(2026, 10, 22), daily_limit=5)
    result = use_case.execute_latest(3, _user("member"), "9.9.9.9")
    assert result["version"] == "1.0"
    assert sw.download_count == 1
    assert ver.download_count == 1

    db = MagicMock()
    db.query.return_value = _chain(first=ver)
    monkeypatch.setattr(storage, "delete_file", lambda key: None)
    assert asyncio.run(software.delete_version(3, 4, db, _user()))["message"] == "删除成功"


def test_software_list_upload_get_add_delete(monkeypatch):
    from app.api import software
    from app.shared.services.storage_service import storage_service

    sw = SimpleNamespace(id=1, original_name="Tool.zip", brand="siemens", latest_version="1.0", download_count=0, is_published=True)
    sw.to_dict = lambda include_versions=False: {"id": sw.id}
    sw.versions = []
    ver = SimpleNamespace(id=10, software_id=1, version="2.0", oss_key="software/new.zip")
    ver.to_dict = lambda: {"id": ver.id}

    db = MagicMock()
    db.query.return_value = _chain(items=[sw], count=1)
    result = asyncio.run(software.list_software(page=1, size=20, keyword="Tool", db=db, viewer=_user()))
    assert result["data"]["total"] == 1

    db = MagicMock()
    db.query.return_value = _chain(first=sw)
    assert asyncio.run(software.get_software(1, db=db, viewer=_user()))["data"]["id"] == 1

    monkeypatch.setattr(storage_service, "upload_file", lambda c, n, p, t: {"oss_key": "software/x.zip", "filename": "x.zip", "file_size": 100, "file_hash": "abc"})
    file = MagicMock()
    file.filename = "test.zip"
    file.size = 100
    file.content_type = "application/octet-stream"

    async def read():
        return b"PK"
    file.read = read

    db = MagicMock()
    db.query.side_effect = [
        _chain(items=[SimpleNamespace(value="siemens")]),
        _chain(items=[SimpleNamespace(value="utility")]),
    ]
    db.flush = lambda: None
    assert asyncio.run(software.upload_software(file=file, brand="siemens", category="utility", version="1.0", description="desc", db=db, user=_user()))["message"] == "投稿成功，等待管理员审核"

    db = MagicMock()
    db.query.return_value = _chain(first=sw)
    assert asyncio.run(software.add_version(1, file=file, version="2.0", db=db, admin=_user()))["message"] == "版本已添加"

    db = MagicMock()
    db.query.return_value = _chain(first=sw)
    monkeypatch.setattr(storage_service, "delete_file", lambda key: None)
    assert asyncio.run(software.delete_software(1, db=db, admin=_user()))["message"] == "删除成功"


def test_portal_api_crud():
    from app.api import portal

    item = SimpleNamespace(id=1, section="hero", content={"title": "Hi"}, sort_order=0, is_active=True, updated_at=datetime(2026, 1, 1))
    item.to_dict = lambda: {"id": item.id, "content": item.content}
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.first.return_value = item
    assert asyncio.run(portal.hero(db))["data"]["content"]["title"] == "Hi"

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [item]
    assert asyncio.run(portal.solutions(db))["data"]["items"][0]["id"] == 1

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    body = portal.ContentIn(content={"title": "Next"}, sort_order=2)
    assert asyncio.run(portal.update_solution(1, body, db, _user()))["data"]["content"]["title"] == "Next"
    assert asyncio.run(portal.delete_solution(1, db, _user()))["message"] == "删除成功"


def test_stats_apis(monkeypatch):
    from app.api import stats

    session = SimpleNamespace(user_id=1, geo_location="CN", ip_address="127.0.0.1", user_agent="ua", last_active_at=datetime(2026, 1, 1), is_active=True)
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [session]
    assert asyncio.run(stats.online(db, _user()))["data"]["online_users"] == 1

    db = MagicMock()
    lh_q = db.query.return_value.join.return_value.order_by.return_value
    lh_q.count.return_value = 1
    lh_q.offset.return_value.limit.return_value.all.return_value = []
    assert asyncio.run(stats.login_history(page=1, size=20, keyword=None, status=None, db=db, admin=_user()))["data"]["total"] == 1


def test_dashboard_page_views_include_unknown_geo_but_exclude_local():
    from sqlalchemy.dialects import postgresql

    from app.modules.analytics.infrastructure import visit_queries

    db = MagicMock()
    visit_queries.quality_visit_query(db)
    conditions = db.query.return_value.filter.call_args.args
    rendered = " AND ".join(
        str(condition.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for condition in conditions
    )

    assert "page_view" in rendered
    assert "本地开发" in rendered
    assert "未知" not in rendered


def test_dashboard_date_buckets_use_beijing_time():
    from sqlalchemy.dialects import postgresql

    from app.api import stats
    from app.shared.models.visit_event import VisitEvent

    for expression in (stats._cst_day(VisitEvent.created_at), stats._cst_month(VisitEvent.created_at)):
        rendered = str(expression.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        assert "INTERVAL '8 hours'" in rendered


def _grouped_chain(items):
    """_chain() with .group_by() also wired into the fluent chain.

    The shared _chain() helper above doesn't chain .group_by() (none of its
    existing callers needed it); stats.py's group-by queries do
    .filter().group_by().all(), so this wires .group_by() to return the same
    mock instead of a fresh, unconfigured one.
    """
    q = _chain(items=items)
    q.group_by.return_value = q
    return q


def test_stats_month_new_members_count():
    from app.api import stats

    db = MagicMock()
    q = _chain()
    q.scalar.return_value = 5
    db.query.return_value = q
    start, end = datetime(2026, 9, 1), datetime(2026, 10, 1)
    assert stats._month_new_members_count(db, start, end) == 5

    # No matching rows -> scalar() is None -> falls back to 0, same "or 0"
    # convention as every other count field in this module.
    q2 = _chain()
    q2.scalar.return_value = None
    db.query.return_value = q2
    assert stats._month_new_members_count(db, start, end) == 0


def test_stats_month_new_members_count_deduplicates_by_target_user():
    """Regression test: a demote-then-reapprove writes 2 member_approve rows
    for the same user in one window; the count must not double them.

    The tests above mock db.query()'s *return value* unconditionally, so they
    pass identically whether the query counts raw rows or distinct users —
    they would not have caught this bug. This test instead inspects the
    actual SQL construct passed to db.query(), the way it would render with
    a real dialect, so a regression back to plain func.count(AdminAuditLog.id)
    fails it.
    """
    from app.api import stats

    db = MagicMock()
    q = _chain()
    q.scalar.return_value = 1
    db.query.return_value = q
    start, end = datetime(2026, 9, 1), datetime(2026, 10, 1)

    stats._month_new_members_count(db, start, end)

    count_expr = db.query.call_args[0][0]
    rendered = str(count_expr.compile(compile_kwargs={"literal_binds": True}))
    assert "DISTINCT" in rendered.upper()
    assert "target_user_id" in rendered


def test_stats_monthly_new_members_zero_fills_and_deduplicates_by_day():
    from app.api import stats

    db = MagicMock()
    db.query.return_value = _grouped_chain(
        [
            SimpleNamespace(day=datetime(2026, 9, 2).date(), cnt=3),
        ]
    )
    result = stats._monthly_new_members(
        db,
        datetime(2026, 8, 31, 16),
        datetime(2026, 9, 30, 16),
        ["2026-09-01", "2026-09-02", "2026-09-03"],
    )

    assert result == [
        {"date": "2026-09-01", "count": 0},
        {"date": "2026-09-02", "count": 3},
        {"date": "2026-09-03", "count": 0},
    ]
    count_expr = db.query.call_args[0][1]
    rendered = str(count_expr.compile(compile_kwargs={"literal_binds": True}))
    assert "DISTINCT" in rendered.upper()
    assert "target_user_id" in rendered


def test_stats_yearly_registrations_and_new_members_bucketing():
    from app.api import stats

    months = [(2026, 7), (2026, 8), (2026, 9)]
    year_start_utc = datetime(2026, 7, 1)

    db = MagicMock()
    db.query.return_value = _grouped_chain([SimpleNamespace(ym="2026-08", cnt=3)])
    assert stats._yearly_registrations(db, year_start_utc, months) == [
        {"date": "2026-07", "count": 0},
        {"date": "2026-08", "count": 3},
        {"date": "2026-09", "count": 0},
    ]

    db2 = MagicMock()
    db2.query.return_value = _grouped_chain([SimpleNamespace(ym="2026-09", cnt=7)])
    assert stats._yearly_new_members(db2, year_start_utc, months) == [
        {"date": "2026-07", "count": 0},
        {"date": "2026-08", "count": 0},
        {"date": "2026-09", "count": 7},
    ]


def test_stats_geo_distribution_merges_anon_and_auth_buckets():
    """_geo_distribution is the block extracted out of dashboard_stats() so both
    it and GET /stats/geo-distribution share one implementation. Exercise the
    anon-only, auth-only, and appears-in-both merge cases together."""
    from app.api import stats

    db = MagicMock()
    db.query.side_effect = [
        _grouped_chain([
            SimpleNamespace(name="四川 成都", country_code="CN", anonymous=3),
            SimpleNamespace(name="美国", country_code="US", anonymous=1),
        ]),
        _grouped_chain([
            SimpleNamespace(name="四川 成都", country_code="CN", authenticated=2),
            SimpleNamespace(name="北京", country_code="CN", authenticated=4),
        ]),
    ]

    result = stats._geo_distribution(db, datetime(2026, 9, 1), datetime(2026, 10, 1))
    by_name = {r["name"]: r for r in result}

    assert by_name["四川 成都"]["anonymous"] == 3
    assert by_name["四川 成都"]["online"] == 2
    assert by_name["四川 成都"]["registrations"] == 2
    assert by_name["四川 成都"]["visitors"] == 5  # anon(3) seeded, then auth bucket adds 2
    assert by_name["美国"]["anonymous"] == 1
    assert by_name["美国"]["online"] == 0
    assert by_name["北京"]["anonymous"] == 0
    assert by_name["北京"]["online"] == 4
    assert by_name["北京"]["registrations"] == 4
    # sorted by -(anonymous+online) desc: 四川(3+2=5) > 北京(0+4=4) > 美国(1+0=1)
    assert [r["name"] for r in result] == ["四川 成都", "北京", "美国"]


def test_dashboard_stats_wires_new_fields_and_reuses_geo_helper(monkeypatch):
    """dashboard_stats() must expose monthly/yearly member trend fields and
    must still populate
    geo_distribution (back-compat) via the extracted _geo_distribution() helper
    with its original month-scoped window — not a full DB-shape assertion
    (that's covered by the helper-level tests above), just correct wiring."""
    from app.api import stats

    chain = MagicMock()
    chain.filter.return_value = chain
    chain.join.return_value = chain
    chain.group_by.return_value = chain
    chain.order_by.return_value = chain
    chain.offset.return_value = chain
    chain.limit.return_value = chain
    chain.with_entities.return_value = chain
    chain.scalar.return_value = 0
    chain.all.return_value = []
    chain.first.return_value = None
    chain.count.return_value = 0
    db = MagicMock()
    db.query.return_value = chain

    geo_sentinel = [{"name": "四川", "country_code": "CN", "lat": 30.6, "lng": 102.0,
                      "visitors": 3, "registrations": 2, "online": 2, "anonymous": 1}]
    geo_calls = []

    def fake_geo(db_arg, start, end):
        geo_calls.append((db_arg, start, end))
        return geo_sentinel

    yearly_calls = {}

    def fake_yearly_registrations(db_arg, year_start_utc, months):
        yearly_calls["registrations"] = (db_arg, year_start_utc, months)
        return [{"date": "2026-09", "count": 5}]

    def fake_yearly_new_members(db_arg, year_start_utc, months):
        yearly_calls["new_members"] = (db_arg, year_start_utc, months)
        return [{"date": "2026-09", "count": 2}]

    monkeypatch.setattr(stats, "_geo_distribution", fake_geo)
    monkeypatch.setattr(stats, "_month_new_members_count", lambda db_arg, start, end: 4)
    monkeypatch.setattr(
        stats,
        "_monthly_new_members",
        lambda db_arg, start, end, dates: [{"date": "2026-09-01", "count": 2}],
    )
    monkeypatch.setattr(stats, "_yearly_registrations", fake_yearly_registrations)
    monkeypatch.setattr(stats, "_yearly_new_members", fake_yearly_new_members)

    result = asyncio.run(stats.dashboard_stats(db=db, admin=_user()))
    data = result["data"]

    assert data["month_new_members"] == 4
    assert data["monthly_new_members"] == [{"date": "2026-09-01", "count": 2}]
    assert data["yearly_registrations"] == [{"date": "2026-09", "count": 5}]
    assert data["yearly_new_members"] == [{"date": "2026-09", "count": 2}]
    # back-compat: geo_distribution is still populated, still month-scoped
    assert data["geo_distribution"] == geo_sentinel
    assert len(geo_calls) == 1
    called_db, called_start, called_end = geo_calls[0]
    assert called_db is db
    expected_month_start, expected_month_end = stats._month_range_utc()
    assert (called_start, called_end) == (expected_month_start, expected_month_end)

    # yearly_registrations/yearly_new_members must reuse the SAME months/
    # year_start_utc dashboard_stats() already computed for yearly_pv/yearly_uv,
    # not a second independent computation.
    assert yearly_calls["registrations"][1:] == yearly_calls["new_members"][1:]
    expected_months, expected_year_start_utc = stats._trailing_12_months()
    assert yearly_calls["registrations"][1] == expected_year_start_utc
    assert yearly_calls["registrations"][2] == expected_months


def test_geo_distribution_endpoint_windows_per_range(monkeypatch):
    """GET /stats/geo-distribution must pick a genuinely different window per
    range value, not the month window with a different label."""
    from app.api import stats

    captured = []

    def fake_geo(db_arg, start, end):
        captured.append((start, end))
        return []

    monkeypatch.setattr(stats, "_geo_distribution", fake_geo)
    db = MagicMock()

    asyncio.run(stats.geo_distribution_stats(range="month", db=db, admin=_user()))
    expected_month = stats._last_30_days_range_utc()
    assert captured[-1][0] == expected_month[0]
    assert abs((captured[-1][1] - expected_month[1]).total_seconds()) < 5

    asyncio.run(stats.geo_distribution_stats(range="day", db=db, admin=_user()))
    expected_day = stats._today_range_utc()
    assert captured[-1] == expected_day
    assert captured[-1] != expected_month
    assert expected_month[0] < expected_day[0] < expected_month[1] <= expected_day[1]

    before = datetime.utcnow()
    asyncio.run(stats.geo_distribution_stats(range="year", db=db, admin=_user()))
    year_start, year_end = captured[-1]
    _, expected_year_start = stats._trailing_12_months()
    assert year_start == expected_year_start
    assert year_start != expected_month[0]
    assert abs((year_end - before).total_seconds()) < 5


def test_users_helpers_and_actions():
    from app.api import users

    active = SimpleNamespace(ip_address="127.0.0.1")
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.first.return_value = active
    db = MagicMock()
    db.query.return_value = q
    data = users._enrich_user_dict(db, {}, 1)
    assert data["online"] is True
    assert data["login_ip"] == "127.0.0.1"

    target = SimpleNamespace(id=2, phone="138", role="user", is_active=True, is_blacklisted=False, blacklisted_at=None, blacklisted_by=None, tokens_invalidated_at=None)
    target.to_dict = lambda: {"id": target.id, "role": target.role, "is_blacklisted": target.is_blacklisted}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = target
    assert asyncio.run(users.change_role(2, users.RoleChange(role="member"), db, _user()))["data"]["role"] == "member"
    assert asyncio.run(users.blacklist(2, db, _user()))["message"] == "已拉黑"
    assert asyncio.run(users.unblacklist(2, db, _user()))["message"] == "已解除拉黑"
    assert asyncio.run(users.force_logout(2, db, _user()))["message"] == "已强制登出"

    db = MagicMock()
    db.query.return_value = _chain(items=[target], count=1)
    result = asyncio.run(users.list_users(page=1, size=20, sort_by="created_at", sort_order="desc", db=db, admin=_user()))
    assert result["data"]["total"] == 1


def test_files_api_local_and_error_paths(tmp_path, monkeypatch):
    from app.api import files

    monkeypatch.setattr(files.storage_service, "_backend", "s3")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(files.download_file("missing.pdf", 1, "bad-token"))
    assert exc.value.status_code == 404

    monkeypatch.setattr(files.storage_service, "_backend", "local")
    monkeypatch.setattr(files.storage_service, "validate_local_download_token", lambda key, expires, token: False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(files.download_file("missing.pdf", 1, "bad-token"))
    assert exc.value.status_code == 401

    monkeypatch.setattr(files.storage_service, "validate_local_download_token", lambda key, expires, token: True)
    monkeypatch.setattr(files.storage_service, "get_file_path", lambda key: (_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(files.download_file("missing.pdf", 1, "token"))
    assert exc.value.status_code == 404

    target = tmp_path / "ok.pdf"
    target.write_bytes(b"pdf")
    monkeypatch.setattr(files.storage_service, "get_file_path", lambda key: target)
    response = asyncio.run(files.download_file("ok.pdf", 1, "token"))
    assert str(target) in response.path


def test_files_api_creates_debug_placeholder(tmp_path, monkeypatch):
    from app.api import files

    monkeypatch.setattr(files.settings, "DATA_DIR", str(tmp_path))
    target = tmp_path / "soft" / "debug-tool.zip"
    files.ensure_debug_placeholder("soft/debug-tool.zip")
    assert target.exists()
    assert target.read_bytes() == files.DEBUG_PLACEHOLDERS["soft/debug-tool.zip"]


def test_storage_service_facade_paths(monkeypatch):
    from app.shared.services import storage_service as module
    from app.shared.services.storage_service import StorageService

    local_impl = MagicMock()
    local_impl.upload_file.return_value = {"filename": "a.pdf"}
    local_impl.list_objects.return_value = [{"key": "a"}]
    local_impl.get_file_path.return_value = "path"
    s3_impl = MagicMock()
    s3_impl.generate_presigned_url.return_value = "https://signed"
    monkeypatch.setattr(module, "file_storage", local_impl)
    monkeypatch.setattr(module, "oss_service", s3_impl)

    service = StorageService()
    service._backend = "local"
    assert service.is_local is True
    assert service.upload_file(b"x", "a.pdf", "docs") == {"filename": "a.pdf"}
    assert service.list_objects("docs") == [{"key": "a"}]
    local_download = service.get_download_url("/docs/a.pdf")
    assert local_download["url"].startswith("/api/v1/files/docs/a.pdf?expires=")
    assert "&token=" in local_download["url"]
    assert local_download["expires_in"] == service.LOCAL_DOWNLOAD_TOKEN_TTL_SECONDS
    assert service.get_file_path("docs/a.pdf") == "path"
    service.delete_file("docs/a.pdf")
    local_impl.delete_file.assert_called_once_with("docs/a.pdf")

    service._backend = "s3"
    assert service.is_local is False
    assert service.get_download_url("docs/a.pdf")["url"] == "https://signed"
    with pytest.raises(RuntimeError):
        service.get_file_path("docs/a.pdf")


def test_file_storage_service(monkeypatch, tmp_path):
    from app.shared.services.file_storage import FileStorageService

    monkeypatch.setattr("app.shared.services.file_storage.settings", SimpleNamespace(DATA_DIR=str(tmp_path)))
    service = FileStorageService()
    result = service.upload_file(io.BytesIO(b"test content"), "file.txt", "test-prefix")
    assert result["file_size"] == len(b"test content")
    assert len(service.list_objects("test-prefix")) == 1
    path = service.get_file_path(result["oss_key"])
    assert path.read_bytes() == b"test content"
    service.delete_file(result["oss_key"])
    with pytest.raises(FileNotFoundError):
        service.get_file_path(result["oss_key"])
    assert service.list_objects("non-existent") == []


def test_dependencies_require_roles():
    from app.shared.core import dependencies

    with pytest.raises(HTTPException):
        asyncio.run(dependencies.require_auth(None))
    assert asyncio.run(dependencies.require_member(_user("member"))).role == "member"
    with pytest.raises(HTTPException):
        asyncio.run(dependencies.require_member(_user("user")))
    assert asyncio.run(dependencies.require_admin(_user("admin"))).role == "admin"
    with pytest.raises(HTTPException):
        asyncio.run(dependencies.require_admin(_user("member")))


def test_auth_helpers():
    from app.api import auth
    assert auth.ok()["code"] == 200
    assert auth.ok({"a": 1})["data"]["a"] == 1


def test_update_me_helper():
    from app.api import auth

    user = SimpleNamespace(nickname=None)
    user.to_dict = lambda: {"nickname": user.nickname}
    db = MagicMock()
    result = asyncio.run(auth.update_me(auth.ProfileUpdateRequest(nickname=" Tom "), db, user))
    assert result["data"]["nickname"] == "Tom"
    db.commit.assert_called_once()
    db.refresh.assert_called_once_with(user)


def test_visit_tracking_records_client_id_and_event_type(monkeypatch):
    from app.api import visits

    monkeypatch.setattr("app.modules.analytics.infrastructure.geo.resolve_ip_geo", lambda ip: {"name": "成都", "country_code": "CN"})
    db = MagicMock()
    body = visits.VisitBody(path="/resources", client_id="client-123", event_type="page_view")
    result = asyncio.run(visits.track_visit(body, _Request("8.8.8.8"), db))

    assert result["data"]["tracked"] is True
    event = db.add.call_args.args[0]
    assert event.ip_address == "8.8.8.8"
    assert event.client_id == "client-123"
    assert event.event_type == "page_view"
    assert event.path == "/resources"


def test_visit_tracking_rejects_unknown_event_type():
    from app.api import visits

    body = visits.VisitBody(path="/", client_id="client-123", event_type="click")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(visits.track_visit(body, _Request("8.8.8.8"), MagicMock()))
    assert exc.value.status_code == 400

def test_identity_phone_validation():
    from app.modules.identity.domain.phone import require_valid_phone
    from app.shared.domain.auth import AuthError, BizCode

    with pytest.raises(AuthError) as exc:
        require_valid_phone("1234")
    assert exc.value.code == BizCode.AUTH_INVALID_PHONE


def test_identity_issue_token_pair():
    from dataclasses import asdict

    from app.modules.identity.application.sessions import IssueTokenPair
    from app.modules.identity.infrastructure.tokens import JwtTokenCodec
    from app.shared.core.utils import utcnow

    account = SimpleNamespace(id=1, phone="13800138000", role="user")
    tokens = asdict(IssueTokenPair(JwtTokenCodec(), None, utcnow, 60, 7).execute(account))
    assert "access_token" in tokens
    assert "refresh_token" in tokens


def test_identity_verify_code():
    from app.modules.identity.application.verification_codes import VerifySmsCode
    from app.shared.core.utils import utcnow

    record = SimpleNamespace(code="654321", is_used=False, verify_attempts=0)

    class Store:
        def latest_active(self, phone, now):
            return record

        def save(self, value):
            pass

    assert VerifySmsCode(Store(), utcnow).execute("13800138000", "wrong") is False
    assert record.verify_attempts == 1


def test_identity_send_code():
    import random

    from app.modules.identity.application.verification_codes import SendVerificationCode
    from app.shared.core.utils import utcnow

    class Store:
        added = False

        def latest(self, phone):
            return None

        def count_since(self, phone, since):
            return 0

        def add(self, *args):
            Store.added = True

        def save(self, record):
            pass

    class NoGateway:
        def send(self, phone, code):
            raise AssertionError("mock mode must not deliver")

    SendVerificationCode(Store(), NoGateway(), None, random.Random(1), utcnow, mock_code="123456").execute("13800138000")
    assert Store.added


def test_identity_sign_in_rejects_bad_code():
    from app.modules.identity.application.authenticate import AuthenticateBySms, AuthenticationIntent
    from app.modules.identity.application.verification_codes import VerifySmsCode
    from app.shared.core.utils import utcnow
    from app.shared.domain.auth import AuthError, BizCode

    class Store:
        def latest_active(self, phone, now):
            return None

        def save(self, record):
            pass

    class Accounts:
        def find_by_phone(self, phone):
            return None

        def count(self):
            return 1

        def create(self, phone, role, now):
            raise AssertionError("must not register on a bad code")

        def record_login(self, account, now):
            pass

    use_case = AuthenticateBySms(Accounts(), VerifySmsCode(Store(), utcnow), utcnow())
    with pytest.raises(AuthError) as exc:
        use_case.execute("13800138000", "wrong", AuthenticationIntent.SIGN_IN)
    assert exc.value.code == BizCode.AUTH_INVALID_CODE


def test_identity_logout():
    from app.modules.identity.application.sessions import EndSession, IssueTokenPair
    from app.modules.identity.infrastructure.tokens import JwtTokenCodec
    from app.shared.core.utils import utcnow

    class Blacklist:
        added = False

        def exists(self, jti):
            return False

        def add(self, *args):
            Blacklist.added = True

    class Sessions:
        def deactivate(self, user_id, client_id=None, user_agent=None):
            pass

        def deactivate_all(self, user_id):
            pass

    account = SimpleNamespace(id=7, phone="13800138000", role="user")
    token = IssueTokenPair(JwtTokenCodec(), None, utcnow, 60, 7).execute(account).access_token
    EndSession(JwtTokenCodec(), Blacklist(), Sessions(), utcnow).execute(token, user_agent="UA", client_id="client-1")
    assert Blacklist.added


def test_identity_decode_token_roundtrip():
    from app.modules.identity.application.sessions import IssueTokenPair
    from app.modules.identity.infrastructure.tokens import JwtTokenCodec, decode_token
    from app.shared.core.utils import utcnow

    account = SimpleNamespace(id=7, phone="13800138000", role="user")
    token = IssueTokenPair(JwtTokenCodec(), None, utcnow, 60, 7).execute(account).access_token
    assert decode_token(token)["type"] == "access"


def test_utcnnow():
    from app.shared.core.utils import utcnow

    now = utcnow()
    assert now is not None
def test_models():
    assert True


def _direct_upload_token(claims_extra: dict) -> str:
    import jwt

    from app.shared.core.config import settings
    claims = {"oss_key": "software/siemens/abc.zip", "filename": "t.zip", "brand": "siemens",
              "category": "utility", "version": "1.0", "description": "",
              "content_type": "application/zip", "size": 100,
              "is_admin": True, "submitted_by": None}
    claims.update(claims_extra)
    return jwt.encode(claims, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def test_software_direct_upload_init_local(monkeypatch):
    from app.api import software
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "local")
    db = MagicMock()
    db.query.side_effect = [_chain(items=[SimpleNamespace(value="siemens")]),
                            _chain(items=[SimpleNamespace(value="utility")]),
                            _chain(first=None)]
    body = software.UploadInitBody(filename="t.zip", brand="siemens", category="utility",
                                   version="1.0", size=100)
    result = asyncio.run(software.upload_init(body, db=db, user=_user()))
    assert result["data"]["mode"] == "sync"


def test_software_direct_upload_init_multipart(monkeypatch):
    from app.api import software
    from app.shared.core.config import settings
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    monkeypatch.setattr(oss_mod.oss_service, "create_multipart_upload", lambda *a, **k: "uid-1")
    monkeypatch.setattr(oss_mod.oss_service, "presign_part_upload",
                        lambda key, uid, pn, exp: f"https://oss/part{pn}")
    db = MagicMock()
    db.query.side_effect = [_chain(items=[SimpleNamespace(value="siemens")]),
                            _chain(items=[SimpleNamespace(value="utility")]),
                            _chain(first=None)]
    body = software.UploadInitBody(filename="t.zip", brand="siemens", category="utility",
                                   version="1.0", content_type="application/zip",
                                   size=settings.UPLOAD_PART_SIZE_MB * 1024 * 1024 + 1)
    result = asyncio.run(software.upload_init(body, db=db, user=_user()))
    data = result["data"]
    assert data["mode"] == "multipart"
    assert len(data["part_urls"]) == 2
    assert data["part_urls"][1] == "https://oss/part2"
    import jwt

    from app.shared.core.config import settings
    claims = jwt.decode(data["token"], settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    assert claims["mode"] == "multipart"
    assert claims["upload_id"] == "uid-1"
    assert claims["size"] == body.size


def test_software_direct_upload_init_single(monkeypatch):
    from app.api import software
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    monkeypatch.setattr(oss_mod.oss_service, "presign_single_put", lambda *a, **k: "https://oss/single")
    db = MagicMock()
    db.query.side_effect = [_chain(items=[SimpleNamespace(value="siemens")]),
                            _chain(items=[SimpleNamespace(value="utility")]),
                            _chain(first=None)]
    body = software.UploadInitBody(filename="t.zip", brand="siemens", category="utility",
                                   version="1.0", size=1024)
    data = asyncio.run(software.upload_init(body, db=db, user=_user()))["data"]
    assert data["mode"] == "single"
    assert data["upload_url"] == "https://oss/single"


def test_software_direct_upload_complete_multipart(monkeypatch):
    from app.api import software
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    monkeypatch.setattr(oss_mod.oss_service, "complete_multipart_upload", lambda *a, **k: None)
    monkeypatch.setattr(oss_mod.oss_service, "head_object", lambda *a, **k: True)
    monkeypatch.setattr(oss_mod.oss_service, "delete_file", lambda *a, **k: None)
    token = _direct_upload_token({"mode": "multipart", "upload_id": "uid-1"})
    db = MagicMock()
    db.query.return_value = _chain(first=None)
    db.flush = lambda: None
    parts = [software.UploadPartResult(part_number=2, etag="B"), software.UploadPartResult(part_number=1, etag="A")]
    body = software.UploadCompleteBody(token=token, parts=parts)
    result = asyncio.run(software.upload_complete(body, db=db, user=_user()))
    assert result["message"] == "投稿成功，等待管理员审核"


def test_software_direct_upload_complete_single(monkeypatch):
    from app.api import software
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    monkeypatch.setattr(oss_mod.oss_service, "head_object", lambda *a, **k: True)
    monkeypatch.setattr(oss_mod.oss_service, "delete_file", lambda *a, **k: None)
    token = _direct_upload_token({"mode": "single"})
    db = MagicMock()
    db.query.return_value = _chain(first=None)
    db.flush = lambda: None
    body = software.UploadCompleteBody(token=token)
    assert asyncio.run(software.upload_complete(body, db=db, user=_user()))["message"] == "投稿成功，等待管理员审核"


def test_software_direct_upload_complete_single_missing(monkeypatch):
    from app.api import software
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    monkeypatch.setattr(oss_mod.oss_service, "head_object", lambda *a, **k: False)
    token = _direct_upload_token({"mode": "single"})
    from app.shared.domain.catalog import CatalogError
    with pytest.raises(CatalogError):
        asyncio.run(software.upload_complete(software.UploadCompleteBody(token=token), db=MagicMock(), user=_user()))


def test_software_direct_upload_abort(monkeypatch):
    from app.api import software
    from app.shared.services import oss_service as oss_mod
    from app.shared.services.storage_service import storage_service as _ss
    monkeypatch.setattr(_ss, "_backend", "s3")
    called = {}
    monkeypatch.setattr(oss_mod.oss_service, "abort_multipart_upload", lambda *a, **k: called.setdefault("aborted", True))
    token = _direct_upload_token({"mode": "multipart", "upload_id": "uid-1"})
    asyncio.run(software.upload_abort(software.UploadAbortBody(token=token), user=_user()))
    assert called.get("aborted") is True


def test_software_direct_upload_bad_token():
    from app.api import software
    from app.shared.domain.catalog import CatalogError
    with pytest.raises(CatalogError):
        asyncio.run(software.upload_complete(software.UploadCompleteBody(token="bad"), db=MagicMock(), user=_user()))
