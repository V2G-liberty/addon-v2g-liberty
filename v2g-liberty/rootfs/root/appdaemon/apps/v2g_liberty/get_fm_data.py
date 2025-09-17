"""Module for importing price and usgae data from FlexMeaures"""

from datetime import datetime, timedelta
import math
from appdaemon.plugins.hass.hassapi import Hass
from .v2g_globals import (
    time_ceil,
    time_floor,
    get_local_now,
    is_price_epex_based,
    is_local_now_between,
    convert_to_duration_string,
)
from . import constants as c
from .notifier_util import Notifier
from .log_wrapper import get_class_method_logger
from .main_app import ChartLine


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
    DAYS_HISTORY: int = 7

    vat_factor: float = 1
    markup_per_kwh: float = 0

    # Price data should normally be available just after 13:00 when data can be
    # retrieved from its original source (ENTSO-E) but sometimes there is a delay of several hours.
    GET_PRICES_TIME: str = "13:35:51"  # When to start check for prices.
    GET_EMISSIONS_TIME: str = "13:45:26"  # When to start check for emissions.

    # If not successful retry every x minutes until this time (the next day)
    TRY_UNTIL: str = "11:22:33"

    # When to check if price data is up to date, and if not notify the user.
    CHECK_DATA_STATUS_TIME: str = "18:34:52"

    # Delay between checks when no data was found
    CHECK_RESOLUTION_SECONDS: int = 30 * 60

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
    fm_client_app: object = None
    notifier: Notifier = None

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

        ##########################################################################
        # TESTDATA: Currency = EUR, moet AUD zijn! Wordt in class definitie al gezet.
        ##########################################################################
        # self.__log(
        #     "- - - T E S T D A T A - - -: Currency = EUR, moet GBP/AUD zijn!",
        #     level="WARNING",
        # )
        # c.CURRENCY = "EUR"

        ##########################################################################
        # TESTDATA: EMISSIONS_UOM = "%", should be kg/MWh
        ##########################################################################
        # self.__log(
        #     "- - - T E S T D A T A - - -: self.EMISSIONS_UOM = %, moet kg/MWh zijn!",
        #     level="WARNING",
        # )
        # c.EMISSIONS_UOM = "%"

        self.__log("Complete")

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

        Request charging costs of last 7 days from the server
        Make costs total costs of this period available in HA by setting them in sensor
        ToDo: Split cost in charging and dis-charging per day
        """
        self.__log("Called")
        now = get_local_now()

        # Getting data since a week so that user can look back a further than just current window.
        start = time_floor(now + timedelta(days=-self.DAYS_HISTORY), timedelta(days=1))
        duration = timedelta(days=self.DAYS_HISTORY)  # Duration as timedelta
        duration = round((duration.total_seconds() / 60), 0)  # Duration in minutes
        duration = convert_to_duration_string(duration)  # Duration as iso string
        if self.fm_client_app is not None:
            charging_costs = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_ACCOUNT_COST_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution="P1D",
                uom=c.CURRENCY,
            )
        else:
            self.__log("Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        self.__log(
            f"sensor_id: {c.FM_ACCOUNT_COST_SENSOR_ID}, charging_costs: {charging_costs}."
        )

        if charging_costs is None:
            self.__log(
                "get_sensor_data on fm_client_app returned None, aborting.",
                level="WARNING",
            )
            # TODO: When to retry?
            return

        total_charging_cost_last_7_days = 0
        charging_cost_points = []
        resolution = timedelta(days=1)
        charging_costs = charging_costs["values"]
        for i, charging_cost in enumerate(charging_costs):
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
            # TODO: All data points are None, what to do?
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
        Make totals of charging and dis-charging per day and over the period

        """
        self.__log("Called.")

        now = get_local_now()
        # Getting data since a week
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))

        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{self.DAYS_HISTORY}D"

        # TODO: change uom to kW, this way the server can do the conversion (more efficient?).
        if self.fm_client_app is not None:
            res = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution=resolution,
                uom="MW",
            )
        else:
            self.__log("Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        if res is None:
            self.__log(
                "get_sensor_data on fm_client_app returned None, aborting.",
                level="WARNING",
            )
            return

        # The res structure:
        # 'duration': 'PT168H',
        # 'start': '2024-09-02T00:00:00+02:00',
        # 'unit': 'MW',
        # 'values': [0.004321, None, ..., 0.005712]
        self.__log(
            f"sensor_id: {c.FM_ACCOUNT_POWER_SENSOR_ID}, "
            f"charge power response: {str(res)[:75]} ... {str(res)[-25:]}."
        )

        total_charged_energy_last_7_days = 0
        total_discharged_energy_last_7_days = 0
        total_emissions_last_7_days = 0
        total_saved_emissions_last_7_days = 0
        total_minutes_charged = 0
        total_minutes_discharged = 0
        charging_energy_points = {}

        charge_power_points = res["values"]
        for i, charge_power in enumerate(charge_power_points):
            if charge_power is None:
                continue
            power = float(charge_power)
            key = start + timedelta(minutes=(i * c.FM_EVENT_RESOLUTION_IN_MINUTES))
            charging_energy_points[key] = power

            # Look up the emission matching with power['event_start'], this will be a match every
            # 3 items as emission has a resolution of 15 minutes and power of 5 min.
            # ToDo: check if resolutions match X times, if not, raise an error.
            emission_intensity = self.emission_intensities.get(key, 0)

            if power < 0:
                # Strangely we add power to energy, this is practical, later convert this to energy.
                total_discharged_energy_last_7_days += power
                total_minutes_discharged += c.FM_EVENT_RESOLUTION_IN_MINUTES
                # We strangely add 5 min. periods as if they are hours, we later converty this
                total_saved_emissions_last_7_days += power * emission_intensity
            elif power > 0:
                # Strangely we add power to energy, this is practical, later convert this to energy.
                total_charged_energy_last_7_days += power
                total_minutes_charged += c.FM_EVENT_RESOLUTION_IN_MINUTES
                # We strangely add 5 min. periods as if they are hours, we later converty this
                total_emissions_last_7_days += power * emission_intensity

        # Convert the returned average power in MW over event_resolution ( 5 minutes)
        # periods to kWh *1000/12 to energy in kWh
        conversion_factor = 1000 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_discharged_energy_last_7_days = int(
            round(total_discharged_energy_last_7_days * conversion_factor, 0)
        )
        total_charged_energy_last_7_days = int(
            round(total_charged_energy_last_7_days * conversion_factor, 0)
        )

        # Convert the returned average MW * kg/MWh over event_resolution (5 minutes) periods to kg
        conversion_factor = 1 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_saved_emissions_last_7_days = round(
            total_saved_emissions_last_7_days * conversion_factor, 1
        )
        total_emissions_last_7_days = round(
            total_emissions_last_7_days * conversion_factor, 1
        )

        await self.hass.set_state(
            entity_id="sensor.total_discharged_energy_last_7_days",
            state=total_discharged_energy_last_7_days,
        )
        await self.hass.set_state(
            entity_id="sensor.total_charged_energy_last_7_days",
            state=total_charged_energy_last_7_days,
        )
        await self.hass.set_state(
            entity_id="sensor.net_energy_last_7_days",
            state=total_charged_energy_last_7_days
            + total_discharged_energy_last_7_days,
        )

        await self.hass.set_state(
            entity_id="sensor.total_saved_emissions_last_7_days",
            state=total_saved_emissions_last_7_days,
        )
        await self.hass.set_state(
            entity_id="sensor.total_emissions_last_7_days",
            state=total_emissions_last_7_days,
        )
        await self.hass.set_state(
            entity_id="sensor.net_emissions_last_7_days",
            state=total_emissions_last_7_days + total_saved_emissions_last_7_days,
        )

        await self.hass.set_state(
            entity_id="sensor.total_discharge_time_last_7_days",
            state=format_duration(total_minutes_discharged),
        )
        await self.hass.set_state(
            entity_id="sensor.total_charge_time_last_7_days",
            state=format_duration(total_minutes_charged),
        )
        self.__log(
            f"stats: \n"
            f"    total_discharged_energy_last_7_days: '{total_discharged_energy_last_7_days}' \n"
            f"    total_charged_energy_last_7_days: '{total_charged_energy_last_7_days}' \n"
            f"    total_saved_emissions_last_7_days: '{total_saved_emissions_last_7_days}' \n"
            f"    total_emissions_last_7_days: '{total_emissions_last_7_days}' \n"
            f"    total discharge time: '{format_duration(total_minutes_discharged)}' \n"
            f"    total charge time: '{format_duration(total_minutes_charged)}'"
        )

    async def get_emission_intensities(self, *args, **kwargs):
        """Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production in NL from the server
        Make values available in HA by setting them in sensor.co2_emissions
        """

        self.__log("Called")
        now = get_local_now()
        # Getting emissions since a week ago. This is needed for calculation of CO2 savings
        # and will be (more than) enough for the graph to show.
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{self.DAYS_HISTORY + 2}D"
        failure_message = ""

        if self.fm_client_app is not None:
            emissions = await self.fm_client_app.get_sensor_data(
                sensor_id=c.FM_EMISSIONS_SENSOR_ID,
                start=start.isoformat(),
                duration=duration,
                resolution=resolution,
                uom=c.EMISSIONS_UOM,
            )

            self.__log(f"emissions: {str(emissions)[:175]}...{str(emissions)[-75:]}.")
            if emissions is None:
                failure_message = "failed"
            else:
                # For use in graph
                emission_points = []

                # For use in calculations, it is cleared as we collect new values. previous values
                self.emission_intensities.clear()

                # We assume start and resolution match the request, and it is not necessary to
                # retrieve these from the response.
                emissions = emissions["values"]
                previous_value = None
                show_in_graph_after = now + timedelta(hours=-5)
                date_latest_emission = (
                    None  # Latest emission with value that is not None
                )
                for i, emission_value in enumerate(emissions):
                    if emission_value is None:
                        continue
                    # Set the real value for use in calculations later
                    emission_start = start + timedelta(
                        minutes=i * c.FM_EVENT_RESOLUTION_IN_MINUTES
                    )
                    date_latest_emission = emission_start
                    self.emission_intensities[emission_start] = emission_value

                    # Adapt value for showing in graph. To optimise we only add points that are
                    # max 5 hours old and actually show a change.
                    if (
                        emission_value != previous_value
                        and emission_start > show_in_graph_after
                    ):
                        emission_value = int(round(float(emission_value) / 10, 0))
                        data_point = {
                            "time": emission_start.isoformat(),
                            "emission": emission_value,
                        }
                        emission_points.append(data_point)
                    previous_value = emission_value

                await self.v2g_main_app.set_records_in_chart(
                    chart_line_name=ChartLine.EMISSION, records=emission_points
                )
                if date_latest_emission is None:
                    failure_message = "no valid data received"
                elif is_price_epex_based():
                    # FM returns all the emissions it has, sometimes it has not retrieved new
                    # emissions yet, than it communicates the emissions it does have.
                    date_tomorrow = now + timedelta(days=1)
                    date_tomorrow = time_ceil(date_tomorrow, timedelta(days=1))
                    # As the emission is for the hour 23:00 - 23:59:59 we need to
                    # subtract one hour and, to give some slack, 1x resolution
                    date_tomorrow += timedelta(
                        minutes=-(60 + c.FM_EVENT_RESOLUTION_IN_MINUTES)
                    )
                    if date_latest_emission < date_tomorrow:
                        failure_message = "emissions are not up to date"
        else:
            self.__log("Could not call get_sensor_data on fm_client_app as it is None.")
            failure_message = "fm_client not available yet"

        if failure_message != "":
            if not is_price_epex_based():
                pass
                # self.__log(f"{failure_message}, not EPEX based: not retrying.")
            elif is_local_now_between(
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

        self.__log("emissions successfully retrieved.")
        # The method needs to return something for the awaited calls to this method to work...
        return "emissions successfully retrieved."

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
         - repeat this methode every x minutes
         - set class variable consumption_price_is_up_to_date / production_price_is_up_to_date to
           False (for notification to user, see __check_if_prices_are_up_to_date() ).

        If changed from not-up-to-date to up-to-date, try to remove a possible notification by
        calling __check_if_prices_are_up_to_date_again()

        Check if any negative prices are present in the future, if so, notify the user.

        Make prices available in HA.

        :param parameters: dict {'price_type': 'consumption' or 'production'}
        :return: string or boolean (not used).
        """

        if price_type not in ["consumption", "production"]:
            self.__log(
                f"called with unknown price_type: '{price_type}'.", level="WARNING"
            )
            return False

        self.__log(f"Called for {price_type}.")

        failure_message = ""
        now = get_local_now()

        # Determine days back based on current time
        if is_local_now_between(start_time=self.GET_PRICES_TIME, end_time="23:59:59"):
            days_back = 1
        else:
            days_back = 2
        start = time_floor(now - timedelta(days=days_back), timedelta(days=1))
        if self.fm_client_app is not None:
            sensor_id = (
                c.FM_PRICE_CONSUMPTION_SENSOR_ID
                if price_type == "consumption"
                else c.FM_PRICE_PRODUCTION_SENSOR_ID
            )
            ##########################################################################
            # TESTDATA: Currency = EUR, should be GBP! Is set in class definition.
            ##########################################################################
            # self.__log("- - - T E S T D A T A - - -: Currency = EUR!", level="WARNING")
            # c.CURRENCY = "EUR"

            prices = await self.fm_client_app.get_sensor_data(
                sensor_id=sensor_id,
                start=start.isoformat(),
                duration="P3D",
                resolution=f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom=f"c{c.CURRENCY}/kWh",
            )
            self.__log(f"({price_type}) | sensor_id: {sensor_id}, prices: {prices}.")
            date_latest_price = None
            net_price = None
            if prices is None:
                failure_message = f"get_prices failed for {price_type}"
            else:
                price_points = []
                first_future_negative_price_point = "No negative prices"
                prices = prices["values"]
                none_prices = 0
                for i, price in enumerate(prices):
                    if price is None:
                        none_prices += 1
                        continue
                    dt = start + timedelta(minutes=(i * c.PRICE_RESOLUTION_MINUTES))
                    date_latest_price = dt
                    net_price = round(
                        (price + self.markup_per_kwh) * self.vat_factor, 2
                    )
                    data_point = {"time": dt.isoformat(), "price": net_price}
                    price_points.append(data_point)

                    if (
                        first_future_negative_price_point == "No negative prices"
                        and data_point["price"] < 0
                        and dt > now
                    ):
                        self.__log(
                            f"({price_type}), negative price: {data_point['price']} at: {dt}."
                        )
                        first_future_negative_price_point = {
                            "time": dt,
                            "price": data_point["price"],
                        }

                self.__log(
                    f"({price_type}) | number of prices: '{len(price_points)}', "
                    f"number of none values '{none_prices}'."
                )

                self.__check_negative_price_notification(
                    first_future_negative_price_point, price_type=price_type
                )

                # To make the step-line in the chart extend to the end of the last (half)hour (or
                # what the resolution might be), a value is added at the end. Not an ideal solution
                # but the chart does not have the option to do this.
                if net_price is not None:
                    data_point = {
                        "time": (
                            dt + timedelta(minutes=c.PRICE_RESOLUTION_MINUTES)
                        ).isoformat(),
                        "price": net_price,
                    }
                    price_points.append(data_point)

                await self.v2g_main_app.set_records_in_chart(
                    chart_line_name=ChartLine.CONSUMPTION_PRICE
                    if price_type == "consumption"
                    else ChartLine.PRODUCTION_PRICE,
                    records=price_points,
                )

                if date_latest_price is None:
                    failure_message = f"no valid {price_type} prices received"
                elif is_price_epex_based():
                    # We expect prices till the end of the day tomorrow
                    # (or today if prices are really late).
                    if is_local_now_between(
                        start_time=self.GET_PRICES_TIME, end_time="23:59:59"
                    ):
                        expected_price_dt = now + timedelta(days=1)
                    else:
                        expected_price_dt = now
                    # Round it to the end of the day
                    expected_price_dt = time_ceil(expected_price_dt, timedelta(days=1))

                    # As the last price is valid for the hour 23:00:00 - 23:59:59 so we need to
                    # subtract one hour and a little extra to give it some slack.
                    expected_price_dt -= timedelta(minutes=65)
                    is_up_to_date = date_latest_price > expected_price_dt
                    if not is_up_to_date:
                        # Set it in th UI right away, no matter which price type it is.
                        # Notification is done later via __check_if_prices_are_up_to_date()
                        await self.v2g_main_app.set_price_is_up_to_date(
                            is_up_to_date=False
                        )

                    self.__log(
                        f"({price_type}): is up to date: '{is_up_to_date}' based on "
                        f"latest_price ({date_latest_price}) > "
                        f"expected_price_dt ({expected_price_dt})."
                    )
                    price_is_up_to_date = (
                        self.consumption_price_is_up_to_date
                        if price_type == "consumption"
                        else self.production_price_is_up_to_date
                    )
                    needs_update_check = not price_is_up_to_date and is_up_to_date
                    if price_type == "consumption":
                        self.consumption_price_is_up_to_date = is_up_to_date
                    else:
                        self.production_price_is_up_to_date = is_up_to_date

                    if not is_up_to_date:
                        failure_message = "prices not up to date"
                        self.__log(f"({price_type}), {failure_message}.")

                    if needs_update_check:
                        self.__log(f"{price_type} prices are up to date again.")
                        await self.__check_if_prices_are_up_to_date_again(run_once=True)
        else:
            self.__log(
                f"({price_type}). Could not call get_sensor_data on fm_client_app as it is None."
            )
            failure_message = "fm_client not available yet"

        if failure_message != "":
            if not is_price_epex_based():
                self.__log(
                    f"({price_type}): {failure_message}, not EPEX based: not retrying."
                )
            elif is_local_now_between(
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

        self.__log(f"{price_type} prices successfully retrieved.")
        return "prices successfully retrieved."

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


def format_duration(duration_in_minutes: int):
    """Format a duration in minutes for presentation in UI (eg. 86735 = 1d 5h 35m)"""
    mid = 60 * 24  # Minutes in a day
    days = math.floor(duration_in_minutes / mid)
    hours = math.floor((duration_in_minutes - days * mid) / 60)
    minutes = duration_in_minutes - days * mid - hours * 60
    return f"{days:02d}d {hours:02d}h {minutes:02d}m"
