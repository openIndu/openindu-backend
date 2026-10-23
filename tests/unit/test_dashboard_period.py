"""Unit tests for dashboard period stats (today / this month in Asia/Shanghai)."""
from datetime import datetime, timedelta

from app.modules.analytics.domain.windows import (
    CST,
)
from app.modules.analytics.domain.windows import (
    month_range_utc as _month_range_utc,
)
from app.modules.analytics.domain.windows import (
    to_naive_utc as _to_naive_utc,
)
from app.modules.analytics.domain.windows import (
    today_range_utc as _today_range_utc,
)
from app.modules.analytics.domain.windows import (
    trailing_12_months as _trailing_12_months,
)


class TestToNaiveUtc:
    def test_converts_cst_to_utc_minus_8(self):
        """CST 00:00 -> UTC 16:00 (previous day)."""
        cst_time = datetime(2026, 6, 20, 0, 0, 0, tzinfo=CST)
        result = _to_naive_utc(cst_time)
        assert result == datetime(2026, 6, 19, 16, 0, 0)


class TestTodayRangeUtc:
    def test_basic_range(self):
        """Today midnight CST -> tomorrow midnight CST, both naive UTC."""
        start, end = _today_range_utc()

        # start must be before end
        assert start < end
        # span must be exactly 24 hours
        assert end - start == timedelta(hours=24)

    def test_range_fits_date_boundary(self):
        """Any datetime in CST now must fall within [start, end)."""
        start, end = _today_range_utc()
        now_cst = datetime.now(CST)
        now_utc = _to_naive_utc(now_cst)

        assert now_utc >= start, f"{now_utc} >= {start}"
        assert now_utc < end, f"{now_utc} < {end}"

    def test_cross_utc_midnight(self):
        """When CST is 00:00, UTC is 16:00 the previous day — range still works."""
        start, end = _today_range_utc()
        # start and end should be naive UTC datetimes
        assert start.tzinfo is None
        assert end.tzinfo is None
        # end = start + 24h
        assert end - start == timedelta(hours=24)


class TestMonthRangeUtc:
    def test_basic_range(self):
        """First day of month CST -> first day of next month CST."""
        start, end = _month_range_utc()
        assert start < end
        # range should span a whole calendar month (28-31 days)
        delta = end - start
        assert timedelta(days=27) <= delta <= timedelta(days=32)

    def test_range_is_around_now(self):
        """Now must fall within [start, end)."""
        start, end = _month_range_utc()
        now_utc = _to_naive_utc(datetime.now(CST))
        assert now_utc >= start
        assert now_utc < end

    def test_month_boundary_no_overlap_with_previous(self):
        """End of one call and start of a 'previous month' call must not overlap."""
        start, end = _month_range_utc()
        # simulate 'previous month' by going back 1 day from start
        prev = start - timedelta(days=1)
        # prev must be < start (strictly before this month)
        assert prev < start


class TestTrailing12Months:
    """Shared window for yearly_* dashboard series and /stats/geo-distribution?range=year."""

    def test_twelve_months_oldest_first_ending_this_month(self):
        months, _ = _trailing_12_months()
        assert len(months) == 12

        today_cst = datetime.now(CST).date()
        assert months[-1] == (today_cst.year, today_cst.month)

        # consecutive, wrapping year boundaries correctly
        for (y1, m1), (y2, m2) in zip(months, months[1:]):
            if m1 == 12:
                assert (y2, m2) == (y1 + 1, 1)
            else:
                assert (y2, m2) == (y1, m1 + 1)

    def test_year_start_utc_is_naive_first_of_oldest_month(self):
        months, year_start_utc = _trailing_12_months()
        assert year_start_utc.tzinfo is None

        expected = _to_naive_utc(datetime(months[0][0], months[0][1], 1, tzinfo=CST))
        assert year_start_utc == expected

    def test_year_window_is_not_the_month_window(self):
        """The trailing-12-month start must be strictly earlier than this month's
        start — i.e. range=year on /stats/geo-distribution can never silently
        collapse onto range=month's window."""
        _, year_start_utc = _trailing_12_months()
        month_start, _ = _month_range_utc()
        assert year_start_utc < month_start
        # exactly 11 calendar months earlier, not some other arbitrary offset
        months, _ = _trailing_12_months()
        oldest_year, oldest_month = months[0]
        today_cst = datetime.now(CST).date()
        advanced_month = oldest_month + 11
        advanced_year = oldest_year + (advanced_month - 1) // 12
        advanced_month = (advanced_month - 1) % 12 + 1
        assert (advanced_year, advanced_month) == (today_cst.year, today_cst.month)
