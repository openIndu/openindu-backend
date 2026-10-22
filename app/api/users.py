"""User administration API."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_admin
from app.core.err_code import MemberMessages
from app.core.utils import ok
from app.membership.application.commands import MembershipCommands
from app.membership.application.queries import ListUsers, UserListCriteria
from app.membership.domain.application import MemberError
from app.membership.infrastructure.accounts import SqlMembershipRepository
from app.models.user import User

router = APIRouter(prefix="/users")


class RoleChange(BaseModel):
    role: str


def _commands(db: Session) -> MembershipCommands:
    return MembershipCommands(SqlMembershipRepository(db), lambda _phone: False, datetime.now(UTC).replace(tzinfo=None))


def _error(exc: MemberError) -> HTTPException:
    return HTTPException(MemberMessages.status(exc.code), MemberMessages.message(exc.code))


def _enrich_user_dict(db: Session, user_dict: dict, user_id: int) -> dict:
    """Compatibility adapter retained for existing internal callers."""
    return SqlMembershipRepository(db)._enrich_user(user_dict, user_id)


@router.get("")
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None),
    role: str | None = Query(None),
    apply_status: str | None = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at|last_login)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    criteria = UserListCriteria(page, size, keyword, role, apply_status, sort_by, sort_order)
    return ok(ListUsers(SqlMembershipRepository(db)).execute(criteria))


@router.put("/{user_id}/role")
async def change_role(user_id: int, body: RoleChange, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    commands = _commands(db)
    try:
        user = commands.change_role(user_id, body.role, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok(user.to_dict(), MemberMessages.message(commands.get_admin_outcome("role_change")))


@router.post("/{user_id}/blacklist")
async def blacklist(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    commands = _commands(db)
    try:
        user = commands.blacklist(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok(user.to_dict(), MemberMessages.message(commands.get_admin_outcome("blacklist")))


@router.post("/{user_id}/unblacklist")
async def unblacklist(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    commands = _commands(db)
    try:
        user = commands.unblacklist(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok(user.to_dict(), MemberMessages.message(commands.get_admin_outcome("unblacklist")))


@router.post("/{user_id}/force-logout")
async def force_logout(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    commands = _commands(db)
    try:
        user = commands.force_logout(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok(user.to_dict(), MemberMessages.message(commands.get_admin_outcome("force_logout")))


@router.delete("/{user_id}")
async def delete_user(user_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    """Soft-delete a user: set ``deleted_at`` + invalidate sessions/tokens.

    Associated data (visit_events / download_records / login_sessions /
    admin_audit_log) is preserved for audit history. The user is hidden from
    the admin list and cannot log in (the auth_service login path also
    refuses if ``deleted_at`` is set).
    """
    commands = _commands(db)
    try:
        user = commands.delete_user(user_id, admin.id)
    except MemberError as exc:
        raise _error(exc) from exc
    return ok(user.to_dict(), MemberMessages.message(commands.get_admin_outcome("delete")))
