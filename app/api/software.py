"""Software package and version API — thin adapter over the catalog context."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.catalog.application.admin import VersionCriteria
from app.catalog.provider import catalog
from app.core.dependencies import get_current_user, get_db, require_admin, require_member
from app.core.utils import iso_utc, ok
from app.models.resource_tag import ResourceTag
from app.models.software import Software, SoftwareVersion
from app.models.user import User

router = APIRouter(prefix="/software")


def _ip(request: Request):
    from app.core.utils import real_client_ip
    return real_client_ip(request)


def _valid_values(db: Session, tag_type: str) -> list[str]:
    return [t.value for t in db.query(ResourceTag).filter(ResourceTag.type == tag_type, ResourceTag.is_active == True).all()]  # noqa: E712


@router.get("")
async def list_software(
    brand: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    published_only: bool = False,
    submission_status: str | None = Query(None, regex="^(pending|published|rejected)$"),
    expand_versions: bool = False,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    sort_by: str = Query("upload_time", regex="^(file_size|upload_time|download_count|brand)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    viewer: User | None = Depends(get_current_user),
):
    public_view = viewer is None or viewer.role != "admin"
    if expand_versions:
        # Version-grained listing: one row per SoftwareVersion. Publish state
        # and download counts come from the version, not the parent software.
        vq = db.query(SoftwareVersion).join(Software, SoftwareVersion.software_id == Software.id).filter(
            Software.is_active.is_(True), SoftwareVersion.is_active.is_(True),
        )
        if published_only or public_view:
            vq = vq.filter(SoftwareVersion.is_published == True)  # noqa: E712
        if submission_status:
            vq = vq.filter(Software.submission_status == submission_status)
        if brand: vq = vq.filter(Software.brand == brand)
        if category: vq = vq.filter(Software.category == category)
        if keyword:
            # Match either the per-version name (preferred) or the parent software name.
            vq = vq.filter((SoftwareVersion.original_name.ilike(f"%{keyword}%")) | (Software.original_name.ilike(f"%{keyword}%")))
        total = vq.count()

        # Apply sorting for version-grained listing.
        # "brand" sorts by Chinese label — do it in Python because the
        # join-based approach conflicts with SQLAlchemy query structure.
        reverse = sort_order == "desc"
        if sort_by == "brand":
            # Load brand → Chinese name mapping
            brand_tags = db.query(ResourceTag).filter(ResourceTag.type == "sw_brand").all()
            brand_label = {t.value: t.label_zh for t in brand_tags}
            all_rows = vq.all()
            all_rows.sort(
                key=lambda v: brand_label.get(v.software.brand, v.software.brand),
                reverse=reverse,
            )
            rows = all_rows[(page - 1) * size : page * size]
        else:
            sort_col = {
                "file_size": SoftwareVersion.file_size,
                "upload_time": SoftwareVersion.upload_time,
                "download_count": SoftwareVersion.download_count,
            }[sort_by]
            if reverse:
                vq = vq.order_by(sort_col.desc())
            else:
                vq = vq.order_by(sort_col.asc())
            rows = vq.offset((page - 1) * size).limit(size).all()

        items = []
        for v in rows:
            s = v.software
            items.append({
                "id": s.id,
                "filename": v.oss_key.rsplit("/", 1)[-1],
                "original_name": v.original_name or s.original_name,
                "brand": s.brand,
                "category": s.category,
                "description": s.description,
                "created_at": iso_utc(s.created_at),
                "version_id": v.id,
                "version": v.version,
                "latest_version_size": v.file_size,
                "file_hash": v.file_hash,
                "oss_key": v.oss_key,
                "version_upload_time": iso_utc(v.upload_time),
                "download_count": v.download_count,
                "version_download_count": v.download_count,
                "is_published": v.is_published,
                "submission_status": s.submission_status,
                "rejected_reason": s.rejected_reason,
                "is_latest_version": v.version == s.latest_version,
            })
        return ok({"items": items, "total": total, "page": page, "size": size})

    q = db.query(Software).filter(Software.is_active.is_(True))
    if published_only or public_view:
        # When the caller doesn't want expansion, treat the software as
        # published if any of its versions is published — keeps the legacy
        # collapsed view useful even though publish lives on the version now.
        published_ids = db.query(SoftwareVersion.software_id).filter(SoftwareVersion.is_published == True).distinct()  # noqa: E712
        q = q.filter(Software.id.in_(published_ids))
    if submission_status:
        q = q.filter(Software.submission_status == submission_status)
    if brand: q = q.filter(Software.brand == brand)
    if category: q = q.filter(Software.category == category)
    if keyword: q = q.filter(Software.original_name.ilike(f"%{keyword}%"))
    total = q.count()
    rows = q.order_by(Software.created_at.desc()).offset((page - 1) * size).limit(size).all()
    items = [s.to_dict() for s in rows]
    return ok({"items": items, "total": total, "page": page, "size": size})


@router.post("/upload")
async def upload_software(file: UploadFile = File(...), brand: str = Form(...), category: str = Form(...), version: str = Form(...), description: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_member)):
    ctx = catalog(db)
    sw = await run_in_threadpool(
        ctx.submit_package.execute,
        file.file, file.filename, file.content_type, brand, category, version, description, user,
        size_bytes=getattr(file, "size", None),
    )
    return ok(sw.to_dict(include_versions=True), "上传成功" if sw.submission_status == "published" else "投稿成功，等待管理员审核")


# ---------------------------------------------------------------------------
# Direct-to-OSS upload (large software packages, several GB)
#
# Browser uploads the file body straight to OSS via presigned URLs, bypassing
# the backend entirely. The backend only signs the upload and records metadata
# afterwards — no large body passes through FastAPI/nginx, so there is no
# event-loop blocking, no memory pressure, and no nginx body/timeout limits.
# ---------------------------------------------------------------------------


class UploadInitBody(BaseModel):
    filename: str
    brand: str
    category: str
    version: str
    description: str = ""
    content_type: str | None = None
    size: int  # bytes
    software_id: int | None = None  # 给已有软件加版本时传入


class UploadPartResult(BaseModel):
    part_number: int
    etag: str


class UploadCompleteBody(BaseModel):
    token: str
    parts: list[UploadPartResult] | None = None
    file_hash: str | None = None


class UploadAbortBody(BaseModel):
    token: str


@router.post("/upload/init")
async def upload_init(body: UploadInitBody, db: Session = Depends(get_db), user: User = Depends(require_member)):
    ctx = catalog(db)
    payload = await run_in_threadpool(
        ctx.plan_direct_upload.execute,
        user,
        filename=body.filename,
        brand=body.brand,
        category=body.category,
        version=body.version,
        description=body.description,
        content_type=body.content_type,
        size=body.size,
        software_id=body.software_id,
    )
    if payload["mode"] == "sync":
        return ok(payload, "使用同步上传")
    return ok(payload, "凭证已签发")


@router.post("/upload/complete")
async def upload_complete(body: UploadCompleteBody, db: Session = Depends(get_db), user: User = Depends(require_member)):
    ctx = catalog(db)
    parts = [(p.part_number, p.etag) for p in body.parts] if body.parts else None
    sw, message = await run_in_threadpool(ctx.complete_direct_upload.execute, body.token, parts, body.file_hash)
    return ok(sw.to_dict(include_versions=True), message)


@router.post("/upload/abort")
async def upload_abort(body: UploadAbortBody, db: Session = Depends(get_db), user: User = Depends(require_member)):
    ctx = catalog(db)
    await run_in_threadpool(ctx.abort_direct_upload.execute, body.token)
    return ok(message="已取消")


# ---------------------------------------------------------------------------
# Member submissions (个人中心投稿)
#
# Members upload through the same pipeline as admins, but their entries start
# as submission_status="pending" and stay invisible on the portal until an
# admin reviews them. Members may list and delete their own submissions
# (including published ones) but there is deliberately no member edit path.
# ---------------------------------------------------------------------------


@router.get("/my-submissions")
async def my_software_submissions(db: Session = Depends(get_db), user: User = Depends(require_member)):
    rows = db.query(Software).filter(Software.submitted_by == user.id, Software.is_active.is_(True)).order_by(Software.created_at.desc()).all()
    items = []
    for s in rows:
        size = None
        if s.latest_version:
            for v in s.versions:
                if v.version == s.latest_version:
                    size = v.file_size
                    break
        items.append({
            "id": s.id,
            "original_name": s.original_name,
            "brand": s.brand,
            "category": s.category,
            "version": s.latest_version,
            "file_size": size,
            "description": s.description,
            "submission_status": s.submission_status,
            "rejected_reason": s.rejected_reason,
            "is_published": s.is_published,
            "created_at": iso_utc(s.created_at),
        })
    return ok({"items": items})


@router.delete("/my-submissions/{software_id}")
async def delete_my_software_submission(software_id: int, db: Session = Depends(get_db), user: User = Depends(require_member)):
    catalog(db).withdraw_submission.execute(software_id, user.id)
    return ok(message="删除成功")


class ReviewSubmissionBody(BaseModel):
    action: str  # "publish" | "reject"
    reason: str | None = None


@router.patch("/{software_id}/review")
async def review_software_submission(software_id: int, body: ReviewSubmissionBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Admin review of a member submission: publish it, or reject it with a reason."""
    sw, message = catalog(db).review_submission.execute(software_id, body.action, body.reason)
    if body.action == "publish":
        return ok(sw.to_dict(include_versions=True), message)
    return ok(sw.to_dict(), message)


@router.get("/brands/list")
async def brands(db: Session = Depends(get_db)):
    return ok(_valid_values(db, "sw_brand"))


@router.get("/categories/list")
async def categories(db: Session = Depends(get_db)):
    return ok(_valid_values(db, "sw_category"))


@router.get("/{software_id}")
async def get_software(software_id: int, db: Session = Depends(get_db), viewer: User | None = Depends(get_current_user)):
    sw = db.query(Software).filter(Software.id == software_id).first()
    if not sw: raise HTTPException(404, "软件不存在")
    can_review = viewer and (viewer.role == "admin" or sw.submitted_by == viewer.id)
    if not sw.is_published and not can_review:
        raise HTTPException(404, "软件不存在")
    data = sw.to_dict(include_versions=True)
    if not can_review:
        data["versions"] = [v.to_dict() for v in sw.versions if v.is_published]
    return ok(data)


@router.get("/{software_id}/download-link")
async def download_latest(software_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_member)):
    return ok(catalog(db).issue_download_link.execute_latest(software_id, user, _ip(request)))


@router.get("/{software_id}/versions/{version_id}/download-link")
async def download_version(software_id: int, version_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_member)):
    return ok(catalog(db).issue_download_link.execute_version(software_id, version_id, user, _ip(request)))


@router.post("/{software_id}/versions")
async def add_version(software_id: int, file: UploadFile = File(...), version: str = Form(...), db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    ctx = catalog(db)
    ver = await run_in_threadpool(
        ctx.add_version.execute,
        software_id, file.file, file.filename, file.content_type, version,
    )
    return ok(ver.to_dict(), "版本已添加")


@router.delete("/{software_id}/versions/{version_id}")
async def delete_version(software_id: int, version_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    catalog(db).delete_version.execute(software_id, version_id)
    return ok(message="删除成功")


class UpdateSoftwareBody(BaseModel):
    original_name: str | None = None
    brand: str | None = None
    category: str | None = None
    description: str | None = None


@router.patch("/{software_id}")
async def update_software(software_id: int, body: UpdateSoftwareBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    sw = catalog(db).update_entry.execute(
        software_id,
        original_name=body.original_name,
        brand=body.brand,
        category=body.category,
        description=body.description,
    )
    return ok(sw.to_dict(), "更新成功")


class BulkPublishSoftwareBody(BaseModel):
    ids: list[int] | None = None  # software ids — kept for backward compat (publishes all versions of those softwares)
    version_ids: list[int] | None = None
    brand: str | None = None
    category: str | None = None
    keyword: str | None = None
    publish: bool = True


class BulkDeleteSoftwareBody(BaseModel):
    version_ids: list[int] | None = None
    ids: list[int] | None = None
    brand: str | None = None
    category: str | None = None
    keyword: str | None = None


@router.patch("/publish/bulk")
async def bulk_publish_software(body: BulkPublishSoftwareBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    # Publish is per-version now. Callers pass version_ids (preferred) or the
    # old ids[] (= software_ids; applies to ALL versions of those softwares).
    criteria = VersionCriteria(
        version_ids=body.version_ids, software_ids=body.ids,
        brand=body.brand, category=body.category, keyword=body.keyword,
    )
    count = catalog(db).bulk_publish.execute(criteria, body.publish)
    return ok({"count": count, "publish": body.publish}, "发布成功" if body.publish else "取消发布成功")


@router.delete("/bulk")
async def bulk_delete_software(body: BulkDeleteSoftwareBody, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Bulk-delete software versions (and parent software if no versions remain)."""
    criteria = VersionCriteria(
        version_ids=body.version_ids, software_ids=body.ids,
        brand=body.brand, category=body.category, keyword=body.keyword,
    )
    deleted = catalog(db).bulk_delete_versions.execute(criteria)
    if deleted == 0:
        return ok({"count": 0}, "没有要删除的版本")
    return ok({"count": deleted}, f"已删除 {deleted} 个版本")


@router.patch("/{software_id}/publish")
async def toggle_publish(software_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Legacy: toggle publish on ALL versions of a software.

    Kept so the older single-row toggle button keeps working — it now flips
    every version of the software to the opposite of whatever the majority
    is, then mirrors that state onto Software.is_published for compatibility.
    """
    sw = catalog(db).toggle_publish.execute(software_id)
    return ok(sw.to_dict(), "发布状态已更新")


@router.patch("/{software_id}/versions/{version_id}/publish")
async def toggle_version_publish(software_id: int, version_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    v = catalog(db).toggle_version_publish.execute(software_id, version_id)
    return ok(v.to_dict(), "发布状态已更新")


@router.delete("/{software_id}")
async def delete_software(software_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    catalog(db).delete_software.execute(software_id)
    return ok(message="删除成功")
