"""User administration API."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.dependencies import get_db, require_admin
from app.core.err_code import MemberMessages
from app.core.utils import _is_private, ok
from app.membership.application.commands import MembershipCommands
from app.membership.domain.application import MemberError
from app.membership.infrastructure.accounts import SqlMembershipRepository
from app.models.login_session import LoginSession
from app.models.user import User

router = APIRouter(prefix="/users")


class RoleChange(BaseModel):
    role: str


def _commands(db: Session) -> MembershipCommands:
    return MembershipCommands(SqlMembershipRepository(db), lambda _phone: False, datetime.now(UTC).replace(tzinfo=None))


def _error(exc: MemberError) -> HTTPException:
    return HTTPException(MemberMessages.status(exc.code), MemberMessages.message(exc.code))


def _enrich_user_dict(db: Session, user_dict: dict, user_id: int) -> dict:
    """Add online status and last public login IP/location to user dict.

    The session picked is the last session whose ``ip_address`` is a real
    (public) address — private / loopback / docker-bridge IPs are skipped so the
    admin UI doesn't display dev-stack noise (e.g. a login from ``localhost``
    stamping the docker default-gateway IP ``172.19.0.1`` onto the row).
    """
    active_session = db.query(LoginSession).filter(
        LoginSession.user_id == user_id,
        LoginSession.is_active.is_(True),
    ).first()
    user_dict["online"] = active_session is not None

    # Walk most-recent sessions and pick the first non-private IP. Fall back to
    # whatever the latest session has (even private) if every session is local,
    # so the column isn't blank for genuinely-local-only test accounts.
    last_session = db.query(LoginSession).filter(
        LoginSession.user_id == user_id,
    ).order_by(LoginSession.last_active_at.desc()).first()
    fallback_ip = last_session.ip_address if last_session else None
    fallback_location = getattr(last_session, "geo_location", None) if last_session else None

    recent_sessions = db.query(LoginSession).filter(
        LoginSession.user_id == user_id,
    ).order_by(LoginSession.last_active_at.desc()).limit(20).all()
    # Unit tests use MagicMock query chains; keep the helper robust when .all()
    # is not configured to return a real list.
    if not isinstance(recent_sessions, list):
        recent_sessions = []
    chosen_ip = None
    chosen_location = None
    for s in recent_sessions:
        ip = getattr(s, "ip_address", None)
        if ip and not _is_private(ip):
            chosen_ip = ip
            chosen_location = getattr(s, "geo_location", None)
            break
    user_dict["login_ip"] = chosen_ip or fallback_ip
    user_dict["login_location"] = chosen_location or fallback_location

    return user_dict


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
    # Soft-deleted users (deleted_at IS NOT NULL) are hidden by default.
    q = db.query(User).filter(User.deleted_at.is_(None))
    if keyword:
        q = q.filter(User.phone.ilike(f"%{keyword}%"))
    if role:
        q = q.filter(User.role == role)
    if apply_status == "none":
        q = q.filter(User.member_apply_status.is_(None))
    elif apply_status:
        q = q.filter(User.member_apply_status == apply_status)

    # Apply sorting
    sort_column = {
        "created_at": User.created_at,
        "last_login": User.last_login,
    }[sort_by]
    if sort_order == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    total = q.count()
    items = [_enrich_user_dict(db, u.to_dict(), u.id) for u in q.offset((page - 1) * size).limit(size).all()]
    return ok({"items": items, "total": total, "page": page, "size": size})


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
