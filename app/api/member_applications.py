"""Member application API — user applies, admin approves."""
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_admin, require_auth
from app.core.utils import iso_utc, mask_phone, ok
from app.models.admin_audit_log import AdminAuditLog
from app.models.user import User
from app.services.sms_service import sms_service

logger = logging.getLogger(__name__)

router = APIRouter()


class ApplyBody(BaseModel):
    note: str | None = None


def _audit(db: Session, admin_id: int, target_user_id: int, action: str, detail: dict | None = None):
    db.add(AdminAuditLog(admin_id=admin_id, target_user_id=target_user_id, action=action, detail=detail or {}))


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ── 用户侧 ────────────────────────────────────────────────────────────────────

@router.post("/member-applications")
async def apply(body: ApplyBody, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    if user.role in ("member", "admin"):
        raise HTTPException(400, "当前账号已是会员或管理员，无需申请")
    if user.member_apply_status == "pending":
        raise HTTPException(400, "已有待审核的申请，请耐心等待")
    # rejected 状态允许重新申请（下方逻辑会覆盖为 pending）
    user.member_apply_status = "pending"
    user.member_apply_note = body.note
    user.member_apply_at = _utcnow()
    user.member_reviewed_by = None
    db.commit()
    db.refresh(user)
    return ok({
        "id": user.id,
        "status": user.member_apply_status,
        "created_at": iso_utc(user.member_apply_at),
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
async def reject(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.member_apply_status != "pending":
        raise HTTPException(400, "该申请已处理或不存在")

    target.member_apply_status = "rejected"
    target.member_reviewed_by = admin.id
    _audit(db, admin.id, target.id, "member_reject", {"user_id": user_id})
    db.commit()
    db.refresh(target)
    return ok({
        "id": target.id,
        "user_id": target.id,
        "status": target.member_apply_status,
        "note": target.member_apply_note,
        "created_at": iso_utc(target.member_apply_at),
        "reviewed_by": target.member_reviewed_by,
        "phone": mask_phone(target.phone),
        "nickname": target.nickname,
        "current_role": target.role,
    }, "已驳回")


@router.put("/admin/member-applications/{user_id}/approve")
async def approve(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if target.member_apply_status != "pending":
        raise HTTPException(400, "该申请已处理或不存在")

    target.role = "member"
    target.member_apply_status = "approved"
    target.member_reviewed_by = admin.id
    _audit(db, admin.id, target.id, "member_approve", {"user_id": user_id})
    db.commit()
    db.refresh(target)

    # Notify the applicant by SMS. The approval is already committed — a
    # failed notice must never roll it back, only surface in the response.
    sms_notified = False
    try:
        sms_notified = sms_service.send_member_approved(target.phone)
    except Exception as exc:
        logger.error("Member-approved SMS failed for user %s (%s): %s", target.id, target.phone, exc)

    return ok({
        "id": target.id,
        "user_id": target.id,
        "status": target.member_apply_status,
        "note": target.member_apply_note,
        "created_at": iso_utc(target.member_apply_at),
        "reviewed_by": target.member_reviewed_by,
        "phone": mask_phone(target.phone),
        "nickname": target.nickname,
        "current_role": target.role,
        "sms_notified": sms_notified,
    }, "已通过，用户已升级为会员" + ("，短信通知已发送" if sms_notified else "，短信通知未发出（详见服务日志）"))
