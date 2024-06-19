from datetime import datetime, timedelta
import json
import pytz
import math
import re
import requests
import time
import constants as c
from typing import AsyncGenerator, List, Optional

import appdaemon.plugins.hass.hassapi as hass
import isodate


class FlexMeasuresDataImporter(hass.Hass):
    # CONSTANTS
    EMISSIONS_URL: str
    PRICES_URL: str
    CHARGING_COST_URL: str
    CHARGE_POWER_URL: str
    GET_CHARGING_DATA_AT: str  # Time string

    # Variables
    fm_token: str
    first_try_time_price_data: str
    second_try_time_price_data: str

    first_try_time_emissions_data: str
    second_try_time_emissions_data: str

    # Emissions /kwh in the last 7 days to now. Populated by a call to FM.
    # Used for:
    # + Intermediate storage to fill an entity for displaying the data in the graph
    # + Calculation of the emission (savings) in the last 7 days.
    emission_intensities: dict

    # For sending notifications to the user.
    v2g_main_app: object

    def initialize(self):
        """Daily get EPEX prices, emissions and cost data for display in the UI.

        Try to get EPEX price data from the FM server on a daily basis.
        Normally the prices are available around 14:35.
        When this fails retry at 18:30. These times are related to the
        attempts in the server for retrieving EPEX price data.

        The retrieved data is written to the HA input_text.epex_prices,
        HA handles this to render the price data in the UI (chart).
        """
        self.log("Initializing FlexMeasuresDataImporter")

        self.v2g_main_app = self.get_app("v2g_liberty")

        self.fm_token = ""
        self.PRICES_URL = c.FM_GET_DATA_URL + str(c.FM_PRICE_CONSUMPTION_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.EMISSIONS_URL = c.FM_GET_DATA_URL + str(c.FM_EMISSIONS_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.CHARGING_COST_URL = c.FM_GET_DATA_URL + str(c.FM_ACCOUNT_COST_SENSOR_ID) + c.FM_GET_DATA_SLUG
        self.CHARGE_POWER_URL = c.FM_GET_DATA_URL + str(c.FM_ACCOUNT_POWER_SENSOR_ID) + c.FM_GET_DATA_SLUG

        # Price data should normally be available just after 13:00 when data can be
        # retrieved from its original source (ENTSO-E) but sometimes there is a delay of several hours.
        self.first_try_time_price_data = "14:32:00"
        self.second_try_time_price_data = "18:32:00"
        self.run_daily(self.daily_kickoff_price_data, self.first_try_time_price_data)
        # At init also run this as (re-) start is not always around self.first_try_time
        self.daily_kickoff_price_data()

        self.emission_intensities = {}
        self.first_try_time_emissions_data = "15:16:17"
        self.second_try_time_emissions_data = "19:18:17"
        self.run_daily(self.daily_kickoff_emissions_data, self.first_try_time_emissions_data)
        # At init also run this as (re-) start is not always around self.first_try_time
        self.daily_kickoff_emissions_data()

        self.GET_CHARGING_DATA_AT = "01:15:00"
        self.run_daily(self.daily_kickoff_charging_data, self.GET_CHARGING_DATA_AT)
        # At init also run this as (re-) start is not always around self.first_try_time
        self.daily_kickoff_charging_data()

        self.log(
            f"Completed initializing FlexMeasuresDataImporter: check daily at {self.first_try_time_price_data} for new price data with FM.")

    def daily_kickoff_charging_data(self, *args):
        """ This sets off the daily routine to check for charging cost."""
        self.get_charging_cost()
        self.get_charged_energy()

    def daily_kickoff_price_data(self, *args):
        """ This sets off the daily routine to check for new prices."""
        self.get_epex_prices()

    def daily_kickoff_emissions_data(self, *args):
        """ This sets off the daily routine to check for new emission data."""
        self.get_emission_intensities()

    def log_failed_response(self, res, endpoint: str):
        """Log failed response for a given endpoint."""
        try:
            self.log(f"{endpoint} failed ({res.status_code}) with JSON response {res.json()}")
        except json.decoder.JSONDecodeError:
            self.log(f"{endpoint} failed ({res.status_code}) with response {res}")

    def get_charging_cost(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging costs of last 7 days from the server
        Make costs total costs of this period available in HA by setting them in input_text.last week costs
        ToDo: Split cost in charging and dis-charging per day
        """
        now = self.get_now()
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

    def get_charged_energy(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request charging volumes of last 7 days from the server.
        ToDo: make this period a setting for the user.
        Make totals of charging and dis-charging per day and over the period

        """
        now = self.get_now()
        # Getting data since start of yesterday so that user can look back a little further than just current window.

        dt = str(now + timedelta(days=-7))
        startDataPeriod = dt[:10] + "T00:00:00" + dt[-6:]
        dt = str(now)
        endDataPeriod = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": startDataPeriod,
            "event_ends_before": endDataPeriod,
        }

        res = requests.get(
            self.CHARGE_POWER_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "Get FM CHARGE POWER")
            self.try_solve_authentication_error(res, self.CHARGE_POWER_URL, self.get_charging_energy, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "Get FM CHARGE POWER")
            # Currently there is no reason to retry as the server will not re-run scheduled script for cost calculation
        charge_power_points = res.json()

        total_charged_energy_last_7_days = 0
        total_discharged_energy_last_7_days = 0
        total_emissions_last_7_days = 0
        total_saved_emissions_last_7_days = 0
        total_minutes_charged = 0
        total_minutes_discharged = 0
        charging_energy_points = {}
        resolution_in_miliseconds = c.FM_EVENT_RESOLUTION_IN_MINUTES * 60 * 1000

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
                    key -= resolution_in_miliseconds
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
        conversionfactor = 1000 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_discharged_energy_last_7_days = int(round(total_discharged_energy_last_7_days * conversionfactor, 0))
        total_charged_energy_last_7_days = int(round(total_charged_energy_last_7_days * conversionfactor, 0))

        # Convert the returned average MW * kg/MWh over event_resolution (5 minutes) periods to kg (/12)
        conversionfactor = 1 / (60 / c.FM_EVENT_RESOLUTION_IN_MINUTES)
        total_saved_emissions_last_7_days = round(total_saved_emissions_last_7_days * conversionfactor, 1)
        total_emissions_last_7_days = round(total_emissions_last_7_days * conversionfactor, 1)

        self.set_value("input_number.total_discharged_energy_last_7_days", total_discharged_energy_last_7_days)
        self.set_value("input_number.total_charged_energy_last_7_days", total_charged_energy_last_7_days)
        self.set_value("input_number.net_energy_last_7_days",
                       total_charged_energy_last_7_days + total_discharged_energy_last_7_days)

        self.set_value("input_number.total_saved_emissions_last_7_days", total_saved_emissions_last_7_days)
        self.set_value("input_number.total_emissions_last_7_days", total_emissions_last_7_days)
        self.set_value("input_number.net_emissions_last_7_days",
                       total_emissions_last_7_days + total_saved_emissions_last_7_days)

        self.set_value("input_text.total_discharge_time_last_7_days", self.format_duration(total_minutes_discharged))
        self.set_value("input_text.total_charge_time_last_7_days", self.format_duration(total_minutes_charged))

    def format_duration(self, duration_in_minutes: int):
        MINUTES_IN_A_DAY = 60 * 24
        days = math.floor(duration_in_minutes / MINUTES_IN_A_DAY)
        hours = math.floor((duration_in_minutes - days * MINUTES_IN_A_DAY) / 60)
        minutes = duration_in_minutes - days * MINUTES_IN_A_DAY - hours * 60
        return "%02dd %02dh %02dm" % (days, hours, minutes)

    def get_epex_prices(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request prices from the server
        Make prices available in HA by setting them in input_text.epex_prices
        Notify user if there will be negative prices for next day
        """
        now = self.get_now()
        self.authenticate_with_fm()
        # Getting prices since start of yesterday so that user can look back a little further than just current window.
        dt = str(now + timedelta(days=-1))
        start_data_period = dt[:10] + "T00:00:00" + dt[-6:]

        url_params = {
            "event_starts_after": start_data_period,
        }
        res = requests.get(
            self.PRICES_URL,
            params=url_params,
            headers={"Authorization": self.fm_token},
        )

        # Authorisation error, retry
        if res.status_code == 401:
            self.log_failed_response(res, "Get FM EPEX data")
            self.try_solve_authentication_error(res, self.PRICES_URL, self.get_epex_prices, *args, **kwargs)
            return

        if res.status_code != 200:
            self.log_failed_response(res, "Get FM EPEX data")

            # Only retry once at second_try_time.
            if self.now_is_between(self.first_try_time_price_data, self.second_try_time_price_data):
                self.log(f"Retry at {self.second_try_time_price_data}.")
                self.run_at(self.get_epex_prices, self.second_try_time_price_data)
            else:
                self.log(f"Retry tomorrow.")
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
        epex_price_points = []
        has_negative_prices = False
        for price in prices:
            data_point = {
                'time': datetime.fromtimestamp(price['event_start'] / 1000).isoformat(),
                'price': round(((price['event_value'] * conversion) +
                                c.ENERGY_PRICE_MARKUP_PER_KWH) * vat_factor, 2)
            }
            # TODO: Also check if data point is in the future
            if data_point['price'] < 0:
                has_negative_prices = True
            epex_price_points.append(data_point)

        # To make sure HA considers this as new info a datetime is added
        new_state = "EPEX prices collected at " + now.isoformat()
        result = {'records': epex_price_points}
        self.set_state("input_text.epex_prices", state=new_state, attributes=result)

        # FM returns all the prices it has, sometimes it has not retrieved new
        # prices yet, than it communicates the prices it does have.
        date_latest_price = datetime.fromtimestamp(prices[-1].get('event_start') / 1000).isoformat()
        date_tomorrow = (now + timedelta(days=1)).isoformat()
        if date_latest_price < date_tomorrow:
            self.log(f"FM EPEX prices seem not renewed yet, latest price at: {date_latest_price}, "
                     f"Retry at {self.second_try_time_price_data}.")
            self.run_at(self.get_epex_prices, self.second_try_time_price_data)
        else:
            if has_negative_prices:
                self.v2g_main_app.notify_user(
                    message="Consider to check times in the app to optimize electricity usage.",
                    title="Negative electricity prices upcoming",
                    tag="negative_energy_prices",
                    critical=False,
                    send_to_all=True,
                    ttl=12 * 60 * 60
                )
            self.log(f"FM EPEX prices successfully retrieved. Latest price at: {date_latest_price}.")

    def get_emission_intensities(self, *args, **kwargs):
        """ Communicate with FM server and check the results.

        Request hourly CO2 emissions due to electricity production in NL from the server
        Make values available in HA by setting them in input_text.co2_emissions
        """

        self.log("FMdata: get_emission_intensities called")

        now = self.get_now()
        self.authenticate_with_fm()
        # Getting emissions since a week ago. This is needed for calculation of CO2 savings
        # and will be (more than) enough for the graph to show.
        # Because we want to show it in the graph we do not use an end url param.
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

            # Only retry once at second_try_time.
            if self.now_is_between(self.first_try_time_emissions_data, self.second_try_time_emissions_data):
                self.log(f"Retry at {self.second_try_time_emissions_data}.")
                self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
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

        # To make sure HA considers this as new info a datetime is added
        new_state = "Emissions collected at " + now.isoformat()
        result = {'records': emission_points}
        self.set_state("input_text.co2_emissions", state=new_state, attributes=result)

        # FM returns all the prices it has, sometimes it has not retrieved new
        # prices yet, than it communicates the prices it does have.
        date_latest_emission = datetime.fromtimestamp(results[-1].get('event_start') / 1000).isoformat()
        date_tomorrow = (now + timedelta(days=1)).isoformat()
        if date_latest_emission < date_tomorrow:
            self.log(
                f"FM CO2 emissions seem not renewed yet. {date_latest_emission}, retry at {self.second_try_time_emissions_data}.")
            self.run_at(self.get_emission_intensities, self.second_try_time_emissions_data)
        else:
            self.log(f"FM CO2 successfully retrieved. Latest price at: {date_latest_emission}.")

    def authenticate_with_fm(self):
        """Authenticate with the FlexMeasures server and store the returned auth token.
        Hint: the lifetime of the token is limited, so also call this method whenever the server returns a 401 status code.
        """
        self.log(f"set_fm_data: Authenticating.")
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
