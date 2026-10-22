"""Member application API — user applies, admin approves."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_admin, require_auth
from app.core.err_code import MemberMessages
from app.core.utils import iso_utc, mask_phone, ok
from app.membership.application.commands import MembershipCommands
from app.membership.application.queries import ApplicationListCriteria, ListMemberApplications
from app.membership.domain.application import MemberCode, MemberError
from app.membership.infrastructure.accounts import SqlMembershipRepository
from app.models.user import User
from app.services.sms_service import sms_service

router = APIRouter()


class ApplyBody(BaseModel):
    note: str | None = None


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _commands(db: Session) -> MembershipCommands:
    return MembershipCommands(SqlMembershipRepository(db), sms_service.send_member_approved, _utcnow())


def _error(exc: MemberError) -> HTTPException:
    return HTTPException(MemberMessages.status(exc.code), MemberMessages.message(exc.code))


# ── 用户侧 ────────────────────────────────────────────────────────────────────

@router.post("/member-applications")
async def apply(body: ApplyBody, db: Session = Depends(get_db), user: User = Depends(require_auth)):
    try:
        _commands(db).apply(user, body.note)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok({
        "id": user.id,
        "status": user.member_apply_status,
        "created_at": iso_utc(user.member_apply_at),
    }, MemberMessages.message(MemberCode.APPLICATION_SUBMITTED))


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
    query_result = ListMemberApplications(SqlMembershipRepository(db)).execute(
        ApplicationListCriteria(page, size, status, sort_order)
    )

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
        for u in query_result["items"]
    ]
    return ok({
        "items": result,
        "total": query_result["total"],
        "page": query_result["page"],
        "size": query_result["size"],
    })


@router.put("/admin/member-applications/{user_id}/reject")
async def reject(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        target = _commands(db).reject(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
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
    }, MemberMessages.message(MemberCode.APPLICATION_REJECTED))


@router.put("/admin/member-applications/{user_id}/approve")
async def approve(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    try:
        target, outcome = _commands(db).approve(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc

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
        "sms_notified": outcome is MemberCode.APPROVED_NOTIFIED,
    }, MemberMessages.message(outcome))
