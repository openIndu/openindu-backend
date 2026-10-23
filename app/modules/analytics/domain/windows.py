"""Reporting windows on the Asia/Shanghai calendar, as naive-UTC DB ranges."""
from datetime import UTC, datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))  # Asia/Shanghai


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def to_naive_utc(dt: datetime) -> datetime:
    """Convert a timezone-aware datetime to naive UTC for DB comparison."""
    return dt.astimezone(UTC).replace(tzinfo=None)


def today_range_utc() -> tuple[datetime, datetime]:
    """Return [today 00:00 CST, tomorrow 00:00 CST) as naive UTC."""
    now_cst = datetime.now(CST)
    start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
    end_cst = start_cst + timedelta(days=1)
    return (to_naive_utc(start_cst), to_naive_utc(end_cst))


def month_range_utc() -> tuple[datetime, datetime]:
    """Return [1st of this month 00:00 CST, 1st of next month 00:00 CST) as naive UTC."""
    now_cst = datetime.now(CST)
    start_cst = now_cst.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_cst.month == 12:
        end_cst = start_cst.replace(year=start_cst.year + 1, month=1)
    else:
        end_cst = start_cst.replace(month=start_cst.month + 1)
    return (to_naive_utc(start_cst), to_naive_utc(end_cst))


def last_30_days_range_utc() -> tuple[datetime, datetime]:
    """Return the last 30 CST calendar days, including today, through now."""
    now = now_utc()
    today_start, _ = today_range_utc()
    return today_start - timedelta(days=29), now


def trailing_12_months() -> tuple[list[tuple[int, int]], datetime]:
    """Return (months, year_start_utc) for the trailing 12 months ending this month (CST).

    ``months`` is oldest-first [(year, month), ...], length 12. ``year_start_utc``
    is the 1st of the oldest month, 00:00 CST, as naive UTC. This is the single
    shared window used by every yearly_* series on the dashboard and by
    GET /stats/geo-distribution?range=year — compute it once, reuse it, never
    re-derive it independently.
    """
    today_cst = datetime.now(CST).date()
    months: list[tuple[int, int]] = []
    y, m = today_cst.year, today_cst.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    year_start_cst = datetime(months[0][0], months[0][1], 1, tzinfo=CST)
    return months, to_naive_utc(year_start_cst)
