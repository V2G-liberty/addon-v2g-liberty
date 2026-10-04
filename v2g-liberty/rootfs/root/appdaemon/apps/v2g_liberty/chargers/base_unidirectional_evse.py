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

from abc import ABC

from pyee.asyncio import AsyncIOEventEmitter

from .. import constants as c


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

    def __init__(self):
        super().__init__()

    async def read_connected_car_id(self) -> tuple[str, str]:
        """Read the id of the connected car, on request from the car dialog.

        Returns ``(ev_id, reason)`` with reason one of ``ok``, ``no_car``,
        ``no_id``, ``read_failed`` or ``unsupported``. Never raises.
        """
        return "", "unsupported"

    # ---- What a concrete driver provides -------------------------------
    # The shared methods below read these. They are listed rather than
    # defaulted on purpose: a driver that leaves one out should fail loudly
    # on first use, not run on a base-class placeholder that quietly means
    # something else for its hardware.
    #
    #   CHARGING_STATE, DISCONNECTED_STATES   the device's own state numbers
    #   _MCE_CHARGER_STATE                    the charger-state register entity
    #   _am_i_active                          whether the app is driving the charger
    #   _is_shut_down                         set once shutdown() has run
    #   _mb_client                            the Modbus client
    #   _log                                  this driver's logger
    #   event_bus, requested_charge_power
    #   _get_and_process_registers, _get_car_soc, _is_power_deviating,
    #   _set_charge_power, _set_charger_action, get_car_remaining_range

    async def get_car_soc(self) -> int:
        """Helper to get SoC in percent"""
        return await self._get_car_soc(do_not_use_cache=False)

    async def get_car_soc_kwh(self) -> float:
        """Helper to get SoC in kWh"""
        soc = await self._get_car_soc(do_not_use_cache=False)
        if soc in [None, "unavailable", "unknown"]:
            return "unavailable"
        return round(soc * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)

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

    async def _handle_charge_power_change(self, new_power):
        if not isinstance(new_power, (int, float)):
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
