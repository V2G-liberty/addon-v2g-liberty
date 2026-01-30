"""Fetcher for charged energy (power) data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ..utils.retry_handler import RetryHandler
from ...v2g_globals import time_floor
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class EnergyFetcher(BaseFetcher):
    """
    Fetches power consumption/production data from FlexMeasures.

    Responsibilities:
    - Retrieve power data from FlexMeasures API
    - Fetch power data for the last 7 days at FM_EVENT_RESOLUTION_IN_MINUTES resolution
    """

    DAYS_HISTORY: int = 7

    def __init__(self, hass: Hass, fm_client_app: object, retry_handler: RetryHandler):
        """
        Initialise the energy fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
            retry_handler: Handler for retry logic
        """
        super().__init__(hass, fm_client_app, retry_handler)
        self.__log = get_class_method_logger(hass.log)

    async def fetch_power_data(self, now: datetime) -> Optional[Dict[str, any]]:
        """
        Fetch power data for the last 7 days.

        Args:
            now: Current datetime

        Returns:
            Dict containing:
            - power_values: List of power values in MW (may contain None)
            - start: Start datetime of the power data

            Returns None if fetch fails or fm_client is not available.
        """
        if not self.is_client_available():
            return None

        # Getting data since a week
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{self.DAYS_HISTORY}D"

        try:
            result = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution=resolution,
                uom="MW",
            )

            if result is None:
                return None

            return {
                "power_values": result["values"],
                "start": start,
            }

        except Exception as e:
            self.__log(f"Exception fetching power data: {str(e)}", level="WARNING")
            return None
