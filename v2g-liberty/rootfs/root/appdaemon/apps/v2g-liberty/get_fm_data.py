from datetime import datetime, timedelta
import json
import pytz
import math
import re
import requests
import time
import asyncio
from v2g_globals import get_local_now, is_price_epex_based
import constants as c
from typing import AsyncGenerator, List, Optional

import appdaemon.plugins.hass.hassapi as hass
import isodate


class FlexMeasuresDataImporter(hass.Hass):
    # CONSTANTS
    EMISSIONS_URL: str
    CONSUMPTION_PRICES_URL: str
    PRODUCTION_PRICES_URL: str
    CHARGING_COST_URL: str
    CHARGE_POWER_URL: str
    GET_CHARGING_DATA_AT: str  # Time string

    # Variables
    fm_token: str
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
    v2g_main_app: object

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

        self.fm_token = ""
        self.CONSUMPTION_PRICES_URL = c.FM_GET_DATA_URL + str(c.FM_PRICE_CONSUMPTION_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.PRODUCTION_PRICES_URL = c.FM_GET_DATA_URL + str(c.FM_PRICE_PRODUCTION_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.EMISSIONS_URL = c.FM_GET_DATA_URL + str(c.FM_EMISSIONS_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.CHARGING_COST_URL = c.FM_GET_DATA_URL + str(c.FM_ACCOUNT_COST_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.CHARGE_POWER_URL = c.FM_GET_DATA_URL + str(c.FM_ACCOUNT_POWER_SENSOR_ID) + c.FM_GET_DATA_SLUG

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

        self.GET_CHARGING_DATA_AT = "01:15:00"
        self.run_daily(self.daily_kickoff_charging_data, self.GET_CHARGING_DATA_AT)

        await self.finalize_initialisation("module initialize")

        self.log(f"Completed initializing FlexMeasuresDataImporter")

    async def finalize_initialisation(self, v2g_args: str):
        # Finalize the initialisation. This is run from initialise and from globals when
        # settings have changed. This is separated :
        # - is not always around self.first_try_time (for day-ahead contracts)
        # - the data-changed might not fire at startup (external HA integration provided data)
        # This is delayed as it's not high priority and gives globals the time to get all settings loaded correctly.

        self.log(f"finalize_initialisation called from source: {v2g_args}.")
        if v2g_args != "module initialize":
            # New settings must result in refreshing the price data.
            # Clear price data
            await self.__set_consumption_prices_in_graph(None)
            await self.__set_production_prices_in_graph(None)
            await self.__set_emissions_in_graph(None)

        if is_price_epex_based():
            self.log("initialize: price update interval is daily")
            self.timer_id_daily_kickoff_price_data = await self.run_daily(
                self.daily_kickoff_price_data, self.first_try_time_price_data)

            self.timer_id_daily_kickoff_emissions_data = await self.run_daily(
                self.daily_kickoff_emissions_data, self.first_try_time_emissions_data)
        else:
            await self.__cancel_timer(self.timer_id_daily_kickoff_price_data)
            await self.__cancel_timer(self.timer_id_daily_kickoff_emissions_data)

        initial_delay_sec = 150
        await self.run_in(self.daily_kickoff_price_data, delay=initial_delay_sec)
        await self.run_in(self.daily_kickoff_emissions_data, delay=initial_delay_sec)
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
        self.log(f"daily_kickoff_price_data get_production_prices returned: {res}.")



    # TODO: make async
    def daily_kickoff_charging_data(self, *args):
        """ This sets off the daily routine to check for charging cost."""
        self.log(f"daily_kickoff_charging_data called")
        self.get_charging_cost()
        self.get_charged_energy()


    # TODO: make async
    def get_charging_cost(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging costs of last 7 days from the server
        Make costs total costs of this period available in HA by setting them in input_text.last week costs
        ToDo: Split cost in charging and dis-charging per day
        """
        self.log(f"get_charging_cost called")
        now = get_local_now()
        self.authenticate_with_fm()

        # Getting data since a week ago so that user can look back a further than just current window.
        dt = str(now + timedelta(days=-7))
        start = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": start,
        }

        res = requests.get(
            self.CHARGING_COST_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "Get FM CHARGING COST data")
            self.try_solve_authentication_error(res, self.CHARGING_COST_URL, self.get_charging_cost, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "Get FM CHARGING COST data")
            # This might include situation where sensor_id is not correct (yet).
            # Currently, there is no reason to retry as the server will not re-run scheduled script for cost calculation
            return

        charging_costs = res.json()

        total_charging_cost_last_7_days = 0
        charging_cost_points = []
        for charging_cost in charging_costs:
            data_point = {'time': datetime.fromtimestamp(charging_cost['event_start'] / 1000).isoformat(),
                          'cost': round(float(charging_cost['event_value']), 2)}
            total_charging_cost_last_7_days += data_point['cost']
            charging_cost_points.append(data_point)
        total_charging_cost_last_7_days = round(total_charging_cost_last_7_days, 2)
        self.log(f"Cost data: {charging_cost_points}, total costs: {total_charging_cost_last_7_days}")

        # To make sure HA considers this as new info a datetime is added
        new_state = "Costs collected at " + now.isoformat()
        result = {'records': charging_cost_points}
        self.set_state("input_text.charging_costs", state=new_state, attributes=result)
        self.set_value("input_number.total_charging_cost_last_7_days", total_charging_cost_last_7_days)


    # TODO: make async
    def get_charged_energy(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging volumes of last 7 days from the server.
        ToDo: make this period a setting for the user.
        Make totals of charging and dis-charging per day and over the period

        """
        self.log("get_charged_energy, called.")

        now = get_local_now()
        # Getting data since start of yesterday so that user can look back a little further than just current window.

        dt = str(now + timedelta(days=-7))
        start_data_period = dt[:10] + "T00:00:00" + dt[-6:]
        dt = str(now)
        end_data_period = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": start_data_period,
            "event_ends_before": end_data_period,
        }

        res = requests.get(
            self.CHARGE_POWER_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "get_charged_energy")
            self.try_solve_authentication_error(res, self.CHARGE_POWER_URL, self.get_charging_energy, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "get_charged_energy")
            # Currently there is no reason to retry as the server will not re-run scheduled script for cost calculation
        charge_power_points = res.json()

        total_charged_energy_last_7_days = 0
        total_discharged_energy_last_7_days = 0
        total_emissions_last_7_days = 0
        total_saved_emissions_last_7_days = 0
        total_minutes_charged = 0
        total_minutes_discharged = 0
        charging_energy_points = {}
        resolution_in_milliseconds = c.FM_EVENT_RESOLUTION_IN_MINUTES * 60 * 1000

        for charge_power in charge_power_points:
            # The API returns both actual and scheduled power, ignore the values from the schedules
            if charge_power['source']['type'] == "scheduler":
                continue

            power = float(charge_power['event_value'])
            key = charge_power['event_start']
            charging_energy_points[key] = power
            if power is None:
                continue

            # Look up the emission matching with power['event_start'], this will be a match every 3 items
            # as emission has a resolution of 15 minutes and power of 5 min.
            # ToDo: check if resolutions match X times, if not, raise an error.
            emission_intensity = 0
            i = 0
            while i < 3:
                em = self.emission_intensities.get(key, None)
                if em is None:
                    # Try a resolution step (5 min.) earlier
                    key -= resolution_in_milliseconds
                    i += 1
                else:
                    emission_intensity = em
                    break

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


    async def get_consumption_prices(self, *args, **kwargs):
        """ Communicate with FM server and check the results.
        Request prices from the server
        Make prices available in HA.
        Notify user if there will be negative prices for next day
        """
        self.log("get_consumption_prices called")
        now = get_local_now()
        self.authenticate_with_fm()
        # Getting prices since start of yesterday so that user can look back a little further than just current window.
        dt = str(now + timedelta(days=-1))
        start_data_period = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": start_data_period,
        }
        res = requests.get(
            self.CONSUMPTION_PRICES_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "get_consumption_prices")
            self.try_solve_authentication_error(res, self.CONSUMPTION_PRICES_URL, self.get_consumption_prices,
                                                *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "get_consumption_prices")

            # If interval is daily retry once at second_try_time.
            if is_price_epex_based():
                if self.now_is_between(self.first_try_time_price_data, self.second_try_time_price_data):
                    self.log(f"Retry at {self.second_try_time_price_data}.")
                    await self.run_at(self.get_consumption_prices, self.second_try_time_price_data)
                else:
                    self.log("get_consumption_prices failed, retry tomorrow.")
                    self.v2g_main_app.notify_user(
                        message="Could not get (all) energy prices, retry tomorrow. Scheduling continues as normal.",
                        title=None,
                        tag="no_price_data",
                        critical=False,
                        send_to_all=True,
                        ttl=15 * 60
                    )
                return

        prices = res.json()

        # From FM format (€/MWh) to user desired format (€ct/kWh)
        # = * 100/1000 = 1/10.
        conversion = 1 / 10
        vat_factor = (100 + c.ENERGY_PRICE_VAT) / 100
        consumption_price_points = []
        first_future_negative_price_point = None
        now = get_local_now()
        self.log(f"get_consumption_prices, now: {now}.")
        for price in prices:
            dt = datetime.fromtimestamp(price['event_start'] / 1000).astimezone(c.TZ)
            data_point = {
                'time': dt.isoformat(),
                'price': round(((price['event_value'] * conversion) +
                                c.ENERGY_PRICE_MARKUP_PER_KWH) * vat_factor, 2)
            }

            if first_future_negative_price_point is None and data_point['price'] < 0:
                if dt > now:
                    self.log(f"get_consumption_prices, negative price: {data_point['price']} at: {dt}.")
                    # We cannot reuse data_point here as we need a date object...
                    first_future_negative_price_point = {'time': dt, 'price': data_point['price']}
            consumption_price_points.append(data_point)

        await self.__set_consumption_prices_in_graph(consumption_price_points)

        if is_price_epex_based():
            # FM returns all the prices it has, sometimes it has not retrieved new
            # prices yet, than it communicates the prices it does have.
            date_latest_price = datetime.fromtimestamp(prices[-1].get('event_start') / 1000).isoformat()
            date_tomorrow = (now + timedelta(days=1)).isoformat()
            if date_latest_price < date_tomorrow:
                self.log(f"FM consumption prices seem not renewed yet, latest price at: {date_latest_price}, "
                         f"Retry at {self.second_try_time_price_data}.")
                await self.run_at(self.get_consumption_prices, self.second_try_time_price_data)
                return

        self.__check_negative_price_notification(first_future_negative_price_point, "consumption_price_point")

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
        self.authenticate_with_fm()
        # Getting prices since start of yesterday so that user can look back a little further than just current window.
        dt = str(now + timedelta(days=-1))
        start_data_period = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": start_data_period,
        }
        res = requests.get(
            self.PRODUCTION_PRICES_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "Get FM production prices")
            self.try_solve_authentication_error(res, self.PRODUCTION_PRICES_URL,
                                                self.get_production_prices, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "Get FM production prices")

            # If interval is daily retry once at second_try_time.
            if  is_price_epex_based():
                if self.now_is_between(self.first_try_time_price_data, self.second_try_time_price_data):
                    self.log(f"Retry at {self.second_try_time_price_data}.")
                    await self.run_at(self.get_production_prices, self.second_try_time_price_data)
                else:
                    self.log("get_production_prices failed, retry tomorrow.")
                    self.v2g_main_app.notify_user(
                        message="Could not get energy prices, retry tomorrow. Scheduling continues as normal.",
                        title=None,
                        tag="no_price_data",
                        critical=False,
                        send_to_all=True,
                        ttl=15 * 60
                    )
            return

        prices = res.json()

        # From FM format (€/MWh) to user desired format (€ct/kWh)
        # = * 100/1000 = 1/10.
        conversion = 1 / 10
        vat_factor = (100 + c.ENERGY_PRICE_VAT) / 100
        production_price_points = []
        first_future_negative_price_point = None
        now = get_local_now()
        for price in prices:
            dt = datetime.fromtimestamp(price['event_start'] / 1000).astimezone(c.TZ)
            data_point = {
                'time': dt.isoformat(),
                'price': round(((price['event_value'] * conversion) +
                                c.ENERGY_PRICE_MARKUP_PER_KWH) * vat_factor, 2)
            }
            if first_future_negative_price_point is None and data_point['price'] < 0 and dt > now:
                self.log(f"get_production_prices, negative price: {data_point['price']} at: {dt}.")
                # We cannot reuse data_point here as we need a date object...
                first_future_negative_price_point = {'time': dt, 'price': data_point['price']}
            production_price_points.append(data_point)

        await self.__set_production_prices_in_graph(production_price_points)

        if is_price_epex_based():
            # FM returns all the prices it has, sometimes it has not retrieved new
            # prices yet, than it communicates the prices it does have.
            date_latest_price = datetime.fromtimestamp(prices[-1].get('event_start') / 1000).isoformat()
            date_tomorrow = (now + timedelta(days=1)).isoformat()
            if date_latest_price < date_tomorrow:
                self.log(f"FM production prices seem not renewed yet, latest price at: {date_latest_price}, "
                         f"Retry at {self.second_try_time_price_data}.")
                self.run_at(self.get_production_prices, self.second_try_time_price_data)
                return "Retry tomorrow"

        # Not in use yet, see comments in __check_negative_price_notification
        # self.__check_negative_price_notification(first_future_negative_price_point, "production_price_point")

        # A bit of a hack, the method needs to return something for the awaited calls to this method to work...
        self.log(f"FM production prices successfully retrieved.")
        return "FM production prices successfully retrieved."


    async def __set_production_prices_in_graph(self, production_price_points):
        # To make sure HA considers this as new info a datetime is added
        now = get_local_now()
        new_state = "Production prices collected at " + now.isoformat()
        if production_price_points is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            production_price_points = [dict(time=(now + timedelta(days=7)).isoformat(), soc=None)]
        result = {'records': production_price_points}
        await self.set_state("input_text.production_prices", state=new_state, attributes=result)


    async def __set_consumption_prices_in_graph(self, consumption_price_points):
        # To make sure HA considers this as new info a datetime is added
        now=get_local_now()
        new_state = "Consumption prices collected at " + now.isoformat()
        if consumption_price_points is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            consumption_price_points = [dict(time=(now + timedelta(days=7)).isoformat(), soc=None)]
        result = {'records': consumption_price_points}
        await self.set_state("input_text.consumption_prices", state=new_state, attributes=result)



    async def __set_emissions_in_graph(self, emission_points):
        # To make sure HA considers this as new info a datetime is added
        now=get_local_now()
        new_state = "Emissions collected at " + now.isoformat()
        if emission_points is None:
            # There seems to be no way to hide the SoC series from the graph,
            # so it is filled with "empty" data, one record of 0.
            # Set it at a week from now, so it's not visible in the default view.
            emission_points = [dict(time=(now + timedelta(days=7)).isoformat(), soc=None)]

        result = {'records': emission_points}
        await self.set_state("input_text.co2_emissions", state=new_state, attributes=result)


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
            - this method is called with a price_type (where √ is not used yet)
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


    async def get_emission_intensities(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production in NL from the server
        Make values available in HA by setting them in input_text.co2_emissions
        """

        self.log("get_emission_intensities called")

        now = get_local_now()

        self.authenticate_with_fm()
        # Getting emissions since a week ago. This is needed for calculation of CO2 savings
        # and will be (more than) enough for the graph to show.
        # Because we want to show it in the graph we do not use an end url_param.
        dt = str(now + timedelta(days=-7))
        start_data_period = dt[:10] + "T00:00:00" + dt[-6:]
        url_params = {
            "event_starts_after": start_data_period,
        }

        res = requests.get(
            self.EMISSIONS_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "get CO2 emissions")
            self.try_solve_authentication_error(res, self.EMISSIONS_URL, self.get_emission_intensities, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "Get FM CO2 emissions data")

            # If interval is daily retry once at second_try_time.
            if is_price_epex_based():
                if self.now_is_between(self.first_try_time_emissions_data, self.second_try_time_emissions_data):
                    self.log(f"Retry at {self.second_try_time_emissions_data}.")
                    await self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
            return

        results = res.json()
        # For use in graph
        emission_points = []
        # For use in calculations, it is cleared as we collect new values.
        self.emission_intensities.clear()
        for emission in results:
            emission_value = emission['event_value']
            if emission_value == "null" or emission_value is None:
                continue
            # Set the real value for use in calculations later
            self.emission_intensities[emission['event_start']] = emission_value
            # Adapt value for showing in graph
            emission_value = int(round(float(emission_value) / 10, 0))
            period_start = datetime.fromtimestamp(emission['event_start'] / 1000).isoformat()
            # ToDO: only make and add data_point if less then 5 hours old this keeps the graph clean.
            data_point = {'time': period_start, 'emission': emission_value}
            emission_points.append(data_point)

        await self.__set_emissions_in_graph(emission_points)

        if is_price_epex_based():
            # FM returns all the prices it has, sometimes it has not retrieved new
            # prices yet, than it communicates the prices it does have.
            date_latest_emission = datetime.fromtimestamp(results[-1].get('event_start') / 1000).isoformat()
            date_tomorrow = (now + timedelta(days=1)).isoformat()
            if date_latest_emission < date_tomorrow:
                self.log(f"FM CO2 emissions seem not renewed yet. {date_latest_emission}, "
                         f"retry at {self.second_try_time_emissions_data}.")
                await self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
                return
        self.log(f"FM CO2 successfully retrieved.")
        # A bit of a hack, the method needs to return something for the awaited calls to this method to work...
        return "FM CO2 successfully retrieved."


    def log_failed_response(self, res, endpoint: str):
        """Log failed response for a given endpoint."""
        try:
            self.log(f"{endpoint} failed ({res.status_code}) with JSON response {res.json()}")
        except json.decoder.JSONDecodeError:
            self.log(f"{endpoint} failed ({res.status_code}) with response {res}")


    def authenticate_with_fm(self):
        """Authenticate with the FlexMeasures server and store the returned auth token.
        Hint: the lifetime of the token is limited, so also call this method whenever the server returns a 401 status code.
        """
        self.log(f"authenticate_with_fm")
        res = requests.post(
            c.FM_AUTHENTICATION_URL,
            json=dict(
                email=c.FM_ACCOUNT_USERNAME,
                password=c.FM_ACCOUNT_PASSWORD,
            ),
        )
        if not res.status_code == 200:
            self.log_failed_response(res, "requestAuthToken")
            return False
        json = res.json()
        if json is None:
            self.log(f"Authenticating failed, no valid json response.")
            return False

        self.fm_token = res.json().get("auth_token", None)
        if self.fm_token is None:
            self.log(f"Authenticating failed, no auth_token in json response: '{json}'.")
            return False
        return True


    def try_solve_authentication_error(self, res, url, fnc, *fnc_args, **fnc_kwargs):
        if fnc_kwargs.get("retry_auth_once", True) and res.status_code == 401:
            self.log(f"Call to  {url} failed on authorization (possibly the token expired); "
                     f"attempting to re-authenticate once")
            self.authenticate_with_fm()
            fnc_kwargs["retry_auth_once"] = False
            fnc(*fnc_args, **fnc_kwargs)


def format_duration(duration_in_minutes: int):
    MINUTES_IN_A_DAY = 60 * 24
    days = math.floor(duration_in_minutes / MINUTES_IN_A_DAY)
    hours = math.floor((duration_in_minutes - days * MINUTES_IN_A_DAY) / 60)
    minutes = duration_in_minutes - days * MINUTES_IN_A_DAY - hours * 60
    return "%02dd %02dh %02dm" % (days, hours, minutes)
