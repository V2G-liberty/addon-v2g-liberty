"""Datetime utilities for FlexMeasures data fetching operations."""

from datetime import datetime, timedelta
from ...v2g_globals import time_ceil, is_local_now_between


class DatetimeUtils:
    """Utilities for date/time calculations in FlexMeasures data operations."""

    @staticmethod
    def calculate_expected_price_datetime(
        now: datetime, fetch_start_time: str = "13:35:51"
    ) -> datetime:
        """
        Calculate the expected datetime when price data should be available.

        For EPEX-based price data, prices are typically available for the current day
        and the next day. This method calculates the latest datetime we expect prices
        to be available for, based on when the fetch was initiated.

        Args:
            now: Current datetime
            fetch_start_time: Time when price fetching begins (e.g., "13:35:51")

        Returns:
            datetime: The expected datetime of the latest price that should be available
        """
        # If fetching after the start time but before midnight, expect tomorrow's prices
        if is_local_now_between(start_time=fetch_start_time, end_time="23:59:59"):
            expected_dt = now + timedelta(days=1)
        else:
            # Otherwise, expect today's prices
            expected_dt = now

        # Round to end of day (midnight of the next day)
        expected_dt = time_ceil(expected_dt, timedelta(days=1))

        # The last price is valid for 23:00:00 - 23:59:59, so we subtract
        # 60 minutes to get to 23:00:00, plus 5 minutes slack = 65 minutes
        expected_dt -= timedelta(minutes=65)

        return expected_dt

    @staticmethod
    def calculate_expected_emission_datetime(
        now: datetime, resolution_minutes: int = 15
    ) -> datetime:
        """
        Calculate the expected datetime when emission data should be available.

        FlexMeasures returns all the emissions it has. Sometimes it has not retrieved
        new emissions yet, then it communicates the emissions it does have. This method
        calculates the latest datetime we expect emissions to be available for.

        Args:
            now: Current datetime
            resolution_minutes: Resolution of emission data in minutes (default: 15)

        Returns:
            datetime: The expected datetime of the latest emission that should be available
        """
        # Calculate tomorrow's date
        date_tomorrow = now + timedelta(days=1)

        # Round to end of day (midnight of the day after tomorrow)
        date_tomorrow = time_ceil(date_tomorrow, timedelta(days=1))

        # The emission is for the hour 23:00 - 23:59:59, so we subtract
        # 60 minutes to get to 23:00:00, plus one resolution period for slack
        date_tomorrow += timedelta(minutes=-(60 + resolution_minutes))

        return date_tomorrow
