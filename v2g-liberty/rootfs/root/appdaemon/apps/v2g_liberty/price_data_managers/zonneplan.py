"""Module for calculating Zonneplan Netherland price data and making it available to
FlexMeasures."""

from datetime import datetime, timedelta
import isodate
import constants as c
from event_bus import EventBus
import log_wrapper
from v2g_globals import time_ceil, time_floor
from appdaemon.plugins.hass.hassapi import Hass


class ZonneplanPriceDataManager:
    """
    Class transforms raw epex price data (from FlexMeasures) it into Zonneplan specific
    prices that are then uploaded to FlexMeasures to use for scheduling.

    There are two price calculations for Zonneplan, regular and "Zonnebonus", both based on the
    (Dutch) EPEX market price.
    Unfortunately Zonneplan currently does not expose this price to the public via their API so it
    needs to be calculated here.

    Regular prices follows the calutation that is also in place for generic_nl:

    - regular_price = (market_price + handling_fee + energy_tax) * (100 + VAT)/100

    The handling_fee is set by Zonneplan yearly (but can change at any time) and now is €16,53/MWh.
    The energy_tax is set by the regulatory authorities yearly and currently is € 101,54/MWh.
    VAT is constant at 21% and no changes expected any time soon.

    Bonus price is a production price and is calculated as follows:

    - bonus_price = regular_price * (100 + bonus_percentage)/100

    This production price is only applicable from (the hour of) sunrise until the (hour of) sunset.
    If quarterly prices are introduced this will be the quarter of sunrise/set.

    The bonus_price is only applicable if (market_price + handling_fee) > 0

    The bonus_percentage is set by Zonneplan and currently is 10% and no changes expected soon.

    """

    fm_client_app: object = None
    event_bus: EventBus = None
    hass: Hass = None
    # TODO:
    # Make these settings in the UI.
    zp_bonus_percentage: int = 10
    zp_bonus_factor: float = None
    zp_handing_fee: float = 16.53  # (EUR/MWh)
    energy_tax: float = 101.54  # (EUR/MWh)

    hour_of_sunrise: datetime = None
    hour_of_sunset: datetime = None

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.event_bus = event_bus
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.__log("completed")

    async def initialize(self):
        """Initialize separate from __init__ due to async nature."""
        self.__log("started")
        if c.ELECTRICITY_PROVIDER != "nl_zonneplan":
            self.__log("Not initialiszing, Energy provider is not nl_zonneplan.")

        self.zp_bonus_factor = (self.zp_bonus_percentage + 100) / 100

        # Close to midnight to make it most likely that both sunrise and sunset are in the furture.
        self.hass.run_daily(self._get_sunrise_and_sunset, start="00:01:23")
        await self._get_sunrise_and_sunset(None)

        self.event_bus.add_event_listener("new_raw_prices", self._handle_new_raw_prices)

        self.__log("completed")

    async def _handle_new_raw_prices(self, price_data: dict):
        self.__log(f"received price data: {price_data}")

        raw_prices = price_data.get("values", None)
        start = price_data.get("start", None)
        duration = price_data.get("duration", None)
        uom = price_data.get("unit", None)

        start_dt = parse_to_local_datetime(start)

        consumption_prices = []
        production_prices = []

        for i, raw_price in enumerate(raw_prices):
            if raw_price is None:
                continue
            dt = start_dt + i * timedelta(minutes=c.PRICE_RESOLUTION_MINUTES)
            consumption_price = raw_price + self.zp_handing_fee
            if self._is_zp_daylight(dt) and consumption_price > 0:
                # Ignoring rules for max 7500kWh/year and "salderingsruimte": too complex.
                production_price = consumption_price * self.zp_bonus_factor
            else:
                production_price = consumption_price

            consumption_prices.append(
                round((consumption_price + self.energy_tax) * c.VAT_FACTOR, 2)
            )
            production_prices.append(
                round((production_price + self.energy_tax) * c.VAT_FACTOR, 2)
            )

        # self.__log(f"{consumption_prices=}")
        # self.__log(f"{production_prices=}")

        res = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_PRICE_CONSUMPTION_SENSOR_ID,
            values=consumption_prices,
            start=start,
            duration=duration,
            uom=uom,
        )

        res = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_PRICE_PRODUCTION_SENSOR_ID,
            values=production_prices,
            start=start,
            duration=duration,
            uom=uom,
        )

    def _is_zp_daylight(self, dt: datetime) -> bool:
        return self.hour_of_sunrise <= dt < self.hour_of_sunset

    async def _get_sunrise_and_sunset(self, _kwargs):
        sun_attributes = await self.hass.get_state("sun.sun", attribute="attributes")
        sunrise_time_str = sun_attributes.get("next_rising")
        sunset_time_str = sun_attributes.get("next_setting")

        sunrise_time = parse_to_local_datetime(sunrise_time_str)
        sunset_time = parse_to_local_datetime(sunset_time_str)

        # Can occur if called after sunrise and before sunset due to restart:
        if sunrise_time > sunset_time:
            # next sunrise is tomorrow while next sunset is today.
            sunrise_time -= timedelta(days=1)

        self.hour_of_sunrise = time_floor(sunrise_time, timedelta(hours=1))
        self.hour_of_sunset = time_ceil(sunset_time, timedelta(hours=1))

        self.__log(
            f"Next hour of sunrise: {self.hour_of_sunrise}, "
            f"hour of sunset: {self.hour_of_sunset}."
        )


def parse_to_local_datetime(date_time: str) -> datetime:
    date_time = date_time.replace(" ", "T")
    return isodate.parse_datetime(date_time).astimezone(c.TZ)
