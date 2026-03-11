"""Module for importing price and usage data from FlexMeasures."""

from datetime import datetime, timedelta, timezone

import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass
from .v2g_globals import (
    get_local_now,
    is_price_epex_based,
    is_local_now_between,
)
from . import constants as c
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger
from .main_app import ChartLine

# Import refactored components
from .data_import.utils.datetime_utils import DatetimeUtils
from .data_import.validators.data_validator import DataValidator
from .data_import.processors.price_processor import PriceProcessor
from .data_import.processors.emission_processor import EmissionProcessor
from .data_import.fetchers.entsoe_fetcher import EntsoeFetcher
from .data_import.fetchers.price_fetcher import PriceFetcher
from .data_import.fetchers.emission_fetcher import EmissionFetcher
from .data_import import data_import_constants as fm_constants


class FlexMeasuresDataImporter:
    """
    Get prices and emissions data for display in the UI. Both consumption and production
    prices are retrieved.

    Price/emissions data fetched daily when:
    For providers with contracts based on a (EPEX) day-ahead market this should be fetched daily
    after the prices have been published, usually around 14:35. When this fails retry at 18:30.
    These times are related to the attempts in the FM server for retrieving EPEX price data.

    Price/emissions data fetched on data change:
    When price data comes in through an (external) HA integration (e.g. Amber Electric or
    Octopus Energy), V2G Liberty sends this to FM to base the charge-schedules on. This is
    handled by separate modules (e.g. amber_price_data_manager).
    Those modules do not provide the data for display in the UI directly (even-though this could
    be more efficient). Instead, they trigger get_prices() and get_emission_intensities() when a
    change in the data is detected (after it has been sent to FM successfully).

    The retrieved data is written to the HA entities for HA to render the data in the UI chart.
    """

    # CONSTANTS and variables
    vat_factor: float = 1
    markup_per_kwh: float = 0

    # Timing constants imported from data_import.fetch_timing for centralised management
    GET_PRICES_TIME: str = fm_constants.GET_PRICES_TIME
    TRY_UNTIL: str = fm_constants.TRY_UNTIL
    CHECK_DATA_STATUS_TIME: str = fm_constants.CHECK_DATA_STATUS_TIME
    CHECK_RESOLUTION_SECONDS: int = fm_constants.CHECK_RESOLUTION_SECONDS

    # Single flag for ENTSOE-based freshness (prices and emissions share the same source).
    # Reset to False at the start of every daily_kickoff_price_data call so a stale True
    # from a previous day cannot persist and suppress notifications.
    entsoe_data_is_up_to_date: bool = False

    # Tracks whether a push notification for missing prices has been sent.
    # Used to distinguish recovery scenario no-push-to-clear from push-to-replace.
    _critical_price_notification_sent: bool = False

    timer_id_daily_kickoff_price_data: str = ""
    timer_id_daily_kickoff_emissions_data: str = ""
    timer_id_daily_check_is_data_up_to_date: str = ""

    # For sending notifications to the user.
    v2g_main_app: object = None
    # For getting data from FM server
    _fm_client_app: object = None
    notifier: Notifier = None
    # For persisting prices to local SQLite database
    data_store = None

    @property
    def fm_client_app(self):
        """Get the FlexMeasures client app."""
        return self._fm_client_app

    @fm_client_app.setter
    def fm_client_app(self, value):
        """Set the FlexMeasures client app and initialise fetchers."""
        self._fm_client_app = value
        if value is not None:
            self._initialise_fetchers()

    first_future_negative_consumption_price_point: dict
    first_future_negative_production_price_point: dict

    hass: Hass = None

    def __init__(self, hass: Hass, notifier: Notifier):
        self.hass = hass
        self.notifier = notifier
        self.__log = get_class_method_logger(hass.log)

        # These are variables are used to decide what message to send to the user and can have the
        # following values:
        # - None: when no prices have been collected yet.
        # - "No negative price": if there are no negative prices.
        # - A price_point dict: When a negative price is detected.
        self.first_future_negative_consumption_price_point = None
        self.first_future_negative_production_price_point = None
        self._critical_price_notification_sent = False

        # Temporary storage for raw price results, used by _persist_epex_prices_to_db()
        self._latest_raw_consumption_result = None
        self._latest_raw_production_result = None

        # Initialise refactored components
        self._initialise_components()

        self.__log("Complete")

    def _initialise_components(self):
        """Initialise the refactored utility, processor, and fetcher components."""
        # Utility components
        self.datetime_utils = DatetimeUtils()
        self.data_validator = DataValidator(self.datetime_utils)

        # Processors
        self.price_processor = PriceProcessor(
            price_resolution_minutes=c.PRICE_RESOLUTION_MINUTES
        )
        self.emission_processor = EmissionProcessor(
            event_resolution_minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES
        )

        # Fetchers (fm_client_app will be set later via property setter)
        # These are initialised with None and updated when fm_client_app is set
        self.entsoe_fetcher = None
        self.price_fetcher = None
        self.emission_fetcher = None

    def _initialise_fetchers(self):
        """Initialise fetcher components when fm_client_app becomes available."""
        self.entsoe_fetcher = EntsoeFetcher(self.hass, self._fm_client_app)
        self.price_fetcher = PriceFetcher(self.hass, self._fm_client_app)
        self.emission_fetcher = EmissionFetcher(self.hass, self._fm_client_app)
        self.__log("Fetcher components initialised.")

    async def initialize(
        self,
        v2g_args: str = None,
        use_vat_and_markup: bool = False,
        energy_price_vat: int = 0,
        markup_per_kwh: float = 0,
    ):
        """
        Second and final stage of initialisation. This is run from globals when
        settings have initialised/changed. This is separated :
        - is not always around self.first_try_time (for day-ahead contracts)
        - the data-changed might not fire at startup (external HA integration provided data)
        This is delayed as it's not high priority and gives globals the time to get all settings
        loaded correctly.

        Args:
            v2g_args: Source identifier for logging.
            use_vat_and_markup: Whether to apply VAT and markup to prices.
            energy_price_vat: VAT percentage to apply (e.g. 21 for 21%).
            markup_per_kwh: Markup per kWh for transport and sustainability.
        """

        self.__log(f"Called from source: {v2g_args}.")

        # Apply VAT and markup if enabled, otherwise use neutral values
        if use_vat_and_markup:
            self.vat_factor = (100 + energy_price_vat) / 100
            self.markup_per_kwh = markup_per_kwh
        else:
            self.vat_factor = 1
            self.markup_per_kwh = 0

        if v2g_args != "module initialize":
            # New settings must result in refreshing the price data.
            # Clear price data
            await self.v2g_main_app.set_records_in_chart(
                chart_line_name=ChartLine.CONSUMPTION_PRICE, records=None
            )
            await self.v2g_main_app.set_records_in_chart(
                chart_line_name=ChartLine.PRODUCTION_PRICE, records=None
            )
            await self.v2g_main_app.set_records_in_chart(
                chart_line_name=ChartLine.EMISSION, records=None
            )

        # Always cancel timers just to be sure
        await self.__cancel_timer(self.timer_id_daily_kickoff_price_data)
        await self.__cancel_timer(self.timer_id_daily_kickoff_emissions_data)
        await self.__cancel_timer(self.timer_id_daily_check_is_data_up_to_date)

        if is_price_epex_based():
            self.__log(
                f"price update interval is daily based on EP: {c.ELECTRICITY_PROVIDER}."
            )
            self.timer_id_daily_kickoff_price_data = await self.hass.run_daily(
                self.daily_kickoff_price_data, start=self.GET_PRICES_TIME
            )

            self.timer_id_daily_check_is_data_up_to_date = await self.hass.run_daily(
                self.__check_if_prices_are_up_to_date, start=self.CHECK_DATA_STATUS_TIME
            )

            initial_delay_sec = 45
            await self.hass.run_in(
                self.daily_kickoff_price_data,
                delay=initial_delay_sec,
                is_initial_call=True,
            )
        self.__log("completed.")

    # TODO: Consolidate. Copied function from v2g_liberty module also in globals..
    async def __cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        :param timer_id: timer_handle to cancel
        """
        if self.hass.timer_running(timer_id):
            silent = True  # Does not really work
            await self.hass.cancel_timer(timer_id, silent)

    async def daily_kickoff_price_data(self, *args):
        """
        This sets off the daily routine to check for new prices.
        Only called when is_price_epex_based() is true.

        Uses ENTSOE data as a gate: if tomorrow's day-ahead data isn't available yet,
        we skip fetching prices and schedule a retry. This avoids redundant API calls
        when the day-ahead market data hasn't been published yet.

        Args:
            args: AppDaemon callback args (kwargs passed as dict in args[0]).
                  Supports 'is_initial_call' (bool): If True, fetch prices even if
                  ENTSOE data isn't fresh (to populate UI on startup).
        """
        # Reset flag at the start of every run so a stale True from the previous day
        # cannot persist and silently suppress the missing-prices notification.
        self.entsoe_data_is_up_to_date = False

        # AppDaemon's run_in passes kwargs as a dict inside args[0]
        is_initial_call = False
        if args and isinstance(args[0], dict):
            is_initial_call = args[0].get("is_initial_call", False)

        self.__log(f"Called, is_initial_call: {is_initial_call}.")

        # Check ENTSOE fetcher availability
        if self.entsoe_fetcher is None:
            self.__log("EntsoeFetcher not initialised (fm_client_app not set yet).")
            return

        now = get_local_now()
        entsoe_latest_dt = await self.entsoe_fetcher.fetch_latest_dt(now)

        # Check if tomorrow's data is available
        is_fresh = self.entsoe_fetcher.is_tomorrow_data_available(entsoe_latest_dt, now)

        self.__log(
            f"ENTSOE latest_dt: {entsoe_latest_dt}, is_fresh: {is_fresh}, "
            f"is_initial_call: {is_initial_call}."
        )

        if not is_fresh and not is_initial_call:
            # Data not ready yet, schedule retry
            await self.v2g_main_app.set_price_is_up_to_date(is_up_to_date=False)
            if is_local_now_between(
                start_time=self.GET_PRICES_TIME, end_time=self.TRY_UNTIL
            ):
                await self.hass.run_in(
                    self.daily_kickoff_price_data, delay=self.CHECK_RESOLUTION_SECONDS
                )
                self.__log(
                    f"ENTSOE data not fresh, retry in {self.CHECK_RESOLUTION_SECONDS} sec."
                )
            else:
                self.__log(
                    "ENTSOE data not fresh and outside retry window, not retrying."
                )
            return

        # ENTSOE confirms fresh data exists (or it's an initial call) — fetch prices.
        # Check return values: even when ENTSOE is fresh, FM price retrieval can fail.
        consumption_ok = await self.get_prices(
            price_type="consumption", entsoe_latest_dt=entsoe_latest_dt
        )
        production_ok = await self.get_prices(
            price_type="production", entsoe_latest_dt=entsoe_latest_dt
        )

        # Persist combined prices to local DB (upsampled to 5-min resolution)
        if consumption_ok and production_ok:
            try:
                self._persist_epex_prices_to_db()
            except Exception as e:
                self.__log(
                    f"Failed to persist EPEX prices to DB: {e}",
                    level="WARNING",
                )

        await self.get_emission_intensities(entsoe_latest_dt=entsoe_latest_dt)

        prices_ok = consumption_ok and production_ok
        if is_fresh and prices_ok:
            # Prices successfully retrieved and ENTSOE confirms they are for tomorrow.
            self.entsoe_data_is_up_to_date = True
            self._critical_price_notification_sent = False
            await self.v2g_main_app.set_price_is_up_to_date(is_up_to_date=True)
        elif not prices_ok:
            # ENTSOE fresh but FM price retrieval failed — flag stays False, schedule retry.
            self.__log(
                "ENTSOE data is fresh but price retrieval failed. Will retry.",
                level="WARNING",
            )
            if is_local_now_between(
                start_time=self.GET_PRICES_TIME, end_time=self.TRY_UNTIL
            ):
                await self.hass.run_in(
                    self.daily_kickoff_price_data, delay=self.CHECK_RESOLUTION_SECONDS
                )

        self.__log("completed")

    async def get_emission_intensities(
        self, *args, entsoe_latest_dt: datetime = None, **kwargs
    ) -> bool:
        """Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production from the server.
        Make values available in HA by setting them in sensor.co2_emissions.

        Freshness validation and retry logic is now handled at the
        daily_kickoff_emissions_data level using the ENTSOE fetcher.

        Args:
            args: AppDaemon callback args (unused)
            entsoe_latest_dt: The latest ENTSOE datetime (from EntsoeFetcher) used for
                            determining the fixed vs forecast boundary in charts.
            kwargs: Additional keyword args (unused)

        Returns:
            True if emissions were successfully retrieved, False otherwise.
        """
        self.__log("Called")

        # Use EmissionFetcher to retrieve data
        if self.emission_fetcher is None:
            self.__log("EmissionFetcher not initialised (fm_client_app not set yet).")
            return False

        now = get_local_now()
        result = await self.emission_fetcher.fetch_emissions(now)

        if result is None:
            self.__log("EmissionFetcher returned None.")
            return False

        # Use entsoe_latest_dt from parameter (from EntsoeFetcher) for EFP boundary
        # entsoe_latest_dt is the START of the last data block
        # We need the END of that block for the visual split, so add one resolution period
        end_of_fixed_prices_dt = None
        if entsoe_latest_dt and is_price_epex_based():
            end_of_fixed_prices_dt = entsoe_latest_dt + timedelta(
                minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES
            )

        # Use EmissionProcessor to process the data
        emission_cache, chart_points, latest_emission_dt = (
            self.emission_processor.process_emissions(
                raw_emissions=result["emissions"],
                start=result["start"],
                now=now,
                history_hours=5,
            )
        )

        # Persist emission data to local database
        if self.data_store is not None and emission_cache:
            rows = [
                (ts.astimezone(timezone.utc).isoformat(), intensity)
                for ts, intensity in emission_cache.items()
            ]
            self.data_store.upsert_emissions(rows)

        # Convert EFP datetime to ISO string for JSON serialisation
        end_of_fixed_prices_iso = (
            end_of_fixed_prices_dt.isoformat() if end_of_fixed_prices_dt else None
        )

        # Update the chart with emission points and EFP boundary for fixed/forecast distinction
        await self.v2g_main_app.set_records_in_chart(
            chart_line_name=ChartLine.EMISSION,
            records={
                "records": chart_points,
                "end_of_fixed_prices_dt": end_of_fixed_prices_iso,
            },
        )

        self.__log("emissions successfully retrieved.")
        return True

    async def get_prices(
        self, price_type: str, entsoe_latest_dt: datetime = None
    ) -> bool:
        """
        Gets consumption / production prices from the server via the fm_client.

        Freshness validation and retry logic is now handled at the daily_kickoff_price_data
        level using the ENTSOE fetcher. This method just fetches and processes prices.

        Checks for negative prices and notifies the user if found.
        Makes prices available in HA.

        Args:
            price_type: 'consumption' or 'production'
            entsoe_latest_dt: The latest ENTSOE datetime (from EntsoeFetcher) used for
                            determining the fixed vs forecast boundary in charts.

        Returns:
            True if prices were successfully retrieved, False otherwise.
        """
        if price_type not in ["consumption", "production"]:
            self.__log(
                f"called with unknown price_type: '{price_type}'.", level="WARNING"
            )
            return False

        self.__log(f"Called for {price_type}.")

        # Use PriceFetcher to retrieve data
        if self.price_fetcher is None:
            self.__log(
                f"({price_type}). PriceFetcher not initialised (fm_client_app not set yet)."
            )
            return False

        now = get_local_now()
        result = await self.price_fetcher.fetch_prices(price_type, now)

        if result is None:
            self.__log(f"({price_type}): fetch_prices returned None.")
            return False

        # Store raw result for later DB persistence by _persist_epex_prices_to_db()
        if price_type == "consumption":
            self._latest_raw_consumption_result = result
        else:
            self._latest_raw_production_result = result

        # Use entsoe_latest_dt from parameter (from EntsoeFetcher) for EFP boundary
        # entsoe_latest_dt is the START of the last data block
        # We need the END of that block for the visual split, so add one resolution period
        end_of_fixed_prices_dt = None
        if entsoe_latest_dt and is_price_epex_based():
            end_of_fixed_prices_dt = entsoe_latest_dt + timedelta(
                minutes=c.PRICE_RESOLUTION_MINUTES
            )

        # Use PriceProcessor to process the data
        # For EPEX-based providers, pass the EFP so chart can distinguish fixed vs forecast
        price_points, first_negative_price, none_count, end_of_fixed_prices_iso = (
            self.price_processor.process_prices(
                raw_prices=result["prices"],
                start=result["start"],
                now=now,
                vat_factor=self.vat_factor,
                markup_per_kwh=self.markup_per_kwh,
                end_of_fixed_prices_dt=end_of_fixed_prices_dt,
            )
        )

        self.__log(
            f"({price_type}) | number of prices: '{len(price_points)}', "
            f"number of none values '{none_count}'."
        )

        # Handle negative price notification (still needs actual prices)
        if first_negative_price is not None:
            self.__log(
                f"({price_type}), negative price: {first_negative_price['price']} "
                f"at: {first_negative_price['time']}."
            )
            self.__check_negative_price_notification(
                first_negative_price, price_type=price_type
            )
        else:
            self.__check_negative_price_notification(
                "No negative prices", price_type=price_type
            )

        # Update the chart with price points and EFP boundary for fixed/forecast distinction
        await self.v2g_main_app.set_records_in_chart(
            chart_line_name=ChartLine.CONSUMPTION_PRICE
            if price_type == "consumption"
            else ChartLine.PRODUCTION_PRICE,
            records={
                "records": price_points,
                "end_of_fixed_prices_dt": end_of_fixed_prices_iso,
            },
        )

        self.__log(f"{price_type} prices successfully retrieved.")
        return True

    def _persist_epex_prices_to_db(self):
        """Persist fetched EPEX prices to local SQLite database.

        Combines consumption and production raw prices, upsamples from
        PRICE_RESOLUTION_MINUTES to 5-min intervals using pandas reindex,
        and writes to price_log via DataStore.upsert_prices().

        VAT and markup are only applied when configured (nl_generic provider);
        other providers already include these in their FM prices.
        """
        if self.data_store is None:
            self.__log(
                "DataStore not available, skipping price persistence.",
                level="WARNING",
            )
            return

        cons_result = self._latest_raw_consumption_result
        prod_result = self._latest_raw_production_result

        if cons_result is None or prod_result is None:
            self.__log("Missing consumption or production data for DB persistence.")
            return

        resolution = c.PRICE_RESOLUTION_MINUTES

        # Build Series from raw FM results (prices in cents/kWh)
        cons_data = {}
        for i, price in enumerate(cons_result["prices"]):
            if price is not None:
                dt = cons_result["start"] + timedelta(minutes=i * resolution)
                cons_data[dt] = price

        prod_data = {}
        for i, price in enumerate(prod_result["prices"]):
            if price is not None:
                dt = prod_result["start"] + timedelta(minutes=i * resolution)
                prod_data[dt] = price

        if not cons_data or not prod_data:
            self.__log("No valid price data to persist.")
            return

        # Combine into DataFrame, keep only timestamps with both prices
        cons_series = pd.Series(cons_data, name="consumption_price_kwh")
        prod_series = pd.Series(prod_data, name="production_price_kwh")
        prices_df = pd.concat([cons_series, prod_series], axis=1, sort=True).dropna()

        if prices_df.empty:
            return

        # Upsample to 5-min resolution using reindex + forward-fill
        n_subintervals = resolution // 5
        full_index = pd.date_range(
            start=prices_df.index.min(),
            end=prices_df.index.max() + timedelta(minutes=resolution - 5),
            freq="5min",
        )
        prices_5min = prices_df.reindex(
            full_index, method="ffill", limit=n_subintervals - 1
        ).dropna()

        if prices_5min.empty:
            return

        # Convert cents/kWh to EUR/kWh
        if self.markup_per_kwh != 0 or self.vat_factor != 1:
            # nl_generic provider: apply markup and VAT
            prices_5min = (prices_5min + self.markup_per_kwh) * self.vat_factor / 100
        else:
            prices_5min = prices_5min / 100

        prices_5min = prices_5min.round(6)

        # Build row tuples for upsert_prices()
        rows = list(
            zip(
                (ts.tz_convert(timezone.utc).isoformat() for ts in prices_5min.index),
                prices_5min["consumption_price_kwh"],
                prices_5min["production_price_kwh"],
                [None] * len(prices_5min),
            )
        )

        if rows:
            self.data_store.upsert_prices(rows)
            self.__log(f"Persisted {len(rows)} EPEX price rows to local DB.")

        # Clean up temporary storage
        self._latest_raw_consumption_result = None
        self._latest_raw_production_result = None

    async def get_prices_wrapper(self, *args):
        """Wrapper for get_prices to be used as a callback with hass.run_in().

        AppDaemon's run_in passes kwargs as a dict in args[0].
        This method extracts the price_type and delegates to get_prices().

        Args:
            args: AppDaemon callback args (kwargs passed as dict in args[0]).
                  Expects 'price_type' (str): 'consumption' or 'production'.
        """
        price_type = None
        if args and isinstance(args[0], dict):
            price_type = args[0].get("price_type")

        if price_type is None:
            self.__log(
                "get_prices_wrapper called without price_type, aborting.",
                level="WARNING",
            )
            return

        await self.get_prices(price_type=price_type)

    async def __check_if_prices_are_up_to_date(self, *args):
        """
        Only used for EPEX based contracts.
        Checks if ENTSOE data is up to date and, if not, notifies user and kicks off
        process to keep checking if notification can be removed.
        To be run once a day a few hours after the normal publication time of the prices
        and soon enough to give user a chance to take measures.

        Scenario C: prices not available at CHECK_DATA_STATUS_TIME — send push notification.
        Note: no title is set deliberately; a title makes notification content harder to
        read on the lock screen as the message already provides sufficient context.
        Future improvement: send as critical to admin only, non-critical to other users,
        so the admin can add prices to FM manually. Requires two separate notify_user calls.

        :return: Nothing
        """
        self.__log("Called")
        if not self.entsoe_data_is_up_to_date:
            self.__log("ENTSOE data not up to date, notifying user.")
            self.notifier.notify_user(
                message=(
                    "Electricity prices for tomorrow are not yet available. "
                    "Scheduling will continue based on existing data."
                ),
                title=None,
                tag="no_price_data",
                critical=False,  # To be reconsidered after user consultation
                send_to_all=False,
            )
            self._critical_price_notification_sent = True
            # Kick off process to clear the notification if prices arrive later.
            await self.hass.run_in(
                self.__check_if_prices_are_up_to_date_again,
                delay=self.CHECK_RESOLUTION_SECONDS,
            )

    async def __check_if_prices_are_up_to_date_again(self, *args):
        """
        Try to remove the notification about price data missing.
        Only used for EPEX based contracts, kicked off by __check_if_prices_are_up_to_date.
        Runs periodically until:
           - ENTSOE data is up to date again (prices arrived)
           - The TRY_UNTIL time is reached

        Scenario D: prices arrive before __check_if_prices_are_up_to_date fires (no push
                    was sent) — only the UI boolean needs clearing.
        Scenario E: prices arrive after the push notification was sent — clear the old
                    notification and send a "prices received" confirmation.
        Scenario F: TRY_UNTIL reached without prices — replace notification with a final
                    informational message.

        :param args: Not used, only for compatibility with 'run_in' method.
        :return: None
        """
        self.__log("Called")
        if self.entsoe_data_is_up_to_date:
            await self.v2g_main_app.set_price_is_up_to_date(is_up_to_date=True)
            if self._critical_price_notification_sent:
                # Scenario E: push notification was sent — replace with confirmation.
                self.notifier.clear_notification(tag="no_price_data")
                self.notifier.notify_user(
                    message=(
                        "Electricity prices for tomorrow have now been received. "
                        "Scheduling will resume as normal."
                    ),
                    title=None,
                    tag="no_price_data",
                    critical=False,
                    send_to_all=False,
                )
                self._critical_price_notification_sent = False
                self.__log(
                    "Prices received: push notification replaced with confirmation."
                )
            else:
                # Scenario D: prices arrived before any push was sent — UI boolean cleared.
                self.__log(
                    "Prices received: UI indicator cleared (no push notification was sent)."
                )
        elif is_local_now_between(
            start_time=self.GET_PRICES_TIME, end_time=self.TRY_UNTIL
        ):
            self.__log("ENTSOE data not up to date yet: scheduling recheck.")
            await self.hass.run_in(
                self.__check_if_prices_are_up_to_date_again,
                delay=self.CHECK_RESOLUTION_SECONDS,
            )
        else:
            # Scenario F: TRY_UNTIL reached without prices — replace notification with
            # a final informational message; the system will retry tomorrow.
            self.__log(
                "ENTSOE data not up to date and outside retry window: "
                "sending final notification."
            )
            self.notifier.notify_user(
                message=(
                    "Electricity prices for tomorrow have not been received. "
                    "V2G Liberty will automatically try again later today."
                ),
                title=None,
                tag="no_price_data",
                critical=False,
                send_to_all=False,
            )

    def __check_negative_price_notification(self, price_point: dict, price_type: str):
        """Method to check if the user needs to be notified about negative consumption
        and/or production prices in a combined message.
        Only used for daily price contracts. Business rules for more frequently updated prices
        are too complex and left to the energy provider.
        To be called from get_prices, only for consumption_prices when the negative prices are in
        the future, and send None if no negative prices are expected.

        :param price_point: dict {'time': <datetime> , 'price': <float>} or "No negative prices"
        :param price_type: str "consumption" or "production"
        :return: nothing
        """
        self.__log("Called")
        if not is_price_epex_based():
            return

        if price_type == "consumption":
            if price_point == self.first_future_negative_consumption_price_point:
                # Nothing change, do nothing
                return
            self.first_future_negative_consumption_price_point = price_point

        elif price_type == "production":
            if price_point == self.first_future_negative_production_price_point:
                # Nothing change, we do nothing
                return
            self.first_future_negative_production_price_point = price_point

        else:
            self.__log(f"unknown price_point type: {price_type}.", level="WARNING")
            return

        cpp = self.first_future_negative_consumption_price_point
        ppp = self.first_future_negative_production_price_point
        if cpp is None or ppp is None:
            # Either consumption or production price has not been retrieved yet
            # so it is not relevant to check for alert yet.
            return

        msg = ""
        if cpp != "No negative prices" and cpp == ppp:
            # For NL contracts
            msg = (
                f"From {cpp['time'].strftime(c.DATE_TIME_FORMAT)} price is "
                f"{cpp['price']} cent/kWh."
            )
        else:
            if cpp != "No negative prices":
                # There was a no consumption price point but now there is
                msg = (
                    f"From {cpp['time'].strftime(c.DATE_TIME_FORMAT)} consumption price is "
                    f"{cpp['price']} cent/kWh."
                )

            msg += " "
            if ppp != "No negative prices":
                msg += (
                    f"From {ppp['time'].strftime(c.DATE_TIME_FORMAT)} production price is "
                    f"{ppp['price']} cent/kWh."
                )

        if msg == " ":
            self.notifier.clear_notification(tag="negative_energy_prices")
            self.__log("Clearing negative price notification")
        else:
            self.notifier.notify_user(
                message=msg,
                title="Negative electricity price",
                tag="negative_energy_prices",
                critical=False,
                send_to_all=True,
                ttl=12 * 60 * 60,
            )
        self.__log(f"notify user with message: {msg}.")

        # Reset stored values.
        self.first_future_negative_consumption_price_point = None
        self.first_future_negative_production_price_point = None

        return
