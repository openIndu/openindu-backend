"""SQL read models for dashboard statistics over visit events, sessions and audit logs."""
from datetime import datetime

from sqlalchemy import func, literal_column
from sqlalchemy.orm import Session

from app.modules.analytics.infrastructure.geo import lookup_point
from app.shared.models.admin_audit_log import AdminAuditLog
from app.shared.models.user import User
from app.shared.models.visit_event import VisitEvent


def cst_day(timestamp):
    """Group a naive UTC database timestamp by its Asia/Shanghai date."""
    return func.date(timestamp + literal_column("INTERVAL '8 hours'"))


def cst_month(timestamp):
    """Group a naive UTC database timestamp by its Asia/Shanghai month."""
    return func.to_char(timestamp + literal_column("INTERVAL '8 hours'"), "YYYY-MM")


def visitor_key():
    """Browser visitor key for UV: client_id first, historical rows by IP."""
    return func.coalesce(VisitEvent.client_id, func.concat("ip:", VisitEvent.ip_address))


def quality_visit_query(db: Session):
    """Count consented page views even when IP geolocation is unavailable."""
    return db.query(VisitEvent).filter(
        VisitEvent.event_type == "page_view",
        VisitEvent.geo_location.is_distinct_from("本地开发"),
    )


def pv_count(db: Session, start: datetime | None = None, end: datetime | None = None) -> int:
    q = quality_visit_query(db)
    if start is not None:
        q = q.filter(VisitEvent.created_at >= start)
    if end is not None:
        q = q.filter(VisitEvent.created_at < end)
    return q.with_entities(func.count(VisitEvent.id)).scalar() or 0


def uv_count(db: Session, start: datetime | None = None, end: datetime | None = None) -> int:
    q = quality_visit_query(db)
    if start is not None:
        q = q.filter(VisitEvent.created_at >= start)
    if end is not None:
        q = q.filter(VisitEvent.created_at < end)
    return q.with_entities(func.count(func.distinct(visitor_key()))).scalar() or 0


def month_new_members_count(db: Session, start: datetime, end: datetime) -> int:
    """Count distinct members approved (AdminAuditLog action=member_approve) in [start, end).

    Source of truth for "when did someone become a member" is the audit-log row
    written at approval time (app/api/member_applications.py's approve()) — NOT
    User.member_apply_at, which is the application timestamp and isn't updated
    on approval, so it can fall in a different month than the actual approval.

    Counts distinct target_user_id, not raw rows: an ordinary demote-then-
    reapprove admin action writes a second member_approve row for the same
    user within the same window, which must not double-count as two members.
    """
    return (
        db.query(func.count(func.distinct(AdminAuditLog.target_user_id)))
        .filter(
            AdminAuditLog.action == "member_approve",
            AdminAuditLog.created_at >= start,
            AdminAuditLog.created_at < end,
        )
        .scalar()
        or 0
    )


def monthly_new_members(
    db: Session, start: datetime, end: datetime, dates: list[str]
) -> list[dict[str, int | str]]:
    """Distinct member approvals per day for the current-month chart."""
    rows = (
        db.query(
            cst_day(AdminAuditLog.created_at).label("day"),
            func.count(func.distinct(AdminAuditLog.target_user_id)).label("cnt"),
        )
        .filter(
            AdminAuditLog.action == "member_approve",
            AdminAuditLog.created_at >= start,
            AdminAuditLog.created_at < end,
        )
        .group_by(cst_day(AdminAuditLog.created_at))
        .order_by(cst_day(AdminAuditLog.created_at))
        .all()
    )
    approve_map = {str(row.day): row.cnt for row in rows}
    return [{"date": day, "count": approve_map.get(day, 0)} for day in dates]


def yearly_registrations(db: Session, year_start_utc: datetime, months: list[tuple[int, int]]) -> list[dict[str, int | str]]:
    """New user signups per month, trailing 12 months — same bucketing as yearly_pv/yearly_uv."""
    rows = (
        db.query(
            cst_month(User.created_at).label("ym"),
            func.count(User.id).label("cnt"),
        )
        .filter(User.created_at >= year_start_utc)
        .group_by("ym")
        .all()
    )
    reg_map = {r.ym: r.cnt for r in rows}
    return [{"date": f"{yy:04d}-{mm:02d}", "count": reg_map.get(f"{yy:04d}-{mm:02d}", 0)} for yy, mm in months]


def yearly_new_members(db: Session, year_start_utc: datetime, months: list[tuple[int, int]]) -> list[dict[str, int | str]]:
    """Distinct members approved per month, trailing 12 months — same bucketing as yearly_pv/yearly_uv.

    Counts distinct target_user_id per month, not raw rows — see
    month_new_members_count for why (demote-then-reapprove writes a second
    member_approve row for the same user).
    """
    rows = (
        db.query(
            cst_month(AdminAuditLog.created_at).label("ym"),
            func.count(func.distinct(AdminAuditLog.target_user_id)).label("cnt"),
        )
        .filter(
            AdminAuditLog.action == "member_approve",
            AdminAuditLog.created_at >= year_start_utc,
        )
        .group_by("ym")
        .all()
    )
    approve_map = {r.ym: r.cnt for r in rows}
    return [{"date": f"{yy:04d}-{mm:02d}", "count": approve_map.get(f"{yy:04d}-{mm:02d}", 0)} for yy, mm in months]


def geo_distribution(db: Session, start: datetime, end: datetime) -> list[dict[str, int | float | str]]:
    """Geo-distribution rows for visit_events in [start, end).

    Anonymous and authenticated visits are counted independently against
    ip_address (anon) / user_id (auth) so the two columns are honest and never
    need subtraction guards. Shared by the dashboard's (month-scoped, for
    back-compat) geo_distribution field and by GET /stats/geo-distribution
    (day/month/year selectable). Per-entry shape: name, country_code, lat, lng,
    visitors, registrations, online, anonymous.
    """
    geo: dict[str, dict[str, int | float | str]] = {}
    anon_geo_rows = (
        db.query(
            VisitEvent.geo_location.label("name"),
            VisitEvent.country_code.label("country_code"),
            func.count(func.distinct(VisitEvent.ip_address)).label("anonymous"),
        )
        .filter(
            VisitEvent.user_id.is_(None),
            VisitEvent.created_at >= start,
            VisitEvent.created_at < end,
        )
        .group_by(VisitEvent.geo_location, VisitEvent.country_code)
        .all()
    )
    for row in anon_geo_rows:
        name = row.name or "未知"
        point = lookup_point(name, row.country_code)
        geo[name] = {
            "name": name,
            "country_code": row.country_code or point["country_code"],
            "lat": point["lat"],
            "lng": point["lng"],
            "visitors": int(row.anonymous or 0),  # legacy field — total dot weight; populated by both buckets below
            "registrations": 0,
            "online": 0,
            "anonymous": int(row.anonymous or 0),
        }

    auth_geo_rows = (
        db.query(
            VisitEvent.geo_location.label("name"),
            VisitEvent.country_code.label("country_code"),
            func.count(func.distinct(VisitEvent.user_id)).label("authenticated"),
        )
        .filter(
            VisitEvent.user_id.isnot(None),
            VisitEvent.created_at >= start,
            VisitEvent.created_at < end,
        )
        .group_by(VisitEvent.geo_location, VisitEvent.country_code)
        .all()
    )
    for row in auth_geo_rows:
        name = row.name or "未知"
        point = lookup_point(name, row.country_code)
        auth_n = int(row.authenticated or 0)
        entry = geo.setdefault(name, {
            "name": name,
            "country_code": row.country_code or point["country_code"],
            "lat": point["lat"],
            "lng": point["lng"],
            "visitors": 0,
            "registrations": 0,
            "online": 0,
            "anonymous": 0,
        })
        entry["online"] = int(entry["online"]) + auth_n  # "online" is the legacy field name the map renders for the auth bucket
        entry["registrations"] = int(entry["registrations"]) + auth_n
        entry["visitors"] = int(entry["visitors"]) + auth_n

    return sorted(geo.values(), key=lambda x: -(int(x["anonymous"]) + int(x["online"])))


