from datetime import datetime, timedelta
import time
import json
import math
import requests
import constants as c
from typing import List, Union
from v2g_globals import get_local_now
import appdaemon.plugins.hass.hassapi as hass
from v2g_globals import time_round, time_ceil


# TODO:
# Start times of Posting data sometimes seem incorrect, it is recommended to research them.

class SetFMdata(hass.Hass):
    """
    App accounts and sends results to FM hourly for intervals @ resolution, eg. 1/12th of an hour:
    + Average charge power in kW
    + Availability of car and charger for automatic charging (% of time)
    + SoC of the car battery

    Power changes occur at irregular intervals (readings): usually about 15 seconds apart but sometimes hours
    We derive a time series of readings with a regular interval (that is, with a fixed period): we chose 5 minutes
    We send the time series to FlexMeasures in batches, periodically: we chose every 1 hour (with re-tries if needed).
    As sending the data might fail the data is only cleared after it has successfully been sent.

    "Visual representation":
    Power changes:         |  |  |    || |                        |   | |  |   |  |
    5 minute intervals:     |                |                |                |
    epochs_of_equal_power: || |  |    || |   |                |   |   | |  |   |  |


    The availability is how much of the time of an interval (again 1/12th of an hour or 5min)
    the charger and car where available for automatic (dis-)charging.

    The State of Charge is a % that is a momentary measure, no calculations are performed as
    the SoC does not change very often in an interval.
    """

    # CONSTANTS

    # Variables
    # Access token for FM
    # TODO: use fm_client instead.
    fm_token: str = ""

    # Data for separate is sent in separate calls.
    # As a call might fail we keep track of when the data (times-) series has started
    hourly_power_readings_since: datetime
    hourly_availability_readings_since: datetime
    hourly_soc_readings_since: datetime

    # Variables to help calculate average power over the last readings_resolution minutes
    current_power_since: datetime
    current_power: int = 0
    # Duration between two changes in power (epochs_of_equal_power) in seconds
    power_period_duration: int = 0

    # This variable is used to add "energy" of all the epochs_of_equal_power.
    # At the end of the fixed interval this is divided by the length of the interval to calculate
    # the average power in the fixed interval
    period_power_x_duration: int = 0

    # Holds the averaged power readings until successfully sent to backend.
    power_readings: List[float] = []

    # Total seconds that charger and car have been available in the current hour.
    current_availability: bool
    availability_duration_in_current_interval: int = 0
    un_availability_duration_in_current_interval: int = 0
    current_availability_since: datetime
    availability_readings: List[float] = []

    # State of Charge (SoC) of connected car battery. If not connected set to None.
    soc_readings: List[Union[int, None]] = []
    connected_car_soc: Union[int, None] = None

    RESOLUTION_TIMEDELTA: timedelta

    evse_client: object

    async def initialize(self):
        self.log(f"Initializing SetFMdata.")
        self.evse_client = await self.get_app("modbus_evse_client")

        self.RESOLUTION_TIMEDELTA = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)

        local_now = get_local_now()

        # Availability related
        self.availability_duration_in_current_interval = 0
        self.un_availability_duration_in_current_interval = 0
        self.availability_readings = []
        self.current_availability = await self.__is_available()
        self.current_availability_since = local_now
        await self.__record_availability(True)

        self.listen_state(self.__handle_charger_state_change, "sensor.charger_charger_state", attribute="all")
        self.listen_state(self.__handle_charge_mode_change, "input_select.charge_mode", attribute="all")


        # Power related initialisation
        power = await self.get_state("sensor.charger_real_charging_power", "state")
        self.current_power_since = local_now
        self.power_period_duration = 0
        self.period_power_x_duration = 0
        self.power_readings = []
        if power != "unavailable":
            # Ignore a state change to 'unavailable'
            self.current_power = int(float(power))
        else:
            self.current_power = 0
        self.listen_state(self.__handle_charge_power_change, "sensor.charger_real_charging_power", attribute="all")


        # SoC related
        self.connected_car_soc = None
        self.soc_readings = []
        self.listen_state(self.__handle_soc_change, "sensor.charger_connected_car_state_of_charge", attribute="all")
        soc = await self.get_state("sensor.charger_connected_car_state_of_charge", "state")
        self.log(f"init soc: {soc}.")
        if soc != "unavailable":
            # Ignore a state change to 'unavailable'
            soc = int(float(soc))
            await self.__process_soc_change(soc)

        self.authenticate_with_fm()

        runtime = time_ceil(local_now, self.RESOLUTION_TIMEDELTA)
        self.hourly_power_readings_since = runtime
        self.hourly_availability_readings_since = runtime
        self.hourly_soc_readings_since = runtime
        self.run_every(self.__conclude_interval, runtime, c.FM_EVENT_RESOLUTION_IN_MINUTES * 60)

        # Reuse variables for starting hourly "send data to FM"
        resolution = timedelta(minutes=60)
        runtime = time_ceil(runtime, resolution)
        self.run_hourly(self.__try_send_data, runtime)
        self.log("Completed initializing SetFMdata")


    async def __handle_soc_change(self, entity, attribute, old, new, kwargs):
        """ Handle changes in the car's state_of_charge"""
        reported_soc = new["state"]
        self.log(f"__handle_soc_change called with raw SoC: {reported_soc}")
        if isinstance(reported_soc, str):
            if not reported_soc.isnumeric():
                # Sometimes the charger returns "Unknown" or "Undefined" or "Unavailable"
                self.connected_car_soc = None
                return
            reported_soc = int(round(float(reported_soc), 0))
            await self.__process_soc_change(reported_soc)

    async def __process_soc_change(self, soc: int):
        if soc == 0:
            self.connected_car_soc = None
            return

        self.log(f"Processed reported SoC, self.connected_car_soc is now set to: {soc}%.")
        self.connected_car_soc = soc
        await self.__record_availability()

    async def __handle_charge_mode_change(self, entity, attribute, old, new, kwargs):
        """ Handle changes in charger (car) state (eg automatic or not)"""
        self.log(f"__handle_charge_mode_change called.")
        await self.__record_availability()

    async def __handle_charger_state_change(self, entity, attribute, old, new, kwargs):
        """ Handle changes in charger (car) state (eg connected or not)
            Ignore states with string "unavailable".
            (This is not a value related to the availability that is recorded here)
        """
        self.log(f"__handle_charger_state_change called.")
        if old is None:
            return
        else:
            old = old.get('state', 'unavailable')
        new = new.get('state', 'unavailable')
        if old == "unavailable" or new == "unavailable":
            # Ignore state changes related to unavailable. These are not of influence on availability of charger/car.
            return
        await self.__record_availability()

    async def __record_availability(self, conclude_interval=False):
        """ Record (non_)availability durations of time in current interval.
            Called at charge_mode_change and charger_status_change
            Use __conclude_interval argument to conclude an interval (without changing the availability)
        """
        self.log(f"record_availability called __conclude_interval: {conclude_interval}.")
        if self.current_availability != await self.__is_available() or conclude_interval:
            local_now = get_local_now()
            duration = int((local_now - self.current_availability_since).total_seconds() * 1000)

            if conclude_interval:
                self.log("Conclude interval for availability")
            else:
                self.log("Availability changed, process it.")

            if self.current_availability:
                self.availability_duration_in_current_interval += duration
            else:
                self.un_availability_duration_in_current_interval += duration

            if conclude_interval is False:
                self.current_availability = not self.current_availability

            self.current_availability_since = local_now

    async def __handle_charge_power_change(self, entity, attribute, old, new, kwargs):
        """Handle a state change in the power sensor."""
        power = new['state']
        if power == "unavailable":
            # Ignore a state change to 'unavailable'
            return
        power = int(float(power))
        await self.__process_power_change(power)

    async def __process_power_change(self, power: int):
        """Keep track of updated power changes within a regular interval."""
        local_now = get_local_now()
        duration = int((local_now - self.current_power_since).total_seconds())
        self.period_power_x_duration += (duration * power)
        self.power_period_duration += duration
        self.current_power_since = local_now
        self.current_power = power

    async def __conclude_interval(self, *args):
        """ Conclude a regular interval.
            Called every c.FM_EVENT_RESOLUTION_IN_MINUTES minutes (usually 5 minutes)
        """
        self.log(f"__conclude_interval called.")

        await self.__process_power_change(self.current_power)
        await self.__record_availability(True)

        # At initialise there might be an incomplete period,
        # duration must be not more than 5% smaller than readings_resolution * 60
        total_interval_duration = self.availability_duration_in_current_interval + \
                                  self.un_availability_duration_in_current_interval
        if total_interval_duration > (c.FM_EVENT_RESOLUTION_IN_MINUTES * 60 * 0.95):
            # Power related processing
            # Initiate with fallback value
            average_period_power = self.period_power_x_duration
            # If duration = 0 it is assumed it can be skipped. Also prevent division by zero.
            if self.power_period_duration != 0:
                # Calculate average power and convert from Watt to MegaWatt
                average_period_power = round((self.period_power_x_duration / self.power_period_duration) / 1000000, 5)
                self.power_readings.append(average_period_power)

            # Availability related processing
            self.log(f"Concluded availability interval, un_/availability was: "
                     f"{self.un_availability_duration_in_current_interval} / "
                     f"{self.availability_duration_in_current_interval} ms.")
            percentile_availability = round(
                100 * (self.availability_duration_in_current_interval / (total_interval_duration)), 2)
            if percentile_availability > 100.00:
                # Prevent reading > 100% (due to rounding)
                percentile_availability = 100.00
            self.availability_readings.append(percentile_availability)

            # SoC related processing
            # SoC does not change very quickly, so we just read it at conclude time and do not do any calculation.
            self.soc_readings.append(self.connected_car_soc)

            self.log(f"Conclude called. Average power in this period: {average_period_power} MW, "
                     f"Availability: {percentile_availability}%, SoC: {self.connected_car_soc}%.")

        else:
            self.log(f"Period duration too short: {self.power_period_duration} s, discarding this reading.")

        # Reset power values
        self.period_power_x_duration = 0
        self.power_period_duration = 0

        # Reset availability values
        self.availability_duration_in_current_interval = 0
        self.un_availability_duration_in_current_interval = 0

    async def __try_send_data(self, *args):
        """ Central function for sending all readings to FM.
            Called every hour
            Reset reading list/variables if sending was successful """
        self.log(f"__try_send_data called.")

        self.authenticate_with_fm()

        start_from = time_round(get_local_now(), self.RESOLUTION_TIMEDELTA)
        res = await self.__post_power_data()
        if res is True:
            self.log(f"Power data successfully sent, resetting readings")
            self.hourly_power_readings_since = start_from
            self.power_readings.clear()

        res = await self.__post_availability_data()
        if res is True:
            self.log(f"Availability data successfully sent, resetting readings")
            self.hourly_availability_readings_since = start_from
            self.availability_readings.clear()

        res = await self.__post_soc_data()
        if res is True:
            self.log(f"SoC data successfully sent, resetting readings")
            self.hourly_soc_readings_since = start_from
            self.soc_readings.clear()

        return

    def log_failed_response(self, res, endpoint: str):
        """Log failed response for a given endpoint."""
        try:
            self.log(f"{endpoint} failed ({res.status_code}) with JSON response {res.json()}")
        except json.decoder.JSONDecodeError:
            self.log(f"{endpoint} failed ({res.status_code}) with response {res}")

    async def __post_soc_data(self, *args, **kwargs):
        """ Try to Post SoC readings to FM.

        Return false if un-successful """
        self.log(f"__post_soc_data called.")

        # If self.soc_readings is empty there is nothing to send.
        if len(self.soc_readings) == 0:
            self.log("List of soc readings is 0 length..")
            return False

        # TODO: Use generic post_data function
        duration = len(self.soc_readings) * c.FM_EVENT_RESOLUTION_IN_MINUTES
        hours = math.floor(duration / 60)
        minutes = duration - hours * 60
        str_duration = "PT" + str(hours) + "H" + str(minutes) + "M"

        message = {
            "type": "PostSensorDataRequest",
            "sensor": c.FM_ENTITY_ADDRESS_SOC,
            "values": self.soc_readings,
            "start": self.hourly_soc_readings_since.isoformat(),
            "duration": str_duration,
            "unit": "%"
        }
        self.log(f"Post_soc_data message: {message}")
        res = requests.post(
            c.FM_SET_DATA_URL,
            json=message,
            headers={"Authorization": self.fm_token},
        )
        if res.status_code != 200:
            self.log_failed_response(res, "PostSensorData for SoC")
            return False

        return True

    async def __post_availability_data(self, *args, **kwargs):
        """ Try to Post Availability readings to FM.

        Return false if un-successful """
        self.log(f"__post_availability_data called.")

        # If self.availability_readings is empty there is nothing to send.
        if len(self.availability_readings) == 0:
            self.log("List of availability readings is 0 length..")
            return False

        # TODO: Use generic post_data function
        duration = len(self.availability_readings) * c.FM_EVENT_RESOLUTION_IN_MINUTES
        hours = math.floor(duration / 60)
        minutes = duration - hours * 60
        str_duration = "PT" + str(hours) + "H" + str(minutes) + "M"

        message = {
            "type": "PostSensorDataRequest",
            "sensor": c.FM_ENTITY_ADDRESS_AVAILABILITY,
            "values": self.availability_readings,
            "start": self.hourly_availability_readings_since.isoformat(),
            "duration": str_duration,
            "unit": "%"
        }
        # self.log(f"Post_availability_data message: {message}")
        res = requests.post(
            c.FM_SET_DATA_URL,
            json=message,
            headers={"Authorization": self.fm_token},
        )
        if res.status_code != 200:
            self.log_failed_response(res, "PostSensorData for Availability")
            return False
        return True

    async def __post_power_data(self, *args, **kwargs):
        """ Try to Post power readings to FM.

        Return false if un-successful """

        self.log(f"__post_power_data called.")

        # TODO: Use generic post_data function
        # If self.power_readings is empty there is nothing to send.
        if len(self.power_readings) == 0:
            self.log("List of power readings is 0 length..")
            return False

        duration = len(self.power_readings) * c.FM_EVENT_RESOLUTION_IN_MINUTES
        hours = math.floor(duration / 60)
        minutes = duration - hours * 60
        str_duration = "PT" + str(hours) + "H" + str(minutes) + "M"

        message = {
            "type": "PostSensorDataRequest",
            "sensor": c.FM_ENTITY_ADDRESS_POWER,
            "values": self.power_readings,
            "start": self.hourly_power_readings_since.isoformat(),
            "duration": str_duration,
            "unit": "MW"
        }
        self.log(message)
        res = requests.post(
            c.FM_SET_DATA_URL,
            json=message,
            headers={"Authorization": self.fm_token},
        )
        if res.status_code != 200:
            self.log_failed_response(res, "PostSensorData for Power")
            return False
        return True


    # TODO: Implement on FM client, not here.
    def post_data(self, fm_entity_address: str, values: list, start: datetime, duration: str, uom: str) -> bool:
        """General function to post data to FM.

        Args:
            fm_entity_address (str): FM entity address
            values (list): list of values to send
            start (datetime): start date-time of first value
            duration (str): duration for which the values are relevant in format PT12H35M
            uom (str): unit of measure, eg MW, EUR/kWh, etc.

        Returns:
            bool: weather or not sending was successful
        """
        self.log(f"post_data called.")

        # If self.power_readings is empty there is nothing to send.
        if len(values) == 0:
            self.log(f"Value list 0 length, not sending data to {fm_entity_address}.")
            return False

        message = {
            "type": "PostSensorDataRequest",
            "sensor": fm_entity_address,
            "values": values,
            "start": start.isoformat(),
            "duration": duration,
            "unit": uom
        }
        self.log(f"post_data, message: '{message}'.")

        # TODO: use  flexmeasures-client instead
        res = requests.post(
            c.FM_SET_DATA_URL,
            json=message,
            headers={"Authorization": self.fm_token},
        )
        if res.status_code != 200:
            self.log_failed_response(res, "PostSensorData for Power")
            return False
        return True

    async def __is_available(self):
        """ Check if car and charger are available for automatic charging. """
        # TODO:
        # How to take an upcoming calendar item in to account?
        charge_mode = await self.get_state("input_select.charge_mode")
        # Forced charging in progress if SoC is below the minimum SoC setting
        is_evse_and_car_available = self.evse_client.is_available_for_automated_charging()
        if is_evse_and_car_available and charge_mode == "Automatic":
            if self.connected_car_soc is None:
                # SoC is unknown, assume availability
                return True
            else:
                return self.connected_car_soc >= c.CAR_MIN_SOC_IN_PERCENT
        return False

    def authenticate_with_fm(self):
        """Authenticate with the FlexMeasures server and store the returned auth token.
        Hint: the lifetime of the token is limited, so also call this method whenever the server returns a 401 status code.
        """
        # TODO: Use flexmeasures-client instead.
        self.log(f"Authenticating with FlexMeasures on URL '{c.FM_AUTHENTICATION_URL}'.")
        res = requests.post(
            c.FM_AUTHENTICATION_URL,
            json=dict(
                email=c.FM_ACCOUNT_USERNAME,
                password=c.FM_ACCOUNT_PASSWORD,
            ),
        )
        if not res.status_code == 200:
            self.log_failed_response(res, "requestAuthToken")
        tmp = res.json()
        if tmp is None:
            self.log(f"Authenticating failed, no valid json response.")
            return False

        self.fm_token = res.json().get("auth_token", None)
        if self.fm_token is None:
            self.log(f"Authenticating failed, no auth_token in json response: '{json}'.")
            return False
        return True
