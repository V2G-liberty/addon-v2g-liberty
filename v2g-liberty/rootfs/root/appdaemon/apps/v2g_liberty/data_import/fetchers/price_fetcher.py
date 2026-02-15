"""Fetcher for price data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from .. import data_import_constants as fm_c
from ...v2g_globals import is_local_now_between, time_floor
from ...log_wrapper import get_class_method_logger
from ... import constants as c


class PriceFetcher(BaseFetcher):
    """
    Fetches consumption and production price data from FlexMeasures.

    Responsibilities:
    - Retrieve price data from FlexMeasures API
    - Fetch both consumption and production prices

    Note: ENTSOE data for freshness checking is now fetched separately by
    EntsoeFetcher to avoid redundant API calls.
    """

    def __init__(self, hass: Hass, fm_client_app: object):
        """
        Initialise the price fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
        """
        super().__init__(hass, fm_client_app)
        self.__log = get_class_method_logger(hass.log)

    async def fetch_prices(
        self, price_type: str, now: datetime
    ) -> Optional[Dict[str, any]]:
        """
        Fetch price data for consumption or production.

        Args:
            price_type: Either "consumption" or "production"
            now: Current datetime

        Returns:
            Dict containing:
            - prices: List of price values (may contain None)
            - start: Start datetime of the price data

            Returns None if fetch fails or fm_client is not available.
        """
        if not self.is_client_available():
            return None

        if price_type not in ["consumption", "production"]:
            self.__log(
                f"fetch_prices called with unknown price_type: '{price_type}'.",
                level="WARNING",
            )
            return None

        # Determine days back based on current time
        if is_local_now_between(
            start_time=fm_c.GET_PRICES_TIME, end_time=fm_c.END_OF_DAY_TIME
        ):
            days_back = 1
        else:
            days_back = 2
        str_duration = f"P{days_back + 2}D"
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))

        sensor_id = (
            c.FM_PRICE_CONSUMPTION_SENSOR_ID
            if price_type == "consumption"
            else c.FM_PRICE_PRODUCTION_SENSOR_ID
        )
        self.__log(
            f"Fetching {price_type} (sensor_id {sensor_id}) prices starting from "
            f"{start} with duration: {str_duration}."
        )
        try:
            # Fetch actual prices
            prices = await self.fm_client_app.get_sensor_data(
                sensor_id=sensor_id,
                start=start.isoformat(),
                duration=str_duration,
                resolution=f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom=f"c{c.CURRENCY}/kWh",
            )

            if prices is None:
                self.__log(f"Failed to fetch {price_type} prices.")
                return None

            price_values = prices["values"]
            price_count = sum(1 for v in price_values if v is not None)
            self.__log(f"Success: fetched {price_count} {price_type} prices.")

            return {
                "prices": price_values,
                "start": start,
            }

        except Exception as e:
            self.__log(
                f"Exception fetching {price_type} prices: {str(e)}", level="WARNING"
            )
            return None
