"""Unit tests for the catalog (software) rules and use cases."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.catalog.application.admin import BulkDeleteSoftwareVersions, VersionCriteria
from app.catalog.application.downloads import IssueSoftwareDownloadLink
from app.catalog.application.submissions import (
    CompleteDirectUpload,
    PlanDirectUpload,
    ReviewSoftwareSubmission,
    SubmitSoftwarePackage,
)
from app.catalog.domain import rules
from app.domain.catalog import CatalogCode, CatalogError

NOW = datetime(2026, 10, 22, 12, 0, 0)


def make_user(uid=1, role="member"):
    return SimpleNamespace(id=uid, role=role)


def make_sw(sid=1, **kw):
    sw = SimpleNamespace(
        id=sid, brand="siemens", category="utility", original_name="tool.zip",
        submitted_by=None, submission_status="pending", is_published=False,
        rejected_reason=None, download_count=0, versions=[],
    )
    for key, value in kw.items():
        setattr(sw, key, value)
    return sw


class FakeTags:
    def __init__(self, values=None):
        self.values = values or {"sw_brand": ["siemens"], "sw_category": ["utility"]}

    def active_values(self, tag_type):
        return self.values.get(tag_type, [])


class FakeStorage:
    def __init__(self):
        self.deleted = []
        self.uploaded = None

    def upload(self, file_stream, filename, prefix, content_type):
        self.uploaded = (filename, prefix)
        return {"filename": "stored-" + (filename or ""), "file_size": 10, "file_hash": "h", "oss_key": "software/siemens/x.zip"}

    def delete(self, oss_key):
        self.deleted.append(oss_key)


class FakeCatalog:
    def __init__(self, entries=None):
        self.entries = {e.id: e for e in (entries or [])}
        self.added = []
        self.saved = []
        self.deleted = []
        self.versions = []

    def find(self, software_id):
        return self.entries.get(software_id)

    def find_owned(self, software_id, submitter_id):
        entry = self.entries.get(software_id)
        return entry if entry is not None and entry.submitted_by == submitter_id else None

    def find_version(self, version_id, software_id):
        return self.versions_by_id().get(version_id)

    def versions_by_id(self):
        return {v.id: v for entry in self.entries.values() for v in entry.versions}

    def find_version_by_oss_key(self, oss_key):
        return any(v.oss_key == oss_key for entry in self.entries.values() for v in entry.versions)

    def latest_active_version(self, software_id, published_only):
        versions = [v for v in self.entries[software_id].versions if v.is_active and (v.is_published or not published_only)]
        return versions[-1] if versions else None

    def add_submission(self, **kw):
        entry = make_sw(sid=len(self.entries) + 1, **{"submission_status": kw["submission_status"], "submitted_by": kw["submitted_by"]})
        self.entries[entry.id] = entry
        self.added.append(kw)
        return entry

    def add_submission_from_oss(self, **kw):
        return self.add_submission(**{**kw, "meta": {"filename": kw["oss_key"].rsplit("/", 1)[-1]}})

    def add_version_to_existing(self, software_id, **kw):
        return SimpleNamespace(id=99, software_id=software_id, **kw)

    def add_version(self, software, **kw):
        return SimpleNamespace(id=98, software_id=software.id, **kw)

    def save(self, entry):
        self.saved.append(entry)
        return entry

    def save_version(self, version):
        self.saved.append(version)

    def save_versions(self, versions):
        self.saved.extend(versions)

    def delete(self, entry):
        self.deleted.append(entry.id)
        self.entries.pop(entry.id, None)

    def delete_version(self, version):
        self.deleted.append(version.id)

    def delete_versions(self, versions):
        for version in versions:
            self.deleted.append(version.id)
            entry = self.entries.get(version.software_id)
            if entry is not None and version in entry.versions:
                entry.versions.remove(version)

    def delete_childless(self, software_ids):
        for sid in software_ids:
            entry = self.entries.get(sid)
            if entry is not None and not entry.versions:
                self.delete(entry)

    def versions_matching(self, criteria, is_published):
        rows = [v for entry in self.entries.values() for v in entry.versions]
        if criteria.version_ids is not None:
            rows = [v for v in rows if v.id in criteria.version_ids]
        if is_published is not None:
            rows = [v for v in rows if v.is_published == is_published]
        return rows


class FakeCodec:
    def __init__(self, claims=None):
        self.claims = claims or {}

    def encode(self, claims):
        return "tok"

    def decode(self, token):
        return self.claims


class FakeSigner:
    is_local = False

    def __init__(self):
        self.multipart_aborted = []

    def new_oss_key(self, filename, prefix):
        return f"{prefix}/gen-{filename}"

    def create_multipart(self, oss_key, content_type):
        return "uid-1"

    def presign_part(self, oss_key, upload_id, part_number, expire_minutes):
        return f"https://oss/part{part_number}"

    def presign_single(self, oss_key, content_type, expire_minutes):
        return "https://oss/single"

    def complete_multipart(self, oss_key, upload_id, parts):
        self.completed = parts

    def abort_multipart(self, oss_key, upload_id):
        self.multipart_aborted.append((oss_key, upload_id))

    def head_exists(self, oss_key):
        return self.head_result

    head_result = True


# ── rules ─────────────────────────────────────────────────────────────────────

def test_file_ext_extraction():
    assert rules.file_ext("setup.EXE") == ".exe"
    assert rules.file_ext("archive") == ""


def test_package_rules_reject_bad_format_and_size():
    with pytest.raises(CatalogError) as fmt:
        rules.require_valid_package("virus.txt", 10, 5)
    assert fmt.value.code is CatalogCode.UNSUPPORTED_PACKAGE_FORMAT
    with pytest.raises(CatalogError) as big:
        rules.require_valid_package("a.zip", 6 * 1024**3, 5)
    assert big.value.code is CatalogCode.FILE_TOO_LARGE
    rules.require_valid_package("a.zip", 1024, 5)


def test_review_rules():
    assert rules.require_reject_reason(" 描述太简单 ") == "描述太简单"
    with pytest.raises(CatalogError) as empty:
        rules.require_reject_reason("  ")
    assert empty.value.code is CatalogCode.REJECT_REASON_REQUIRED
    with pytest.raises(CatalogError) as blocked:
        rules.require_rejectable("published", True)
    assert blocked.value.code is CatalogCode.REJECT_PUBLISHED_BLOCKED
    rules.require_rejectable("pending", False)


def test_download_quota_admin_bypass():
    with pytest.raises(CatalogError) as exhausted:
        rules.require_download_quota("member", 5, 5)
    assert exhausted.value.code is CatalogCode.DOWNLOAD_LIMIT_REACHED
    rules.require_download_quota("admin", 999, 5)


def test_visibility_and_member_entry_rules():
    with pytest.raises(CatalogError) as hidden:
        rules.require_visible(False, is_reviewer=False)
    assert hidden.value.code is CatalogCode.SW_NOT_FOUND
    rules.require_visible(False, is_reviewer=True)
    with pytest.raises(CatalogError) as member:
        rules.require_member_new_entry("member", software_id=7)
    assert member.value.code is CatalogCode.MEMBER_CANNOT_ADD_VERSION
    rules.require_member_new_entry("admin", software_id=7)


# ── review ────────────────────────────────────────────────────────────────────

def test_review_publish_flips_all_versions():
    ver = SimpleNamespace(is_published=False)
    entry = make_sw(versions=[ver], submission_status="pending")
    catalog = FakeCatalog([entry])

    result, message = ReviewSoftwareSubmission(catalog).execute(1, "publish", None)

    assert message == "已发布"
    assert entry.is_published is True
    assert entry.submission_status == "published"
    assert ver.is_published is True
    assert entry.rejected_reason is None
    assert catalog.saved == [entry]


def test_review_reject_sets_reason():
    entry = make_sw(submission_status="pending")
    catalog = FakeCatalog([entry])

    result, message = ReviewSoftwareSubmission(catalog).execute(1, "reject", "描述太简单")

    assert message == "已驳回"
    assert entry.submission_status == "rejected"
    assert entry.rejected_reason == "描述太简单"


def test_review_invalid_action_and_missing_entry():
    catalog = FakeCatalog()
    with pytest.raises(CatalogError) as missing:
        ReviewSoftwareSubmission(catalog).execute(404, "publish", None)
    assert missing.value.code is CatalogCode.SW_NOT_FOUND
    with pytest.raises(CatalogError) as bad:
        ReviewSoftwareSubmission(FakeCatalog([make_sw()]), ).execute(1, "nonsense", None)
    assert bad.value.code is CatalogCode.INVALID_REVIEW_ACTION


# ── direct upload ─────────────────────────────────────────────────────────────

def test_plan_direct_upload_sync_mode_for_local_storage():
    signer = FakeSigner()
    signer.is_local = True
    use_case = PlanDirectUpload(FakeTags(), FakeCatalog(), signer, FakeCodec(), "software", 5, 10, 30)

    payload = use_case.execute(make_user(), filename="t.zip", brand="siemens", category="utility",
                               version="1.0", description="", content_type=None, size=100, software_id=None)

    assert payload == {"mode": "sync"}


def test_plan_direct_upload_multipart_layout():
    use_case = PlanDirectUpload(FakeTags(), FakeCatalog(), FakeSigner(), FakeCodec(), "software", 5, part_size_bytes=100, expire_minutes=30)

    payload = use_case.execute(make_user(role="admin"), filename="t.zip", brand="siemens", category="utility",
                               version="1.0", description="", content_type="application/zip", size=250, software_id=None)

    assert payload["mode"] == "multipart"
    assert payload["part_size"] == 100
    assert payload["part_urls"] == ["https://oss/part1", "https://oss/part2", "https://oss/part3"]
    assert payload["expires_in"] == 30 * 60


def test_plan_direct_upload_member_cannot_target_existing():
    use_case = PlanDirectUpload(FakeTags(), FakeCatalog(), FakeSigner(), FakeCodec(), "software", 5, 100, 30)
    with pytest.raises(CatalogError) as blocked:
        use_case.execute(make_user(), filename="t.zip", brand="siemens", category="utility",
                         version="1.0", description="", content_type=None, size=50, software_id=7)
    assert blocked.value.code is CatalogCode.MEMBER_CANNOT_ADD_VERSION


def test_complete_direct_upload_single_requires_object_on_oss():
    signer = FakeSigner()
    signer.head_result = False
    codec = FakeCodec({"mode": "single", "oss_key": "k", "is_admin": True})
    use_case = CompleteDirectUpload(FakeCatalog(), FakeStorage(), signer, codec)
    with pytest.raises(CatalogError) as missing:
        use_case.execute("tok", None, None)
    assert missing.value.code is CatalogCode.FILE_NOT_ON_OSS


def test_complete_direct_upload_new_entry_and_duplicate():
    catalog = FakeCatalog()
    storage = FakeStorage()
    codec = FakeCodec({
        "mode": "single", "oss_key": "software/siemens/dup.zip", "is_admin": False, "submitted_by": 5,
        "filename": "dup.zip", "brand": "siemens", "category": "utility", "version": "1.0",
        "description": "", "size": 10,
    })
    use_case = CompleteDirectUpload(catalog, storage, FakeSigner(), codec)

    entry, message = use_case.execute("tok", None, "hash")
    assert message == "投稿成功，等待管理员审核"
    assert entry.submitted_by == 5

    existing = make_sw()
    existing.versions = [SimpleNamespace(oss_key="software/siemens/dup.zip", version="1.0")]
    catalog.entries[existing.id] = existing
    with pytest.raises(CatalogError) as dup:
        use_case.execute("tok", None, "hash")
    assert dup.value.code is CatalogCode.SOFTWARE_FILE_EXISTS
    assert storage.deleted == ["software/siemens/dup.zip"]


def test_direct_upload_init_rejects_duplicate_new_entry():
    catalog = FakeCatalog()
    existing = make_sw(sid=1)
    existing.versions = [SimpleNamespace(oss_key="software/siemens/gen-t.zip", version="1.0")]
    catalog.entries[1] = existing
    use_case = PlanDirectUpload(FakeTags(), catalog, FakeSigner(), FakeCodec({}), "software", 5, 1024, 60)

    with pytest.raises(CatalogError) as dup:
        use_case.execute(SimpleNamespace(id=2, role="member"), filename="t.zip", brand="siemens",
                         category="utility", version="2.0", description="", content_type="application/zip",
                         size=10, software_id=None)
    assert dup.value.code is CatalogCode.SOFTWARE_FILE_EXISTS


def test_direct_upload_init_rejects_duplicate_version():
    catalog = FakeCatalog()
    existing = make_sw(sid=1)
    existing.versions = [SimpleNamespace(oss_key="software/siemens/gen-t.zip", version="1.0")]
    catalog.entries[1] = existing
    use_case = PlanDirectUpload(FakeTags(), catalog, FakeSigner(), FakeCodec({}), "software", 5, 1024, 60)

    with pytest.raises(CatalogError) as dup:
        use_case.execute(SimpleNamespace(id=3, role="admin"), filename="t.zip", brand="siemens",
                         category="utility", version="2.0", description="", content_type="application/zip",
                         size=10, software_id=1)
    assert dup.value.code is CatalogCode.VERSION_FILE_EXISTS


def test_submit_software_records_submitter_and_pending():
    catalog = FakeCatalog()
    storage = FakeStorage()
    import io
    use_case = SubmitSoftwarePackage(tags=FakeTags(), catalog=catalog, storage=storage,
                                     software_prefix="software", max_size_gb=5)
    entry = use_case.execute(io.BytesIO(b"PK"), "a.zip", "application/zip", "siemens",
                             "utility", "1.0", "", SimpleNamespace(id=7, role="admin"))
    assert entry.submitted_by == 7
    assert entry.submission_status == "pending"
    assert catalog.added[0]["submitted_by"] == 7
    assert catalog.added[0]["submission_status"] == "pending"


# ── downloads ─────────────────────────────────────────────────────────────────

def test_download_blocked_by_daily_quota():
    ver = SimpleNamespace(id=1, version="1.0", oss_key="k", is_published=True, is_active=True, download_count=0)
    entry = make_sw(versions=[ver])
    ledger = SimpleNamespace(
        count_today=lambda user_id, resource_type, since: 5,
        record_download=lambda *a: None,
    )
    use_case = IssueSoftwareDownloadLink(FakeCatalog([entry]), ledger, SimpleNamespace(download_url=lambda k: {"url": "u", "expires_in": 1}), now=lambda: NOW, daily_limit=5)
    with pytest.raises(CatalogError) as limited:
        use_case.execute_latest(1, make_user(role="member"), "1.1.1.1")
    assert limited.value.code is CatalogCode.DOWNLOAD_LIMIT_REACHED


def test_download_latest_hidden_version_404_for_member():
    ver = SimpleNamespace(id=1, version="1.0", oss_key="k", is_published=False, is_active=True, download_count=0)
    entry = make_sw(versions=[ver])
    use_case = IssueSoftwareDownloadLink(FakeCatalog([entry]), SimpleNamespace(count_today=lambda *a: 0, record_download=lambda *a: None),
                                         SimpleNamespace(download_url=lambda k: {"url": "u", "expires_in": 1}), now=lambda: NOW, daily_limit=5)
    with pytest.raises(CatalogError) as hidden:
        use_case.execute_latest(1, make_user(role="member"), None)
    assert hidden.value.code is CatalogCode.SW_VERSION_NOT_FOUND


# ── bulk delete ───────────────────────────────────────────────────────────────

def test_bulk_delete_removes_childless_parents():
    ver_a = SimpleNamespace(id=1, oss_key="a", is_published=True, software_id=1)
    ver_b = SimpleNamespace(id=2, oss_key="b", is_published=True, software_id=2)
    sw1 = make_sw(sid=1, versions=[ver_a])
    sw2 = make_sw(sid=2, versions=[ver_b, SimpleNamespace(id=3, oss_key="c", is_published=True, software_id=2)])
    catalog = FakeCatalog([sw1, sw2])
    storage = FakeStorage()

    deleted = BulkDeleteSoftwareVersions(catalog, storage).execute(VersionCriteria(version_ids=[1, 2]))

    assert deleted == 2
    assert storage.deleted == ["a", "b"]
    assert 1 in catalog.deleted and 2 in catalog.deleted
    assert catalog.find(1) is None      # parent left without versions → deleted
    assert catalog.find(2) is not None  # still has version 3 → kept
