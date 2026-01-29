"""Data validator for FlexMeasures data freshness validation."""

from datetime import datetime
from ..utils.datetime_utils import DatetimeUtils
from .. import fetch_timing as fm_c


class DataValidator:
    """Validates data freshness and completeness."""

    def __init__(self, datetime_utils: DatetimeUtils = None):
        """
        Initialise the data validator.

        Args:
            datetime_utils: DatetimeUtils instance (optional, created if not provided)
        """
        self.datetime_utils = datetime_utils or DatetimeUtils()

    def validate_price_freshness(
        self,
        latest_price_dt: datetime,
        now: datetime,
        fetch_start_time: str = None,
    ) -> tuple[bool, str]:
        """
        Validate if price data is fresh enough.

        Checks if the latest price datetime is newer than the expected price datetime,
        which is calculated based on the current time and fetch start time.

        Args:
            latest_price_dt: Datetime of the latest price data point
            now: Current datetime
            fetch_start_time: Time when price fetching begins (default: GET_PRICES_TIME)

        Returns:
            tuple[bool, str]: (is_valid, error_message)
                is_valid: True if data is fresh, False otherwise
                error_message: Empty string if valid, error description if invalid
        """
        if fetch_start_time is None:
            fetch_start_time = fm_c.GET_PRICES_TIME

        if latest_price_dt is None:
            return False, "no valid prices received"

        expected_dt = self.datetime_utils.calculate_expected_price_datetime(
            now, fetch_start_time
        )

        is_up_to_date = latest_price_dt > expected_dt

        if not is_up_to_date:
            return False, "prices not up to date"

        return True, ""

    def validate_emission_freshness(
        self, latest_emission_dt: datetime, now: datetime, resolution_minutes: int = 15
    ) -> tuple[bool, str]:
        """
        Validate if emission data is fresh enough.

        Checks if the latest emission datetime is newer than the expected emission datetime,
        which is calculated based on the current time and data resolution.

        Args:
            latest_emission_dt: Datetime of the latest emission data point
            now: Current datetime
            resolution_minutes: Resolution of emission data in minutes (default: 15)

        Returns:
            tuple[bool, str]: (is_valid, error_message)
                is_valid: True if data is fresh, False otherwise
                error_message: Empty string if valid, error description if invalid
        """
        if latest_emission_dt is None:
            return False, "no valid data received"

        expected_dt = self.datetime_utils.calculate_expected_emission_datetime(
            now, resolution_minutes
        )

        if latest_emission_dt < expected_dt:
            return False, "emissions are not up to date"

        return True, ""
