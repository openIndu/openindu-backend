"""Catalog context — document and tag use cases."""
import io
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.modules.catalog.application.documents import (
    BulkSetDocumentPublishing,
    DeleteDocument,
    DocumentCriteria,
    IssueDocumentLink,
    ReviewDocumentSubmission,
    SubmitDocument,
    ToggleDocumentPublishing,
    UpdateDocumentEntry,
    WithdrawDocumentSubmission,
)
from app.modules.catalog.application.tags import CreateTag, DeleteTag, UpdateTag
from app.modules.catalog.domain.rules import PENDING, PUBLISHED, REJECTED
from app.shared.core.err_code import ErrorCode
from app.shared.domain.catalog import CatalogCode, CatalogError


def _doc(**overrides):
    base = dict(id=1, brand="siemens", category="manual", series=None, original_name="a.pdf",
                submitted_by=None, submission_status=PUBLISHED, is_published=True,
                rejected_reason=None, download_count=0, oss_key="docs/a.pdf", description=None)
    base.update(overrides)
    return SimpleNamespace(**base)


class FakeTags:
    def __init__(self, brands=("siemens",), categories=("manual",), series_ok=True):
        self.brands = list(brands)
        self.categories = list(categories)
        self.series_ok = series_ok

    def active_values(self, tag_type):
        return {"doc_brand": self.brands, "doc_category": self.categories}.get(tag_type, [])

    def series_exists(self, series, brand, category):
        return self.series_ok


class FakeStorage:
    def __init__(self):
        self.deleted = []
        self.prefix = None
        self.content_type = None

    def upload(self, file_stream, filename, prefix, content_type):
        self.prefix = prefix
        self.content_type = content_type
        return {"oss_key": "docs/x.pdf", "filename": "x.pdf", "file_size": 10, "file_hash": "h"}

    def delete(self, oss_key):
        self.deleted.append(oss_key)


class FakeDocs:
    def __init__(self, docs=(), add_raises=None):
        self.docs = {d.id: d for d in docs}
        self.deleted = []
        self.saved = []
        self.saved_many = None
        self.add_kwargs = None
        self.add_raises = add_raises

    def find(self, doc_id):
        return self.docs.get(doc_id)

    def find_owned(self, doc_id, submitter_id):
        d = self.docs.get(doc_id)
        return d if d is not None and d.submitted_by == submitter_id else None

    def matching(self, criteria, is_published):
        rows = list(self.docs.values())
        if criteria.ids is not None:
            rows = [d for d in rows if d.id in criteria.ids]
        if is_published is not None:
            rows = [d for d in rows if d.is_published == is_published]
        return rows

    def add(self, **kwargs):
        self.add_kwargs = kwargs
        if self.add_raises:
            raise self.add_raises
        doc = _doc(id=len(self.docs) + 100, **{k: v for k, v in kwargs.items() if k in base_keys()})
        self.docs[doc.id] = doc
        return doc

    def save(self, entry):
        self.saved.append(entry)
        return entry

    def save_many(self, entries):
        self.saved_many = list(entries)

    def delete(self, entry, *, actor_id=None):
        self.deleted.append(entry.id)


def base_keys():
    return {"original_name", "brand", "category", "series", "description", "submitted_by", "submission_status"}


class FakeLedger:
    def __init__(self, used=0):
        self.used = used
        self.events = []

    def count_today(self, user_id, resource_type, since):
        self.counted_type = resource_type
        return self.used

    def record_document_download(self, doc, user_id, ip):
        self.events.append(("document", doc.id))
        doc.download_count += 1

    def record_document_preview(self, doc, user_id, ip):
        self.events.append(("document_preview", doc.id))


class FakeSigner:
    def download_url(self, key):
        return {"url": f"/dl/{key}", "expires_in": 60}

    def preview_url(self, key):
        return {"url": f"/pv/{key}", "expires_in": 60}


def _admin():
    return SimpleNamespace(id=1, role="admin")


def _member(uid=7):
    return SimpleNamespace(id=uid, role="member")


NOW = datetime(2026, 10, 22, 12, 0, 0)


# ── SubmitDocument ────────────────────────────────────────────────────────────

def test_submit_document_admin_enters_review_with_owner():
    storage, docs = FakeStorage(), FakeDocs()
    admin = _admin()
    admin_doc = SubmitDocument(FakeTags(), storage, docs, "docs", 50).execute(
        io.BytesIO(b"pdf"), "a.pdf", None, "siemens", "manual", "", "desc", admin)
    assert admin_doc.submission_status == PENDING
    assert admin_doc.submitted_by == admin.id
    assert storage.prefix == "docs/siemens"
    assert storage.content_type == "application/pdf"

    member_doc = SubmitDocument(FakeTags(), storage, FakeDocs(), "docs", 50).execute(
        io.BytesIO(b"pdf"), "a.pdf", "application/pdf", "siemens", "manual", "s7", "desc", _member())
    assert member_doc.submission_status == PENDING
    assert member_doc.submitted_by == 7
    assert member_doc.series == "s7"


def test_submit_document_validations():
    use_case = SubmitDocument(FakeTags(series_ok=False), FakeStorage(), FakeDocs(), "docs", 50)

    with pytest.raises(CatalogError) as exc:
        use_case.execute(io.BytesIO(b"pdf"), "a.pdf", None, "siemens", "manual", "ghost", "", _admin())
    assert exc.value.code == CatalogCode.INVALID_SERIES

    with pytest.raises(CatalogError) as exc:
        use_case.execute(io.BytesIO(b"pdf"), "a.docx", None, "siemens", "manual", "", "", _admin())
    assert exc.value.code == CatalogCode.DOC_UNSUPPORTED_FORMAT

    storage = FakeStorage()
    with pytest.raises(CatalogError) as exc:
        SubmitDocument(FakeTags(), storage, FakeDocs(), "docs", 50).execute(
            io.BytesIO(b"pdf"), "a.pdf", None, "siemens", "manual", "", "", _admin(), size_bytes=51 * 1024 * 1024)
    assert exc.value.code == CatalogCode.FILE_TOO_LARGE
    assert storage.prefix is None  # rejected before any upload side effect


def test_submit_document_sizes_stream_when_size_unknown():
    class Stream:
        def __init__(self, size):
            self._size = size
            self.pos = 0

        def tell(self):
            return self.pos

        def seek(self, pos, whence=0):
            self.pos = self._size + pos if whence == 2 else pos
            return self.pos

    stream = Stream(5)
    docs = FakeDocs()
    SubmitDocument(FakeTags(), FakeStorage(), docs, "docs", 50).execute(
        stream, "a.pdf", None, "siemens", "manual", "", "", _admin())
    assert stream.pos == 0  # stream position restored after sizing
    assert docs.add_kwargs["submission_status"] == PENDING


def test_submit_document_compensates_upload_when_db_fails():
    storage = FakeStorage()
    docs = FakeDocs(add_raises=RuntimeError("db down"))
    with pytest.raises(RuntimeError):
        SubmitDocument(FakeTags(), storage, docs, "docs", 50).execute(
            io.BytesIO(b"pdf"), "a.pdf", None, "siemens", "manual", "", "", _admin())
    assert storage.deleted == ["docs/x.pdf"]


# ── ReviewDocumentSubmission ─────────────────────────────────────────────────

def test_review_document_publish_and_reject():
    doc = _doc(id=5, submission_status=PENDING, is_published=False)
    docs = FakeDocs([doc])
    published, message = ReviewDocumentSubmission(docs).execute(5, "publish", None)
    assert published.is_published is True
    assert published.submission_status == PUBLISHED
    assert published.rejected_reason is None
    assert message == "已发布"

    doc2 = _doc(id=6, submission_status=PENDING, is_published=False)
    docs2 = FakeDocs([doc2])
    rejected, message = ReviewDocumentSubmission(docs2).execute(6, "reject", "  质量不足  ")
    assert rejected.submission_status == REJECTED
    assert rejected.rejected_reason == "质量不足"
    assert message == "已驳回"


def test_review_document_guards():
    docs = FakeDocs([_doc(id=5, submission_status=PUBLISHED, is_published=True)])
    use_case = ReviewDocumentSubmission(docs)

    with pytest.raises(CatalogError) as exc:
        use_case.execute(404, "publish", None)
    assert exc.value.code == CatalogCode.DOC_NOT_FOUND

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, "reject", "   ")
    assert exc.value.code == CatalogCode.REJECT_REASON_REQUIRED

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, "reject", "late")
    assert exc.value.code == CatalogCode.REJECT_PUBLISHED_BLOCKED

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, "delete", None)
    assert exc.value.code == CatalogCode.INVALID_REVIEW_ACTION


# ── WithdrawDocumentSubmission ───────────────────────────────────────────────

def test_withdraw_document_owner_only():
    storage = FakeStorage()
    docs = FakeDocs([_doc(id=5, submitted_by=7)])
    WithdrawDocumentSubmission(docs, storage).execute(5, 7)
    assert storage.deleted == ["docs/a.pdf"]
    assert docs.deleted == [5]

    with pytest.raises(CatalogError) as exc:
        WithdrawDocumentSubmission(docs, storage).execute(5, 8)
    assert exc.value.code == CatalogCode.SUBMISSION_NOT_FOUND

    with pytest.raises(CatalogError) as exc:
        WithdrawDocumentSubmission(docs, storage).execute(999, 7)
    assert exc.value.code == CatalogCode.SUBMISSION_NOT_FOUND


# ── IssueDocumentLink ────────────────────────────────────────────────────────

def test_document_download_bumps_and_logs():
    doc = _doc(id=5)
    ledger = FakeLedger(used=1)
    result = IssueDocumentLink(FakeDocs([doc]), ledger, FakeSigner(), lambda: NOW, 5, 5).execute_download(5, _member(), "9.9.9.9")
    assert result == {"download_url": "/dl/docs/a.pdf", "expires_in": 60, "filename": "a.pdf"}
    assert ledger.counted_type == "document"
    assert ledger.events == [("document", 5)]
    assert doc.download_count == 1


def test_document_download_quota_separate_from_preview():
    docs = FakeDocs([_doc(id=5)])
    ledger = FakeLedger(used=5)
    use_case = IssueDocumentLink(docs, ledger, FakeSigner(), lambda: NOW, 5, 3)

    with pytest.raises(CatalogError) as exc:
        use_case.execute_download(5, _member(), None)
    assert exc.value.code == CatalogCode.DOC_DOWNLOAD_LIMIT_REACHED
    assert ErrorCode.message(exc.value.code) == "今日文档下载次数已用完（5次/天）"
    assert ErrorCode.status(exc.value.code) == 429

    # Downloads maxed out but preview budget (3/day) still has room — and
    # previewing must not bump download_count.
    ledger2 = FakeLedger(used=2)
    doc2 = _doc(id=5)
    result = IssueDocumentLink(FakeDocs([doc2]), ledger2, FakeSigner(), lambda: NOW, 5, 3).execute_preview(5, _member(), None)
    assert result["preview_url"] == "/pv/docs/a.pdf"
    assert ledger2.counted_type == "document_preview"
    assert ledger2.events == [("document_preview", 5)]
    assert doc2.download_count == 0

    with pytest.raises(CatalogError) as exc:
        IssueDocumentLink(docs, FakeLedger(used=3), FakeSigner(), lambda: NOW, 5, 3).execute_preview(5, _member(), None)
    assert exc.value.code == CatalogCode.DOC_PREVIEW_LIMIT_REACHED
    assert ErrorCode.message(exc.value.code, limit=3) == "今日文档预览次数已用完（3次/天）"


def test_document_link_visibility():
    hidden = _doc(id=5, is_published=False, submitted_by=7)
    use_case = IssueDocumentLink(FakeDocs([hidden]), FakeLedger(), FakeSigner(), lambda: NOW, 5, 5)

    with pytest.raises(CatalogError) as exc:
        use_case.execute_download(5, _member(uid=8), None)
    assert exc.value.code == CatalogCode.DOC_NOT_FOUND

    use_case.execute_download(5, _member(uid=7), None)  # owner sees their own
    use_case.execute_download(5, _admin(), None)  # admin sees everything


# ── UpdateDocumentEntry ──────────────────────────────────────────────────────

def test_update_document_brand_change_clears_series_when_not_repeated():
    """Legacy quirk kept deliberately: changing brand/category without echoing
    the series field clears it, because a series is scoped to (brand, category)."""
    doc = _doc(id=5, series="s7")
    brands = FakeTags(brands=("siemens", "mitsubishi"))
    UpdateDocumentEntry(FakeDocs([doc]), brands).execute(5, brand="mitsubishi")
    assert doc.brand == "mitsubishi"
    assert doc.series is None

    doc2 = _doc(id=6, series="s7")
    UpdateDocumentEntry(FakeDocs([doc2]), FakeTags(brands=("siemens", "mitsubishi"), series_ok=True)).execute(6, brand="mitsubishi", series="fx5u")
    assert doc2.brand == "mitsubishi"
    assert doc2.series == "fx5u"


def test_update_document_validations():
    doc = _doc(id=5)
    use_case = UpdateDocumentEntry(FakeDocs([doc]), FakeTags())

    with pytest.raises(CatalogError) as exc:
        use_case.execute(404, original_name="x")
    assert exc.value.code == CatalogCode.DOC_NOT_FOUND

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, brand="unknown")
    assert exc.value.code == CatalogCode.INVALID_BRAND

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, category="unknown")
    assert exc.value.code == CatalogCode.INVALID_CATEGORY

    with pytest.raises(CatalogError) as exc:
        UpdateDocumentEntry(FakeDocs([doc]), FakeTags(series_ok=False)).execute(5, series="ghost")
    assert exc.value.code == CatalogCode.INVALID_SERIES

    with pytest.raises(CatalogError) as exc:
        use_case.execute(5, original_name="   ")
    assert exc.value.code == CatalogCode.DOC_NAME_REQUIRED

    updated = use_case.execute(5, original_name="  b.pdf  ", description="d2")
    assert updated.original_name == "b.pdf"
    assert updated.description == "d2"


# ── Bulk / toggle / delete ───────────────────────────────────────────────────

def test_bulk_publish_documents_flips_only_differing_rows():
    published = _doc(id=1, is_published=True)
    unpublished = _doc(id=2, is_published=False)
    docs = FakeDocs([published, unpublished])
    use_case = BulkSetDocumentPublishing(docs)

    with pytest.raises(CatalogError) as exc:
        use_case.execute(DocumentCriteria(ids=[]), True)
    assert exc.value.code == CatalogCode.NO_DOCUMENTS_SELECTED

    count = use_case.execute(DocumentCriteria(ids=[1, 2]), True)
    assert count == 1  # only the unpublished row differs from the target state
    assert published.is_published is True
    assert docs.saved_many == [unpublished]


def test_toggle_and_delete_document():
    doc = _doc(id=5, is_published=False)
    docs = FakeDocs([doc])
    ToggleDocumentPublishing(docs).execute(5)
    assert doc.is_published is True

    with pytest.raises(CatalogError) as exc:
        ToggleDocumentPublishing(docs).execute(404)
    assert exc.value.code == CatalogCode.DOC_NOT_FOUND

    storage = FakeStorage()
    DeleteDocument(docs, storage).execute(5)
    assert storage.deleted == ["docs/a.pdf"]
    assert docs.deleted == [5]

    with pytest.raises(CatalogError) as exc:
        DeleteDocument(docs, storage).execute(404)
    assert exc.value.code == CatalogCode.DOC_NOT_FOUND


# ── Tags ─────────────────────────────────────────────────────────────────────

class FakeTag:
    def __init__(self, tag_id, tag_type, value, label_zh="L", parent_value=None, brand_value=None):
        self.id = tag_id
        self.type = tag_type
        self.value = value
        self.label_zh = label_zh
        self.parent_value = parent_value
        self.brand_value = brand_value
        self.is_active = True
        self.sort_order = 0
        self.used = False


class FakeBoard:
    def __init__(self, tags=()):
        self.tags = list(tags)
        self.renamed = []
        self.child_deletes = []
        self.deleted = []
        self.saved = []

    def find(self, tag_id):
        return next((t for t in self.tags if t.id == tag_id), None)

    def exists(self, tag_type, value, parent_value, scoped):
        return any(
            t.type == tag_type and t.value == value and (not scoped or t.parent_value == parent_value)
            for t in self.tags
        )

    def create(self, **kw):
        tag = FakeTag(len(self.tags) + 1, kw["tag_type"], kw["value"], kw["label_zh"], kw["parent_value"], kw["brand_value"])
        tag.sort_order = kw["sort_order"]
        self.tags.append(tag)
        return tag

    def rename_references(self, old_value, new_value, tag_type):
        self.renamed.append((old_value, new_value, tag_type))

    def in_use(self, tag):
        return tag.used

    def delete_child_series(self, parent_value):
        self.child_deletes.append(parent_value)

    def save(self, tag):
        self.saved.append(tag)
        return tag

    def delete(self, tag):
        self.deleted.append(tag.id)


def test_create_tag_strips_and_scopes_uniqueness():
    board = FakeBoard([FakeTag(1, "doc_brand", "siemens")])
    tag = CreateTag(board).execute(tag_type="sw_brand", value="  omron  ", label_zh=" 欧姆龙 ", sort_order=3)
    assert tag.value == "omron"
    assert tag.label_zh == "欧姆龙"
    assert tag.sort_order == 3

    # sw_brand uniqueness is scoped to parent_value (None here) — "siemens"
    # exists under no parent but with a different type, so creating a sw_brand
    # "omron" again must collide.
    with pytest.raises(CatalogError) as exc:
        CreateTag(board).execute(tag_type="sw_brand", value="omron", label_zh="x")
    assert exc.value.code == CatalogCode.TAG_ALREADY_EXISTS
    assert exc.value.params == {"tag_type": "sw_brand", "value": "omron"}
    assert ErrorCode.message(exc.value.code, **exc.value.params) == "标签已存在：type=sw_brand, value=omron"
    assert ErrorCode.status(exc.value.code) == 409

    # doc_series values are globally unique (parent scope ignored).
    board2 = FakeBoard([FakeTag(1, "doc_series", "s7", parent_value="manual", brand_value="siemens")])
    with pytest.raises(CatalogError) as exc:
        CreateTag(board2).execute(tag_type="doc_series", value="s7", label_zh="x", parent_value="other", brand_value="other")
    assert exc.value.code == CatalogCode.TAG_ALREADY_EXISTS


def test_create_tag_guards():
    board = FakeBoard()

    with pytest.raises(CatalogError) as exc:
        CreateTag(board).execute(tag_type="sw_series", value="x", label_zh="x")
    assert exc.value.code == CatalogCode.TAG_TYPE_INVALID
    assert exc.value.params["options"] == ", ".join(sorted(["doc_brand", "sw_brand", "doc_category", "sw_category", "doc_series"]))
    assert ErrorCode.message(exc.value.code, **exc.value.params).startswith("无效 type，可选：doc_brand, ")

    with pytest.raises(CatalogError) as exc:
        CreateTag(board).execute(tag_type="doc_brand", value="  ", label_zh=" ")
    assert exc.value.code == CatalogCode.TAG_VALUE_AND_LABEL_REQUIRED

    with pytest.raises(CatalogError) as exc:
        CreateTag(board).execute(tag_type="doc_series", value="s7", label_zh="x", parent_value="manual")
    assert exc.value.code == CatalogCode.TAG_SERIES_PARENT_REQUIRED


def test_update_tag_renames_with_cascade():
    board = FakeBoard([FakeTag(1, "doc_brand", "siemens")])
    tag = UpdateTag(board).execute(1, value="  mitsubishi  ", label_zh="三菱", is_active=False, sort_order=2)
    assert tag.value == "mitsubishi"
    assert tag.label_zh == "三菱"
    assert tag.is_active is False
    assert tag.sort_order == 2
    assert board.renamed == [("siemens", "mitsubishi", "doc_brand")]

    with pytest.raises(CatalogError) as exc:
        UpdateTag(board).execute(404, value="x")
    assert exc.value.code == CatalogCode.TAG_NOT_FOUND

    with pytest.raises(CatalogError) as exc:
        UpdateTag(board).execute(1, value="   ")
    assert exc.value.code == CatalogCode.TAG_VALUE_REQUIRED

    with pytest.raises(CatalogError) as exc:
        UpdateTag(board).execute(1, label_zh=" ")
    assert exc.value.code == CatalogCode.TAG_LABEL_REQUIRED


def test_delete_tag_guards_and_child_series_cleanup():
    used = FakeTag(1, "doc_brand", "siemens")
    used.used = True
    with pytest.raises(CatalogError) as exc:
        DeleteTag(FakeBoard([used])).execute(1)
    assert exc.value.code == CatalogCode.TAG_IN_USE
    assert ErrorCode.message(exc.value.code) == "该标签仍有文档或软件在使用，无法删除"

    board = FakeBoard([FakeTag(2, "doc_category", "manual")])
    DeleteTag(board).execute(2)
    assert board.child_deletes == ["manual"]
    assert board.deleted == [2]

    board2 = FakeBoard([FakeTag(3, "doc_series", "s7")])
    DeleteTag(board2).execute(3)
    assert board2.child_deletes == []  # only doc_category cascades
    assert board2.deleted == [3]

    with pytest.raises(CatalogError) as exc:
        DeleteTag(FakeBoard()).execute(404)
    assert exc.value.code == CatalogCode.TAG_NOT_FOUND
