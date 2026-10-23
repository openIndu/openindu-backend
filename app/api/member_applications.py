"""Member application API — thin adapter over the membership context."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.modules.membership.application.review import ApplicationView
from app.modules.membership.provider import membership
from app.shared.core.audit import self_audited
from app.shared.core.dependencies import get_db, require_admin, require_auth
from app.shared.core.utils import iso_utc, mask_phone, ok
from app.shared.models.user import User

router = APIRouter()


class ApplyBody(BaseModel):
    note: str | None = None


def _application_payload(view: ApplicationView) -> dict:
    return {
        "id": view.id,
        "user_id": view.id,
        "status": view.status,
        "note": view.note,
        "created_at": iso_utc(view.created_at),
        "reviewed_by": view.reviewed_by,
        "phone": view.phone,
        "nickname": view.nickname,
        "current_role": view.current_role,
    }


# ── 用户侧 ────────────────────────────────────────────────────────────────────

@router.post("/member-applications")
async def apply(body: ApplyBody, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    view = membership(db).apply.execute(user, body.note)
    return ok({
        "id": view.id,
        "status": view.status,
        "created_at": iso_utc(view.created_at),
    }, "申请已提交，等待审核")


@router.get("/member-applications/mine")
async def my_application(db: Session = Depends(get_db), user: User = Depends(require_auth)):
    if not user.member_apply_status:
        return ok(None)
    return ok({
        "id": user.id,
        "status": user.member_apply_status,
        "created_at": iso_utc(user.member_apply_at),
    })


# ── 管理员侧 ──────────────────────────────────────────────────────────────────

@router.get("/admin/member-applications")
async def list_applications(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    q = (
        db.query(User)
        .filter(User.member_apply_status.isnot(None), User.deleted_at.is_(None))
    )
    if status:
        q = q.filter(User.member_apply_status == status)

    # Apply sorting
    sort_column = {
        "created_at": User.member_apply_at,
    }[sort_by]
    if sort_order == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    total = q.count()
    items = q.offset((page - 1) * size).limit(size).all()

    result = [
        {
            "id": u.id,
            "user_id": u.id,
            "status": u.member_apply_status,
            "note": u.member_apply_note,
            "created_at": iso_utc(u.member_apply_at),
            "reviewed_by": u.member_reviewed_by,
            "phone": mask_phone(u.phone),
            "nickname": u.nickname,
            "current_role": u.role,
        }
        for u in items
    ]
    return ok({"items": result, "total": total, "page": page, "size": size})


@router.put("/admin/member-applications/{user_id}/reject")
@self_audited
async def reject(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    view = membership(db).review.execute(user_id, admin.id, approve=False)
    return ok(_application_payload(view), "已驳回")


@router.put("/admin/member-applications/{user_id}/approve")
@self_audited
async def approve(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    view = membership(db).review.execute(user_id, admin.id, approve=True)
    sms_notified = bool(view.sms_notified)
    payload = _application_payload(view)
    payload["sms_notified"] = sms_notified
    return ok(payload, "已通过，用户已升级为会员" + ("，短信通知已发送" if sms_notified else "，短信通知未发出（详见服务日志）"))
