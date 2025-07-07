"""Module for calculating Zonneplan Netherland price data and making it available to
FlexMeasures."""

from datetime import datetime
import constants as c
from event_bus import EventBus
import log_wrapper
from v2g_globals import time_round, convert_to_duration_string
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

    The handling_fee is set by Zonneplan yearly (but can change at any time) and currently is € 0,01653/kWh
    The energy_tax is set by the regulatory authorities yearly and currently is € 0,10154/kWh.
    VAT is constant at 21% and no changes expected any time soon.

    Bonus price is a production price and is calculated as follows:

    - bonus_price = regular_price * (100 + bonus_percentage)/100

    This consumtion price is only applicable from (the hour of) sunrise until the (hour of) sunset.
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
    zp_handing_fee: float = 0.01653  # (EUR/kWh)
    energy_tax: float = 0.10154  # (EUR/kWh)

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.event_bus = event_bus
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.__log("completed")

    async def initialise(self):
        self.__log("started")
        # TODO:
        # Make get_fm_data get raw epex data from FM (sensor 14) and if new data has come in
        # fire and event containing thsi dat.
        # This module should subscribe to this event and if received:
        # - read the data form the event
        # - calculate the prices
        # - send the new prices to FM
        # - set the prices in local entities (via get_fm_data?) for display

        self.__log("completed")
