"""Generic electric vehicle (EV) class."""

from apps.v2g_liberty.evs.base_ev import BaseEV
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from appdaemon.plugins.hass.hassapi import Hass


class ElectricVehicle(BaseEV):
    """Generic electric vehicle (EV) class."""

    # A practical absolute max. capacity of the car battery in percent.
    # Quasar + Nissan Leaf will never charge higher than 97%.
    _SOC_SYSTEM_LIMIT_PERCENT: int = 97

    # TODO: Make this a NissanLeaf specific class because it has the skip-soc-check.
    # TODO: Move many methods/properties to the BaseEV class.
    def __init__(self, hass: Hass, event_bus: EventBus):
        super().__init__()
        self.hass = hass
        self._eb = event_bus
        self.__log = get_class_method_logger(hass.log)
        self._name: str | None = None
        self._ev_id: str | None = None
        self._battery_capacity_kwh: int | None = None
        self._charging_efficiency: int | None = None
        self._consumption_wh_per_km: int | None = None
        self._soc: float | None = None
        self._min_soc_percent: int | None = None
        self._max_soc_percent: int | None = None
        self._min_soc_kwh: int | None = None
        self._max_soc_kwh: int | None = None
        self._max_range_km: int | None = None

    def initialise_ev(
        self,
        name: str,
        ev_id: str,
        battery_capacity_kwh: int,
        charging_efficiency_percent: int,
        consumption_wh_per_km: int,
        min_soc_percent: int,
        max_soc_percent: int,
    ):
        """
        TODO: Move to __init__????
        Initialize ElectricVehicle

        Parameters:
        - name (str):
          Name of the car e.g. model/type
        - ev_id (str):
          Unique id (often exposed by the car through ISO15118.)
        - battery_capacity_kwh (int):
          Usable battery capacity in kWh (10-200).
          For now: entered by user, preferably read from car via charger.
        - charging_efficiency_percent (int):
          Charging efficiency in percent (10-100).
          For now: entered by user, preferably read from car via charger.
        - consumption_wh_per_km (int):
          Consumption in Wh per km (100 - 400).
        - min_soc_percent (int):
          Minimum state of charge in percent (10-55). Entered by user.
        - max_soc_percent (int):
          Maximum state of charge in percent (60-95). Entered by user.
        """
        self._name = name
        self._ev_id = ev_id
        self._set_battery_capacity_kwh(battery_capacity_kwh)
        self._set_charging_efficiency(charging_efficiency_percent)
        self._set_consumption_wh_per_km(consumption_wh_per_km)
        self._set_soc_limits(min_soc_percent, max_soc_percent)
        self._max_range_km = int(
            round((self._battery_capacity_kwh * 1000 / self._consumption_wh_per_km), 0)
        )

    def _set_consumption_wh_per_km(self, consumption_wh_per_km: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if consumption_wh_per_km < 100:
            self.__log(
                f"Consumption {consumption_wh_per_km} Wh/km is too low, 100 wh/km is used.",
                level="WARNING",
            )
            consumption_wh_per_km = 100
        elif consumption_wh_per_km > 400:
            self.__log(
                f"Consumption {consumption_wh_per_km} Wh/km is too high, 400 wh/km is used.",
                level="WARNING",
            )
            consumption_wh_per_km = 400
        else:
            self.__log(f"Set car consumption to {consumption_wh_per_km} Wh/km.")
        self._consumption_wh_per_km = consumption_wh_per_km

    def _set_battery_capacity_kwh(self, capacity_kwh: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if capacity_kwh < 10:
            self.__log(
                f"Battery capacity {capacity_kwh} kWh is too low, 10 kWh is used.",
                level="WARNING",
            )
            capacity_kwh = 10
        elif capacity_kwh > 200:
            self.__log(
                f"Battery capacity {capacity_kwh} kWh is too high, 200 kWh is used.",
                level="WARNING",
            )
            capacity_kwh = 200
        else:
            self.__log(f"Set ev battery capacity to {capacity_kwh} kWh.")
        self._battery_capacity_kwh = capacity_kwh

    def _set_charging_efficiency(self, efficiency_percent: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if efficiency_percent < 50:
            self.__log(
                f"Efficiency percent {efficiency_percent} is to low, using minimum 50.",
                level="WARNING",
            )
            efficiency_percent = 50
        elif efficiency_percent > 100:
            self.__log(
                f"Efficiency percent {efficiency_percent} is too high, using maximum 100.",
                level="WARNING",
            )
            efficiency_percent = 100
        else:
            self.__log(f"Set EVSE charging efficiency to {efficiency_percent}%.")
        self._charging_efficiency = round(efficiency_percent / 100, 2)

    def _set_soc_limits(self, min_soc: int, max_soc: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if min_soc is None or min_soc < 10:
            self.__log(
                f"Min_soc percent {min_soc} is to low, using minimum 10.",
                level="WARNING",
            )
            min_soc = 10
        elif min_soc > 55:
            self.__log(
                f"Min_soc percent {min_soc} is too high, using maximum 55.",
                level="WARNING",
            )
            min_soc = 55
        self._min_soc_percent = min_soc
        self._min_soc_kwh = int(
            round((min_soc / 100.0) * self._battery_capacity_kwh, 0)
        )
        self.__log(f"Set min_soc to {min_soc}%.")

        if max_soc is None or max_soc < 60:
            self.__log(
                f"Max_soc percent {max_soc} is to low, using minimum 60.",
                level="WARNING",
            )
            max_soc = 60
        elif max_soc > 95:
            self.__log(
                f"Max_soc percent {max_soc} is too high, using maximum 95.",
                level="WARNING",
            )
            max_soc = 95

        self._max_soc_percent = max_soc
        self._max_soc_kwh = int(
            round((max_soc / 100.0) * self._battery_capacity_kwh, 0)
        )
        self.__log(f"Set max_soc to {max_soc}%.")

    @property
    def is_soc_below_minimum(self) -> bool:
        """Returns True if SoC is below minimum SoC setting."""
        if self.soc is None:
            self.__log(
                "Cannot determine if SoC is below minimum, soc is None.",
                level="WARNING",
            )
            return False
        return self._soc < self._min_soc_percent

    @property
    def is_soc_above_maximum(self) -> bool:
        """Returns True if SoC is above maximum SoC setting."""
        if self.soc is None:
            self.__log(
                "Cannot determine if SoC is above maximum, soc is None.",
                level="WARNING",
            )
            return False
        return self._soc > self._max_soc_percent

    @property
    def wh_to_min_soc(self) -> int:
        """Returns the amount of energy (wh) needed to reach minimum SoC, taking roundtrip
        efficiency into account."""
        if self.is_soc_above_maximum:
            return 0
        # For % /100, for kwh to wh * 1000 results in *10...
        return int(
            ((self._min_soc_percent - self._soc) * self._battery_capacity_kwh * 10)
            / (self._charging_efficiency**0.5)
        )

    @property
    def min_soc_percent(self) -> int:
        return self._min_soc_percent

    @property
    def max_soc_percent(self) -> int:
        return self._max_soc_percent

    @property
    def min_soc_kwh(self) -> int:
        return self._min_soc_kwh

    @property
    def max_soc_kwh(self) -> int:
        return self._max_soc_kwh

    @property
    def soc_system_limit_percent(self) -> int:
        """Returns the system limit for maximum SoC in percent."""
        return self._SOC_SYSTEM_LIMIT_PERCENT

    @property
    def name(self) -> str:
        return self._name

    @property
    def ev_id(self) -> str:
        return self._ev_id

    @property
    def charging_efficiency(self) -> float:
        return self._charging_efficiency

    @property
    def battery_capacity_kwh(self) -> int | None:
        return self._battery_capacity_kwh

    def set_soc(self, new_soc: float):
        """Set State of Charge (SoC) in %.
        Emits event if SoC changes.

        Parameters:
         - new_soc (float): New state of charge in %
        """

        current_soc = self._soc
        self._soc = new_soc

        if new_soc is not None and (
            new_soc < 0.0 or new_soc > self._SOC_SYSTEM_LIMIT_PERCENT
        ):
            self.__log(
                f"Attempted to set invalid SoC value: {new_soc}%. Must be between 0 and 100%.",
                level="WARNING",
            )
            return
        # self.__log(f"Setting SoC from {self._soc} to {new_soc}%.")

        if current_soc != new_soc:
            self._eb.emit_event(
                self.SOC_CHANGED_EVENT_NAME, new_soc=new_soc, old_soc=current_soc
            )
            self.__log(
                f"Emitting remaining_range_change event with range {self.remaining_range_km} km."
            )
            self._eb.emit_event(
                self.REMAINING_RANGE_EVENT_NAME,
                remaining_range=self.remaining_range_km,
            )
            self._check_soc_skip(new_soc, current_soc)

    @property
    def soc(self) -> float | None:
        """State of Charge in %"""
        return self._soc

    @property
    def soc_kwh(self) -> float | None:
        """State of Charge in kWh"""
        if self.soc is None or self._battery_capacity_kwh is None:
            self.__log(
                "Cannot calculate soc_kwh, soc or battery_capacity_kwh is None.",
                level="WARNING",
            )
            return None
        else:
            return round((self.soc / 100.0) * self._battery_capacity_kwh, 2)

    @property
    def remaining_range_km(self) -> int | None:
        """Remaining range in km"""
        if self.soc_kwh is None:
            self.__log(
                "Cannot calculate remaining range, soc_kwh is None.", level="WARNING"
            )
            return None
        else:
            remaining_range = int(
                round((self.soc_kwh * 1000 / self._consumption_wh_per_km), 0)
            )
            self.__log(f"Calculated remaining range: {remaining_range} km.")
            return remaining_range

    @property
    def max_range_km(self) -> int | None:
        return self._max_range_km

    def _check_soc_skip(self, new_soc: int, old_soc: int):
        """Check if the car has skipped the minimum SoC during a charge cycle.
        The Nissan Leaf (or to be specific, it's BMS) has a strange behaviour. When (dis-) charging
        the state of charge (soc) changes but skips 20%, so from 21% jumps to 19%, even when
        discharge power is low or slowly build up.
        This 20% typicaly is the lower limit for the schedules. So, the software at 19% changes form
        a scheduled discharge to a boost charge. Then the 20% is skipped again, jumping from
        19% to 21%. This cycle repeats every 15 minutes to an hour or so untill the schedules
        optimum is no longer discharging to the lower limit.
        This is undesired behaviour as it is in-efficient and possibly harmfull for the battery.

        This module monitors if this behaviour is happening and then warns the users, suggesting to
        set the lower-limit to e.g. 18 or 19%.
        """

        if not isinstance(new_soc, (int, float)) or not isinstance(
            old_soc, (int, float)
        ):
            return

        if (new_soc < self._min_soc_percent < old_soc) or (
            old_soc < self._min_soc_percent < new_soc
        ):
            self.__log(
                f"SoC change jump: old_soc '{old_soc}', new_soc '{new_soc}', "
                f"skipped self._min_soc_percent '{self._min_soc_percent}'. "
                f"Emitting `nissan_leaf_soc_skipped` event",
                level="WARNING",
            )
            self._eb.emit_event(
                "nissan_leaf_soc_skipped",
                min_soc=self._min_soc_percent,
                ev_name=self._name,
            )

    def __str__(self) -> str:
        return (
            f"ElectricVehicle(name={self._name}, --- "
            f"soc={self._soc}, "
            f"soc_kwh={self.soc_kwh}, "
            f"remaining_range_km={self.remaining_range_km}, --- "
            f"battery_capacity_kwh={self._battery_capacity_kwh}, "
            f"charging_efficiency={self._charging_efficiency}, "
            f"consumption_wh_per_km={self._consumption_wh_per_km}, "
            f"soc={self._soc}, "
            f"min_soc={self._min_soc_percent}, "
            f"max_soc={self._max_soc_percent})"
        )
