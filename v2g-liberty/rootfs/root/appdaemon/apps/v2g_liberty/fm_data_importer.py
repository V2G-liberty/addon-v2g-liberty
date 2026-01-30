"""Module for importing price and usage data from FlexMeasures."""

from datetime import timedelta

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
from .data_import.utils.retry_handler import RetryHandler, RetryConfig
from .data_import.utils.datetime_utils import DatetimeUtils
from .data_import.validators.data_validator import DataValidator
from .data_import.processors.price_processor import PriceProcessor
from .data_import.processors.emission_processor import EmissionProcessor
from .data_import.processors.energy_processor import EnergyProcessor
from .data_import.fetchers.price_fetcher import PriceFetcher
from .data_import.fetchers.emission_fetcher import EmissionFetcher
from .data_import.fetchers.energy_fetcher import EnergyFetcher
from .data_import.fetchers.cost_fetcher import CostFetcher
from .data_import import fetch_timing as fm_constants


class FlexMeasuresDataImporter:
    """
    Get prices, emissions and cost data for display in the UI. Only the consumption prices
    and not the production (aka feed_in) prices are retrieved (and displayed).

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

    The cost data is, independent of price_type of provider contract, fetched daily in the early
    morning.
    """

    # CONSTANTS and variables
    vat_factor: float = 1
    markup_per_kwh: float = 0

    # Timing constants imported from data_import.fetch_timing for centralised management
    GET_PRICES_TIME: str = fm_constants.GET_PRICES_TIME
    GET_EMISSIONS_TIME: str = fm_constants.GET_EMISSIONS_TIME
    TRY_UNTIL: str = fm_constants.TRY_UNTIL
    CHECK_DATA_STATUS_TIME: str = fm_constants.CHECK_DATA_STATUS_TIME
    CHECK_RESOLUTION_SECONDS: int = fm_constants.CHECK_RESOLUTION_SECONDS

    consumption_price_is_up_to_date: bool = False
    production_price_is_up_to_date: bool = False
    emission_data_is_up_to_date: bool = False

    timer_id_daily_kickoff_price_data: str = ""
    timer_id_daily_kickoff_emissions_data: str = ""
    timer_id_daily_check_is_data_up_to_date: str = ""

    # Emissions /kwh in the last 7 days to now. Populated by a call to FM.
    # Used for:
    # + Intermediate storage to fill an entity for displaying the data in the graph
    # + Calculation of the emission (savings) in the last 7 days.
    emission_intensities: dict

    # For sending notifications to the user.
    v2g_main_app: object = None
    # For getting data from FM server
    _fm_client_app: object = None
    notifier: Notifier = None

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
        self.hass.run_daily(self.daily_kickoff_charging_data, start="01:15:00")

        self.emission_intensities = {}

        # These are variables are used to decide what message to send to the user and can have the
        # following values:
        # - None: when no prices have been collected yet.
        # - "No negative price": if there are no negative prices.
        # - A price_point dict: When a negative price is detected.
        self.first_future_negative_consumption_price_point = None
        self.first_future_negative_production_price_point = None

        # Initialise refactored components
        self._initialise_components()

        self.__log("Complete")

    def _initialise_components(self):
        """Initialise the refactored utility, processor, and fetcher components."""
        # Retry configuration for EPEX-based contracts
        retry_config = RetryConfig(
            start_time=self.GET_PRICES_TIME,
            end_time=self.TRY_UNTIL,
            interval_seconds=self.CHECK_RESOLUTION_SECONDS,
        )
        self.retry_handler = RetryHandler(self.hass, retry_config)

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
        self.energy_processor = EnergyProcessor(
            event_resolution_minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES
        )

        # Fetchers (fm_client_app will be set later via property setter)
        # These are initialised with None and updated when fm_client_app is set
        self.price_fetcher = None
        self.emission_fetcher = None
        self.energy_fetcher = None
        self.cost_fetcher = None

    def _initialise_fetchers(self):
        """Initialise fetcher components when fm_client_app becomes available."""
        self.price_fetcher = PriceFetcher(
            self.hass, self._fm_client_app, self.retry_handler
        )
        self.emission_fetcher = EmissionFetcher(
            self.hass, self._fm_client_app, self.retry_handler
        )
        self.energy_fetcher = EnergyFetcher(
            self.hass, self._fm_client_app, self.retry_handler
        )
        self.cost_fetcher = CostFetcher(
            self.hass, self._fm_client_app, self.retry_handler
        )
        self.__log("Fetcher components initialised.")

    async def initialize(self, v2g_args: str = None):
        """
        Second and final stage of initialisation. This is run from globals when
        settings have initialised/changed. This is separated :
        - is not always around self.first_try_time (for day-ahead contracts)
        - the data-changed might not fire at startup (external HA integration provided data)
        This is delayed as it's not high priority and gives globals the time to get all settings
        loaded correctly.
        """

        self.__log(f"Called from source: {v2g_args}.")

        # This way the markup/vat can be retained "underwater" in the settings and do not have
        # to be reset
        if c.USE_VAT_AND_MARKUP:
            self.vat_factor = (100 + c.ENERGY_PRICE_VAT) / 100
            self.markup_per_kwh = c.ENERGY_PRICE_MARKUP_PER_KWH
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

            self.timer_id_daily_kickoff_emissions_data = await self.hass.run_daily(
                self.daily_kickoff_emissions_data, start=self.GET_EMISSIONS_TIME
            )

            self.timer_id_daily_check_is_data_up_to_date = await self.hass.run_daily(
                self.__check_if_prices_are_up_to_date, start=self.CHECK_DATA_STATUS_TIME
            )

            initial_delay_sec = 45
            await self.hass.run_in(
                self.daily_kickoff_price_data, delay=initial_delay_sec
            )
            await self.hass.run_in(
                self.daily_kickoff_emissions_data, delay=initial_delay_sec
            )
            await self.hass.run_in(
                self.daily_kickoff_charging_data, delay=initial_delay_sec
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
        """
        self.__log(f"Called, args: {args}.")

        self.consumption_price_is_up_to_date = None
        await self.get_prices(price_type="consumption")

        self.production_price_is_up_to_date = None
        await self.get_prices(price_type="production")

        self.__log("completed")

    async def daily_kickoff_emissions_data(self, *args):
        """
        This sets off the daily routine to check for new emission data.
        Only called when is_price_epex_based() is true.
        """
        await self.get_emission_intensities()

    async def daily_kickoff_charging_data(self, *args):
        """This sets off the daily routine to check for charging cost."""
        self.__log("Called")
        await self.get_charging_cost()
        await self.get_charged_energy()

    async def get_charging_cost(self, *args, **kwargs):
        """Communicate with FM server and check the results.

        Request charging costs of last 7 days from the server.
        Make costs total costs of this period available in HA by setting them in sensor.
        ToDo: Split cost in charging and dis-charging per day
        """
        self.__log("Called")

        # Use CostFetcher to retrieve data
        if self.cost_fetcher is None:
            self.__log("CostFetcher not initialised (fm_client_app not set yet).")
            return False

        now = get_local_now()
        result = await self.cost_fetcher.fetch_costs(now)

        if result is None:
            self.__log(
                "CostFetcher returned None, aborting.",
                level="WARNING",
            )
            return False

        # Process the cost data
        costs = result["costs"]
        start = result["start"]
        resolution = timedelta(days=1)

        total_charging_cost_last_7_days = 0.0
        charging_cost_points = []

        for i, charging_cost in enumerate(costs):
            if charging_cost is None:
                continue
            self.__log(f"cost: '{charging_cost}'.")
            data_point = {
                "time": (start + i * resolution).isoformat(),
                "cost": round(float(charging_cost), 2),
            }
            total_charging_cost_last_7_days += data_point["cost"]
            charging_cost_points.append(data_point)

        if len(charging_cost_points) == 0:
            self.__log("No charging cost data available")

        total_charging_cost_last_7_days = round(total_charging_cost_last_7_days, 2)
        self.__log(
            f"Cost data: {charging_cost_points}, total costs: {total_charging_cost_last_7_days}"
        )

        await self.hass.set_state(
            entity_id="sensor.total_charging_cost_last_7_days",
            state=total_charging_cost_last_7_days,
        )

    async def get_charged_energy(self, *args, **kwargs):
        """Communicate with FM server and check the results.

        Request charging volumes of last 7 days from the server.
        ToDo: make this period a setting for the user.
        Make totals of charging and dis-charging per day and over the period.
        """
        self.__log("Called.")

        # Use EnergyFetcher to retrieve data
        if self.energy_fetcher is None:
            self.__log("EnergyFetcher not initialised (fm_client_app not set yet).")
            return False

        now = get_local_now()
        result = await self.energy_fetcher.fetch_power_data(now)

        if result is None:
            self.__log(
                "EnergyFetcher returned None, aborting.",
                level="WARNING",
            )
            return False

        # Use EnergyProcessor to calculate statistics
        power_values = result["power_values"]
        start = result["start"]

        self.__log(
            f"Power data received: {len(power_values)} values starting from {start}."
        )

        stats = self.energy_processor.calculate_energy_stats(
            power_values=power_values,
            start=start,
            emission_cache=self.emission_intensities,
        )

        # Update all sensors with calculated statistics
        await self.hass.set_state(
            entity_id="sensor.total_discharged_energy_last_7_days",
            state=stats.total_discharged_energy_kwh,
        )
        await self.hass.set_state(
            entity_id="sensor.total_charged_energy_last_7_days",
            state=stats.total_charged_energy_kwh,
        )
        await self.hass.set_state(
            entity_id="sensor.net_energy_last_7_days",
            state=stats.net_energy_kwh,
        )
        await self.hass.set_state(
            entity_id="sensor.total_saved_emissions_last_7_days",
            state=stats.total_saved_emissions_kg,
        )
        await self.hass.set_state(
            entity_id="sensor.total_emissions_last_7_days",
            state=stats.total_emissions_kg,
        )
        await self.hass.set_state(
            entity_id="sensor.net_emissions_last_7_days",
            state=stats.net_emissions_kg,
        )
        await self.hass.set_state(
            entity_id="sensor.total_discharge_time_last_7_days",
            state=stats.total_discharge_time,
        )
        await self.hass.set_state(
            entity_id="sensor.total_charge_time_last_7_days",
            state=stats.total_charge_time,
        )

        self.__log(
            f"stats: \n"
            f"    total_discharged_energy: '{stats.total_discharged_energy_kwh}' kWh\n"
            f"    total_charged_energy: '{stats.total_charged_energy_kwh}' kWh\n"
            f"    total_saved_emissions: '{stats.total_saved_emissions_kg}' kg\n"
            f"    total_emissions: '{stats.total_emissions_kg}' kg\n"
            f"    total discharge time: '{stats.total_discharge_time}'\n"
            f"    total charge time: '{stats.total_charge_time}'"
        )

    async def get_emission_intensities(self, *args, **kwargs):
        """Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production from the server.
        Make values available in HA by setting them in sensor.co2_emissions.
        """
        self.__log("Called")

        # Use EmissionFetcher to retrieve data
        if self.emission_fetcher is None:
            self.__log("EmissionFetcher not initialised (fm_client_app not set yet).")
            return await self._handle_emission_fetch_failure(
                "fm_client not available yet"
            )

        now = get_local_now()
        result = await self.emission_fetcher.fetch_emissions(now)

        if result is None:
            return await self._handle_emission_fetch_failure("failed")

        # Use EmissionProcessor to process the data
        emission_cache, chart_points, latest_emission_dt = (
            self.emission_processor.process_emissions(
                raw_emissions=result["emissions"],
                start=result["start"],
                now=now,
                history_hours=5,
            )
        )

        # Update the emission_intensities cache for use by get_charged_energy()
        self.emission_intensities.clear()
        self.emission_intensities.update(emission_cache)

        # Update the chart
        await self.v2g_main_app.set_records_in_chart(
            chart_line_name=ChartLine.EMISSION, records=chart_points
        )

        # Validate freshness for EPEX-based contracts
        if latest_emission_dt is None:
            return await self._handle_emission_fetch_failure("no valid data received")

        if is_price_epex_based():
            is_valid, error_message = self.data_validator.validate_emission_freshness(
                latest_emission_dt=latest_emission_dt,
                now=now,
                resolution_minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES,
            )
            if not is_valid:
                return await self._handle_emission_fetch_failure(error_message)

        self.__log("emissions successfully retrieved.")
        return "emissions successfully retrieved."

    async def _handle_emission_fetch_failure(self, failure_message: str):
        """Handle emission fetch failure with retry logic for EPEX-based contracts."""
        if not is_price_epex_based():
            # Non-EPEX contracts don't retry
            return False

        if is_local_now_between(
            start_time=self.GET_EMISSIONS_TIME, end_time=self.TRY_UNTIL
        ):
            await self.hass.run_in(
                self.get_emission_intensities, delay=self.CHECK_RESOLUTION_SECONDS
            )
            self.__log(
                f"{failure_message}, try again in '{self.CHECK_RESOLUTION_SECONDS}' sec."
            )
        else:
            self.__log(
                f"{failure_message}, 'now' is out of time bounds "
                f"start: '{self.GET_EMISSIONS_TIME}' - end: '{self.TRY_UNTIL}', not retrying."
            )
        return False

    async def get_prices_wrapper(self, kwargs):
        """Wrapper to fix strange run_in behaviour"""
        price_type = kwargs.get("price_type")
        self.__log(f"WRAPPER, price_type: {price_type}")
        await self.get_prices(price_type=price_type)

    async def get_prices(self, price_type: str):
        """
        Gets consumption / production prices from the server via the fm_client.

        Check if prices are up to date, if not:
         - show a message in UI by calling set_price_is_up_to_date() on main app.
         - repeat this method every x minutes
         - set class variable consumption_price_is_up_to_date / production_price_is_up_to_date to
           False (for notification to user, see __check_if_prices_are_up_to_date() ).

        If changed from not-up-to-date to up-to-date, try to remove a possible notification by
        calling __check_if_prices_are_up_to_date_again()

        Check if any negative prices are present in the future, if so, notify the user.

        Make prices available in HA.

        :param price_type: 'consumption' or 'production'
        :return: string or boolean (not used).
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
            return await self._handle_price_fetch_failure(
                "fm_client not available yet", price_type
            )

        now = get_local_now()
        result = await self.price_fetcher.fetch_prices(price_type, now)

        if result is None:
            return await self._handle_price_fetch_failure(
                f"get_prices failed for entsoe / {price_type}", price_type
            )

        # Extract entsoe_latest_price_dt for EFP (end of fixed prices) boundary
        # entsoe_latest_price_dt is the START of the last price block (e.g., 23:45)
        # We need the END of that block for the visual split, so add one resolution period
        entsoe_latest_price_dt = result["entsoe_latest_price_dt"]
        end_of_fixed_prices_dt = None
        if entsoe_latest_price_dt and is_price_epex_based():
            end_of_fixed_prices_dt = entsoe_latest_price_dt + timedelta(
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

        # Handle negative price notification
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

        # Validate freshness for EPEX-based contracts
        if entsoe_latest_price_dt is None:
            return await self._handle_price_fetch_failure(
                f"no valid {price_type} prices received", price_type
            )

        if is_price_epex_based():
            is_valid, error_message = self.data_validator.validate_price_freshness(
                latest_price_dt=entsoe_latest_price_dt,
                now=now,
                fetch_start_time=self.GET_PRICES_TIME,
            )

            # Track whether prices were previously up to date for notification handling
            price_was_up_to_date = (
                self.consumption_price_is_up_to_date
                if price_type == "consumption"
                else self.production_price_is_up_to_date
            )

            # Update the status
            if price_type == "consumption":
                self.consumption_price_is_up_to_date = is_valid
            else:
                self.production_price_is_up_to_date = is_valid

            self.__log(
                f"({price_type}): is up to date: '{is_valid}' based on "
                f"latest_price ({entsoe_latest_price_dt})."
            )

            if not is_valid:
                # Set it in the UI right away
                await self.v2g_main_app.set_price_is_up_to_date(is_up_to_date=False)
                return await self._handle_price_fetch_failure(error_message, price_type)

            # Check if prices became up to date again (was False, now True)
            if not price_was_up_to_date and is_valid:
                self.__log(f"{price_type} prices are up to date again.")
                await self.__check_if_prices_are_up_to_date_again(run_once=True)

        self.__log(f"{price_type} prices successfully retrieved.")
        return "prices successfully retrieved."

    async def _handle_price_fetch_failure(
        self, failure_message: str, price_type: str
    ) -> bool:
        """Handle price fetch failure with retry logic for EPEX-based contracts."""
        if not is_price_epex_based():
            self.__log(
                f"({price_type}): {failure_message}, not EPEX based: not retrying."
            )
            return False

        if is_local_now_between(
            start_time=self.GET_PRICES_TIME, end_time=self.TRY_UNTIL
        ):
            await self.hass.run_in(
                self.get_prices_wrapper,
                delay=self.CHECK_RESOLUTION_SECONDS,
                price_type=price_type,
            )
            self.__log(
                f"Failed to get {price_type}: {failure_message}, "
                f"try again in '{self.CHECK_RESOLUTION_SECONDS}' sec."
            )
        else:
            self.__log(
                f"({price_type}): {failure_message}, 'now' is out of time bounds "
                f"start: '{self.GET_PRICES_TIME}' - end: '{self.TRY_UNTIL}', not retrying."
            )
        return False

    async def __check_if_prices_are_up_to_date(self, *args):
        """
        Only used for EPEX based contracts, only aimed at price data, not emissions data.
        Checks if price data is uptodate and, if not, notifies user and kicks off proces to keep
        checking if notification can be removed.
        To be run once a day a few hours after the normal publication time of the prices and soon
        enough to give user a chance to take measures.
        :return: Nothing
        """
        self.__log("Called")
        unavailable = ""
        if not self.consumption_price_is_up_to_date:
            unavailable = "Consumption"
        if not self.production_price_is_up_to_date:
            unavailable = "Production"
        if (
            not self.consumption_price_is_up_to_date
            and not self.production_price_is_up_to_date
        ):
            unavailable = "Consumption and production"
        if unavailable != "":
            self.__log(f"Unavailable price(s): '{unavailable=}'.")
            self.notifier.notify_user(
                message=f"{unavailable} price not available, cloud not check for negative prices. "
                f"Scheduling should continue as normal.",
                title=None,
                tag="no_price_data",
                critical=False,
                send_to_all=False,
            )
            # Kickoff process to clear the notification if possible.
            await self.hass.run_in(
                self.__check_if_prices_are_up_to_date_again,
                run_once=False,
                delay=self.CHECK_RESOLUTION_SECONDS,
            )

    async def __check_if_prices_are_up_to_date_again(
        self,
        *args,
        run_once: bool = False,
    ):
        """
        Try to remove the notification about price data missing.
        Only used for EPEX based contracts, kicked off by __check_if_prices_are_up_to_date.
        To be run once every hour until:
           - Prices are up to date again
           - The TRY_UNTIL time

        :param run_once: To prevent extra threads where this method calls itself,
                         used when get_prices detects a change
                         in self.consumption_price_is_up_to_date or
                         self.consumption_price_is_up_to_date.
        :param args: Not used, only for compatibility with 'run_in' method.
        :return: None
        """
        self.__log(" called")
        if (
            self.consumption_price_is_up_to_date
            and self.consumption_price_is_up_to_date
        ):
            await self.v2g_main_app.set_price_is_up_to_date(is_up_to_date=True)
            self.notifier.clear_notification(tag="no_price_data")
            self.__log("prices up to date again: notification cleared.")
        else:
            if run_once:
                self.__log(
                    "prices not up to date but, called with 'run_once': no re-run."
                )
            elif is_local_now_between(
                start_time=self.GET_PRICES_TIME, end_time=self.TRY_UNTIL
            ):
                self.__log("prices not up to date yet: rerun.")
                await self.hass.run_in(
                    self.__check_if_prices_are_up_to_date_again,
                    run_once=False,
                    delay=self.CHECK_RESOLUTION_SECONDS,
                )
            else:
                self.__log(
                    "prices not up to date but, 'now' is out of time bounds, no re-run."
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
