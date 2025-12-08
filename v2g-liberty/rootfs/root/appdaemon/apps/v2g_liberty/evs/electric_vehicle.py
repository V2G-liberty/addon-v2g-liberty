"""Generic electric vehicle (EV) class."""

from apps.v2g_liberty.evs.base_ev import BaseEV
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger

class ElectricVehicle(BaseEV):
    """Generic electric vehicle (EV) class."""

    def __init__(self, hass: Hass, event_bus: EventBus):
        super().__init__()
        self.hass = hass
        self._eb = event_bus
        self.__log = get_class_method_logger(hass.log)
        self._name: str | None = None
        self._battery_capacity_kwh: int | None = None
        self._charging_efficiency: int | None = None
        self._consumption_wh_per_km: int | None = None
        self._soc: float | None = None
        self._min_soc: int | None = None
        self._max_soc: int | None = None

    def initialise_ev(
        self,
        name: str,
        battery_capacity_kwh: int,
        charging_efficiency_percent: int,
        consumption_wh_per_km: int,
        min_soc_percent: int,
        max_soc_percent: int
    ):
        """
        TODO: Move to __init__????
        Initialize ElectricVehicle

        Parameters:
        - name (str):
          Name of the car e.g. model/type, chosen by user.
        - battery_capacity_kwh (int):
          Usable battery capacity in kWh. For now: entered by user, preferably read from car via charger.
        - charging_efficiency_percent (int):
          Charging efficiency in percent (10-100). For now: entered by user, preferably read from
          car via charger.
        """
        self._name = name
        self._set_battery_capacity_kwh(battery_capacity_kwh)
        self._set_charging_efficiency(charging_efficiency_percent)
        self._set_consumption_wh_per_km(consumption_wh_per_km)
        self._set_soc_limits(min_soc_percent, max_soc_percent)


    def _set_consumption_wh_per_km(self, consumption_wh_per_km: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if consumption_wh_per_km < 100:
            self.__log(
                f"Consumption {consumption_wh_per_km} Wh/km is invalid, must be 100 or greater.",
                level="WARNING",
            )
            return

        if consumption_wh_per_km > 400:
            self.__log(
                f"Consumption {consumption_wh_per_km} Wh/km is invalid, must be 400 or lower.",
                level="WARNING",
            )
            return
        self._consumption_wh_per_km = consumption_wh_per_km
        self.__log(f"Set car consumption to {consumption_wh_per_km} Wh/km.")

    def _set_battery_capacity_kwh(self, capacity_kwh: int):
        # Same limits that are enforced in UI based on entities in v2g_liberty_package.yaml
        if capacity_kwh < 10:
            self.__log(
                f"Battery capacity {capacity_kwh} kWh is invalid, must be 10 or greater.",
                level="WARNING",
            )
            return

        if capacity_kwh > 200:
            self.__log(
                f"Battery capacity {capacity_kwh} kWh is invalid, must be 200 or lower.",
                level="WARNING",
            )
            return
        self._battery_capacity_kwh = capacity_kwh
        self.__log(f"Set car battery capacity to {capacity_kwh} kWh.")

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
        self._min_soc = min_soc
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

        self._max_soc = max_soc
        self.__log(f"Set max_soc to {max_soc}%.")

    @property
    def min_soc(self) -> int:
        return self._min_soc

    @property
    def max_soc(self) -> int:
        return self._max_soc

    @property
    def name(self) -> str:
        return self._name

    @property
    def charging_efficiency(self) -> float:
        return self.charging_efficiency_percent / 100.0

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

        if new_soc < 0.0 or new_soc > 100.0:
            self.__log(
                f"Attempted to set invalid SoC value: {new_soc}%. Must be between 0 and 100%.",
                level="WARNING"
            )
            return
        # self.__log(f"Setting SoC from {self._soc} to {new_soc}%.")

        if current_soc != new_soc:
            self._eb.emit_event(self.SOC_CHANGED_EVENT_NAME, new_soc=new_soc, old_soc=current_soc)
            self.__log(f"Emitting remaining_range_change event with range {self.remaining_range_km} km.")
            self._eb.emit_event(
                self.REMAINING_RANGE_EVENT_NAME,
                remaining_range=self.remaining_range_km,
            )

    @property
    def soc(self) -> float | None:
        return self._soc

    @property
    def soc_kwh(self) -> float | None:
        """State of Charge in kWh"""
        if self.soc is None or self._battery_capacity_kwh is None:
            self.__log("Cannot calculate soc_kwh, soc or battery_capacity_kwh is None.", level="WARNING")
            return None
        else:
            return round((self.soc / 100.0) * self._battery_capacity_kwh, 2)

    @property
    def remaining_range_km(self) -> int | None:
        """Remaining range in km"""
        if self.soc_kwh is None:
            self.__log("Cannot calculate remaining range, soc_kwh is None.", level="WARNING")
            return None
        else:
            remaining_range = int(round((self.soc_kwh * 1000 / self._consumption_wh_per_km), 0))
            self.__log(f"Calculated remaining range: {remaining_range} km.")
            return remaining_range

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
            f"min_soc={self._min_soc}, "
            f"max_soc={self._max_soc})"
        )