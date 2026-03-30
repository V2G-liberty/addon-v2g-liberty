"""Tests for timezone handling in data_import utilities.

This test module verifies that all utility functions correctly handle
timezone-aware datetimes and preserve timezone information across operations.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from datetime import timezone
from zoneinfo import ZoneInfo
from apps.v2g_liberty.data_import.utils.datetime_utils import DatetimeUtils
from apps.v2g_liberty.data_import.validators.data_validator import DataValidator


# Define various timezones to test
TIMEZONES = [
    ("UTC", timezone.utc),
    ("Europe/Amsterdam", ZoneInfo("Europe/Amsterdam")),
    ("America/New_York", ZoneInfo("America/New_York")),
    ("Australia/Sydney", ZoneInfo("Australia/Sydney")),
    ("Asia/Tokyo", ZoneInfo("Asia/Tokyo")),
    ("Europe/London", ZoneInfo("Europe/London")),
]


class TestDatetimeUtilsTimezonePreservation:
    """Test that DatetimeUtils preserves timezone information."""

    @pytest.mark.parametrize("tz_name,tz_obj", TIMEZONES)
    @patch("apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between")
    @patch("apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil")
    def test_price_datetime_preserves_timezone(
        self, mock_time_ceil, mock_is_between, tz_name, tz_obj
    ):
        """Test that calculate_expected_price_datetime preserves input timezone."""
        # Create timezone-aware datetime
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz_obj)

        # Mock is_local_now_between to return True
        mock_is_between.return_value = True

        # Mock time_ceil to preserve timezone (as real implementation does)
        def mock_ceil(dt, delta):
            # Simulate rounding to midnight while preserving timezone
            return dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                days=1
            )

        mock_time_ceil.side_effect = mock_ceil

        result = DatetimeUtils.calculate_expected_price_datetime(now)

        # Verify timezone is preserved
        assert result.tzinfo is not None, f"Result lost timezone for {tz_name}"
        assert result.tzinfo == tz_obj, (
            f"Timezone changed from {tz_name} to {result.tzinfo}"
        )

    @pytest.mark.parametrize("tz_name,tz_obj", TIMEZONES)
    @patch("apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil")
    def test_emission_datetime_preserves_timezone(
        self, mock_time_ceil, tz_name, tz_obj
    ):
        """Test that calculate_expected_emission_datetime preserves input timezone."""
        # Create timezone-aware datetime
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz_obj)

        # Mock time_ceil to preserve timezone
        def mock_ceil(dt, delta):
            return dt.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
                days=1
            )

        mock_time_ceil.side_effect = mock_ceil

        result = DatetimeUtils.calculate_expected_emission_datetime(now)

        # Verify timezone is preserved
        assert result.tzinfo is not None, f"Result lost timezone for {tz_name}"
        assert result.tzinfo == tz_obj, (
            f"Timezone changed from {tz_name} to {result.tzinfo}"
        )


class TestDataValidatorTimezoneHandling:
    """Test that DataValidator correctly handles timezone-aware datetimes."""

    @pytest.mark.parametrize("tz_name,tz_obj", TIMEZONES)
    def test_price_validation_with_different_timezones(self, tz_name, tz_obj):
        """Test price validation works correctly across different timezones."""
        validator = DataValidator()

        # Create timezone-aware datetimes
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz_obj)
        latest_price_dt = datetime(2026, 1, 29, 23, 0, 0, tzinfo=tz_obj)

        # Mock the datetime_utils to return timezone-aware datetime
        expected_dt = datetime(2026, 1, 29, 22, 55, 0, tzinfo=tz_obj)

        with patch.object(
            validator.datetime_utils,
            "calculate_expected_price_datetime",
            return_value=expected_dt,
        ):
            is_valid, error_msg = validator.validate_price_freshness(
                latest_price_dt, now
            )

            assert is_valid is True, f"Validation failed for timezone {tz_name}"
            assert error_msg == ""

            # Verify the comparison worked correctly with timezone-aware datetimes
            assert latest_price_dt > expected_dt

    @pytest.mark.parametrize("tz_name,tz_obj", TIMEZONES)
    def test_emission_validation_with_different_timezones(self, tz_name, tz_obj):
        """Test emission validation works correctly across different timezones."""
        validator = DataValidator()

        # Create timezone-aware datetimes
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz_obj)
        latest_emission_dt = datetime(2026, 1, 29, 23, 0, 0, tzinfo=tz_obj)

        # Mock the datetime_utils to return timezone-aware datetime
        expected_dt = datetime(2026, 1, 29, 22, 45, 0, tzinfo=tz_obj)

        with patch.object(
            validator.datetime_utils,
            "calculate_expected_emission_datetime",
            return_value=expected_dt,
        ):
            is_valid, error_msg = validator.validate_emission_freshness(
                latest_emission_dt, now
            )

            assert is_valid is True, f"Validation failed for timezone {tz_name}"
            assert error_msg == ""

            # Verify the comparison worked correctly with timezone-aware datetimes
            assert latest_emission_dt >= expected_dt


class TestTimezoneEdgeCases:
    """Test edge cases related to timezone handling."""

    def test_datetime_comparison_across_dst_boundary(self):
        """Test that datetime comparisons work across DST boundaries."""
        # Europe/Amsterdam has DST: switches in March and October
        tz = ZoneInfo("Europe/Amsterdam")

        # Date before DST (winter time)
        winter_dt = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz)

        # Date after DST (summer time)
        summer_dt = datetime(2026, 6, 28, 15, 0, 0, tzinfo=tz)

        validator = DataValidator()

        # Both should have timezone info
        assert winter_dt.tzinfo is not None
        assert summer_dt.tzinfo is not None

        # Comparison should work correctly
        assert summer_dt > winter_dt

    def test_naive_datetime_raises_or_handles_gracefully(self):
        """Test behaviour with naive (non-timezone-aware) datetimes."""
        # Create naive datetime (no timezone)
        naive_now = datetime(2026, 1, 28, 15, 0, 0)
        naive_latest = datetime(2026, 1, 29, 23, 0, 0)

        validator = DataValidator()

        # Mock to return naive datetime
        naive_expected = datetime(2026, 1, 29, 22, 55, 0)

        with patch.object(
            validator.datetime_utils,
            "calculate_expected_price_datetime",
            return_value=naive_expected,
        ):
            # Should handle naive datetimes (for backward compatibility)
            is_valid, error_msg = validator.validate_price_freshness(
                naive_latest, naive_now
            )

            # The comparison should still work
            assert is_valid is True

    def test_timezone_arithmetic_preserves_tzinfo(self):
        """Test that timedelta operations preserve timezone info."""
        tz = ZoneInfo("Europe/Amsterdam")
        dt = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz)

        # Adding/subtracting timedelta should preserve timezone
        dt_plus_day = dt + timedelta(days=1)
        dt_minus_hour = dt - timedelta(hours=1)

        # Check that timezone info is preserved (not None)
        assert dt_plus_day.tzinfo is not None
        assert dt_minus_hour.tzinfo is not None

        # ZoneInfo objects are cached, so identity/equality checks work reliably
        assert dt_plus_day.tzinfo == dt.tzinfo
        assert dt_minus_hour.tzinfo == dt.tzinfo

    @pytest.mark.parametrize("tz_name,tz_obj", TIMEZONES)
    def test_midnight_calculation_in_different_timezones(self, tz_name, tz_obj):
        """Test that midnight calculations work correctly in different timezones."""
        # A datetime in the afternoon
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=tz_obj)

        # Mock time_ceil to simulate rounding to midnight
        def mock_ceil(dt, delta):
            # Round to next midnight in the same timezone
            next_day = dt.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) + timedelta(days=1)
            return next_day

        with (
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
                side_effect=mock_ceil,
            ),
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
                return_value=True,
            ),
        ):
            result = DatetimeUtils.calculate_expected_price_datetime(now)

            # Result should be in the same timezone
            assert result.tzinfo == tz_obj, f"Lost timezone {tz_name}"

            # Result should be earlier than midnight (22:55 due to -65 minutes)
            assert result.hour == 22
            assert result.minute == 55


class TestCrossTimezoneComparisons:
    """Test comparisons between datetimes in different timezones."""

    def test_comparison_with_different_timezones(self):
        """Test that datetime comparisons work when timezones differ."""
        # Same moment in time, different timezones
        utc_dt = datetime(2026, 1, 28, 12, 0, 0, tzinfo=timezone.utc)
        amsterdam_dt = utc_dt.astimezone(ZoneInfo("Europe/Amsterdam"))

        # Should be equal despite different tzinfo
        assert utc_dt == amsterdam_dt

        # Validator should handle cross-timezone comparisons
        validator = DataValidator()

        # Latest price in Amsterdam timezone
        latest_price_amsterdam = datetime(
            2026, 1, 29, 23, 0, 0, tzinfo=ZoneInfo("Europe/Amsterdam")
        )

        # Expected in UTC
        expected_utc = datetime(2026, 1, 29, 21, 55, 0, tzinfo=timezone.utc)

        with patch.object(
            validator.datetime_utils,
            "calculate_expected_price_datetime",
            return_value=expected_utc,
        ):
            # Should work correctly even with different timezones
            is_valid, error_msg = validator.validate_price_freshness(
                latest_price_amsterdam,
                datetime(2026, 1, 28, 15, 0, 0, tzinfo=ZoneInfo("Europe/Amsterdam")),
            )

            # Both represent times, comparison should work
            assert is_valid is True

    def test_utc_vs_local_timezone(self):
        """Test that UTC and local timezones are handled correctly."""
        validator = DataValidator()

        # Now in UTC
        now_utc = datetime(2026, 1, 28, 12, 0, 0, tzinfo=timezone.utc)

        # Latest price in Europe/Amsterdam (UTC+1 in winter)
        tz_amsterdam = ZoneInfo("Europe/Amsterdam")
        latest_price_local = datetime(2026, 1, 29, 23, 0, 0, tzinfo=tz_amsterdam)

        # Expected in UTC
        expected_utc = datetime(2026, 1, 29, 21, 55, 0, tzinfo=timezone.utc)

        with patch.object(
            validator.datetime_utils,
            "calculate_expected_price_datetime",
            return_value=expected_utc,
        ):
            is_valid, error_msg = validator.validate_price_freshness(
                latest_price_local, now_utc
            )

            # Should handle mixed timezones correctly
            assert is_valid is True
