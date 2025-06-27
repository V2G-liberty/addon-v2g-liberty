"""Module to collect and formats data to send it to FlexMeasures"""

from datetime import datetime, timedelta
import math
from typing import List, Union
import constants as c
import log_wrapper
from v2g_globals import get_local_now, time_round, time_ceil
from event_bus import EventBus
from appdaemon.plugins.hass.hassapi import Hass


# TODO:
# Start times of Posting data sometimes seem incorrect, it is recommended to research them.


class DataMonitor:
    """
    This class monitors data changes, collects this data and formats in the right way.
    It sends results to FM hourly for intervals @ resolution, eg. 1/12th of an hour:
    + Average charge power in kW
    + Availability of car and charger for automatic charging (% of time)
    + SoC of the car battery

    Power changes occur at irregular intervals (readings): usually about 15 seconds apart but
    sometimes hours. We derive a time series of readings with a regular interval (that is, with a
    fixed period): we chose 5 minutes. We send the time series to FlexMeasures in batches,
    periodically: we chose every 1 hour (with re-tries if needed).
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

    event_bus: EventBus = None

    # CONSTANTS
    EMPTY_STATES = [None, "unknown", "unavailable", ""]

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

    fm_client_app: object = None
    evse_client_app: object = None
    hass: Hass = None

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)
        self.event_bus = event_bus

    async def initialize(self):
        self.__log("Initializing DataMonitor.")

        local_now = get_local_now()

        # Availability related
        self.availability_duration_in_current_interval = 0
        self.un_availability_duration_in_current_interval = 0
        self.availability_readings = []
        self.current_availability = await self.__is_available()
        self.current_availability_since = local_now
        await self.__record_availability(True)

        self.event_bus.add_event_listener(
            "charger_state_change", self._handle_charger_state_change
        )

        await self.hass.listen_state(
            self.__handle_charge_mode_change,
            "input_select.charge_mode",
            attribute="all",
        )

        # Power related initialisation
        # power = 0
        self.current_power_since = local_now
        self.power_period_duration = 0
        self.period_power_x_duration = 0
        self.power_readings = []
        self.current_power = 0
        self.event_bus.add_event_listener(
            "charge_power_change", self._process_power_change
        )

        # SoC related
        self.connected_car_soc = None
        self.soc_readings = []
        self.event_bus.add_event_listener("soc_change", self._process_soc_change)

        runtime = time_ceil(local_now, c.EVENT_RESOLUTION)
        self.hourly_power_readings_since = runtime
        self.hourly_availability_readings_since = runtime
        self.hourly_soc_readings_since = runtime
        await self.hass.run_every(
            self.__conclude_interval, runtime, c.FM_EVENT_RESOLUTION_IN_MINUTES * 60
        )

        resolution = timedelta(minutes=60)
        runtime = time_ceil(runtime, resolution)
        await self.hass.run_hourly(self.__try_send_data, runtime)
        self.__log("Completed initializing DataMonitor")

    async def _process_soc_change(self, new_soc: int, old_soc: int):
        if new_soc in self.EMPTY_STATES:
            # Sometimes the charger returns "Unknown" or "Undefined" or "Unavailable"
            self.connected_car_soc = None
            return

        if isinstance(new_soc, int):
            self.connected_car_soc = new_soc
        else:
            self.connected_car_soc = None
            return
        await self.__record_availability()

    async def __handle_charge_mode_change(self, entity, attribute, old, new, kwargs):
        """Handle changes in charger (car) state (eg automatic or not)"""
        await self.__record_availability()

    async def _handle_charger_state_change(
        self, new_charger_state: int, old_charger_state: int, new_charger_state_str: str
    ):
        """Handle changes in charger (car) state (eg not_connected, idle, charging, error, etc.)
        Ignore states with string "unavailable", this is not a value related to the availability
        that is recorded here.
        """
        if (
            old_charger_state in self.EMPTY_STATES
            or new_charger_state in self.EMPTY_STATES
        ):
            # Ignore state changes related to unavailable. These are not of influence on
            # availability of charger/car.
            return
        await self.__record_availability()

    async def __record_availability(self, conclude_interval=False):
        """Record (non_)availability durations of time in current interval.
        Called at charge_mode_change and charger_status_change
        Use __conclude_interval argument to conclude an interval (without changing the availability)
        """
        if (
            self.current_availability != await self.__is_available()
            or conclude_interval
        ):
            local_now = get_local_now()
            duration = int(
                (local_now - self.current_availability_since).total_seconds() * 1000
            )

            if self.current_availability:
                self.availability_duration_in_current_interval += duration
            else:
                self.un_availability_duration_in_current_interval += duration

            if conclude_interval is False:
                self.current_availability = not self.current_availability

            self.current_availability_since = local_now

    async def _process_power_change(self, new_power: int):
        """Keep track of updated power changes within a regular interval."""
        local_now = get_local_now()
        duration = int((local_now - self.current_power_since).total_seconds())
        self.period_power_x_duration += duration * new_power
        self.power_period_duration += duration
        self.current_power_since = local_now
        self.current_power = new_power

    async def __conclude_interval(self, *args):
        """Conclude a regular interval.
        Called every c.FM_EVENT_RESOLUTION_IN_MINUTES minutes (usually 5 minutes)
        """

        await self._process_power_change(self.current_power)
        await self.__record_availability(True)

        # At initialise there might be an incomplete period,
        # duration must be not more than 5% smaller than readings_resolution * 60
        total_interval_duration = (
            self.availability_duration_in_current_interval
            + self.un_availability_duration_in_current_interval
        )
        if total_interval_duration > (c.FM_EVENT_RESOLUTION_IN_MINUTES * 60 * 0.95):
            # Power related processing
            # Initiate with fallback value
            average_period_power = self.period_power_x_duration
            # If duration = 0 it is assumed it can be skipped. Also prevent division by zero.
            if self.power_period_duration != 0:
                # Calculate average power and convert from Watt to MegaWatt
                average_period_power = round(
                    (self.period_power_x_duration / self.power_period_duration)
                    / 1000000,
                    5,
                )
                self.power_readings.append(average_period_power)

            # Availability related processing
            percentile_availability = round(
                100
                * (
                    self.availability_duration_in_current_interval
                    / (total_interval_duration)
                ),
                2,
            )
            if percentile_availability > 100.00:
                # Prevent reading > 100% (due to rounding)
                percentile_availability = 100.00
            self.availability_readings.append(percentile_availability)

            # SoC does not change very quickly, so we just read it at conclude time and do not do
            # any calculation.
            self.soc_readings.append(self.connected_car_soc)

        else:
            self.__log(
                f"Period duration too short: {self.power_period_duration} s, "
                f"discarding this reading."
            )

        # Reset power values
        self.period_power_x_duration = 0
        self.power_period_duration = 0

        # Reset availability values
        self.availability_duration_in_current_interval = 0
        self.un_availability_duration_in_current_interval = 0

    async def __try_send_data(self, *args):
        """Central function for sending all readings to FM.
        Called every hour
        Reset reading list/variables if sending was successful"""
        self.__log("Trying to send data")

        start_from = time_round(get_local_now(), c.EVENT_RESOLUTION)
        res = await self.__post_power_data()
        if res is True:
            self.__log("Power data successfully sent.")
            self.hourly_power_readings_since = start_from
            self.power_readings.clear()

        res = await self.__post_availability_data()
        if res is True:
            self.__log("Availability data successfully sent.")
            self.hourly_availability_readings_since = start_from
            self.availability_readings.clear()

        res = await self.__post_soc_data()
        if res is True:
            self.__log("SoC data successfully sent")
            self.hourly_soc_readings_since = start_from
            self.soc_readings.clear()

        return

    async def __post_soc_data(self, *args, **kwargs):
        """Try to Post SoC readings to FM.
        Return false if un-successful"""
        # If self.soc_readings is empty there is nothing to send.
        if len(self.soc_readings) == 0:
            self.__log("List of soc readings is 0 length..", level="WARNING")
            return False

        str_duration = len_to_iso_duration(len(self.soc_readings))

        res = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_SOC_SENSOR_ID,
            values=self.soc_readings,
            start=self.hourly_soc_readings_since.isoformat(),
            duration=str_duration,
            uom="%",
        )
        return res

    async def __post_availability_data(self, *args, **kwargs):
        """Try to Post Availability readings to FM.
        Return false if un-successful"""
        # If self.availability_readings is empty there is nothing to send.
        if len(self.availability_readings) == 0:
            self.__log("List of availability readings is 0 length..", level="WARNING")
            return False

        str_duration = len_to_iso_duration(len(self.availability_readings))

        res = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
            values=self.availability_readings,
            start=self.hourly_availability_readings_since.isoformat(),
            duration=str_duration,
            uom="%",
        )
        return res

    async def __post_power_data(self, *args, **kwargs):
        """Try to Post power readings to FM.
        Return false if un-successful"""
        # If self.power_readings is empty there is nothing to send.
        if len(self.power_readings) == 0:
            self.__log("List of power readings is 0 length..", level="WARNING")
            return False

        str_duration = len_to_iso_duration(len(self.power_readings))

        res = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
            values=self.power_readings,
            start=self.hourly_power_readings_since.isoformat(),
            duration=str_duration,
            uom="MW",
        )
        return res

    async def __is_available(self):
        """Check if car and charger are available for automatic charging."""
        # TODO:
        # How to take an upcoming calendar item in to account?
        charge_mode = await self.hass.get_state("input_select.charge_mode")
        # Forced charging in progress if SoC is below the minimum SoC setting
        is_evse_and_car_available = (
            self.evse_client_app.is_available_for_automated_charging()
        )
        if is_evse_and_car_available and charge_mode == "Automatic":
            if self.connected_car_soc in self.EMPTY_STATES:
                # SoC is unknown. Rare after previous check. Unknown would normally mean,
                # disconnected or error.
                # NOTE: 2024-12-12 version 0.4.3, this changed from assume availability to
                # no-availability.
                return False
            else:
                return self.connected_car_soc >= c.CAR_MIN_SOC_IN_PERCENT
        return False


def len_to_iso_duration(nr_of_intervals: int) -> str:
    duration = nr_of_intervals * c.FM_EVENT_RESOLUTION_IN_MINUTES
    hours = math.floor(duration / 60)
    minutes = duration - hours * 60
    str_duration = f"PT{hours}H{minutes}M"
    return str_duration
