from datetime import datetime, timedelta
import pytz
import math
import re
import time
import asyncio
from v2g_globals import time_ceil, time_floor, get_local_now, is_price_epex_based, convert_to_duration_string
import constants as c
from v2g_liberty import ChartLine
from typing import AsyncGenerator, List, Optional
import appdaemon.plugins.hass.hassapi as hass
import isodate


class FlexMeasuresDataImporter(hass.Hass):
    # CONSTANTS
    DAYS_HISTORY: int = 7

    # For converting raw EPEX prices from FM to user_friendly UI values
    price_conversion_factor: float = 1 / 10
    vat_factor: float = 1

    # Variables
    first_try_time_price_data: str
    second_try_time_price_data: str

    first_try_time_emissions_data: str
    second_try_time_emissions_data: str

    timer_id_daily_kickoff_price_data: str = ""
    timer_id_daily_kickoff_emissions_data: str = ""

    # Emissions /kwh in the last 7 days to now. Populated by a call to FM.
    # Used for:
    # + Intermediate storage to fill an entity for displaying the data in the graph
    # + Calculation of the emission (savings) in the last 7 days.
    emission_intensities: dict

    # For sending notifications to the user.
    v2g_main_app: object = None
    # For getting data from FM server
    fm_client_app: object = None

    first_future_negative_consumption_price_point: dict
    first_future_negative_production_price_point: dict


    async def initialize(self):
        """
        Get prices, emissions and cost data for display in the UI. Only the consumption prices
        and not the production (aka feed_in) prices are retrieved (and displayed).

        Price/emissions data fetched daily when:
        For providers with contracts based on a (EPEX) day-ahead market this should be fetched daily
        after the prices have been published, usually around 14:35. When this fails retry at 18:30.
        These times are related to the attempts in the FM server for retrieving EPEX price data.

        Price/emissions data fetched on data change:
        When price data comes in through an (external) HA integration (e.g. Amber Electric or Octopus),
        V2G Liberty sends this to FM to base the charge-schedules on. This is handled by separate modules
        (e.g. amber_price_data_manager).
        Those modules do not provide the data for display in the UI directly (even-though this could be more
        efficient). Instead, they trigger get_consumption_prices() and get_emission_intensities() when a change
        in the data is detected (after it has been sent to FM successfully).

        The retrieved data is written to the HA entities for HA to render the data in the UI (chart).

        The cost data is, independent of price_type of provider contract, fetched daily in the early morning.

        """
        self.log("Initializing FlexMeasuresDataImporter")

        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.fm_client_app = await self.get_app("fm_client")

        # The number of days in the history for statistics
        self.DAYS_HISTORY  = 7

        # Price data should normally be available just after 13:00 when data can be
        # retrieved from its original source (ENTSO-E) but sometimes there is a delay of several hours.
        self.first_try_time_price_data = "14:32:00"
        self.second_try_time_price_data = "18:32:00"

        # Emission data is calculated based on the production data and is therefore available a little later
        # than the price data.
        self.first_try_time_emissions_data = "15:16:17"
        self.second_try_time_emissions_data = "19:18:17"

        self.emission_intensities = {}
        self.first_future_negative_consumption_price_point = None
        self.first_future_negative_production_price_point = None

        self.run_daily(self.daily_kickoff_charging_data, start = "01:15:00")

        await self.finalize_initialisation("module initialize")

        self.log(f"Completed initializing FlexMeasuresDataImporter")

    async def finalize_initialisation(self, v2g_args: str):
        # Finalize the initialisation. This is run from initialise and from globals when
        # settings have changed. This is separated :
        # - is not always around self.first_try_time (for day-ahead contracts)
        # - the data-changed might not fire at startup (external HA integration provided data)
        # This is delayed as it's not high priority and gives globals the time to get all settings loaded correctly.

        self.log(f"finalize_initialisation called from source: {v2g_args}.")

        # From FM format (€/MWh) to user desired format (€ct/kWh)
        # = * 100/1000 = 1/10.
        self.price_conversion_factor = 1 / 10
        self.vat_factor = (100 + c.ENERGY_PRICE_VAT) / 100

        if v2g_args != "module initialize":
            # New settings must result in refreshing the price data.
            # Clear price data
            await self.v2g_main_app.set_records_in_chart(chart_line_name = ChartLine.CONSUMPTION_PRICE, records = None)
            await self.v2g_main_app.set_records_in_chart(chart_line_name = ChartLine.PRODUCTION_PRICE, records = None)
            await self.v2g_main_app.set_records_in_chart(chart_line_name = ChartLine.EMISSION, records = None)

        if is_price_epex_based():
            self.log("initialize: price update interval is daily")
            self.timer_id_daily_kickoff_price_data = self.run_daily(
                self.daily_kickoff_price_data, start = self.first_try_time_price_data)

            self.timer_id_daily_kickoff_emissions_data = self.run_daily(
                self.daily_kickoff_emissions_data, start = self.first_try_time_emissions_data)
        else:
            await self.__cancel_timer(self.timer_id_daily_kickoff_price_data)
            await self.__cancel_timer(self.timer_id_daily_kickoff_emissions_data)

        initial_delay_sec = 45
        self.run_in(self.daily_kickoff_price_data, delay=initial_delay_sec)
        self.run_in(self.daily_kickoff_emissions_data, delay=initial_delay_sec)
        self.run_in(self.daily_kickoff_charging_data, delay=initial_delay_sec)
        self.log("finalize_initialisation completed.")


    # TODO: Consolidate. Copied function from v2g_liberty module also in globals..
    async def __cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if self.info_timer(timer_id):
            silent = True  # Does not really work
            await self.cancel_timer(timer_id, silent)


    async def daily_kickoff_price_data(self, *args):
        """
           This sets off the daily routine to check for new prices.
           Only called when is_price_epex_based() is true.
        """
        self.log(f"daily_kickoff_price_data called, args: {args}.")
        res = await self.get_consumption_prices()
        self.log(f"daily_kickoff_price_data get_consumption_prices returned: {res}.")
        res = await self.get_production_prices()
        self.log(f"daily_kickoff_price_data get_production_prices returned: {res}.")
        self.log(f"daily_kickoff_price_data completed")


    async def daily_kickoff_emissions_data(self, *args):
        """
           This sets off the daily routine to check for new emission data.
           Only called when is_price_epex_based() is true.
        """
        self.log(f"daily_kickoff_emissions_data called")
        res = await self.get_emission_intensities()
        self.log(f"daily_kickoff_price_data get_emission_intensities returned: {res}.")


    async def daily_kickoff_charging_data(self, *args):
        """ This sets off the daily routine to check for charging cost."""
        self.log(f"daily_kickoff_charging_data called")
        await self.get_charging_cost()
        await self.get_charged_energy()


    async def get_charging_cost(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging costs of last 7 days from the server
        Make costs total costs of this period available in HA by setting them in input_text.last week costs
        ToDo: Split cost in charging and dis-charging per day
        """
        self.log(f"get_charging_cost called")
        now = get_local_now()

        # Getting data since a week ago so that user can look back a further than just current window.
        start = time_floor(now + timedelta(days=-self.DAYS_HISTORY), timedelta(days=1))
        duration = timedelta(days=self.DAYS_HISTORY) # Duration as timedelta
        duration = round((duration.total_seconds()/60), 0)  # Duration in minutes
        duration = convert_to_duration_string(duration) # Duration as iso string
        if self.fm_client_app is not None:
            charging_costs = await self.fm_client_app.get_sensor_data(
                sensor_id = c.FM_ACCOUNT_COST_SENSOR_ID,
                start = start.isoformat(),
                duration = duration,
                resolution = "P1D",
                uom = c.CURRENCY,
            )
        else:
            self.log(f"get_charging_cost. Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        self.log(f"get_charging_cost | sensor_id: {c.FM_ACCOUNT_COST_SENSOR_ID}, charging_costs: {charging_costs}.")

        if charging_costs is None:
            # TODO: When to retry?
            return

        total_charging_cost_last_7_days = 0
        charging_cost_points = []
        resolution = timedelta(days = 1)
        charging_costs = charging_costs['values']
        for i, charging_cost in enumerate(charging_costs):
            if charging_cost is None:
                continue
            self.log(f"charging_cost: '{charging_cost}'.")
            data_point = {'time': (start + i * resolution).isoformat(),
                          'cost': round(float(charging_cost), 2)}
            total_charging_cost_last_7_days += data_point['cost']
            charging_cost_points.append(data_point)
        if len(charging_cost_points) == 0:
            # TODO: All data points are None, what to do?
            self.log("get_charging_cost. No charging cost data available")
        total_charging_cost_last_7_days = round(total_charging_cost_last_7_days, 2)
        self.log(f"get_charging_cost Cost data: {charging_cost_points}, total costs: {total_charging_cost_last_7_days}")

        # To make sure HA considers this as new info a datetime is added
        new_state = "Costs collected at " + now.isoformat()
        result = {'records': charging_cost_points}
        self.set_state("input_text.charging_costs", state=new_state, attributes=result)
        self.set_value("input_number.total_charging_cost_last_7_days", total_charging_cost_last_7_days)


    async def get_charged_energy(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging volumes of last 7 days from the server.
        ToDo: make this period a setting for the user.
        Make totals of charging and dis-charging per day and over the period

        """
        self.log("get_charged_energy, called.")

        now = get_local_now()
        # Getting data since a week
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))

        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{self.DAYS_HISTORY}D"

        # TODO: change uom to kW, this way the server can do the conversion (more efficient?).
        if self.fm_client_app is not None:
            res = await self.fm_client_app.get_sensor_data(
                sensor_id = c.FM_ACCOUNT_POWER_SENSOR_ID,
                start = start.isoformat(),
                duration = duration,
                resolution = resolution,
                uom = "MW",
            )
        else:
            self.log(f"get_charged_energy. Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        # The res structure:
        # 'duration': 'PT168H',
        # 'start': '2024-09-02T00:00:00+02:00',
        # 'unit': 'MW',
        # 'values': [0.004321, None, ..., 0.005712]
        self.log(f"get_charged_energy sensor_id: {c.FM_ACCOUNT_POWER_SENSOR_ID}, "
                 f"charge power response: {str(res)[:175]} ... {str(res)[-75:]}.")

        total_charged_energy_last_7_days = 0
        total_discharged_energy_last_7_days = 0
        total_emissions_last_7_days = 0
        total_saved_emissions_last_7_days = 0
        total_minutes_charged = 0
        total_minutes_discharged = 0
        charging_energy_points = {}

        charge_power_points = res['values']
        self.log(f"charge_power_points length: '{len(charge_power_points)}'.")
        for i, charge_power in enumerate(charge_power_points):
            if charge_power is None:
                continue
            power = float(charge_power)
            key = start + timedelta(minutes = (i * c.FM_EVENT_RESOLUTION_IN_MINUTES))
            charging_energy_points[key] = power

            # Look up the emission matching with power['event_start'], this will be a match every 3 items
            # as emission has a resolution of 15 minutes and power of 5 min.
            # ToDo: check if resolutions match X times, if not, raise an error.
            emission_intensity = self.emission_intensities.get(key, 0)

            if power < 0:
                # Strangely we add power to energy... this is practical, we later convert this to energy.
                total_discharged_energy_last_7_days += power
                total_minutes_discharged += c.FM_EVENT_RESOLUTION_IN_MINUTES
                # We strangely add 5 min. periods as if they are hours, we later converty this
                total_saved_emissions_last_7_days += power * emission_intensity
            elif power > 0:
                # Strangely we add power to energy... this is practical, we later convert this to energy.
                total_charged_energy_last_7_days += power
                total_minutes_charged += c.FM_EVENT_RESOLUTION_IN_MINUTES
                # We strangely add 5 min. periods as if they are hours, we later converty this
                total_emissions_last_7_days += power * emission_intensity

        # Convert the returned average power in MW over event_resolution ( 5 minutes)
        # periods to kWh *1000/12 to energy in kWh
        conversion_factor = 1000 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_discharged_energy_last_7_days = int(round(total_discharged_energy_last_7_days * conversion_factor, 0))
        total_charged_energy_last_7_days = int(round(total_charged_energy_last_7_days * conversion_factor, 0))

        # Convert the returned average MW * kg/MWh over event_resolution (5 minutes) periods to kg (/12)
        conversion_factor = 1 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_saved_emissions_last_7_days = round(total_saved_emissions_last_7_days * conversion_factor, 1)
        total_emissions_last_7_days = round(total_emissions_last_7_days * conversion_factor, 1)

        self.set_value("input_number.total_discharged_energy_last_7_days", total_discharged_energy_last_7_days)
        self.set_value("input_number.total_charged_energy_last_7_days", total_charged_energy_last_7_days)
        self.set_value("input_number.net_energy_last_7_days",
                       total_charged_energy_last_7_days + total_discharged_energy_last_7_days)

        self.set_value("input_number.total_saved_emissions_last_7_days", total_saved_emissions_last_7_days)
        self.set_value("input_number.total_emissions_last_7_days", total_emissions_last_7_days)
        self.set_value("input_number.net_emissions_last_7_days",
                       total_emissions_last_7_days + total_saved_emissions_last_7_days)

        self.set_value("input_text.total_discharge_time_last_7_days", format_duration(total_minutes_discharged))
        self.set_value("input_text.total_charge_time_last_7_days", format_duration(total_minutes_charged))
        self.log(f"get_charged_energy stats: \n"
                 f"    total_discharged_energy_last_7_days: '{total_discharged_energy_last_7_days}' \n"
                 f"    total_charged_energy_last_7_days: '{total_charged_energy_last_7_days}' \n"
                 f"    total_saved_emissions_last_7_days: '{total_saved_emissions_last_7_days}' \n"
                 f"    total_emissions_last_7_days: '{total_emissions_last_7_days}' \n"
                 f"    total discharge time: '{format_duration(total_minutes_discharged)}' \n"
                 f"    total charge time: '{format_duration(total_minutes_charged)}'")


    async def get_consumption_prices(self, *args, **kwargs):
        """ Communicate with FM server and check the results.
        Request prices from the server
        Make prices available in HA.
        Notify user if there will be negative prices for next day
        """
        self.log("get_consumption_prices called")
        now = get_local_now()
        # Getting prices since start of yesterday so that user can look back a little further than just current window.
        start = time_floor(now + timedelta(days=-1), timedelta(days=1))

        if self.fm_client_app is not None:
            prices = await self.fm_client_app.get_sensor_data(
                sensor_id = c.FM_PRICE_CONSUMPTION_SENSOR_ID,
                start = start.isoformat(),
                duration = "P2D",
                resolution = f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom = f"{c.CURRENCY}/MWh",
            )
        else:
            self.log(f"get_consumption_prices. Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        self.log(f"get_consumption_prices sensor_id: {c.FM_PRICE_CONSUMPTION_SENSOR_ID}, prices: {prices}.")

        failure_message = ""
        if prices is None:
            failure_message = "get_consumption_prices failed"
        else:
            consumption_price_points = []
            first_future_negative_price_point = None
            prices = prices['values']

            for i, price in enumerate(prices):
                if price is None:
                    continue
                dt = start + timedelta(minutes = (i * c.PRICE_RESOLUTION_MINUTES))
                data_point = {
                    'time': dt.isoformat(),
                    'price': round(((float(price) * self.price_conversion_factor) + c.ENERGY_PRICE_MARKUP_PER_KWH)
                                   * self.vat_factor, 2)
                }

                if first_future_negative_price_point is None and data_point['price'] < 0:
                    if dt > now:
                        self.log(f"get_consumption_prices, negative price: {data_point['price']} at: {dt}.")
                        # We cannot reuse data_point here as we need a date object...
                        first_future_negative_price_point = {'time': dt, 'price': data_point['price']}
                consumption_price_points.append(data_point)

            await self.v2g_main_app.set_records_in_chart(
                chart_line_name = ChartLine.CONSUMPTION_PRICE,
                records = consumption_price_points
            )

            self.__check_negative_price_notification(first_future_negative_price_point, "consumption_price_point")

            if is_price_epex_based():
                # FM returns all the prices it has, sometimes it has not retrieved new
                # prices yet, than it communicates the prices it does have.
                date_latest_price = start + timedelta(minutes = (len(prices) * c.PRICE_RESOLUTION_MINUTES))
                date_tomorrow = time_ceil(now, timedelta(days=1))
                # to give some slack, subtract 2x resolution
                date_tomorrow -= timedelta(minutes=2 * c.FM_EVENT_RESOLUTION_IN_MINUTES)
                self.log(f"Checking consumption prices for renewal; "
                         f"date_latest_price: {date_latest_price}, date_tomorrow: {date_tomorrow}.")
                if date_latest_price < date_tomorrow:
                    failure_message = (f"FM consumption prices seem not renewed yet, "
                                       f"date_latest_price: {date_latest_price}, date_tomorrow: {date_tomorrow}.")

        # If interval is daily retry once at second_try_time.
        if failure_message != "" and is_price_epex_based():
            if self.now_is_between(self.first_try_time_price_data, self.second_try_time_price_data):
                self.log(f"{failure_message}, retry at {self.second_try_time_price_data}.")
                await self.run_at(self.get_consumption_prices, self.second_try_time_price_data)
            else:
                self.log(f"{failure_message}, retry tomorrow.")
                await self.__send_cannot_get_prices_notification()
            return False

        self.log(f"FM consumption prices successfully retrieved.")
        # A bit of a hack, the method needs to return something for the awaited calls to this method to work...
        return "FM consumption prices successfully retrieved."


    async def get_production_prices(self, *args, **kwargs):
        """ Communicate with FM server and check the results.
        Request prices from the server
        Make prices available in HA
        Notify user if there will be negative prices for next day
        """
        now = get_local_now()
        # Getting prices since start of yesterday so that user can look back a little further than just current window.
        start = time_floor(now + timedelta(days=-1), timedelta(days=1))
        failure_message = ""

        if self.fm_client_app is not None:
            prices = await self.fm_client_app.get_sensor_data(
                sensor_id = c.FM_PRICE_PRODUCTION_SENSOR_ID,
                start = start.isoformat(),
                duration = "P2D",
                resolution = f"PT{c.PRICE_RESOLUTION_MINUTES}M",
                uom = f"{c.CURRENCY}/MWh",
            )
        else:
            self.log(f"get_production_prices. Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        self.log(f"get_production_prices | sensor_id: {c.FM_PRICE_PRODUCTION_SENSOR_ID}, prices: {prices}.")

        if prices is None:
            failure_message = "get_production_prices failed"
        else:
            production_price_points = []
            first_future_negative_price_point = None
            prices = prices['values']
            for i, price in enumerate(prices):
                if price is None:
                    continue
                dt = start + timedelta(minutes = (i * c.PRICE_RESOLUTION_MINUTES))
                data_point = {
                    'time': dt.isoformat(),
                    'price': round(((float(price) * self.price_conversion_factor) +
                                    c.ENERGY_PRICE_MARKUP_PER_KWH) * self.vat_factor, 2)
                }
                if first_future_negative_price_point is None and data_point['price'] < 0 and dt > now:
                    self.log(f"get_production_prices, negative price: {data_point['price']} at: {dt}.")
                    # We cannot reuse data_point here as we need a date object...
                    first_future_negative_price_point = {'time': dt, 'price': data_point['price']}
                production_price_points.append(data_point)

        await self.v2g_main_app.set_records_in_chart(
            chart_line_name=ChartLine.PRODUCTION_PRICE,
            records=production_price_points
        )
        # Not in use yet, see comments in __check_negative_price_notification
        # self.__check_negative_price_notification(first_future_negative_price_point, "production_price_point")

        if is_price_epex_based():
            # FM returns all the prices it has, sometimes it has not retrieved new
            # prices yet, than it communicates the prices it does have.
            date_latest_price = start + timedelta(minutes = (len(prices) * c.PRICE_RESOLUTION_MINUTES))
            date_tomorrow = time_ceil(now, timedelta(days=1))
            # to give some slack, subtract 2x resolution
            date_tomorrow -= timedelta(minutes=2*c.FM_EVENT_RESOLUTION_IN_MINUTES)
            if date_latest_price < date_tomorrow:
                failure_message = (f"FM production prices seem not renewed yet,"
                                   f"date_latest_price: {date_latest_price}, date_tomorrow: {date_tomorrow}.")

        # If interval is daily retry once at second_try_time.
        if failure_message != "":
            if (is_price_epex_based() and
                self.now_is_between(self.first_try_time_price_data, self.second_try_time_price_data)
               ):
                self.log(f"{failure_message}, retry at {self.second_try_time_price_data}.")
                await self.run_at(self.get_production_prices, self.second_try_time_price_data)
            else:
                self.log(f"{failure_message}, retry later/tomorrow.")
                await self.__send_cannot_get_prices_notification()
            return False

        # A bit of a hack, the method needs to return something for the awaited calls to this method to work...
        self.log(f"FM production prices successfully retrieved.")
        return "FM production prices successfully retrieved."

    async def __send_cannot_get_prices_notification(self):
        self.v2g_main_app.notify_user(
            message="Could not get (all) energy prices/emissions to show in graph, retry later. Scheduling continues as normal.",
            title=None,
            tag="no_price_data",
            critical=False,
            send_to_all=True,
            ttl=15 * 60
        )

    async def get_emission_intensities(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production in NL from the server
        Make values available in HA by setting them in input_text.co2_emissions
        """

        self.log("get_emission_intensities called")

        now = get_local_now()

        # Getting emissions since a week ago. This is needed for calculation of CO2 savings
        # and will be (more than) enough for the graph to show.
        start = time_floor(now - timedelta(days=self.DAYS_HISTORY), timedelta(days=1))
        resolution = f"PT{c.FM_EVENT_RESOLUTION_IN_MINUTES}M"
        duration = f"P{ self.DAYS_HISTORY + 1 }D"

        if self.fm_client_app is not None:
            emissions = await self.fm_client_app.get_sensor_data(
                sensor_id = c.FM_EMISSIONS_SENSOR_ID,
                start = start.isoformat(),
                duration = duration,
                resolution = resolution,
                uom = c.EMISSIONS_UOM,
            )
        else:
            self.log(f"get_emission_intensities. Could not call get_sensor_data on fm_client_app as it is None.")
            return False

        self.log(f"get_emission_intensities, emissions: {str(emissions)[:175]}...{str(emissions)[-75:]}.")

        if emissions is None:
            # If interval is daily retry once at second_try_time.
            if is_price_epex_based():
                if self.now_is_between(self.first_try_time_emissions_data, self.second_try_time_emissions_data):
                    self.log(f"Retry get_emission_intensities at {self.second_try_time_emissions_data}.")
                    await self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
            return False

        # For use in graph
        emission_points = []

        # For use in calculations, it is cleared as we collect new values. previous values
        self.emission_intensities.clear()

        # We assume start and resolution match the request, and it is not necessary to retrieve these from the response.
        emissions = emissions['values']
        previous_value = None
        show_in_graph_after = now + timedelta(hours=-5)
        date_latest_emission = None  # Latest emission with value that is not None
        for i, emission_value in enumerate(emissions):
            if emission_value is None:
                continue
            # Set the real value for use in calculations later
            emission_start = start + timedelta(minutes = i * c.FM_EVENT_RESOLUTION_IN_MINUTES)
            date_latest_emission = emission_start
            self.emission_intensities[emission_start] = emission_value

            # Adapt value for showing in graph
            # To optimise we only add points that are max 5 hours old and actually show a change
            if emission_value != previous_value and emission_start > show_in_graph_after:
                emission_value = int(round(float(emission_value) / 10, 0))
                data_point = {'time': emission_start.isoformat(), 'emission': emission_value}
                emission_points.append(data_point)
            previous_value = emission_value

        await self.v2g_main_app.set_records_in_chart(chart_line_name=ChartLine.EMISSION, records=emission_points)

        if is_price_epex_based():
            # FM returns all the emissions it has, sometimes it has not retrieved new
            # emissions yet, than it communicates the emissions it does have.
            date_tomorrow = time_ceil(now, timedelta(days=1))
            # to give some slack, subtract 2x resolution
            date_tomorrow += timedelta(minutes= -(2*c.FM_EVENT_RESOLUTION_IN_MINUTES))
            if date_latest_emission < date_tomorrow:
                self.log(f"FM CO2 emissions | seem not renewed yet: date_latest_emission: {date_latest_emission}, "
                         f"date_tomorrow: {date_tomorrow}. Retry at {self.second_try_time_emissions_data}.")
                await self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
                return
        self.log(f"FM emissions successfully retrieved.")
        # A bit of a hack, the method needs to return something for the awaited calls to this method to work...
        return "FM emissions successfully retrieved."


    def __check_negative_price_notification(self, price_point: dict, price_type: str):
        """ Method to check if the user needs to be notified about negative consumption
            and/or production prices in a combined message.
            Only used for daily price contracts. Business rules for more frequently updated prices
            are too complex and left to the energy provider.
            To be called from get_consumption_prices, only when the negative prices are in the future,
            and send None if no negative prices are expected.
            TODO: Also set this up for get_production_prices if these actually are (much) different from
                  consumption prices.

            In preparation to the forgoing to-do:
            - this method is called with a price_type (where production_price_point is not used yet)
            - the price_points are stored in separate variables:
              self.first_future_negative_consumption_price_point and self.first_future_negative_production_price_point
        """
        self.log("__check_negative_price_notification called")
        if not is_price_epex_based():
            return
        if price_type == "consumption_price_point":
            if price_point == self.first_future_negative_consumption_price_point:
                # Nothing change, do nothing
                return
            self.first_future_negative_consumption_price_point = price_point

        elif price_type == "production_price_point":
            if price_point == self.first_future_negative_production_price_point:
                # Nothing change, we do nothing
                return
            self.first_future_negative_production_price_point = price_point

        else:
            self.log(f"check_negative_price_notification, unknown price_point type: {price_type}.")
            return

        msg = ""
        cpp = self.first_future_negative_consumption_price_point
        if cpp is not None:
            # There was a no consumption price point but now there is
            msg = f"From {cpp['time'].strftime(c.DATE_TIME_FORMAT)} consumption price is {cpp['price']} cent/kWh."

        msg += " "
        ppp = self.first_future_negative_production_price_point
        if ppp is not None:
            msg += f"From {ppp['time'].strftime(c.DATE_TIME_FORMAT)} production price is {ppp['price']} cent/kWh."

        if msg == " ":
            self.v2g_main_app.clear_notification(tag="negative_energy_prices")
            self.log("__check_negative_price_notification, clearing negative price notification")
        else:
            self.v2g_main_app.notify_user(
                message=msg,
                title="Negative electricity price",
                tag="negative_energy_prices",
                critical=False,
                send_to_all=True,
                ttl=12 * 60 * 60
            )
        self.log(f"__check_negative_price_notification, notify user with message: {msg}.")
        return


def format_duration(duration_in_minutes: int):
    MINUTES_IN_A_DAY = 60 * 24
    days = math.floor(duration_in_minutes / MINUTES_IN_A_DAY)
    hours = math.floor((duration_in_minutes - days * MINUTES_IN_A_DAY) / 60)
    minutes = duration_in_minutes - days * MINUTES_IN_A_DAY - hours * 60
    return "%02dd %02dh %02dm" % (days, hours, minutes)
