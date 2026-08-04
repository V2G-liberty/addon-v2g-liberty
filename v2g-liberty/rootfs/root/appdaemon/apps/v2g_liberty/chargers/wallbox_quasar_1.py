"""Wallbox Quasar 1 charger driver.

Ported (behaviour-preserving) from the ``modbus_evse_client`` monolith onto the
``chargers/`` package structure introduced in Fase 2a of the 359 migration:

- ``WallboxQuasar1Client(BidirectionalEVSE)`` (was ``ModbusEVSEclient``).
- The Wallbox register table is expressed as ``ModbusConfigEntity`` (MCE)
  instances holding an ``MBR`` (Modbus register) plus min/max/relaxed limits and
  a change handler; the write registers stay as plain integer addresses.
- Raw modbus reads/writes are routed through :class:`V2GmodbusClient`, but the
  exception/grace-timer state machine and all polling orchestration stay on the
  charger, exactly as in the monolith.

All observable behaviour (events, cached values, direct v2g_main_app / notifier
calls) is preserved; the public method names and return shapes are unchanged so
external callers need no edits.
"""

import asyncio

from pymodbus.exceptions import ModbusException

from appdaemon.plugins.hass.hassapi import Hass

from .. import constants as c
from ..log_wrapper import get_class_method_logger
from ..notifier_util import Notifier
from ..v2g_globals import parse_to_int
from ..event_bus import EventBus
from ..timer_utils import cancel_timer_silent, set_oneshot_timer, set_recurring_timer
from .base_bidirectional_evse import BidirectionalEVSE
from .modbus_types import MBR, ModbusConfigEntity
from .v2g_modbus_client import V2GmodbusClient


class WallboxQuasar1Client(BidirectionalEVSE):
    """Class to communicate with the Wallbox Quasar 1 EVSE via modbus.
    It does this mainly by polling the EVSE for states and values in an
    asynchronous way, as the charger might not always react instantly.

    Values of the EVSE (like charger status or car SoC) are emitted onto the
    event_bus for other modules to use / subscribe to.
    """

    event_bus: EventBus = None

    #######################################################################################
    #   This file contains the Modbus address information for the Wallbox Quasar 1 EVSE.  #
    #   This is provided by the Wallbox Chargers S.L. as is.                              #
    #   For reference see https://wallbox.com/en_uk/quasar-dc-charger                     #
    #   Wallbox is not provider of the software and does not provide any type of service  #
    #   for the software.                                                                 #
    #   Wallbox will not be responsible for any damage or malfunction generated on        #
    #   the Charger by the Software.                                                      #
    #######################################################################################

    # Max value of an “unsigned short integer” 2^16, used for negative values in modbus.
    MAX_USI = 65536
    HALF_MAX_USI = MAX_USI / 2

    ################################################################################
    #   EVSE Config Entities (MCE)                                                 #
    #   These hold the constants for an entity (modbus register, min/max value)    #
    #   and cache the current value read from the charger.                         #
    #   The polled registers are declared int16 so that the universal two's        #
    #   complement decode (_get_2comp) is reproduced: an error code >= 32768        #
    #   decodes negative, fails the >= 0 minimum and is ignored, exactly as in the #
    #   monolith.                                                                   #
    ################################################################################

    _MCE_ACTUAL_POWER = ModbusConfigEntity(
        modbus_register=MBR(address=526, data_type="int16"),
        minimum_value=-7400,
        maximum_value=7400,
        current_value=None,
        change_handler="_handle_charge_power_change",
    )
    _MCE_CHARGER_STATE = ModbusConfigEntity(
        modbus_register=MBR(address=537, data_type="int16"),
        minimum_value=0,
        maximum_value=11,
        current_value=None,
        change_handler="_handle_charger_state_change",
    )
    _MCE_CAR_SOC = ModbusConfigEntity(
        modbus_register=MBR(address=538, data_type="int16"),
        minimum_value=2,
        maximum_value=97,
        relaxed_min_value=1,
        relaxed_max_value=100,
        current_value=None,
        change_handler="_handle_soc_change",
    )
    # About the relaxed minimum value of 1%:
    # The minimum is used for correct processing of the SoC as the charger reports a SoC of 0 when:
    # + The charge power is 0
    #   The reported 0 SoC does not represent an actual true value and should be ignored.
    #   and the current value is to be preserved.
    # + When no car is connected
    #   This represents 'unavailable' and should be reflected as such in the HA sensor history.
    # About the minimum value of 2%:
    #  The Quasar sometimes returns 1% while the true value is (much) higher.
    #  As 1% can be a valid value we want to be sure it is not the hick-up version, we
    #  only accept this value if we have read this for a longer time.
    # About the maximum of 97%
    #  The charger + car will never charge above 97% so reading above this are likely a glitch.
    #  The car can however return with a SoC above this value, so it the value remains above this
    #  limit until the timeout it is accepted.

    _MCE_ERROR_1 = ModbusConfigEntity(
        modbus_register=MBR(address=539, data_type="int16"),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_state_change",
    )
    _MCE_ERROR_2 = ModbusConfigEntity(
        modbus_register=MBR(address=540, data_type="int16"),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_state_change",
    )
    _MCE_ERROR_3 = ModbusConfigEntity(
        modbus_register=MBR(address=541, data_type="int16"),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_state_change",
    )
    _MCE_ERROR_4 = ModbusConfigEntity(
        modbus_register=MBR(address=542, data_type="int16"),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_state_change",
    )

    _MCE_CHARGER_LOCKED = ModbusConfigEntity(
        modbus_register=MBR(address=256, data_type="int16"),
        minimum_value=0,
        maximum_value=1,
        current_value=None,
        change_handler=None,
    )

    # Groups of entities for efficient reading of the modbus registers.
    CHARGER_POLLING_ENTITIES: list
    CHARGER_ERROR_ENTITIES: list

    ######################################################################
    #                 Modbus addresses for setting values                #
    ######################################################################

    # Charger can be controlled by the app = user or by code = remote (Read/Write)
    # For all other settings mentioned here to work, this setting must be remote.
    SET_CHARGER_CONTROL_REGISTER: int = 81
    CONTROL_TYPES = {"user": 0, "remote": 1}

    # Start charging/discharging on EV-Gun connected (Read/Write)
    # Resets to default (=enabled) when control set to user
    # Must be set to "disabled" when controlled from this code.
    CHARGER_AUTOSTART_ON_CONNECT_REGISTER: int = 82
    AUTOSTART_ON_CONNECT_SETTING = {"enable": 1, "disable": 0}

    # Control if charger can be set through current setting or power setting (Read/Write)
    # This software uses power only.
    SET_SETPOINT_TYPE_REGISTER: int = 83
    SETPOINT_TYPES = {"current": 0, "power": 1}

    # Charger setting to go to idle state if not receive modbus message within this timeout.
    # Fail-safe in case this software crashes: if timeout passes charger will stop (dis-)charging.
    CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER: int = 88

    # Timeout in seconds. Half an hour is long, polling communicates every 5 or 15 seconds.
    CMIT: int = 1800

    # Charger charging can be started/stopped remote (Read/Write)
    # Not implemented: restart and update software
    SET_ACTION_REGISTER: int = 257
    ACTIONS = {"start_charging": 1, "stop_charging": 2}

    # For setting the desired charge power, reading the actual charging power is done
    # through _MCE_ACTUAL_POWER
    CHARGER_SET_CHARGE_POWER_REGISTER: int = 260
    # Holds the last known requested charge power that was set in the
    # charger register CHARGER_SET_CHARGE_POWER_REGISTER. Used for deviation comparison.
    requested_charge_power: int = 0
    _is_power_deviating: bool = False

    # AC Max Charging Power (by phase) (hardware) setting in charger (Read/Write)
    # (int16) unit W, min_value 1380, max_value 7400
    # Used when set_setpoint_type = power
    MAX_AVAILABLE_POWER_REGISTER: int = 514
    # The Quasar does not accept a setting lower than 6A => 6A*230V = 1380W
    CHARGE_POWER_LOWER_LIMIT: int = 1380
    # The Quasar does not accept a setting lower than 32A => 32A*230V = 7400W
    CHARGE_POWER_UPPER_LIMIT: int = 7400

    # 0: Goes to this status when the charge plug is disconnected from the car
    # 1: Connected and charging; goes to this status when action = start
    # 2: Connected and waiting for car demand; sometimes shortly goes to this status when
    #    action = start
    # 3: Connected and waiting for next schedule; this occurs when a charging session is scheduled
    #    via the Wallbox app.
    #    As we control the charger we override this setting
    # 4: Connected and paused by user; goes to this status when action = stop or when gun is
    #    connected and auto start = disabled
    # 7: In error; the charger sometimes returns error first minutes after restart
    # 10: Connected and in queue by Power Boost
    # 11: Connected and discharging. This status is reached when the power or current setting is set
    #     to a negative value and the action = start
    CHARGER_STATES = {
        0: "No car connected",
        1: "Charging",
        2: "Connected: waiting for car demand",
        3: "Connected: controlled by Wallbox App",
        4: "Connected: not charging (paused)",
        5: "Connected: end of schedule",
        6: "No car connected and charger locked",
        7: "Error",
        8: "Connected: In queue by Power Sharing",
        9: "Error: Un-configured Power Sharing System",
        10: "Connected: In queue by Power Boost (Home uses all available power)",
        11: "Discharging",
    }
    # One could argue that Error states should also be considered "not connected",
    # but these are handled in other ways.
    DISCONNECTED_STATES = [0, 6]
    CHARGING_STATE: int = 1
    DISCHARGING_STATE: int = 11
    AVAILABILITY_STATES = [1, 2, 4, 5, 11]
    ERROR_STATES = [7, 9]

    # Modbus variables
    WAIT_AFTER_MODBUS_WRITE_IN_MS: int = 2500
    WAIT_AFTER_MODBUS_READ_IN_MS: int = 50

    # For handling non responsive charger.
    # TODO: Implement with event_bus instead?
    v2g_main_app: object

    # Handle for polling_timer, needed for cancelling polling.
    poll_timer_handle: object
    BASE_POLLING_INTERVAL_SECONDS: int = 5
    MINIMAL_POLLING_INTERVAL_SECONDS: int = 15

    try_get_new_soc_in_process: bool = False

    # How old may data retrieved from HA entities be before it is renewed from the EVSE
    STATE_MAX_AGE_IN_SECONDS: int = 15

    # For tracking modbus failure in charger
    # At first successful connection this counter is set to 0
    # Until then do not trigger this counter, as most likely the user is still busy configuring
    modbus_exception_counter: int = None
    timer_id_check_modus_exception_state: str = None
    timer_id_check_error_state: str = None
    MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 60

    # Recovery probing after an unrecoverable error: periodically read a single
    # register until the charger is reachable again, then trigger auto-recovery.
    RECOVERY_PROBE_INTERVAL_SECONDS: int = 120
    timer_id_recovery_probe: str = None

    # For (un)blocking of calls and keeping the client in-active when it should
    # Set only(!) by set_inactive and set_active.
    _am_i_active: bool = None

    hass: Hass = None
    notifier: Notifier = None

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        """Initialise WallboxQuasar1Client.
        Setting up constants and variables.
        Configuration and connecting the modbus client is done separately in initialise_charger.
        """
        super().__init__()
        self.hass = hass
        self._log = get_class_method_logger(module_name="wallbox_quasar_1")

        self.event_bus = event_bus
        self.notifier = notifier

        # Raw modbus transport; the exception/grace-timer state machine stays on this charger.
        self._mb_client = V2GmodbusClient(hass)

        self.CHARGER_ERROR_ENTITIES = [
            self._MCE_ERROR_1,
            self._MCE_ERROR_2,
            self._MCE_ERROR_3,
            self._MCE_ERROR_4,
        ]
        self.CHARGER_POLLING_ENTITIES = [
            self._MCE_ACTUAL_POWER,
            self._MCE_CHARGER_STATE,
            self._MCE_CAR_SOC,
            self._MCE_ERROR_1,
            self._MCE_ERROR_2,
            self._MCE_ERROR_3,
            self._MCE_ERROR_4,
        ]
        self.poll_timer_handle = None

    ######################################################################
    #                     PUBLIC FUNCTIONAL METHODS                      #
    ######################################################################

    async def test_charger_connection(self, host, port):
        """Test client settings and return max_available_power in Watt.
        To be called from UI (via globals). Works even if this module has not been
        initialised yet."""
        self._log(f"Testing Modbus EVSE client at {host}:{port}")

        success, max_available_power = await self._mb_client.adhoc_read_register(
            modbus_address=self.MAX_AVAILABLE_POWER_REGISTER,
            host=host,
            port=port,
        )

        if not success:
            return False, None
        return True, max_available_power

    async def initialise_charger(self, v2g_args=None):
        """Initialise charger
        Configuring the client, making the connection and reading
        min/max charge power.
        Activating the polling is done in set_active.
        """

        # Remove old client if needed.
        self._mb_client.terminate()

        connected = await self._mb_client.initialise(
            host=c.CHARGER_HOST_URL, port=c.CHARGER_PORT
        )

        if not connected:
            return False, None

        self.modbus_exception_counter = 0

        max_available_power_by_charger = await self._force_get_register(
            address=self.MAX_AVAILABLE_POWER_REGISTER,
            min_value_at_forced_get=self.CHARGE_POWER_LOWER_LIMIT,
            max_value_at_forced_get=self.CHARGE_POWER_UPPER_LIMIT,
        )
        self._log(f"Returning max. power: {max_available_power_by_charger}.")
        return True, max_available_power_by_charger

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self._log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )

        await self._set_charger_action("stop", reason="stop_charging")
        await self._set_charge_power(charge_power=0, source="stop_charging")

    async def start_charge_with_power(self, charge_power: int, source: str = "unknown"):
        """Function to start a charge session with a given power in Watt.
           To be called from v2g-liberty module.

        Args:
            charge_power (int): charge_power with a value in Watt, can be negative.
            source (str, optional): for debugging. Defaults to "unknown".
        """
        # Check for automatic mode should be done by V2G Liberty app
        if not self._am_i_active:
            self._log(
                f"Not setting charge_rate: _am_i_active == False. Requested by '{source}'."
            )
            return

        if charge_power is None:
            self._log("charge_power = None, abort", level="WARNING")
            return

        if not await self.is_car_connected():
            self._log(
                f"Not setting charge_rate: No car connected. Requested by '{source}'."
            )
            return

        await self._set_charger_control("take")
        if charge_power == 0:
            await self._set_charger_action(
                action="stop",
                reason=f"called from {source} with power = 0",
            )
        else:
            await self._set_charger_action(
                action="start",
                reason=f"called from {source} with {charge_power=}",
            )

        await self._set_charge_power(
            charge_power=charge_power,
            source=f"{source} => start_charge_with_power",
        )

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop
        Do not cancel polling, the information is still relevant.
        """
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("made inactive")
        await self.stop_charging()
        await self._set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("activated")
        # A manual switch back to Automatic recovers too; stop any recovery probe.
        await self._cancel_recovery_probe()
        self._am_i_active = True
        await self._set_charger_control("take")
        await self._get_car_soc(do_not_use_cache=True)
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._set_poll_strategy()

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
        else:
            return int(round((soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM), 0))

    # TODO: AVAILABILITY_STATES is knowledge that does not belong here but in data monitor.
    # Move this method out of this module.
    def is_available_for_automated_charging(self) -> bool:
        """Whether the car and EVSE are available for automated charging.
        To simplify things for the caller, this is implemented as a synchronous function.
        This means the state is retrieved from HA instead of the charger and as a result
        can be as old as the maximum polling interval.
        """
        if not self._am_i_active:
            self._log("called while _am_i_active == False. Returning False.")
            return False

        # The method self._get_charger_state() cannot be used as it is async and this
        # method should not be as it is called from sync code (data_monitor.py).
        return self._MCE_CHARGER_STATE.current_value in self.AVAILABILITY_STATES

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

    async def is_charging(self) -> bool:
        """Indicates if currently the connected car is charging (not discharging)"""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        return await self._get_charger_state() == self.CHARGING_STATE

    async def is_discharging(self) -> bool:
        """Indicates if currently the connected car is discharging (not charging)"""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        return await self._get_charger_state() == self.DISCHARGING_STATE

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################

    async def complete_init(self):
        """
        This public function is to be called from v2g-liberty once after its own init is complete.
        This timing is essential, the following code might need v2g-liberty for notifications etc.
        """
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("kicking off")

        self.event_bus.emit_event(
            "update_charger_info", charger_info=await self._get_charger_info()
        )

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)

        # SoC is essential for many decisions, so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first
        # opportunity to do a poll.
        await self._get_car_soc(do_not_use_cache=True)

    async def _get_charger_info(self):
        firware_version_modbus_address = 1
        # serial_number_high_modbus_address = 2
        serial_number_low_modbus_address = 3

        length = serial_number_low_modbus_address - firware_version_modbus_address + 1
        try:
            results = await self._modbus_read(
                address=firware_version_modbus_address,
                length=length,
                source="_get_charger_info",
            )
            charger_info = (
                f"Wallbox Quasar 1 - Firmware version: {results[0]}, "
                f"Serial number high: {results[1]}, Serial Number Low: {results[2]}."
            )
            return charger_info
        except:  # noqa: E722
            return "unknown"

    async def _set_charger_control(self, take_or_give_control: str):
        """Set charger control: take control from the user or give control back to the user
        (the EVSE app).

        This is a private function. The V2G Liberty module should use the function set_active() and
        set_inactive().

        With taking control:
        + the user cannot use the app, it becomes exclusive for the modbus connection.
        + the charger automatic charge upon connection is disabled.

        With giving user control:
        + the user can use the app for controlling the charger and
        + the charger will start charging automatically upon connection.

        Args:
            take_or_give_control (str): "take" remote control or "give" user control

        Raises:
            ValueError: if other than "take" or "give" is sent

        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if take_or_give_control == "take":
            await self._modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["remote"],
                source="_set_charger_control, take_control",
            )
            await self._modbus_write(
                address=self.CHARGER_AUTOSTART_ON_CONNECT_REGISTER,
                value=self.AUTOSTART_ON_CONNECT_SETTING["disable"],
                source="_set_charger_control, set_auto_connect",
            )
            await self._modbus_write(
                address=self.SET_SETPOINT_TYPE_REGISTER,
                value=self.SETPOINT_TYPES["power"],
                source="_set_charger_control: power",
            )
            await self._modbus_write(
                address=self.CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER,
                value=self.CMIT,
                source="_set_charger_control: Modbus idle timeout",
            )

        elif take_or_give_control == "give":
            # Setting control to user automatically sets:
            # + autostart to enable
            # + set_point to Ampere
            # + idle timeout to 0 (disabled)
            await self._set_charge_power(
                charge_power=0, source="_set_charger_control, give_control"
            )
            await self._modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["user"],
                source="_set_charger_control, give_control",
            )
            # For the rare case that forced get soc is in action when the car gets disconnected.
            self.try_get_new_soc_in_process = False

        else:
            raise ValueError(
                f"Unknown option for take_or_give_control: {take_or_give_control}"
            )
        return

    ######################################################################
    #                    PRIVATE CALLBACK FUNCTIONS                      #
    ######################################################################

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

    async def _handle_charger_state_change(
        self, new_charger_state: int, old_charger_state: int
    ):
        self._log(f"called {new_charger_state=}, {old_charger_state=}.")
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if (
            new_charger_state in self.ERROR_STATES
            or old_charger_state in self.ERROR_STATES
        ):
            # Check if user needs to be notified or if notification process needs to be aborted
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_charger_state, "is_final_check": False}
            )

        if self.try_get_new_soc_in_process:
            return

        charger_state_text = self.CHARGER_STATES.get(new_charger_state, None)
        self.event_bus.emit_event(
            "charger_state_change",
            new_charger_state=new_charger_state,
            old_charger_state=old_charger_state,
            new_charger_state_str=charger_state_text,
        )

        if new_charger_state in self.DISCONNECTED_STATES:
            # Goes to this status when the plug is removed from the car-socket,
            # not when disconnect is requested from the UI.

            # When disconnected the SoC of the car goes from cur soc to unavailable.
            await self._update_evse_entity(
                evse_entity=self._MCE_CAR_SOC, new_value="unavailable"
            )

            # To prevent the charger from auto-start charging after the car gets connected again,
            # explicitly send a stop-charging command:
            await self._set_charger_action("stop", reason="car disconnected")
            await self._set_poll_strategy()
            self.event_bus.emit_event("is_car_connected", is_car_connected=False)
        elif old_charger_state in self.DISCONNECTED_STATES or old_charger_state is None:
            # new_charger_state must be a connected state, so if the old state was disconnected
            # there was a change in connected state.
            self._log("From disconnected to connected: try to refresh the SoC")
            await self._get_car_soc(do_not_use_cache=True)
            await self._set_poll_strategy()
            self.event_bus.emit_event("is_car_connected", is_car_connected=True)
        else:
            # From one connected state to an other connected state: not a change that this method
            # needs to react upon.
            pass

        return

    ######################################################################
    #                    PRIVATE FUNCTIONAL METHODS                      #
    ######################################################################

    async def _set_charger_action(self, action: str, reason: str = ""):
        """Set action to start/stop charging the charger.
           To be called from both this module and v2g-liberty.
           Restart is not implemented.

        Args:
            action (str): Action to perform on the charger. One of 'start', 'stop'
            reason (str), optional: for debugging

        Raises:
            ValueError: If another action than 'start' or 'stop' is sent.

        Returns:
            nothing
        """
        self._log(f"Called with action '{action}', reason: '{reason}'.", level="DEBUG")

        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        action_value = ""

        if action == "start":
            if not await self.is_car_connected():
                self._log("Not performing charger action 'start': No car connected.")
                return
            if await self._is_charging_or_discharging():
                self._log("Not performing charger action 'start': Already charging.")
                return
            action_value = self.ACTIONS["start_charging"]

        elif action == "stop":
            # Stop needs to be very reliable, so we always perform this action, even if currently
            # not charging.
            action_value = self.ACTIONS["stop_charging"]

        else:
            # Restart not implemented
            self._log(
                f"Unknown option for action: '{action}'.{reason}", level="WARNING"
            )

        txt = f"set_charger_action: {action}"
        await self._modbus_write(
            address=self.SET_ACTION_REGISTER, value=action_value, source=txt
        )
        self._log(f"{txt}{reason}", level="DEBUG")
        return

    async def _is_charging_or_discharging(self) -> bool:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        state = await self._get_charger_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self._log(
            f"state: {state} ({self.CHARGER_STATES[state]}), charging: {is_charging}."
        )
        return is_charging

    async def _get_car_soc(self, do_not_use_cache: bool = False) -> int:
        """Checks if a SoC value is new enough to return directly or if it should be updated first.

        :param do_not_use_cache (bool):
        This forces the method to get the soc from the car and bypass any cached value.

        :return (int):
        SoC value from 2 to 97 (%) or "unavailable".
        If the car is disconnected the charger returns 0 representing "unavailable".
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if not await self.is_car_connected():
            self._log("no car connected, returning SoC = 'unavailable'")
            return "unavailable"

        ecs = self._MCE_CAR_SOC
        soc_value = ecs.current_value
        should_be_renewed = False
        if soc_value is None:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            self._log("current_value is None so should_be_renewed = True")
            should_be_renewed = True

        if do_not_use_cache:
            # Needed usually only when car has been disconnected. The polling then does not read SoC
            # and this probably changed and polling might not have picked this up yet.
            self._log("do_not_use_cache == True so should_be_renewed = True")
            should_be_renewed = True

        if should_be_renewed:
            self._log("old or invalid SoC in HA Entity: renew")
            soc_address = ecs.modbus_register.address
            min_value_at_forced_get = ecs.minimum_value
            max_value_at_forced_get = ecs.maximum_value
            relaxed_min_value = ecs.relaxed_min_value
            relaxed_max_value = ecs.relaxed_max_value

            if await self._is_charging_or_discharging():
                self._log("called")
                soc_in_charger = await self._force_get_register(
                    address=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                # This should can occure if charger is in error
                if soc_in_charger in [None, 0]:
                    soc_in_charger = "unavailable"
                await self._update_evse_entity(
                    evse_entity=ecs,
                    new_value=soc_in_charger,
                    force_emit=do_not_use_cache,
                )
            else:
                self._log("start a charge and read the soc until value is valid")
                # When not charging reading a SoC will return a false 0-value. To resolve this start
                # charging (with minimum power) then read a SoC and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading,
                # polling is paused.
                # try_get_new_soc_in_process is used to prevent polling to start again from
                # elsewhere and to stop other processes.
                self.try_get_new_soc_in_process = True
                await self._cancel_polling(reason="try get new soc")
                await self._set_charger_control("take")
                await self._set_charge_power(
                    charge_power=1, skip_min_soc_check=True, source="get_car_soc"
                )
                await self._set_charger_action("start", reason="try_get_new_soc")
                # Reading the actual SoC
                soc_in_charger = await self._force_get_register(
                    address=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                # Setting things back to inactive as it was before SoC reading started.
                await self._set_charge_power(
                    charge_power=0, skip_min_soc_check=True, source="get_car_soc"
                )  # This also sets action to stop
                await self._set_charger_action("stop", reason="try_get_new_soc")
                # This should can occure if charger is in error
                if soc_in_charger in [None, 0]:
                    soc_in_charger = "unavailable"
                # Do before restart polling
                await self._update_evse_entity(
                    evse_entity=ecs,
                    new_value=soc_in_charger,
                    force_emit=do_not_use_cache,
                )
                self.try_get_new_soc_in_process = False

                await self._set_poll_strategy()
            soc_value = soc_in_charger
        self._log(f"returning: '{soc_value}'.")
        return soc_value

    async def _get_charger_state(self) -> int:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        charger_state = self._MCE_CHARGER_STATE.current_value
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_CHARGER_STATE])
            charger_state = self._MCE_CHARGER_STATE.current_value

        return charger_state

    async def _get_charge_power(self) -> int:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        state = self._MCE_ACTUAL_POWER.current_value
        if state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_ACTUAL_POWER])
            state = self._MCE_ACTUAL_POWER.current_value

        return state

    async def _get_and_process_registers(self, entities: list):
        """This function reads the values from the EVSE via modbus and
        emits/caches these values for corresponding sensors in HA.

        The entities are read as one contiguous modbus range and each entity's
        value is taken by its register offset from the start.
        """
        start = entities[0].modbus_register.address
        end = entities[-1].modbus_register.address

        length = end - start + 1
        results = await self._modbus_read(
            address=start, length=length, source="_get_and_process_registers"
        )
        if results is None:
            # Could not read
            self._log("results is None, abort processing.", level="WARNING")
            return

        if len(results) < length:
            self._log(
                f"Modbus returned {len(results)} registers, expected {length}. "
                f"Partial data, aborting processing.",
                level="WARNING",
            )
            is_unrecoverable = await self._handle_modbus_exception(
                source="_get_and_process_registers (partial result)"
            )
            if is_unrecoverable:
                await self._handle_un_recoverable_error(
                    reason="persistent partial modbus responses",
                    source="_get_and_process_registers",
                )
            return

        for entity in entities:
            entity_name = f"register_{entity.modbus_register.address}"
            register_index = entity.modbus_register.address - start
            new_state = results[register_index]
            if new_state is None:
                self._log(f"New value 'None' for entity '{entity_name}' ignored.")
                continue

            try:
                new_state = int(float(new_state))
            except ValueError as ve:
                self._log(
                    f"New value '{new_state}' for entity '{entity_name}' "
                    f"ignored due to ValueError: {ve}."
                )
                continue

            if not (entity.minimum_value <= new_state <= entity.maximum_value):
                # Ignore and keep current value unless that is None
                if entity.current_value is None:
                    # This is very rare: current_value will only be None at startup.
                    # Not setting a value will cause the application to hang, so lets use
                    # the relaxed min/max in the entity supports that.
                    # If that fails assume 'unavailable'.
                    relaxed_min_value = entity.relaxed_min_value
                    relaxed_max_value = entity.relaxed_max_value
                    if relaxed_min_value is None or relaxed_max_value is None:
                        new_state = "unavailable"
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of range {entity.minimum_value} "
                            f"- {entity.maximum_value} but current value is None, so this polled"
                            f" value cannot be ignored, so new_value set to 'unavailable'."
                        )
                    elif relaxed_min_value <= new_state <= relaxed_max_value:
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of min/max range but in relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value}. So, as the current value is None, this this "
                            f"polled value is still used."
                        )
                    else:
                        new_state = "unavailable"
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value} but current value is None, so this polled value"
                            f" cannot be ignored, so new_value set to 'unavailable'."
                        )
                else:
                    # If there is a current value ignore the new value and keep that current value.
                    # This occurs when car is connected but charger is idle, it then
                    # returns 0 for the SoC.
                    continue

            await self._update_evse_entity(evse_entity=entity, new_value=new_state)
        return

    async def _update_evse_entity(
        self,
        evse_entity: ModbusConfigEntity,
        new_value,
        force_emit: bool = False,
    ):
        """
        Update evse_entity.
        :param evse_entity: evse_entity
        :param new_value: new_value, can be "unavailable"
        :param force_emit: if True, always fire the change handler even if the
            value has not changed. Used by set_active to broadcast the current
            state to all listeners on activation.
        :return: Nothing

        Note: the value has already been validated by the caller
        (_get_and_process_registers or _get_car_soc). This method therefore does
        NOT re-validate/coerce; in particular it stores the "unavailable" string
        sentinel verbatim (MCE.set_value would coerce it to None).
        """
        current_value = evse_entity.current_value

        if current_value != new_value or force_emit:
            evse_entity.current_value = new_value
            # Call change_handler if defined
            if evse_entity.change_handler is not None:
                str_action = evse_entity.change_handler
                # TODO: Find an more elegant way (without 'eval') to do this...
                if str_action == "_handle_charger_state_change":
                    await self._handle_charger_state_change(
                        new_charger_state=new_value,
                        old_charger_state=current_value,
                    )
                elif str_action == "_handle_soc_change":
                    await self._handle_soc_change(
                        new_soc=new_value, old_soc=current_value
                    )
                elif str_action == "_handle_charger_error_state_change":
                    # This is the case for the _MCE_ERROR_1..4. The charger_state
                    # does not necessarily change only (one or more of) these error-states.
                    # So the state is not added to the call.
                    await self._handle_charger_error_state_change({"dummy": None})
                elif str_action == "_handle_charge_power_change":
                    await self._handle_charge_power_change(new_power=new_value)
                else:
                    self._log(f"unknown action: '{str_action}'.", level="WARNING")

    async def _set_charge_power(
        self, charge_power: int, skip_min_soc_check: bool = False, source: str = None
    ):
        """Private function to set desired (dis-)charge power in Watt in the charger.
           Check in place not to discharge below the set minimum.
           Setting the charge_power does not imply starting the charge.

        Args:
            charge_power (int):
                Power in Watt, is checked to be between
                CHARGER_MAX_CHARGE_POWER and -CHARGER_MAX_DISCHARGE_POWER
            skip_min_soc_check (bool, optional):
                boolean is used when the check for the minimum soc needs to be skipped.
                This is used when this method is called from the _get_car_soc Defaults to False.
            source (str, optional):
              For logging purposes.
        """
        self._log(f"called from {source}, power {charge_power}.", level="DEBUG")
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self._get_car_soc()
            if current_soc in ["unavailable", "unknown"]:
                self._log(
                    "current SoC is 'unavailable', only expected when car is not connected",
                    level="WARNING",
                )
            elif current_soc <= c.CAR_MIN_SOC_IN_PERCENT:
                # Fail-safe, this should never happen...
                self._log(
                    f"A discharge is attempted from {source=}, while the current SoC is below the "
                    f"minimum ({c.CAR_MIN_SOC_IN_PERCENT})%. Stopping discharging.",
                    level="WARNING",
                )
                charge_power = 0

        # Clip values to min/max charging current
        if charge_power > c.CHARGER_MAX_CHARGE_POWER:
            self._log(
                f"Requested charge power {charge_power} Watt too high.", level="WARNING"
            )
            charge_power = c.CHARGER_MAX_CHARGE_POWER
        elif charge_power < -c.CHARGER_MAX_DISCHARGE_POWER:
            self._log(
                f"Requested discharge power {charge_power} Watt too high.",
                level="WARNING",
            )
            charge_power = -c.CHARGER_MAX_DISCHARGE_POWER

        if self.requested_charge_power == charge_power:
            self._log(
                f"New-charge-power-setting from {source=} is same as "
                f"current-charge-power-setting: {charge_power} W. Not writing to charger.",
                level="DEBUG",
            )
            return

        res = await self._modbus_write(
            address=self.CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source=f"set_charge_power, from {source}",
        )
        self.requested_charge_power = charge_power

        if not res:
            self._log(
                f"Failed to set charge power to {charge_power} Watt.", level="WARNING"
            )
            # If negative value result in false, check if grid code is set correct in charger.
        return

    ######################################################################
    #                   POLLING RELATED FUNCTIONS                        #
    ######################################################################

    async def _set_poll_strategy(self):
        """Poll strategy:
        Should only be called if connection state has really changed.
        Minimal: Car is disconnected, poll for just the charger state every 15 seconds.
        Base: Car is connected, poll for all info every 5 seconds
        When Charge mode is off, is handled by handle_charge_mode
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if self.try_get_new_soc_in_process:
            # At the end of the process of (forcefully) getting a soc this method is called (again).
            return

        await self._cancel_polling(reason="setting new polling strategy")

        charger_state = await self._get_charger_state()
        if charger_state in [None, "unavailable", "unknown"]:
            # Probably initialization is not complete yet, assume not connected
            charger_state = self.DISCONNECTED_STATES[0]
            self._log(
                "Deciding polling strategy based on state unavailable charger state, "
                "assume disconnected."
            )
        else:
            self._log(
                f"Deciding polling strategy based on state: {self.CHARGER_STATES[charger_state]}."
            )

        if charger_state in self.DISCONNECTED_STATES:
            self._log(
                "Minimal polling strategy (lower freq., charger_state register only.)"
            )
            self.poll_timer_handle = await self.hass.run_every(
                self._minimal_polling, "now", self.MINIMAL_POLLING_INTERVAL_SECONDS
            )
        else:
            self._log("Base polling strategy (higher freq., all registers).")
            self.poll_timer_handle = await self.hass.run_every(
                self._base_polling, "now", self.BASE_POLLING_INTERVAL_SECONDS
            )

    async def _cancel_polling(self, reason: str = ""):
        """Stop the polling process by cancelling the polling timer.
           Further reset the polling indicator in the UI.

        Args:
            reason (str, optional): For debugging only
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        self._log(f"reason: {reason}")
        await cancel_timer_silent(self.hass, self.poll_timer_handle)
        self.poll_timer_handle = None
        self.event_bus.emit_event("evse_polled", stop=True)

    async def _minimal_polling(self, kwargs):
        """Should only be called from set_poll_strategy
        Minimal polling strategy:
        When car is disconnected
        Only poll for charger status to see if car is connected again.
        """
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self._get_and_process_registers([self._MCE_CHARGER_STATE])
        await self._get_and_process_registers([self._MCE_CHARGER_LOCKED])
        self.event_bus.emit_event("evse_polled", stop=False)

    async def _base_polling(self, kwargs):
        """Should only be called from set_poll_strategy
        Base polling strategy:
        When car is connected
        Poll for soc, state, power, lock etc...
        """
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._get_and_process_registers([self._MCE_CHARGER_LOCKED])
        self.event_bus.emit_event("evse_polled", stop=False)

    ######################################################################
    #                   MODBUS RELATED FUNCTIONS                         #
    ######################################################################

    async def _update_charger_communication_state(self, can_communicate: bool):
        self.event_bus.emit_event(
            "charger_communication_state_change", can_communicate=can_communicate
        )

    async def _force_get_register(
        self,
        address: int,
        min_value_at_forced_get: int,
        max_value_at_forced_get: int,
        min_value_after_forced_get: int = None,
        max_value_after_forced_get: int = None,
    ) -> int:
        """
        When a 'realtime' reading from the modbus server is needed, as opposed to
        stored value from polling.
        It is expected to be between min_value_at_forced_get/max_value_at_forced_get.
        This is aimed at the SoC, this is expected to be between 2 and 97%, but at
        timeout 1% to 100% is acceptable.

        If the value is not in the wider acceptable range at timeout we assume
        the modbus server has crashed, and we call _handle_un_recoverable_error.

        :param address: The address to read from
        :param min_value_at_forced_get: min acceptable value
        :param max_value_at_forced_get: max acceptable value
        :param min_value_after_forced_get: min acceptable value after the timeout
        :param max_value_after_forced_get: max acceptable value after the timeout
        :return: the read value
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        # Times in seconds
        total_time = 0
        delay_between_reads = 0.25

        # If the real SoC is not available yet, keep trying for
        # max. self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS
        while True:
            result = None
            try:
                # Only one register is read so count = 1, the charger expects device_id to be 1.
                result = await self._mb_client.read_holding_registers(address=address)
            except ModbusException as me:
                self._log(f"ModbusException {me}", level="WARNING")
                is_unrecoverable = await self._handle_modbus_exception(
                    source="_force_get_register"
                )
                if is_unrecoverable:
                    return
            else:
                await self._reset_modbus_exception()

            if result is not None:
                try:
                    result = self._get_2comp(result.registers[0])
                    if min_value_at_forced_get <= result <= max_value_at_forced_get:
                        # Acceptable result retrieved
                        self._log(
                            f"After {total_time} sec. value {result} was retrieved."
                        )
                        break
                except TypeError:
                    pass
            total_time += delay_between_reads

            # We need to stop at some point
            if total_time >= self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS:
                # No check for value_to_translate_to_none as this method is only called when:
                # - for SoC when car is connected and 0 represents an error
                # - for max_charger_power there is no value_to_translate_to_none

                # After the timeout a more lenient range is applicable for some entities
                if (
                    min_value_after_forced_get is not None
                    and max_value_after_forced_get is not None
                    and result is not None
                ):
                    if (
                        min_value_after_forced_get
                        <= result
                        <= max_value_after_forced_get
                    ):
                        self._log(f"after timed out relevant value was {result}.")
                        break

                self._log("timed out, no relevant value was retrieved.")
                # This does not always trigger a connection exception, but we can assume the
                # connection is down. This normally would result in ModbusExceptions earlier
                # and these would normally trigger _handle_un_recoverable_error already.
                await self._handle_un_recoverable_error(
                    reason="timeout", source="_force_get_register"
                )
                return None

            await asyncio.sleep(delay_between_reads)
            continue
        # End of while loop

        await self._update_charger_communication_state(can_communicate=True)
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)
        return result

    async def _modbus_write(self, address: int, value: int, source: str) -> bool:
        """Generic modbus write function.
           Writing to the modbus server should exclusively be done through this function

        Args:
            address (int): the register / address to write to
            value (int): the value to write
            source (str): only for debugging

        Returns:
            bool: True if write was successful
        """

        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if value < 0:
            # Modbus cannot handle negative values directly.
            value = self.MAX_USI + value

        if not self._mb_client.is_initialised:
            self._log("Client is None, aborting.", level="WARNING")
            return

        if not self._mb_client.connected:
            try:
                self._log("Trying to connect...")
                await self._mb_client.connect()
            except ModbusException as me:
                self._log(f"Could not connect, exception: {me}.", level="WARNING")

        result = None
        try:
            result = await self._mb_client.write_register(
                address=address,
                value=value,
                device_id=1,
            )
        except ModbusException as me:
            self._log(f"ModbusException {me}", level="WARNING")
            is_unrecoverable = await self._handle_modbus_exception(
                source="_modbus_write"
            )
            if is_unrecoverable:
                return
        else:
            await self._reset_modbus_exception()

        if result is None:
            self._log("Failed to write to modbus server.")
        # Sleep for a while to create time between writes does not work as this is async..
        return result

    async def _modbus_read(
        self, address: int, length: int = 1, source: str = "unknown"
    ):
        """Generic modbus read function.
           Reading from the modbus server is preferably done through this function

        Args:
            address (int): The starting register/address from which to read the values
            length (int, optional): Number of successive addresses to read. Defaults to 1.
            source (str, optional): only for debugging.

        Raises:
            exc: ModbusException

        Returns:
            _type_: List of int values
        """

        result = None
        try:
            result = await self._mb_client.read_holding_registers(
                address=address,
                count=length,
                device_id=1,
            )
        except ModbusException as me:
            self._log(f"ModbusException {me}", level="WARNING")
            is_unrecoverable = await self._handle_modbus_exception(
                source="_modbus_read"
            )
            if is_unrecoverable:
                return None
        else:
            await self._reset_modbus_exception()

        if result is None:
            self._log(f"result is None for address '{address}' and length '{length}'.")
            return None
        return list(map(self._get_2comp, result.registers))

    async def _handle_bad_modbus_config(self):
        """Function to call when no connection with the modbus server could be made.
        This is only expected at startup.
        A sticky memo will be posted pointing out that the configuration might not be ok.
        Polling is canceled as this is pointless without a connection.
        """

        self.notifier.post_sticky_memo(
            title="Error in charger configuration",
            message="Please check if charger is powered, has IP connection and "
            "if Host/Port are correct in configuration.",
            memo_id="no_comm_with_evse",
        )
        await self._cancel_polling(reason="no modbus connection")

    async def _handle_charger_error_state_change(self, kwargs):
        """Handle errors reported by the charger.
        To be called when:
        - When the charger state changes to or from one of the ERROR_STATES
          Then the new_charger_state is added to the call
        - Any of the error entities _MCE_ERROR_1..4 change
          Then the new_charger_state is not in the call.
        - After a MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS delay this method calls
          itself with is_final_check = true

        The parameters in the kwargs
        - new_charger_state: used when called due to changed charger_state (was or is error)
        - is_final_check: If True then it is time to call _handle_un_recoverable_error
        :return: nothing
        These are not normal parameters otherwise the run_in cannot be used.
        """
        new_charger_state = kwargs.get("new_charger_state", None)
        is_final_check = kwargs.get("is_final_check", False)
        self._log(f"{new_charger_state=}, {is_final_check=}")
        has_error = False

        if new_charger_state is None:
            new_charger_state = await self._get_charger_state()
            self._log(
                f"Called without charger state, _get_charger_state: {new_charger_state}."
            )

        if new_charger_state in self.ERROR_STATES:
            self._log(
                f"Charger in error state: '{new_charger_state}'.", level="WARNING"
            )
            has_error = True

        for entity in self.CHARGER_ERROR_ENTITIES:
            # None = uninitialised, 0 = no error.
            if entity.current_value not in [None, 0]:
                self._log(
                    f"Charger reports error at register {entity.modbus_register.address}: "
                    f"{entity.current_value}",
                    level="WARNING",
                )
                has_error = True

        if has_error:
            if is_final_check:
                self._log(
                    f"Error in charger for more than "
                    f"{self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS}s.",
                    level="WARNING",
                )
                await self._handle_un_recoverable_error(reason="charger reports error")
            elif self.timer_id_check_error_state is None:
                self._log(
                    f"Starting check_error_state timer "
                    f"{self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS}s."
                )
                self.timer_id_check_error_state = await set_oneshot_timer(
                    self.hass,
                    self.timer_id_check_error_state,
                    self._handle_charger_error_state_change,
                    delay=self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
                    new_charger_state=None,
                    is_final_check=True,
                )
                return
            else:
                self._log(
                    "Error still present, waiting for check_error_state timeout..."
                )

        else:
            self._log("Reset check_error_state timer, no error anymore.")
            await cancel_timer_silent(self.hass, self.timer_id_check_error_state)
            self.timer_id_check_error_state = None

    async def _handle_modbus_exception(self, source):
        """Modbus (connection) exception occurs regularly with the Wallbox Quasar (e.g. bi-weekly)
        and is usually not self resolving.
        This method checks the severity of the connection problem and notifies the user if needed.

        This method is to be called from _modbus_read and _modbus_write methods.
        Connection exceptions occurs on client.read() and client.write() instead of, as you would
        expect, on client.connect().

        :param source: Only for logging
        :return: Is the exception persistent for longer than the set timeout.
        """
        self._log("called")
        is_unrecoverable = False
        # The counter is initiated at None.
        # At first successful modbus call this counter is set to 0 by _reset_modbus_exception.
        # Until then do not treat the exception as a problem and do not increment the counter.
        # Most likely the app is still initialising or user is still busy configuring.
        if self.modbus_exception_counter is None:
            self._log(f"{source}: modbus exception. Configuration (not yet) invalid?")
            await self._handle_bad_modbus_config()
            is_unrecoverable = False

        # So, there is an exception after initialisation, this still could self recover.
        # We'll wait self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS, until then consider it
        # recoverable.
        if self.modbus_exception_counter == 0:
            self._log(f"{source}: First modbus exception.")
            self.timer_id_check_modus_exception_state = await set_oneshot_timer(
                self.hass,
                self.timer_id_check_modus_exception_state,
                self._handle_un_recoverable_error,
                delay=self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
            )
            self.modbus_exception_counter = 1
            is_unrecoverable = False
        else:
            # This is a repeated exception, so a timer has been set to handle this
            # as unrecoverable, just above here.
            # If there is no timer any more the time has run out to see this as
            # recoverable.
            if self.timer_id_check_modus_exception_state in [None, ""]:
                is_unrecoverable = True
            else:
                is_unrecoverable = False

        return is_unrecoverable

    async def _reset_modbus_exception(self):
        """Reset modbus_exception_counter and cancel timer_id_check_modus_exception_state
        and set the connection status in the UI to is_alive=True
        Works in conjunction with _handle_modbus_exception.
        To be called every time there has been a successful modbus read/write.
        :return: Nothing
        """
        if self.modbus_exception_counter == 1:
            self._log("There was an modbus exception, now solved.")
            await self.v2g_main_app.reset_charger_communication_fault()
        self.modbus_exception_counter = 0
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        self.timer_id_check_modus_exception_state = None
        await self._update_charger_communication_state(can_communicate=True)

    async def _handle_un_recoverable_error(
        self, reason: str = None, source: str = None
    ):
        """There are four ways to determine if the charger can be considered
         none-responsive:
         - When the charger reports a charger_state = error for a longer period
         - When the charger ERROR_ENTITY1..4 report an error for a longer period.
         - When modbus read/write throw an exception for a longer period
         - When forced reading a value returns an invalid result for a longer period

        If any of these occur, this method is called.
        This method will cancel polling, notify the user (high priority notification).
        There is no way to programmable undo this situation as a manual restart of the
        charger and V2G Liberty is needed.

        :param reason: for debug/logging only
        :param source: for debug/logging only
        :return: Nothing
        """
        self._log(f"{source=}, {reason=}.")

        # This method could be called from two timers. Make sure both are canceled so no double
        # notifications get sent.
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        await cancel_timer_silent(self.hass, self.timer_id_check_error_state)

        await self._cancel_polling(reason="un_recoverable charger error")
        # The only exception to the rule that _am_i_active should only be set from set_(in)active().
        self._am_i_active = False
        await self.v2g_main_app.handle_none_responsive_charger(
            was_car_connected=await self.is_car_connected()
        )
        await self._update_charger_communication_state(can_communicate=False)

        # The soc and power are not known any more so let's represent this in the app
        await self._update_evse_entity(
            evse_entity=self._MCE_ACTUAL_POWER, new_value="unavailable"
        )
        await self._update_evse_entity(
            evse_entity=self._MCE_CAR_SOC, new_value="unavailable"
        )
        # Clear the cached charger state too. Otherwise, if the charger recovers
        # in the same state it crashed in, the recovery poll equals the cache, no
        # charger_state_change is emitted, and the UI keeps showing "Error".
        self._MCE_CHARGER_STATE.current_value = None

        # Arm the recovery probe so the charger auto-recovers once reachable
        # again — but only one, even if two grace timers escalate concurrently
        # (each would otherwise see timer_id_recovery_probe == None and arm its
        # own recurring timer, orphaning one). Claim the slot synchronously.
        if self.timer_id_recovery_probe is not None:
            return
        self.timer_id_recovery_probe = ""  # synchronous claim before the await
        self._log(
            f"Starting recovery probe every {self.RECOVERY_PROBE_INTERVAL_SECONDS}s."
        )
        try:
            self.timer_id_recovery_probe = await set_recurring_timer(
                self.hass,
                self.timer_id_recovery_probe,
                self._probe_charger_recovery,
                start=f"now+{self.RECOVERY_PROBE_INTERVAL_SECONDS}",
                interval=self.RECOVERY_PROBE_INTERVAL_SECONDS,
            )
        except Exception as ex:
            # Arming failed: release the claim so a later crash can retry.
            self.timer_id_recovery_probe = None
            self._log(f"Failed to arm recovery probe: {ex}", level="WARNING")

    async def _probe_charger_recovery(self, kwargs: dict = None):
        """Periodic probe that checks whether a charger which hit an unrecoverable
        error is reachable again.

        Reads a single register directly via the transport — NOT via
        ``_modbus_read`` — so it never re-enters the exception/grace-timer state
        machine (no timers, no side effects). On a clean read that is not an error
        state it cancels itself and asks the main app to auto-recover (which
        switches charge_mode back to Automatic → ``set_active``).
        """
        if self._am_i_active:
            # Already recovered (e.g. the user manually switched to Automatic).
            await self._cancel_recovery_probe()
            return

        mbr = self._MCE_CHARGER_STATE.modbus_register
        try:
            result = await self._mb_client.read_holding_registers(
                address=mbr.address, count=mbr.length, device_id=mbr.device_id
            )
        except Exception as e:
            # Isolate the probe from all transport errors; wait for the next probe.
            self._log(f"Recovery probe: charger still unreachable ({e}).")
            return

        if result is None or result.isError():
            self._log("Recovery probe: charger still unreachable (error response).")
            return

        charger_state = mbr.decode(result.registers)
        if charger_state in self.ERROR_STATES:
            self._log(
                f"Recovery probe: charger reachable but in error state "
                f"{charger_state}; waiting."
            )
            return

        if self._am_i_active:
            # A manual recovery (set_active) landed while we were reading; the
            # charger is already active, so avoid a redundant auto-recover.
            await self._cancel_recovery_probe()
            return

        self._log(
            f"Recovery probe: charger reachable (state {charger_state}); auto-recovering."
        )
        await self._cancel_recovery_probe()
        await self.v2g_main_app.auto_recover_from_charger_crash()

    async def _cancel_recovery_probe(self):
        """Cancel the recovery probe timer, if running."""
        await cancel_timer_silent(self.hass, self.timer_id_recovery_probe)
        self.timer_id_recovery_probe = None

    def _get_2comp(self, number):
        """Util function to covert a modbus read value to in with two's complement values
           into negative int numbers.

        Args:
            number: value to convert, normally int, but can be other type
                    should be: 0 < number < self.MAX_USI

        Returns:
            int: With negative values if applicable
        """
        return_value = parse_to_int(number, None)
        if return_value is None:
            return number
        if return_value > self.HALF_MAX_USI:
            # This represents a negative value.
            return_value = return_value - self.MAX_USI
        return return_value
