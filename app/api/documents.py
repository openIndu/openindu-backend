"""Document catalog API — thin adapter over the catalog context."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.catalog.application.documents import DocumentCriteria
from app.modules.catalog.provider import catalog
from app.shared.core.audit import add_audit_detail
from app.shared.core.dependencies import get_current_user, get_db, require_admin, require_member
from app.shared.core.utils import iso_utc, ok
from app.shared.models.document import Document
from app.shared.models.resource_tag import ResourceTag
from app.shared.models.user import User

router = APIRouter(prefix="/documents")


def _valid_values(db: Session, tag_type: str) -> list[str]:
    return [t.value for t in db.query(ResourceTag).filter(ResourceTag.type == tag_type, ResourceTag.is_active == True).all()]  # noqa: E712


@router.get("")
async def list_documents(
    brand: str | None = None,
    category: str | None = None,
    series: str | None = None,
    keyword: str | None = None,
    published_only: bool = False,
    submission_status: str | None = Query(None, regex="^(pending|published|rejected)$"),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("upload_time", regex="^(file_size|upload_time|download_count|brand)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user),
):
    q = db.query(Document)
    if published_only or viewer is None or viewer.role != "admin":
        q = q.filter(Document.is_published == True)  # noqa: E712
    if submission_status:
        q = q.filter(Document.submission_status == submission_status)
    if brand:
        q = q.filter(Document.brand == brand)
    if category:
        q = q.filter(Document.category == category)
    if series:
        q = q.filter(Document.series == series)
    if keyword:
        q = q.filter(Document.original_name.ilike(f"%{keyword}%"))
    total = q.count()

    # Apply sorting
    sort_column = {
        "file_size": Document.file_size,
        "upload_time": Document.upload_time,
        "download_count": Document.download_count,
        "brand": Document.brand,
    }[sort_by]

    if sort_order == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    items = [d.to_dict() for d in q.offset((page - 1) * size).limit(size).all()]
    return ok({"items": items, "total": total, "page": page, "size": size})


@router.post("/upload")
async def upload_document(file: UploadFile = File(...), brand: str = Form(...), category: str = Form(...), series: str = Form(""), description: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_member)):
    doc = await run_in_threadpool(
        catalog(db).submit_document.execute,
        file.file, file.filename, file.content_type, brand, category, series, description, user,
        size_bytes=getattr(file, "size", None),
    )
    return ok(doc.to_dict(), "投稿成功，等待管理员审核")


@router.get("/brands/list")
async def brands(db: Session = Depends(get_db)):
    return ok(_valid_values(db, "doc_brand"))


@router.get("/categories/list")
async def categories(db: Session = Depends(get_db)):
    values = _valid_values(db, "doc_category")
    return ok(values)


# ---------------------------------------------------------------------------
# Member submissions (个人中心投稿)
#
# Members upload documents through the same endpoint as admins, but their
# entries start as submission_status="pending" and stay invisible on the
# portal until an admin reviews them. Members may list and delete their own
# submissions (including published ones) but there is no member edit path.
# ---------------------------------------------------------------------------


@router.get("/my-submissions")
async def my_document_submissions(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(require_member),
):
    rows, total = catalog(db).list_document_submissions.execute(user.id, limit, offset)
    items = [{
        "id": d.id,
        "original_name": d.original_name,
        "brand": d.brand,
        "category": d.category,
        "series": d.series,
        "file_size": d.file_size,
        "description": d.description,
        "submission_status": d.submission_status,
        "rejected_reason": d.rejected_reason,
        "is_published": d.is_published,
        "upload_time": iso_utc(d.upload_time),
    } for d in rows]
    return ok({"items": items, "total": total, "truncated": offset + len(items) < total})


@router.delete("/my-submissions/{doc_id}")
async def delete_my_document_submission(doc_id: int, db: Session = Depends(get_db), user: User = Depends(require_member)):
    catalog(db).withdraw_document.execute(doc_id, user.id)
    return ok(message="删除成功")


class ReviewSubmissionBody(BaseModel):
    action: str  # "publish" | "reject"
    reason: str | None = None


@router.patch("/{doc_id}/review")
async def review_document_submission(doc_id: int, body: ReviewSubmissionBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin review of a member submission: publish it, or reject it with a reason."""
    doc, message = catalog(db).review_document.execute(doc_id, body.action, body.reason)
    add_audit_detail(request, action=body.action, reason=body.reason)
    return ok(doc.to_dict(), message)


@router.get("/{doc_id}")
async def get_document(doc_id: int, db: Session = Depends(get_db), viewer: User | None = Depends(get_current_user)):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc or (not doc.is_published and not (viewer and (viewer.role == "admin" or doc.submitted_by == viewer.id))):
        raise HTTPException(404, "文档不存在")
    return ok(doc.to_dict())


@router.get("/{doc_id}/download-link")
async def get_document_download_link(doc_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_member)):
    return ok(catalog(db).issue_document_link.execute_download(doc_id, current_user, _client_ip(request)))


@router.get("/{doc_id}/preview-link")
async def get_document_preview_link(doc_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_member)):
    """Return an inline presigned URL so the browser renders the PDF in‑page.

    Preview uses a SEPARATE daily quota (``PREVIEW_DAILY_LIMIT``) and does NOT
    consume the download budget nor bump ``download_count`` — previewing is not a
    download. The preview is logged under ``resource_type=document_preview`` so it
    is counted independently and still capped (preview can't bypass the limit).
    """
    return ok(catalog(db).issue_document_link.execute_preview(doc_id, current_user, _client_ip(request)))


class UpdateDocumentBody(BaseModel):
    original_name: str | None = None
    brand: str | None = None
    category: str | None = None
    series: str | None = None
    description: str | None = None


class BulkPublishDocumentsBody(BaseModel):
    ids: list[int] | None = None
    brand: str | None = None
    category: str | None = None
    series: str | None = None
    keyword: str | None = None
    publish: bool = True


@router.patch("/publish/bulk")
async def bulk_publish_documents(body: BulkPublishDocumentsBody, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    criteria = DocumentCriteria(ids=body.ids, brand=body.brand, category=body.category, series=body.series, keyword=body.keyword)
    count = catalog(db).bulk_publish_documents.execute(criteria, body.publish)
    add_audit_detail(request, **body.model_dump(exclude_none=True), count=count)
    return ok({"count": count, "publish": body.publish}, "发布成功" if body.publish else "取消发布成功")


@router.patch("/{doc_id}")
async def update_document(doc_id: int, body: UpdateDocumentBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    doc = catalog(db).update_document.execute(
        doc_id,
        original_name=body.original_name,
        brand=body.brand,
        category=body.category,
        series=body.series,
        description=body.description,
    )
    return ok(doc.to_dict(), "更新成功")


@router.patch("/{doc_id}/publish")
async def toggle_publish(doc_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    doc = catalog(db).toggle_document_publish.execute(doc_id)
    add_audit_detail(request, is_published=doc.is_published)
    return ok(doc.to_dict(), "发布状态已更新")


@router.delete("/{doc_id}")
async def delete_document(doc_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    catalog(db).delete_document.execute(doc_id)
    return ok(message="删除成功")


def _client_ip(request: Request) -> str | None:
    from app.shared.core.utils import real_client_ip
    return real_client_ip(request)
