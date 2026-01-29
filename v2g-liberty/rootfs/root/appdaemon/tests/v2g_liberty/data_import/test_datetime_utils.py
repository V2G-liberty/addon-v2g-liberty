"""Tests for DatetimeUtils class."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
import pytz
from apps.v2g_liberty.data_import.utils.datetime_utils import DatetimeUtils
from apps.v2g_liberty.data_import import fetch_timing as fm_c


class TestCalculateExpectedPriceDatetime:
    """Test calculate_expected_price_datetime method."""

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
        return_value=True,
    )
    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_price_datetime_after_fetch_time(
        self, mock_time_ceil, mock_is_between
    ):
        """Test calculation when current time is after fetch start time."""
        # Current time is 15:00:00 (after GET_PRICES_TIME fetch start)
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_price_datetime(
            now, fm_c.GET_PRICES_TIME
        )

        # Should expect tomorrow's prices
        # now + 1 day = 2026-01-29 15:00:00
        # ceil to midnight = 2026-01-30 00:00:00
        # minus 65 minutes = 2026-01-29 22:55:00
        expected = datetime(2026, 1, 29, 22, 55, 0, tzinfo=pytz.UTC)

        assert result == expected
        mock_is_between.assert_called_once_with(
            start_time=fm_c.GET_PRICES_TIME, end_time=fm_c.END_OF_DAY_TIME
        )
        mock_time_ceil.assert_called_once()

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
        return_value=False,
    )
    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_price_datetime_before_fetch_time(
        self, mock_time_ceil, mock_is_between
    ):
        """Test calculation when current time is before fetch start time."""
        # Current time is 10:00:00 (before GET_PRICES_TIME fetch start)
        now = datetime(2026, 1, 28, 10, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_price_datetime(
            now, fm_c.GET_PRICES_TIME
        )

        # Should expect today's prices
        # now (no +1 day) = 2026-01-28 10:00:00
        # ceil to midnight = 2026-01-29 00:00:00
        # minus 65 minutes = 2026-01-28 22:55:00
        expected = datetime(2026, 1, 28, 22, 55, 0, tzinfo=pytz.UTC)

        assert result == expected
        mock_is_between.assert_called_once_with(
            start_time=fm_c.GET_PRICES_TIME, end_time=fm_c.END_OF_DAY_TIME
        )
        mock_time_ceil.assert_called_once()

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
        return_value=True,
    )
    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_price_datetime_custom_fetch_time(
        self, mock_time_ceil, mock_is_between
    ):
        """Test calculation with custom fetch start time."""
        now = datetime(2026, 1, 28, 16, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_price_datetime(now, "14:00:00")

        expected = datetime(2026, 1, 29, 22, 55, 0, tzinfo=pytz.UTC)
        assert result == expected
        mock_is_between.assert_called_once_with(
            start_time="14:00:00", end_time=fm_c.END_OF_DAY_TIME
        )

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
        return_value=True,
    )
    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_price_datetime_near_midnight(
        self, mock_time_ceil, mock_is_between
    ):
        """Test calculation when current time is near midnight."""
        # Current time is 23:30:00
        now = datetime(2026, 1, 28, 23, 30, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_price_datetime(
            now, fm_c.GET_PRICES_TIME
        )

        # Should expect tomorrow's prices
        expected = datetime(2026, 1, 29, 22, 55, 0, tzinfo=pytz.UTC)
        assert result == expected


class TestCalculateExpectedEmissionDatetime:
    """Test calculate_expected_emission_datetime method."""

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_emission_datetime_with_default_resolution(self, mock_time_ceil):
        """Test calculation with default 15-minute resolution."""
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_emission_datetime(now)

        # now + 1 day = 2026-01-29 15:00:00
        # ceil to midnight = 2026-01-30 00:00:00
        # minus (60 + 15) = 75 minutes = 2026-01-29 22:45:00
        expected = datetime(2026, 1, 29, 22, 45, 0, tzinfo=pytz.UTC)

        assert result == expected
        mock_time_ceil.assert_called_once()

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_emission_datetime_with_custom_resolution(self, mock_time_ceil):
        """Test calculation with custom resolution (5 minutes)."""
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_emission_datetime(
            now, resolution_minutes=5
        )

        # now + 1 day = 2026-01-29 15:00:00
        # ceil to midnight = 2026-01-30 00:00:00
        # minus (60 + 5) = 65 minutes = 2026-01-29 22:55:00
        expected = datetime(2026, 1, 29, 22, 55, 0, tzinfo=pytz.UTC)

        assert result == expected
        mock_time_ceil.assert_called_once()

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_emission_datetime_near_midnight(self, mock_time_ceil):
        """Test calculation when current time is near midnight."""
        now = datetime(2026, 1, 28, 23, 30, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_emission_datetime(
            now, resolution_minutes=15
        )

        # now + 1 day = 2026-01-29 23:30:00
        # ceil to midnight = 2026-01-30 00:00:00
        # minus 75 minutes = 2026-01-29 22:45:00
        expected = datetime(2026, 1, 29, 22, 45, 0, tzinfo=pytz.UTC)

        assert result == expected

    @patch(
        "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
        side_effect=lambda dt, delta: dt.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        + timedelta(days=1),
    )
    def test_expected_emission_datetime_early_morning(self, mock_time_ceil):
        """Test calculation when current time is in early morning."""
        now = datetime(2026, 1, 28, 2, 0, 0, tzinfo=pytz.UTC)

        result = DatetimeUtils.calculate_expected_emission_datetime(
            now, resolution_minutes=15
        )

        # now + 1 day = 2026-01-29 02:00:00
        # ceil to midnight = 2026-01-30 00:00:00
        # minus 75 minutes = 2026-01-29 22:45:00
        expected = datetime(2026, 1, 29, 22, 45, 0, tzinfo=pytz.UTC)

        assert result == expected


class TestDatetimeUtilsIntegration:
    """Integration tests for DatetimeUtils."""

    def test_price_and_emission_calculations_are_different(self):
        """Test that price and emission calculations produce different results."""
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=pytz.UTC)

        # Mock the dependencies
        with (
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
                return_value=True,
            ),
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
                side_effect=lambda dt, delta: dt.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                + timedelta(days=1),
            ),
        ):
            price_dt = DatetimeUtils.calculate_expected_price_datetime(now)
            emission_dt = DatetimeUtils.calculate_expected_emission_datetime(now, 15)

            # Price: minus 65 minutes -> 22:55:00
            # Emission: minus 75 minutes -> 22:45:00
            assert price_dt != emission_dt
            assert (
                price_dt - emission_dt
            ).total_seconds() == 600  # 10 minutes difference

    def test_static_methods_can_be_called_without_instance(self):
        """Test that all methods are static and don't require instance."""
        now = datetime(2026, 1, 28, 15, 0, 0, tzinfo=pytz.UTC)

        # Should be able to call without creating instance
        with (
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.is_local_now_between",
                return_value=True,
            ),
            patch(
                "apps.v2g_liberty.data_import.utils.datetime_utils.time_ceil",
                side_effect=lambda dt, delta: dt.replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                + timedelta(days=1),
            ),
        ):
            result1 = DatetimeUtils.calculate_expected_price_datetime(now)
            result2 = DatetimeUtils.calculate_expected_emission_datetime(now)

            assert result1 is not None
            assert result2 is not None
