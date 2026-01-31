"""Fetcher for charging cost data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ...v2g_globals import time_floor, convert_to_duration_string
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class CostFetcher(BaseFetcher):
    """
    Fetches charging cost data from FlexMeasures.

    Responsibilities:
    - Retrieve charging cost data from FlexMeasures API
    - Fetch daily cost data for the last 7 days
    """

    DAYS_HISTORY: int = 7

    def __init__(self, hass: Hass, fm_client_app: object):
        """
        Initialise the cost fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
        """
        super().__init__(hass, fm_client_app)
        self.__log = get_class_method_logger(hass.log)

    async def fetch_costs(self, now: datetime) -> Optional[Dict[str, any]]:
        """
        Fetch charging cost data for the last 7 days.

        Args:
            now: Current datetime

        Returns:
            Dict containing:
            - costs: List of daily cost values (may contain None)
            - start: Start datetime of the cost data

            Returns None if fetch fails or fm_client is not available.
        """
        if not self.is_client_available():
            return None

        # Getting data since a week
        start = time_floor(now + timedelta(days=-self.DAYS_HISTORY), timedelta(days=1))
        duration = timedelta(days=self.DAYS_HISTORY)  # Duration as timedelta
        duration = round((duration.total_seconds() / 60), 0)  # Duration in minutes
        duration = convert_to_duration_string(duration)  # Duration as iso string

        try:
            charging_costs = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_ACCOUNT_COST_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution="P1D",
                uom=c.CURRENCY,
            )

            if charging_costs is None:
                return None

            return {
                "costs": charging_costs["values"],
                "start": start,
            }

        except Exception as e:
            self.__log(f"Exception fetching costs: {str(e)}", level="WARNING")
            return None
