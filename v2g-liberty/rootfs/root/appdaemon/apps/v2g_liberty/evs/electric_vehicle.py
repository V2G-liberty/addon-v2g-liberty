"""Electric vehicle (EV) abstraction.

Holds the connected car's state of charge and exposes its energy content and
range, so consumers can read the car from one place instead of from the charger.

Fase 1 of the 359 migration (EV-abstraction before the charger refactor): the
charger monolith stays the SOLE emitter and owner of the SoC getters. This
object subscribes to the ``soc_change`` event and mirrors the value with a
SYNC, NON-emitting setter, and derives soc_kwh / remaining-range from the SAME
live car constants the charger reads (``c.CAR_MAX_CAPACITY_IN_KWH`` /
``c.CAR_CONSUMPTION_WH_PER_KM``). Reading the live constants — rather than a
stored copy — keeps the derived values bit-identical to the charger in EVERY
state, including the boot window before the car settings are loaded (both then
read the module default). Fase 2 (the charger port) makes this the
authoritative, emitting SoC owner and moves the car constants into it.
"""

from appdaemon.plugins.hass.hassapi import Hass

from .. import constants as c
from ..event_bus import EventBus
from ..log_wrapper import get_class_method_logger

# SoC sentinels. Dev represents "no valid SoC" with the string "unavailable"
# (never None), so the derived getters must return the same string and consumers'
# EMPTY_STATES guards keep firing identically.
_EMPTY_SOC_VALUES = [None, "unavailable", "unknown"]


class ElectricVehicle:
    """The single connected electric vehicle."""

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self._eb = event_bus
        self.__log = get_class_method_logger(module_name="electric_vehicle")
        self._soc = None

        # Subscribe here in __init__ (before any module's initialize() runs) and
        # with a SYNC handler: pyee runs sync listeners inline during emit, so
        # ev.soc is refreshed before the async soc_change consumers (e.g. main_app)
        # execute in the same dispatch. No registration-order fragility.
        self._eb.add_event_listener("soc_change", self.update_soc)
        self.__log("initialised.")

    def update_soc(self, new_soc, old_soc=None):
        """Mirror the charger's SoC. NON-emitting in Fase 1 (the charger is the
        sole emitter). ``old_soc`` is accepted (a soc_change kwarg) but unused."""
        self._soc = new_soc

    @property
    def soc(self):
        """State of charge in %, or the string "unavailable"."""
        return self._soc

    @property
    def soc_kwh(self):
        """State of charge in kWh, or "unavailable".

        Mirrors ``modbus_evse_client.get_car_soc_kwh`` (same live constant).
        """
        if self._soc in _EMPTY_SOC_VALUES:
            return "unavailable"
        return round(self._soc * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)

    @property
    def remaining_range_km(self):
        """Remaining range in km, or "unavailable".

        Mirrors ``modbus_evse_client.get_car_remaining_range`` (same live constant).
        """
        soc_kwh = self.soc_kwh
        if soc_kwh in _EMPTY_SOC_VALUES:
            return "unavailable"
        return int(round(soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM, 0))
