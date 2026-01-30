"""Fetcher for price data from FlexMeasures."""

from datetime import datetime, timedelta
from typing import Dict, Optional
from appdaemon.plugins.hass.hassapi import Hass
from .base_fetcher import BaseFetcher
from ..utils.retry_handler import RetryHandler
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
    - Fetch ENTSOE prices for comparison (experimental)
    - Find latest price datetime for validation
    """

    def __init__(self, hass: Hass, fm_client_app: object, retry_handler: RetryHandler):
        """
        Initialise the price fetcher.

        Args:
            hass: AppDaemon Hass instance
            fm_client_app: FlexMeasures client for API calls
            retry_handler: Handler for retry logic
        """
        super().__init__(hass, fm_client_app, retry_handler)
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
            - entsoe_prices: List of ENTSOE price values for comparison (may contain None)
            - start: Start datetime of the price data
            - latest_price_dt: Datetime of the latest non-None price
            - entsoe_latest_price_dt: Datetime of the latest non-None ENTSOE price

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

        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))

        sensor_id = (
            c.FM_PRICE_CONSUMPTION_SENSOR_ID
            if price_type == "consumption"
            else c.FM_PRICE_PRODUCTION_SENSOR_ID
        )

        try:
            # Fetch ENTSOE prices (experimental/testing)
            entsoe_prices = await self.fm_client_app.get_sensor_data(
                sensor_id=14,
                start=start.isoformat(),
                duration="P3D",
                resolution=f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom=f"c{c.CURRENCY}/kWh",
                source=37,
            )

            # Fetch actual prices
            prices = await self.fm_client_app.get_sensor_data(
                sensor_id=sensor_id,
                start=start.isoformat(),
                duration="P3D",
                resolution=f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom=f"c{c.CURRENCY}/kWh",
            )

            if prices is None or entsoe_prices is None:
                return None

            # Extract values and find latest datetimes
            price_values = prices["values"]
            entsoe_values = entsoe_prices["values"]

            latest_price_dt = self._find_latest_value_datetime(
                price_values, start, c.PRICE_RESOLUTION_MINUTES
            )
            entsoe_latest_price_dt = self._find_latest_value_datetime(
                entsoe_values, start, c.PRICE_RESOLUTION_MINUTES
            )

            return {
                "prices": price_values,
                "entsoe_prices": entsoe_values,
                "start": start,
                "latest_price_dt": latest_price_dt,
                "entsoe_latest_price_dt": entsoe_latest_price_dt,
            }

        except Exception as e:
            self.__log(
                f"Exception fetching {price_type} prices: {str(e)}", level="WARNING"
            )
            return None

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
        latest_dt = None
        for i, value in enumerate(values):
            if value is not None:
                latest_dt = start + timedelta(minutes=(i * resolution_minutes))
        return latest_dt
