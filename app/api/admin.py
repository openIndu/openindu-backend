"""Admin management API (audit logs, etc.)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.shared.core.dependencies import get_db, require_admin
from app.shared.core.utils import iso_utc, mask_phone, mask_phones_in_text, ok
from app.shared.models.admin_audit_log import AdminAuditLog
from app.shared.models.user import User

router = APIRouter(prefix="/admin")


@router.get("/audit-logs")
async def audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
    admin_keyword: str | None = Query(None),
    target_keyword: str | None = Query(None),
    action: str | None = Query(None),
    sort_by: str = Query("created_at", regex="^(created_at)$"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    db: Session = Depends(get_db),
    admin_user: User = Depends(require_admin),
):
    from sqlalchemy.orm import aliased
    AdminAlias = aliased(User, name="admin_alias")
    TargetAlias = aliased(User, name="target_alias")

    q = (
        db.query(AdminAuditLog, AdminAlias.phone.label("admin_phone"), TargetAlias.phone.label("target_phone"))
        .join(AdminAlias, AdminAuditLog.admin_id == AdminAlias.id, isouter=True)
        .join(TargetAlias, AdminAuditLog.target_user_id == TargetAlias.id, isouter=True)
    )
    if admin_keyword:
        q = q.filter(AdminAlias.phone.ilike(f"%{admin_keyword}%"))
    if target_keyword:
        q = q.filter(TargetAlias.phone.ilike(f"%{target_keyword}%"))
    if action:
        q = q.filter(AdminAuditLog.action == action)

    # Apply sorting
    sort_column = {
        "created_at": AdminAuditLog.created_at,
    }[sort_by]
    if sort_order == "asc":
        q = q.order_by(sort_column.asc())
    else:
        q = q.order_by(sort_column.desc())

    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()

    items = [{
        "id": log.id,
        "admin_username": mask_phone(admin_phone) or f"ID:{log.admin_id}",
        "target_user": mask_phone(target_phone) or (f"ID:{log.target_user_id}" if log.target_user_id else "-"),
        "action": log.action,
        "detail": mask_phones_in_text(str(log.detail)) if log.detail else "-",
        "created_at": iso_utc(log.created_at),
    } for log, admin_phone, target_phone in rows]

    return ok({"items": items, "total": total, "page": page, "size": page_size})
