"""Abstract base for a (uni-directional) EVSE / charger driver.

Fase 2a of the 359 migration introduced the ``chargers/`` package structure; the
base classes provided the shared type hierarchy and little else, pending a second
charger to share a real contract with. The EVtec arrived in Fase 3, and the two
drivers turned out to hold a number of byte-identical methods. Those live here
now (Fase 6): a fix to any of them used to have to be made twice, and was once
made only once.

Only methods that were identical in both drivers moved, and they moved
unchanged. What stayed behind is as deliberate as what came: the charger
lifecycle (``shutdown``, ``_charger_is_healthy``) reaches into timer handles, the
recovery probe and the AppDaemon handle, which this class does not create, and
methods that merely resemble each other differ exactly where the two chargers
differ.
"""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from pyee.asyncio import AsyncIOEventEmitter

from .. import constants as c
from ..event_bus import EventBus
from ..log_wrapper import get_class_method_logger
from .modbus_types import ModbusConfigEntity
from .settle_time import GIVE_UP_AFTER_S, SettleTimeTracker
from .v2g_modbus_client import V2GmodbusClient

SENSOR_CHARGER_SETTLE_TIME = "sensor.charger_settle_time"


class UnidirectionalEVSE(AsyncIOEventEmitter, ABC):
    """Base class for a uni-directional EVSE / charger driver.

    Concrete drivers implement V2G Liberty's charger API (test_charger_connection,
    initialise_charger, complete_init, set_active/set_inactive,
    start_charge_with_power, stop_charging, is_car_connected/is_charging, the SoC
    getters, ...) and route their events through the shared EventBus (not the
    pyee emitter).
    """

    # Whether this charger can tell cars apart (ISO 15118 EvccId). Not
    # abstract: a driver that cannot simply inherits "no", and callers can
    # read it without hasattr.
    IDENTIFIES_CAR: bool = False

    @property
    def min_charge_power_w(self) -> int:
        """The smallest power above 0 W this charger charges at, in W.

        Between 0 W and this value lies the charger's dead zone: a setpoint
        there is not followed as given (the Quasar accepts nothing under 6 A,
        the BiDiPro raises it to its charge floor). The fuse guard rounds a
        limit that lands there to 0 W. 0 when the driver knows no minimum.
        """
        return 0

    def __init__(self):
        super().__init__()
        # Each driver replaces this with a logger carrying its own module name.
        # A working one here rather than None: the shared methods below log, and
        # a driver that forgot should produce a line under the wrong name, not
        # fall over on the log statement itself.
        self._log: Callable[..., None] = get_class_method_logger(module_name="evse")
        # How long the charger takes to follow a setpoint, per kind of
        # transition; published as sensor.charger_settle_time. The clock is
        # an attribute so a test can hold it still.
        self._settle = SettleTimeTracker()
        self._clock = time.monotonic

    async def read_connected_car_id(self) -> tuple[str, str]:
        """Read the id of the connected car, on request from the car dialog.

        Returns ``(ev_id, reason)`` with reason one of ``ok``, ``no_car``,
        ``no_id``, ``read_failed`` or ``unsupported``. Never raises.
        """
        return "", "unsupported"

    # ---- What a concrete driver provides -------------------------------
    # Declared, not defaulted: a driver that leaves one of these out should
    # fail on first use rather than run on a base-class placeholder that
    # quietly means something else for its hardware. These are annotations
    # only, so nothing is created here and nothing is shadowed.

    # The device's own numbers and register entity; every driver overrides
    # these. They carry a None rather than no value at all so that reading one
    # is an ordinary attribute access: a bare annotation creates nothing, and
    # the editor then flags every use here as a missing member -- which teaches
    # people to stop reading those warnings.
    CHARGING_STATE: int = None
    DISCHARGING_STATE: int = None
    DISCONNECTED_STATES: list[int] = None
    CHARGER_STATES: dict[int, str] = None  # state number -> text, for logging
    _MCE_CHARGER_STATE: ModbusConfigEntity = None
    _mb_client: V2GmodbusClient = None

    # Both drivers declared these identically, so they live here now.
    hass = None  # the AppDaemon handle; each driver sets its own
    event_bus: EventBus = None
    _am_i_active: bool = None  # whether the app is driving the charger
    requested_charge_power: int = 0

    # Owned here: only _handle_charge_power_change reads or writes it, and
    # that moved. Left in the drivers it would be state nobody there touches.
    _is_power_deviating: bool = False

    @abstractmethod
    async def _get_and_process_registers(
        self, entities: list, force_emit: bool = False
    ):
        """Read the given register entities and apply their new values."""

    @abstractmethod
    async def _get_car_soc(self, do_not_use_cache: bool = False):
        """The car's state of charge, as a percentage."""

    @abstractmethod
    async def _set_charge_power(
        self, charge_power: int, skip_min_soc_check: bool = False, source: str = None
    ):
        """Ask the charger for this power in Watt, positive to charge."""

    @abstractmethod
    async def _set_charger_action(self, action: str, reason: str = ""):
        """Start or stop charging, in whatever way this device needs."""

    async def get_car_soc(self) -> int:
        """Helper to get SoC in percent"""
        return await self._get_car_soc(do_not_use_cache=False)

    async def get_car_soc_kwh(self) -> float:
        """Helper to get SoC in kWh"""
        soc = await self._get_car_soc(do_not_use_cache=False)
        if soc in [None, "unavailable", "unknown"]:
            return "unavailable"
        return round(soc * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)

    async def get_car_remaining_range(self) -> int:
        """Helper to get remaining range in km"""
        soc_kwh = await self.get_car_soc_kwh()
        if soc_kwh in [None, "unavailable", "unknown"]:
            return "unavailable"
        return int(round((soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM), 0))

    async def _is_charging_or_discharging(self) -> bool:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        state = await self._get_charger_state()
        if state is None:
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self._log(
            f"state: {state} ({self.CHARGER_STATES.get(state)}), charging: {is_charging}."
        )
        return is_charging

    async def is_charging(self) -> bool:
        """Indicates if currently the connected car is charging (not discharging)"""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        return await self._get_charger_state() == self.CHARGING_STATE

    async def is_car_connected(self) -> bool:
        """Indicates if currently a car is connected to the charger."""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        is_connected = self._mb_client.is_initialised
        is_connected = (
            is_connected
            and await self._get_charger_state() not in self.DISCONNECTED_STATES
        )
        self._log(f"is_connected: {is_connected}", level="DEBUG")
        return is_connected

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self._log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )

        await self._set_charger_action("stop", reason="stop_charging")
        await self._set_charge_power(charge_power=0, source="stop_charging")

    async def _get_charger_state(self) -> int:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        charger_state = self._MCE_CHARGER_STATE.current_value
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_CHARGER_STATE])
            charger_state = self._MCE_CHARGER_STATE.current_value

        return charger_state

    async def _update_charger_communication_state(self, can_communicate: bool):
        self.event_bus.emit_event(
            "charger_communication_state_change", can_communicate=can_communicate
        )

    async def _handle_soc_change(self, new_soc: int, old_soc: int):
        self.event_bus.emit_event("soc_change", new_soc=new_soc, old_soc=old_soc)
        self.event_bus.emit_event(
            "remaining_range_change",
            remaining_range=await self.get_car_remaining_range(),
        )

    def _note_requested_power(self, charge_power: int, at: float | None = None):
        """Record the setpoint that just went to the charger and start timing
        how long the charger takes to follow it. ``at`` is the clock reading
        from before the Modbus write, when the caller took one; the write
        itself may take a while."""
        self.requested_charge_power = charge_power
        if at is None:
            at = self._clock()
        kind = self._settle.note_setpoint(int(charge_power), at)
        if kind is not None:
            self._log(f"Timing {kind} to {charge_power} W.", level="DEBUG")

    async def _handle_charge_power_change(self, new_power):
        is_number = isinstance(new_power, (int, float))
        if not is_number:
            self._log(f"Charge power is not a number: '{new_power}', treating as 0W.")
            new_power = 0
        self.event_bus.emit_event("charge_power_change", new_power=new_power)
        is_deviating = abs(new_power - self.requested_charge_power) > 500
        if is_deviating and not self._is_power_deviating:
            self._log(
                f"Actual charge power ({new_power}W) deviates > 500W from "
                f"requested ({self.requested_charge_power}W)."
            )
        elif not is_deviating and self._is_power_deviating:
            self._log(
                f"Charge power deviation resolved, actual: {new_power}W, "
                f"requested: {self.requested_charge_power}W."
            )
        self._is_power_deviating = is_deviating
        if is_number:
            await self._time_settling(int(new_power))

    async def _time_settling(self, actual_w: int):
        """One more power report: does it end the transition being timed?"""
        await self._report_settling(self._settle.note_actual(actual_w, self._clock()))

    async def _time_settling_on_poll(self):
        """A poll without a change in power: has the charger settled short of
        the setpoint, or has the time to give up passed? Called by the
        drivers from their base poll, as a power that stands still is not
        reported."""
        await self._report_settling(self._settle.note_poll(self._clock()))

    async def _report_settling(self, measured):
        if measured is None:
            return
        if measured.settled:
            self._log(
                f"Charger settled at {measured.actual_w} W, {measured.seconds:.0f} s "
                f"after the setpoint {measured.to_w} W ({measured.kind}, from "
                f"{measured.from_w} W)."
            )
            await self._publish_settle_time()
        else:
            self._log(
                f"Charger did not settle on {measured.to_w} W within "
                f"{GIVE_UP_AFTER_S} s, at {measured.actual_w} W ({measured.kind}, "
                f"from {measured.from_w} W); not counted.",
                level="DEBUG",
            )

    async def _publish_settle_time(self):
        """The slowest median as the state, the rest in the attributes."""
        slowest = self._settle.slowest()
        last = self._settle.last
        if slowest is None or last is None:
            return
        attributes = {
            "medians_s": {k: round(v) for k, v in self._settle.medians().items()},
            "counts": self._settle.counts(),
            "slowest": slowest[0],
            "last_kind": last.kind,
            "last_s": round(last.seconds),
            "last_from_w": last.from_w,
            "last_to_w": last.to_w,
        }
        try:
            await self.hass.set_state(
                SENSOR_CHARGER_SETTLE_TIME,
                state=round(slowest[1]),
                attributes=attributes,
            )
        except Exception as e:  # noqa: BLE001  # HA may be restarting
            self._log(
                f"Could not write {SENSOR_CHARGER_SETTLE_TIME}: {e}", level="WARNING"
            )
