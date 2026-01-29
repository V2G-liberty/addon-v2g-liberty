"""Fetcher for emission intensity data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, List, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ..utils.retry_handler import RetryHandler
from ...v2g_globals import time_floor
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class EmissionFetcher(BaseFetcher):
    """
    Fetches CO2 emission intensity data from FlexMeasures.

    Responsibilities:
    - Retrieve emission data from FlexMeasures API
    - Find latest emission datetime for validation
    """

    DAYS_HISTORY: int = 7

    def __init__(self, hass: Hass, fm_client_app: object, retry_handler: RetryHandler):
        """
        Initialise the emission fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
            retry_handler: Handler for retry logic
        """
        super().__init__(hass, fm_client_app, retry_handler)
        self.__log = get_class_method_logger(hass.log)

    async def fetch_emissions(self, now: datetime) -> Optional[Dict[str, any]]:
        """
        Fetch emission intensity data.

        Args:
            now: Current datetime

        Returns:
            Dict containing:
            - emissions: List of emission values (may contain None)
            - start: Start datetime of the emission data
            - latest_emission_dt: Datetime of the latest non-None emission

            Returns None if fetch fails or fm_client is not available.
        """
        if not self.is_client_available():
            return None

        # Getting emissions since a week ago
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{self.DAYS_HISTORY + 2}D"

        try:
            emissions = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_EMISSIONS_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution=resolution,
                uom=c.EMISSIONS_UOM,
            )

            if emissions is None:
                return None

            emission_values = emissions["values"]

            # Find latest emission datetime
            latest_emission_dt = self._find_latest_value_datetime(
                emission_values, start, c.FM_EVENT_RESOLUTION_IN_MINUTES
            )

            return {
                "emissions": emission_values,
                "start": start,
                "latest_emission_dt": latest_emission_dt,
            }

        except Exception as e:
            self.__log(f"Exception fetching emissions: {str(e)}", level="WARNING")
            return None

    def _find_latest_value_datetime(
        self, values: List[Optional[float]], start: datetime, resolution_minutes: int
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
        latest_dt = None
        for i, value in enumerate(values):
            if value is not None:
                latest_dt = start + timedelta(minutes=(i * resolution_minutes))
        return latest_dt
