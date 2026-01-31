"""Fetcher for ENTSOE timing data.

This fetcher retrieves ENTSOE day-ahead data to determine when fresh price/emission
data is available. It acts as a gate before fetching actual prices/emissions.
"""

from datetime import datetime, timedelta
from typing import Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ...v2g_globals import time_floor
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class EntsoeFetcher(BaseFetcher):
    """
    Fetches ENTSOE day-ahead data to determine data freshness.

    This fetcher is used as a gate before fetching actual price/emission data.
    If ENTSOE shows tomorrow's data is available, we know prices and emissions
    will also have fresh data (they come from the same day-ahead market).

    The kickoff methods call this once and pass the result to price/emission fetchers,
    so no caching is needed.
    """

    # ENTSOE sensor and source IDs in FlexMeasures
    ENTSOE_SENSOR_ID: int = 14
    ENTSOE_SOURCE_ID: int = 37

    def __init__(
        self,
        hass: Hass,
        fm_client_app: object,
    ):
        """
        Initialise the ENTSOE fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
        """
        super().__init__(hass, fm_client_app)
        self.__log = get_class_method_logger(hass.log)

    async def fetch_latest_dt(self, now: datetime) -> Optional[datetime]:
        """
        Fetch the datetime of the latest available ENTSOE data.

        Args:
            now: Current datetime

        Returns:
            Datetime of the latest non-None ENTSOE value, or None if fetch fails.
            This datetime represents the START of the last available data block.
        """
        if not self.is_client_available():
            return None

        # Calculate fetch parameters
        days_back = 1
        str_duration = f"P{days_back + 2}D"
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"

        try:
            entsoe_data = await self.fm_client_app.get_sensor_data(
                sensor_id=self.ENTSOE_SENSOR_ID,
                start=start.isoformat(),
                duration=str_duration,
                resolution=resolution,
                uom=f"c{c.CURRENCY}/kWh",
                source=self.ENTSOE_SOURCE_ID,
            )

            if entsoe_data is None or "values" not in entsoe_data:
                self.__log("Failed to fetch ENTSOE data.")
                return None

            entsoe_values = entsoe_data["values"]
            latest_dt = self._find_latest_value_datetime(
                entsoe_values, start, c.FM_EVENT_RESOLUTION_IN_MINUTES
            )

            value_count = sum(1 for v in entsoe_values if v is not None)
            self.__log(f"Fetched {value_count} ENTSOE values, latest at {latest_dt}.")

            return latest_dt

        except Exception as e:
            self.__log(f"Exception fetching ENTSOE data: {str(e)}", level="WARNING")
            return None

    def is_tomorrow_data_available(
        self, entsoe_latest_dt: Optional[datetime], now: datetime
    ) -> bool:
        """
        Check if tomorrow's day-ahead data is available.

        Day-ahead prices/emissions are published daily, typically around 13:00 CET.
        Once published, the data extends into tomorrow (until end of next day).

        Args:
            entsoe_latest_dt: The latest datetime from ENTSOE data
            now: Current datetime

        Returns:
            True if tomorrow's data is available, False otherwise.
        """
        if entsoe_latest_dt is None:
            return False

        # Tomorrow starts at midnight
        tomorrow_start = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        # Data is considered fresh if it extends into tomorrow
        return entsoe_latest_dt >= tomorrow_start

    def _find_latest_value_datetime(
        self, values: list, start: datetime, resolution_minutes: int
    ) -> Optional[datetime]:
        """
        Find the datetime of the latest non-None value in the list.

        Args:
            values: List of values (may contain None)
            start: Start datetime
            resolution_minutes: Resolution in minutes

        Returns:
            Datetime of the latest non-None value, or None if all values are None
        """
        # Iterate backwards: values have data at the start and trailing Nones for
        # future periods without data yet, so this finds the last value quickly.
        for i in range(len(values) - 1, -1, -1):
            if values[i] is not None:
                return start + timedelta(minutes=(i * resolution_minutes))
        return None
