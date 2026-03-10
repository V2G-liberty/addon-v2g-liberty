"""Module to collect and monitor charge data at regular intervals."""

from datetime import datetime, timedelta
from typing import Union

from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .log_wrapper import get_class_method_logger
from .v2g_globals import get_local_now, time_ceil, time_round
from .event_bus import EventBus


class DataMonitor:
    """
    This class monitors data changes and collects charge metrics at regular intervals.

    It tracks:
    + Average charge power in kW
    + Availability of car and charger for automatic charging (% of time)
    + SoC of the car battery

    Power changes occur at irregular intervals (readings): usually about 15 seconds apart but
    sometimes hours. We derive a time series of readings with a regular interval (that is, with a
    fixed period): we chose 5 minutes.

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

    # App state priority: lower number = higher priority
    STATE_PRIORITY = {
        "error": 1,
        "not_connected": 2,
        "max_boost": 3,
        "charge": 4,
        "discharge": 4,
        "pause": 4,
        "automatic": 4,
        "unknown": 5,
    }

    # Mapping from HA charge_mode to app_state
    CHARGE_MODE_TO_APP_STATE = {
        "Max boost now": "charge",
        "Max discharge now": "discharge",
        "Stop": "pause",
        "Automatic": "automatic",
    }

    # Variables to help calculate average power over the last readings_resolution minutes
    current_power_since: datetime
    current_power: int = 0
    # Duration between two changes in power (epochs_of_equal_power) in seconds
    power_period_duration: int = 0

    # This variable is used to add "energy" of all the epochs_of_equal_power.
    # At the end of the fixed interval this is divided by the length of the interval to calculate
    # the average power in the fixed interval
    period_power_x_duration: int = 0

    # Total seconds that charger and car have been available in the current hour.
    current_availability: bool
    availability_duration_in_current_interval: int = 0
    un_availability_duration_in_current_interval: int = 0
    current_availability_since: datetime

    # State of Charge (SoC) of connected car battery. If not connected set to None.
    connected_car_soc: Union[int, None] = None

    # App state tracking variables
    _current_charger_state: Union[int, None] = None
    _current_charge_mode: str = ""
    _current_app_state: str = "unknown"
    _current_app_state_since: datetime
    _app_state_durations: dict

    evse_client_app: object = None
    # For persisting interval data to local SQLite database
    data_store = None
    # For subscribing to calendar_change events
    reservations_client = None
    hass: Hass = None

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.__log = get_class_method_logger(hass.log)
        self.event_bus = event_bus

    async def initialize(self):
        self.__log("Initialising DataMonitor.")

        local_now = get_local_now()

        # State initialisation — must come before availability check
        self.connected_car_soc = None
        self._current_charger_state = None
        charge_mode = await self.hass.get_state("input_select.charge_mode")
        self._current_charge_mode = (
            charge_mode if charge_mode not in self.EMPTY_STATES else ""
        )
        self._app_state_durations = {}
        self._current_app_state = self._derive_app_state()
        self._current_app_state_since = local_now

        # Power related initialisation
        self.current_power_since = local_now
        self.power_period_duration = 0
        self.period_power_x_duration = 0
        self.current_power = 0

        # Availability — after state is initialised
        self.availability_duration_in_current_interval = 0
        self.un_availability_duration_in_current_interval = 0
        self.current_availability = await self.__is_available()
        self.current_availability_since = local_now
        await self.__record_availability(True)

        # Event listeners
        self.event_bus.add_event_listener(
            "charger_state_change", self._handle_charger_state_change
        )
        await self.hass.listen_state(
            self.__handle_charge_mode_change,
            "input_select.charge_mode",
            attribute="all",
        )
        self.event_bus.add_event_listener(
            "charge_power_change", self._process_power_change
        )
        self.event_bus.add_event_listener("soc_change", self._process_soc_change)

        # Reservation logging
        if self.reservations_client is not None:
            self.reservations_client.add_listener(
                "calendar_change", self._handle_calendar_change
            )

        runtime = time_ceil(local_now, c.EVENT_RESOLUTION)
        await self.hass.run_every(
            self.__conclude_interval, runtime, c.FM_EVENT_RESOLUTION_IN_MINUTES * 60
        )

        self.__log("Completed initialising DataMonitor")

    async def _process_soc_change(self, new_soc: int, old_soc: int):
        if new_soc in self.EMPTY_STATES:
            # Sometimes the charger returns "Unknown" or "Undefined" or "Unavailable"
            self.connected_car_soc = None
            self._update_app_state()
            return

        if isinstance(new_soc, int):
            self.connected_car_soc = new_soc
        else:
            self.connected_car_soc = None
            self._update_app_state()
            return
        self._update_app_state()
        await self.__record_availability()

    async def __handle_charge_mode_change(self, entity, attribute, old, new, kwargs):
        """Handle changes in charge mode (eg automatic, stop, etc.)"""
        # Track charge_mode for app_state derivation
        new_state = new.get("state", "") if isinstance(new, dict) else str(new)
        if new_state not in self.EMPTY_STATES:
            self._current_charge_mode = new_state
            self._update_app_state()
        await self.__record_availability()

    async def _handle_charger_state_change(
        self, new_charger_state: int, old_charger_state: int, new_charger_state_str: str
    ):
        """Handle changes in charger (car) state (eg not_connected, idle, charging, error, etc.)
        Ignore states with string "unavailable", this is not a value related to the availability
        that is recorded here.
        """
        # Track charger state for app_state derivation
        if new_charger_state not in self.EMPTY_STATES:
            self._current_charger_state = new_charger_state
            self._update_app_state()

        if (
            old_charger_state in self.EMPTY_STATES
            or new_charger_state in self.EMPTY_STATES
        ):
            # Ignore state changes related to unavailable. These are not of influence on
            # availability of charger/car.
            return
        await self.__record_availability()

    async def _handle_calendar_change(self, v2g_events=None, v2g_args=None):
        """Write reservation snapshots to the local database.

        Called when the calendar changes. Each active (non-dismissed) reservation
        is logged as a snapshot in reservation_log.
        """
        if self.data_store is None or v2g_events is None:
            return

        timestamp = get_local_now().isoformat()

        for event in v2g_events:
            if event == "un-initiated":
                continue
            if event.get("dismissed"):
                continue

            start = time_round(event["start"], c.EVENT_RESOLUTION)
            end = time_round(event["end"], c.EVENT_RESOLUTION)
            target_soc_pct = event.get("target_soc_percent")

            try:
                self.data_store.insert_reservation(
                    timestamp=timestamp,
                    start_timestamp=start.isoformat(),
                    end_timestamp=end.isoformat(),
                    target_soc_pct=(
                        float(target_soc_pct) if target_soc_pct is not None else None
                    ),
                )
            except Exception as e:
                self.__log(
                    f"Failed to write reservation to DB: {e}",
                    level="WARNING",
                )

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

    def _derive_app_state(self) -> str:
        """Derive app_state from charger_state, charge_mode and SoC.

        Priority: error (1) > not_connected (2) > max_boost (3) > charge_mode-based (4).
        """
        # Priority 1: error
        if (
            self._current_charger_state is not None
            and self.evse_client_app is not None
            and self._current_charger_state in self.evse_client_app.ERROR_STATES
        ):
            return "error"

        # Priority 2: not_connected
        if (
            self._current_charger_state is not None
            and self.evse_client_app is not None
            and self._current_charger_state in self.evse_client_app.DISCONNECTED_STATES
        ):
            return "not_connected"

        # Priority 3: max_boost (SoC below minimum, independent of charge_mode)
        if (
            isinstance(self.connected_car_soc, int)
            and self.connected_car_soc < c.CAR_MIN_SOC_IN_PERCENT
        ):
            return "max_boost"

        # Priority 4: from charge_mode
        return self.CHARGE_MODE_TO_APP_STATE.get(self._current_charge_mode, "unknown")

    def _update_app_state(self):
        """Recalculate app_state and track duration of previous state."""
        new_state = self._derive_app_state()
        if new_state == self._current_app_state:
            return

        local_now = get_local_now()
        duration_ms = int(
            (local_now - self._current_app_state_since).total_seconds() * 1000
        )
        self._app_state_durations[self._current_app_state] = (
            self._app_state_durations.get(self._current_app_state, 0) + duration_ms
        )
        self._current_app_state = new_state
        self._current_app_state_since = local_now

    def _conclude_app_state(self) -> str:
        """Close current state, pick winner for this interval, and reset.

        Winner: highest priority state that occurred. If multiple states
        share the same priority, the one with the longest duration wins.
        """
        # Close out the current state
        local_now = get_local_now()
        duration_ms = int(
            (local_now - self._current_app_state_since).total_seconds() * 1000
        )
        self._app_state_durations[self._current_app_state] = (
            self._app_state_durations.get(self._current_app_state, 0) + duration_ms
        )

        # Pick winner
        winner = self._pick_winning_state()

        # Reset for next interval
        self._app_state_durations = {}
        self._current_app_state_since = local_now

        return winner

    def _pick_winning_state(self) -> str:
        """Pick the winning app_state from tracked durations.

        Highest priority (lowest number) wins. Among equal priority,
        the state with the longest duration wins.
        """
        if not self._app_state_durations:
            return "unknown"

        min_prio = min(
            self.STATE_PRIORITY.get(s, 99) for s in self._app_state_durations
        )
        candidates = {
            s: d
            for s, d in self._app_state_durations.items()
            if self.STATE_PRIORITY.get(s, 99) == min_prio
        }
        return max(candidates, key=candidates.get)

    async def _process_power_change(self, new_power: int):
        """Keep track of updated power changes within a regular interval."""
        if not isinstance(new_power, (int, float)):
            return
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
        app_state = self._conclude_app_state()

        # At initialise there might be an incomplete period,
        # duration must be not more than 5% smaller than readings_resolution * 60
        total_interval_duration = (
            self.availability_duration_in_current_interval
            + self.un_availability_duration_in_current_interval
        )
        if total_interval_duration > (c.FM_EVENT_RESOLUTION_IN_MINUTES * 60 * 0.95):
            # Power related processing
            average_power_kw = 0.0
            # If duration = 0 it is assumed it can be skipped. Also prevent division by zero.
            if self.power_period_duration != 0:
                # Calculate average power and convert from Watt to kilowatt
                average_power_kw = round(
                    self.period_power_x_duration / self.power_period_duration / 1000,
                    3,
                )

            # Availability related processing
            availability_pct = round(
                100
                * (
                    self.availability_duration_in_current_interval
                    / (total_interval_duration)
                ),
                2,
            )
            if availability_pct > 100.00:
                # Prevent reading > 100% (due to rounding)
                availability_pct = 100.00

            # SoC does not change very quickly, so we just read it at conclude time and do not do
            # any calculation.
            soc = self.connected_car_soc

            # Calculate energy from average power and interval duration
            energy_kwh = round(
                average_power_kw * c.FM_EVENT_RESOLUTION_IN_MINUTES / 60, 6
            )

            # Persist interval data to local database
            await self._write_interval_to_db(
                energy_kwh, availability_pct, soc, app_state
            )

            # Update homepage sensors with today's totals
            await self._emit_today_totals()

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

    async def _write_interval_to_db(
        self,
        energy_kwh: float,
        availability_pct: float,
        soc: Union[int, None],
        app_state: str,
    ):
        """Write a concluded interval to the local SQLite database."""
        if self.data_store is None:
            return

        # Timestamp = start of the interval that just concluded
        interval_end = time_round(get_local_now(), c.EVENT_RESOLUTION)
        interval_start = interval_end - timedelta(
            minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES
        )
        timestamp = interval_start.isoformat()

        try:
            self.data_store.insert_interval(
                timestamp=timestamp,
                energy_kwh=energy_kwh,
                app_state=app_state,
                soc_pct=float(soc) if soc is not None else None,
                availability_pct=availability_pct,
            )
        except Exception as e:
            self.__log(
                f"Failed to write interval to DB: {e}",
                level="WARNING",
            )

    async def _emit_today_totals(self):
        """Query today's aggregated data and emit an event for homepage sensors."""
        if self.data_store is None:
            return

        now = get_local_now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        try:
            result = self.data_store.get_aggregated_data(
                start=today_start.isoformat(),
                end=today_end.isoformat(),
                granularity="days",
            )
        except Exception as e:
            self.__log(
                f"Failed to query today's totals: {e}",
                level="WARNING",
            )
            return

        if result:
            day = result[0]
            charge_kwh = day["charge_kwh"]
            charge_cost = day["charge_cost"]
            discharge_kwh = day["discharge_kwh"]
            discharge_revenue = day["discharge_revenue"]
        else:
            charge_kwh = 0.0
            charge_cost = 0.0
            discharge_kwh = 0.0
            discharge_revenue = 0.0

        self.event_bus.emit_event(
            "today_energy_update",
            charge_kwh=charge_kwh,
            charge_cost=charge_cost,
            discharge_kwh=discharge_kwh,
            discharge_revenue=discharge_revenue,
        )

    async def __is_available(self):
        """Check if car and charger are available for automatic charging."""
        # TODO:
        # How to take an upcoming calendar item in to account?
        is_evse_and_car_available = (
            self.evse_client_app.is_available_for_automated_charging()
        )
        if is_evse_and_car_available and self._current_charge_mode == "Automatic":
            if self.connected_car_soc in self.EMPTY_STATES:
                # SoC is unknown. Rare after previous check. Unknown would normally mean,
                # disconnected or error.
                # NOTE: 2024-12-12 version 0.4.3, this changed from assume availability to
                # no-availability.
                return False
            else:
                return self.connected_car_soc >= c.CAR_MIN_SOC_IN_PERCENT
        return False
