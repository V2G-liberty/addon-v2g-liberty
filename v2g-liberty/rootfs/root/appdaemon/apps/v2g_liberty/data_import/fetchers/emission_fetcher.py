"""Fetcher for emission intensity data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ...v2g_globals import time_floor
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class EmissionFetcher(BaseFetcher):
    """
    Fetches CO2 emission intensity data from FlexMeasures.

    Responsibilities:
    - Retrieve emission data from FlexMeasures API

    Note: ENTSOE data for freshness checking is now fetched separately by
    EntsoeFetcher to avoid redundant API calls.
    """

    DAYS_HISTORY: int = 7

    def __init__(self, hass: Hass, fm_client_app: object):
        """
        Initialise the emission fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
        """
        super().__init__(hass, fm_client_app)
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

            Returns None if fetch fails or fm_client is not available.
        """
        if not self.is_client_available():
            return None

        # Getting emissions since a week ago
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        str_duration = f"P{self.DAYS_HISTORY + 2}D"

        try:
            # Fetch emission data
            emissions = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_EMISSIONS_SENSOR_ID,
                start=start.isoformat(),
                duration=str_duration,
                resolution=resolution,
                uom=c.EMISSIONS_UOM,
            )

            if emissions is None:
                self.__log("Failed to fetch emissions data.")
                return None

            emission_values = emissions["values"]
            emission_count = sum(1 for v in emission_values if v is not None)
            self.__log(f"Success: fetched {emission_count} emission values.")

            return {
                "emissions": emission_values,
                "start": start,
            }

        except Exception as e:
            self.__log(f"Exception fetching emissions: {str(e)}", level="WARNING")
            return None
