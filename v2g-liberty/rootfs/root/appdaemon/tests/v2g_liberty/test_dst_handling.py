"""Tests for correct DST (Daylight Saving Time) handling.

Verifies that timezone offset calculations are correct across DST boundaries,
particularly for price fetching where incorrect offsets caused flatline prices.

Root cause: pytz preserved the current UTC offset when computing historical
datetimes via timedelta arithmetic, resulting in wrong API query windows.
Migration to zoneinfo fixes this as timedelta operations are DST-aware.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apps.v2g_liberty.v2g_globals import time_floor, time_ceil, time_mod


# Europe/Amsterdam DST 2026:
# Spring forward: last Sunday of March = 29 March 2026, 02:00 CET → 03:00 CEST
# Fall back: last Sunday of October = 25 October 2026, 03:00 CEST → 02:00 CET
AMS = ZoneInfo("Europe/Amsterdam")


class TestTimeFloorDST:
    """Test time_floor across DST boundaries."""

    def test_time_floor_after_spring_forward_for_pre_dst_date(self):
        """After spring-forward, time_floor for a pre-DST date must use CET (+01:00).

        This was the root cause of the flatline price bug: pytz preserved +02:00
        for dates that were still in CET.
        """
        # It's 23:35 CEST on March 29 (after DST transition)
        now_cest = datetime(2026, 3, 29, 23, 35, tzinfo=AMS)
        assert now_cest.utcoffset() == timedelta(hours=2)  # CEST

        # Go back 1 day and floor to midnight
        one_day_back = now_cest - timedelta(days=1)
        result = time_floor(one_day_back, timedelta(days=1))

        # March 28 midnight should be CET (+01:00), not CEST (+02:00)
        assert result.utcoffset() == timedelta(hours=1), (
            f"Expected CET (+01:00) but got {result.utcoffset()}"
        )
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 28
        assert result.hour == 0
        assert result.minute == 0

    def test_time_floor_on_dst_day_itself(self):
        """Floor to midnight on the DST transition day (March 29)."""
        # 15:00 CEST on March 29 (after the 02:00→03:00 transition)
        now = datetime(2026, 3, 29, 15, 0, tzinfo=AMS)
        result = time_floor(now, timedelta(days=1))

        # March 29 midnight was still CET (transition is at 02:00)
        assert result.day == 29
        assert result.hour == 0
        assert result.utcoffset() == timedelta(hours=1)  # CET before transition

    def test_time_floor_normal_summer_day(self):
        """Floor on a normal summer day should give CEST (+02:00)."""
        now = datetime(2026, 7, 15, 14, 30, tzinfo=AMS)
        result = time_floor(now, timedelta(days=1))

        assert result.day == 15
        assert result.hour == 0
        assert result.utcoffset() == timedelta(hours=2)  # CEST

    def test_time_floor_normal_winter_day(self):
        """Floor on a normal winter day should give CET (+01:00)."""
        now = datetime(2026, 1, 15, 14, 30, tzinfo=AMS)
        result = time_floor(now, timedelta(days=1))

        assert result.day == 15
        assert result.hour == 0
        assert result.utcoffset() == timedelta(hours=1)  # CET

    def test_time_floor_after_fall_back_for_pre_dst_date(self):
        """After fall-back, time_floor for a pre-DST (summer) date must use CEST."""
        # October 26 (after fall-back on Oct 25), 10:00 CET
        now_cet = datetime(2026, 10, 26, 10, 0, tzinfo=AMS)
        assert now_cet.utcoffset() == timedelta(hours=1)  # CET

        # Go back 2 days and floor to midnight
        two_days_back = now_cet - timedelta(days=2)
        result = time_floor(two_days_back, timedelta(days=1))

        # October 24 midnight should be CEST (+02:00)
        assert result.day == 24
        assert result.hour == 0
        assert result.utcoffset() == timedelta(hours=2)  # CEST


class TestTimeCeilDST:
    """Test time_ceil across DST boundaries."""

    def test_time_ceil_before_spring_forward(self):
        """Ceil to midnight on the morning of DST spring-forward."""
        # March 28 at 23:30 CET (before spring-forward on March 29)
        now = datetime(2026, 3, 28, 23, 30, tzinfo=AMS)
        result = time_ceil(now, timedelta(days=1))

        # Next midnight (March 29 00:00) is still CET
        assert result.day == 29
        assert result.hour == 0
        assert result.utcoffset() == timedelta(hours=1)  # CET


class TestPriceFetchStartTimeDST:
    """Test that the price fetch start time is correct during DST transitions.

    This simulates the calculation done in price_fetcher.py:
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))
    """

    def test_price_fetch_start_after_spring_forward(self):
        """Simulate price fetch at 13:35 CEST on March 30 (day after DST).

        days_back=1, so start should be March 29 00:00 CET (+01:00).
        """
        now = datetime(2026, 3, 30, 13, 35, tzinfo=AMS)
        days_back = 1
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))

        assert start == datetime(2026, 3, 29, 0, 0, tzinfo=AMS)
        # March 29 midnight is CET (before the 02:00→03:00 transition)
        assert start.utcoffset() == timedelta(hours=1)

        # Verify UTC equivalent is correct
        start_utc = start.astimezone(timezone.utc)
        assert start_utc == datetime(2026, 3, 28, 23, 0, tzinfo=timezone.utc)

    def test_price_fetch_start_evening_before_spring_forward(self):
        """Simulate price fetch at 23:35 CEST on March 29 (evening of DST day).

        This was the exact scenario from the logs.
        days_back=1, so start should be March 28 00:00 CET (+01:00).
        """
        now = datetime(2026, 3, 29, 23, 35, tzinfo=AMS)
        days_back = 1
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))

        assert start.day == 28
        assert start.hour == 0
        # March 28 is winter time: must be CET (+01:00), NOT CEST (+02:00)
        assert start.utcoffset() == timedelta(hours=1), (
            f"Expected CET (+01:00) for March 28 but got {start.utcoffset()}"
        )

        # In UTC: March 28 00:00 CET = March 27 23:00 UTC
        start_utc = start.astimezone(timezone.utc)
        assert start_utc == datetime(2026, 3, 27, 23, 0, tzinfo=timezone.utc)


class TestTimestampReconstructionDST:
    """Test that timestamp reconstruction from index + start works across DST."""

    def test_timestamps_across_spring_forward(self):
        """Reconstruct timestamps at 15-min resolution across spring-forward.

        zoneinfo uses wall-clock arithmetic: adding 2 hours to 00:00 gives 02:00,
        even if 02:00 is in the DST gap. The UTC equivalent is still correct.
        """
        start = datetime(2026, 3, 29, 0, 0, tzinfo=AMS)
        resolution_minutes = 15

        # Index 8 = 2 hours wall-clock later
        dt_at_2h = start + timedelta(minutes=8 * resolution_minutes)
        # zoneinfo wall-clock arithmetic: 00:00 + 2h = 02:00 (in DST gap)
        assert dt_at_2h.hour == 2

        # What matters for the FM API is the ISO format string.
        # Verify the UTC equivalents are correct across the transition:
        # Index 12 = 3 hours wall-clock = 03:00 CEST (after the gap)
        dt_at_3h = start + timedelta(minutes=12 * resolution_minutes)
        assert dt_at_3h.hour == 3
        assert dt_at_3h.utcoffset() == timedelta(hours=2)  # CEST

        # Index 96 = 24 wall-clock hours later = March 30 00:00 CEST
        dt_at_24h = start + timedelta(minutes=96 * resolution_minutes)
        assert dt_at_24h.day == 30
        assert dt_at_24h.hour == 0
        assert dt_at_24h.utcoffset() == timedelta(hours=2)  # CEST
