from datetime import datetime
import asyncio
import constants as c
import log_wrapper
from v2g_globals import get_local_now, parse_to_int
import pymodbus.client as modbusClient
from pymodbus.exceptions import ModbusException

from appdaemon.plugins.hass.hassapi import Hass


class ModbusEVSEclient:
    """Communicate with the Electric Vehicle Supply Equipment (EVSE) via modbus.
    It does this mainly by polling the EVSE for states and values in an
    asynchronous way, as the charger might not always react instantly.

    Values of the EVSE (like charger status or car SoC) are then written to
    Home Assistant entities for other modules to use / subscribe to.
    """

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
    #   EVSE Entities                                                              #
    #   These hold the constants for entity (e.g. modbus address, min/max value,   #
    #   and store (cache) the values of the charger.                               #
    ################################################################################

    ENTITY_CHARGER_CURRENT_POWER = {
        "modbus_address": 526,
        "minimum_value": -7400,
        "maximum_value": 7400,
        "current_value": None,
        "ha_entity_name": "charger_real_charging_power",
    }
    ENTITY_CHARGER_STATE = {
        "modbus_address": 537,
        "minimum_value": 0,
        "maximum_value": 11,
        "current_value": None,
        "change_handler": "__handle_charger_state_change",
        "ha_entity_name": "charger_state_int",
    }
    ENTITY_CAR_SOC = {
        "modbus_address": 538,
        "minimum_value": 2,
        "maximum_value": 97,
        "relaxed_min_value": 1,
        "relaxed_max_value": 100,
        "current_value": None,
        "change_handler": "__handle_soc_change",
        "ha_entity_name": "car_state_of_charge",
    }
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
    # About the current / previous_value:
    #  These are initiated with None to indicate they have not been touched yet.

    ENTITY_ERROR_1 = {
        "modbus_address": 539,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "__handle_charger_error_state_change",
        "ha_entity_name": "unrecoverable_errors_register_high",
    }
    ENTITY_ERROR_2 = {
        "modbus_address": 540,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "__handle_charger_error_state_change",
        "ha_entity_name": "unrecoverable_errors_register_low",
    }
    ENTITY_ERROR_3 = {
        "modbus_address": 541,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "__handle_charger_error_state_change",
        "ha_entity_name": "recoverable_errors_register_high",
    }
    ENTITY_ERROR_4 = {
        "modbus_address": 542,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "__handle_charger_error_state_change",
        "ha_entity_name": "recoverable_errors_register_low",
    }

    ENTITY_CHARGER_LOCKED = {
        "modbus_address": 256,
        "minimum_value": 0,
        "maximum_value": 1,
        "current_value": None,
        "ha_entity_name": "charger_locked",
    }

    ENTITY_FIRMWARE_VERSION = {
        "modbus_address": 1,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "ha_entity_name": "firmware_version",
    }

    ENTITY_SERIAL_NUMBER_HIGH = {
        "modbus_address": 2,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "ha_entity_name": "serial_number_high",
    }

    ENTITY_SERIAL_NUMBER_LOW = {
        "modbus_address": 3,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "ha_entity_name": "serial_number_low",
    }

    # Groups of entities for efficient reading of the modbus registers.
    CHARGER_POLLING_ENTITIES: list
    CHARGER_ERROR_ENTITIES: list
    # Contain static data, only needs to be initialised once
    CHARGER_INFO_ENTITIES: list

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
    # through ENTITY_CHARGER_CURRENT_POWER
    CHARGER_SET_CHARGE_POWER_REGISTER: int = 260

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
    client: modbusClient = None
    WAIT_AFTER_MODBUS_WRITE_IN_MS: int = 2500
    WAIT_AFTER_MODBUS_READ_IN_MS: int = 50

    # For sending notifications to the user.
    v2g_main_app: object
    v2g_globals: object

    # Handle for polling_timer, needed for cancelling polling.
    poll_timer_handle: object
    BASE_POLLING_INTERVAL_SECONDS: int = 5
    MINIMAL_POLLING_INTERVAL_SECONDS: int = 15
    # For indication to the user if/how fast polling is in progress
    poll_update_text: str = ""

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

    # For (un)blocking of calls and keeping the client in-active when it should
    # Set only(!) by set_inactive and set_active.
    _am_i_active: bool = None

    hass: Hass = None

    def __init__(self, hass: Hass):
        """initialise modbus_evse_client
        Setting up constants and variables.
        Configuration and connecting the modbus client is done separately in initialise_charger.
        """
        self.hass = hass
        self.__log = log_wrapper.get_class_method_logger(hass.log)

        self.CHARGER_ERROR_ENTITIES = [
            self.ENTITY_ERROR_1,
            self.ENTITY_ERROR_2,
            self.ENTITY_ERROR_3,
            self.ENTITY_ERROR_4,
        ]
        self.CHARGER_POLLING_ENTITIES = [
            self.ENTITY_CHARGER_CURRENT_POWER,
            self.ENTITY_CHARGER_STATE,
            self.ENTITY_CAR_SOC,
            self.ENTITY_ERROR_1,
            self.ENTITY_ERROR_2,
            self.ENTITY_ERROR_3,
            self.ENTITY_ERROR_4,
        ]
        self.CHARGER_INFO_ENTITIES = [
            self.ENTITY_FIRMWARE_VERSION,
            self.ENTITY_SERIAL_NUMBER_HIGH,
            self.ENTITY_SERIAL_NUMBER_LOW,
        ]
        self.poll_timer_handle = None

    ######################################################################
    #                     PUBLIC FUNCTIONAL METHODS                      #
    ######################################################################

    async def test_charger_connection(self, host, port):
        """Test client settings and return max_available_power in Watt.
        To be called from UI (via globals). Works even if this module has not been
        initialised yet."""
        self.__log(f"Testing Modbus EVSE client at {host}:{port}")

        client = modbusClient.AsyncModbusTcpClient(
            host=host,
            port=port,
        )
        try:
            await client.connect()
            if client.connected:
                max_available_power = await self.__get_max_available_power(client)
                return True, max_available_power
            else:
                return False, None
        finally:
            client.close()

    async def __get_max_available_power(self, client):
        result = await client.read_holding_registers(
            self.MAX_AVAILABLE_POWER_REGISTER, count=1, slave=1
        )
        return result.registers[0]

    async def initialise_charger(self, v2g_args=None):
        """Initialise charger
        Configuring the client, making the connection and reading
        min/max charge power.
        Activating the polling is done in set_active.
        """
        if c.CHARGER_HOST_URL is None or c.CHARGER_PORT is None:
            self.__log(
                f"Could not configure Modbus EVSE client host or port are None."
                f"reason: {v2g_args}"
            )
            return False, None
        self.__log(
            f"Configuring Modbus EVSE client at {c.CHARGER_HOST_URL}:{c.CHARGER_PORT}, "
            f"reason: {v2g_args}"
        )

        # Remove old client if needed.
        if self.client is not None:
            if self.client.connected:
                self.client.close()

        self.client = modbusClient.AsyncModbusTcpClient(
            host=c.CHARGER_HOST_URL,
            port=c.CHARGER_PORT,
        )
        await self.client.connect()
        self.modbus_exception_counter = 0
        if self.client.connected:
            max_available_power_by_charger = await self.__force_get_register(
                register=self.MAX_AVAILABLE_POWER_REGISTER,
                min_value_at_forced_get=self.CHARGE_POWER_LOWER_LIMIT,
                max_value_at_forced_get=self.CHARGE_POWER_UPPER_LIMIT,
            )
            return True, max_available_power_by_charger
        else:
            return False, None

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self.__log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )

        await self.__set_charger_action("stop", reason="stop_charging")
        await self.__set_charge_power(charge_power=0, source="stop_charging")

    async def start_charge_with_power(self, charge_power: int, source: str = "unknown"):
        """Function to start a charge session with a given power in Watt.
           To be called from v2g-liberty module.

        Args:
            charge_power (int): charge_power with a value in Watt, can be negative.
            source (str, optional): for debugging. Defaults to "unknown".
        """
        # Check for automatic mode should be done by V2G Liberty app
        if not self._am_i_active:
            self.__log(
                f"Not setting charge_rate: _am_i_active == False. Requested by '{source}'."
            )
            return

        if charge_power is None:
            self.__log("charge_power = None, abort", level="WARNING")
            return

        if not await self.is_car_connected():
            self.__log(
                f"Not setting charge_rate: No car connected. Requested by '{source}'."
            )
            return

        await self.__set_charger_control("take")
        if charge_power == 0:
            await self.__set_charger_action(
                action="stop",
                reason=f"called from {source} with power = 0",
            )
        else:
            await self.__set_charger_action(
                action="start",
                reason=f"called from {source} with {charge_power=}",
            )

        await self.__set_charge_power(
            charge_power=charge_power,
            source=f"{source} => start_charge_with_power",
        )

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop
        Do not cancel polling, the information is still relevant.
        """
        self.__log("made inactive")
        await self.stop_charging()
        await self.__set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if self.client is None:
            self.__log("Client not initialised, aborting", level="WARNING")
            return
        self.__log("activated")
        self._am_i_active = True
        await self.__set_charger_control("take")
        await self.__get_car_soc(do_not_use_cache=True)
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self.__set_poll_strategy()

    async def get_car_soc(self) -> int:
        """Helper to get SoC in percent"""
        return await self.__get_car_soc(do_not_use_cache=False)

    async def get_car_soc_kwh(self) -> float:
        """Helper to get SoC in kWh"""
        soc = await self.__get_car_soc(do_not_use_cache=False)
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
            self.__log("called while _am_i_active == False. Returning False.")
            return False

        # The method self.__get_charger_state() cannot be used as it is async and this
        # method should not be as it is called from sync code (data_monitor.py).
        return self.ENTITY_CHARGER_STATE["current_value"] in self.AVAILABILITY_STATES

    async def is_car_connected(self) -> bool:
        """Indicates if currently a car is connected to the charger."""
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        is_connected = self.client is not None
        is_connected = (
            is_connected
            and await self.__get_charger_state() not in self.DISCONNECTED_STATES
        )
        self.__log(f"called, returning: {is_connected}")
        return is_connected

    async def is_charging(self) -> bool:
        """Indicates if currently the connected car is charging (not discharging)"""
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        return await self.__get_charger_state() == self.CHARGING_STATE

    async def is_discharging(self) -> bool:
        """Indicates if currently the connected car is discharging (not charging)"""
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        return await self.__get_charger_state() == self.DISCHARGING_STATE

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################

    async def complete_init(self):
        """
        This public function is to be called from v2g-liberty once after its own init is complete.
        This timing is essential, the following code might need v2g-liberty for notifications etc.
        """
        if self.client is None:
            self.__log("Client not initialised, aborting", level="WARNING")
            return
        self.__log("kicking off")

        # So the status page can show if communication with charge is ok.
        for entity in self.CHARGER_INFO_ENTITIES:
            # Reset values
            entity_name = f"sensor.{entity['ha_entity_name']}"
            await self.__update_ha_entity(entity_id=entity_name, new_value="unknown")
        await self.__get_and_process_registers(self.CHARGER_INFO_ENTITIES)

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)

        # SoC is essential for many decisions, so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first
        # opportunity to do a poll.
        await self.__get_car_soc()

    async def __set_charger_control(self, take_or_give_control: str):
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
            self.__log("called while _am_i_active == False. Not blocking.")

        if take_or_give_control == "take":
            await self.__modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["remote"],
                source="__set_charger_control, take_control",
            )
            await self.__modbus_write(
                address=self.CHARGER_AUTOSTART_ON_CONNECT_REGISTER,
                value=self.AUTOSTART_ON_CONNECT_SETTING["disable"],
                source="__set_charger_control, set_auto_connect",
            )
            await self.__modbus_write(
                address=self.SET_SETPOINT_TYPE_REGISTER,
                value=self.SETPOINT_TYPES["power"],
                source="__set_charger_control: power",
            )
            await self.__modbus_write(
                address=self.CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER,
                value=self.CMIT,
                source="__set_charger_control: Modbus idle timeout",
            )

        elif take_or_give_control == "give":
            # Setting control to user automatically sets:
            # + autostart to enable
            # + set_point to Ampere
            # + idle timeout to 0 (disabled)
            await self.__set_charge_power(
                charge_power=0, source="__set_charger_control, give_control"
            )
            await self.__modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["user"],
                source="__set_charger_control, give_control",
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

    async def __handle_soc_change(self):
        """Handle changed soc, set remaining range sensor."""
        await self.__update_ha_entity(
            entity_id="sensor.car_remaining_range",
            new_value=await self.get_car_remaining_range(),
        )

    async def __handle_charger_state_change(
        self, new_charger_state: int, old_charger_state: int
    ):
        """
        Called when __update_evse_entity detects a changed value.
        """
        self.__log(f"called {new_charger_state=}, {old_charger_state=}.")
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")
        # Also make a text version of the state available in the UI
        charger_state_text = self.CHARGER_STATES.get(new_charger_state, None)
        if charger_state_text is not None and not self.try_get_new_soc_in_process:
            # self.try_get_new_soc_in_process should not happen as polling is stopped,
            # just to be safe...
            await self.__update_ha_entity(
                entity_id="sensor.charger_state_text", new_value=charger_state_text
            )
            self.__log(f"set state in text for UI = {charger_state_text}.")
        else:
            self.__log(f"Unknown charger state: {new_charger_state}.", level="WARNING")

        if (
            new_charger_state in self.ERROR_STATES
            or old_charger_state in self.ERROR_STATES
        ):
            # Check if user needs to be notified or if notification process needs to be aborted
            await self.__handle_charger_error_state_change(
                {"new_charger_state": new_charger_state, "is_final_check": False}
            )

        if new_charger_state in self.DISCONNECTED_STATES:
            # Goes to this status when the plug is removed from the car-socket,
            # not when disconnect is requested from the UI.

            # When disconnected the SoC of the car is unavailable.
            await self.__update_ha_and_evse_entity(
                evse_entity=self.ENTITY_CAR_SOC,
                new_value="unavailable",
            )
            # To prevent the charger from auto-start charging after the car gets connected again,
            # explicitly send a stop-charging command:
            await self.__set_charger_action("stop", reason="car disconnected")
            await self.__set_poll_strategy()
            await self.__update_ha_entity(
                entity_id="binary_sensor.is_car_connected",
                new_value="off",
            )
        elif old_charger_state in self.DISCONNECTED_STATES:
            # new_charger_state must be a connected state, so if the old state was disconnected
            # there was a change in connected state.
            self.__log("From disconnected to connected: try to refresh the SoC")
            await self.__get_car_soc(do_not_use_cache=True)
            await self.__set_poll_strategy()
            await self.__update_ha_entity(
                entity_id="binary_sensor.is_car_connected",
                new_value="on",
            )
        else:
            # From one connected state to an other connected state: not a change that this method
            # needs to react upon.
            pass

        return

    ######################################################################
    #                    PRIVATE FUNCTIONAL METHODS                      #
    ######################################################################

    async def __set_charger_action(self, action: str, reason: str = ""):
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
        if reason != "":
            reason = f" Reason for action: '{reason}'."

        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        action_value = ""
        if not await self.is_car_connected():
            self.__log(
                f"Not performing charger action '{action}': No car connected.{reason}"
            )
            return False

        if action == "start":
            if await self.__is_charging_or_discharging():
                self.__log(
                    f"Not performing charger action 'start': already charging.{reason}"
                )
                return True
            action_value = self.ACTIONS["start_charging"]
        elif action == "stop":
            # Stop needs to be very reliable, so we always perform this action, even if currently
            # not charging.
            action_value = self.ACTIONS["stop_charging"]
        else:
            # Restart not implemented
            self.__log(
                f"Unknown option for action: '{action}'.{reason}", level="WARNING"
            )

        txt = f"set_charger_action: {action}"
        await self.__modbus_write(
            address=self.SET_ACTION_REGISTER, value=action_value, source=txt
        )
        self.__log(f"{txt}{reason}")
        return

    async def __is_charging_or_discharging(self) -> bool:
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        state = await self.__get_charger_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self.__log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self.__log(
            f"state: {state} ({self.CHARGER_STATES[state]}), charging: {is_charging}."
        )
        return is_charging

    async def __get_car_soc(self, do_not_use_cache: bool = False) -> int:
        """Checks if a SoC value is new enough to return directly or if it should be updated first.

        :param do_not_use_cache (bool):
        This forces the method to get the soc from the car and bypass any cached value.

        :return (int):
        SoC value from 2 to 97 (%) or "unavailable".
        If the car is disconnected the charger returns 0 representing "unavailable".
        """
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        if not await self.is_car_connected():
            self.__log("no car connected, returning SoC = 'unavailable'")
            return "unavailable"

        ecs = self.ENTITY_CAR_SOC
        soc_value = ecs["current_value"]
        should_be_renewed = False
        if soc_value is None:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            self.__log("current_value is None so should_be_renewed = True")
            should_be_renewed = True

        if do_not_use_cache:
            # Needed usually only when car has been disconnected. The polling then does not read SoC
            # and this probably changed and polling might not have picked this up yet.
            self.__log("do_not_use_cache == True so should_be_renewed = True")
            should_be_renewed = True

        if should_be_renewed:
            self.__log("old or invalid SoC in HA Entity: renew")
            soc_address = ecs["modbus_address"]
            min_value_at_forced_get = ecs["minimum_value"]
            max_value_at_forced_get = ecs["maximum_value"]
            relaxed_min_value = ecs["relaxed_min_value"]
            relaxed_max_value = ecs["relaxed_max_value"]

            if await self.__is_charging_or_discharging():
                self.__log("called")
                soc_in_charger = await self.__force_get_register(
                    register=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                await self.__update_ha_and_evse_entity(
                    evse_entity=ecs, new_value=soc_in_charger
                )
            else:
                self.__log("start a charge and read the soc until value is valid")
                # When not charging reading a SoC will return a false 0-value. To resolve this start
                # charging (with minimum power) then read a SoC and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading,
                # polling is paused.
                # try_get_new_soc_in_process is used to prevent polling to start again from
                # elsewhere and to stop other processes.
                self.try_get_new_soc_in_process = True
                await self.__cancel_polling(reason="try get new soc")
                await self.__set_charger_control("take")
                await self.__set_charge_power(
                    charge_power=1, skip_min_soc_check=True, source="get_car_soc"
                )
                await self.__set_charger_action("start", reason="try_get_new_soc")
                # Reading the actual SoC
                soc_in_charger = await self.__force_get_register(
                    register=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                # Setting things back to inactive as it was before SoC reading started.
                await self.__set_charge_power(
                    charge_power=0, skip_min_soc_check=True, source="get_car_soc"
                )  # This also sets action to stop
                await self.__set_charger_action("stop", reason="try_get_new_soc")
                # Do before restart polling
                await self.__update_ha_and_evse_entity(
                    evse_entity=ecs, new_value=soc_in_charger
                )
                self.try_get_new_soc_in_process = False

                await self.__set_poll_strategy()
            soc_value = soc_in_charger
        self.__log(f"returning: '{soc_value}'.")
        return soc_value

    async def __get_charger_state(self) -> int:
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self.__get_and_process_registers([self.ENTITY_CHARGER_STATE])
            charger_state = self.ENTITY_CHARGER_STATE["current_value"]

        return charger_state

    async def __get_charge_power(self) -> int:
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        state = self.ENTITY_CHARGER_CURRENT_POWER["current_value"]
        if state is None:
            # This can be the case before initialisation has finished.
            await self.__get_and_process_registers([self.ENTITY_CHARGER_CURRENT_POWER])
            state = self.ENTITY_CHARGER_CURRENT_POWER["current_value"]

        return state

    async def __get_and_process_registers(self, entities: list):
        """This function reads the values from the EVSE via modbus and
        writes these values to corresponding sensors in HA.

        The registers dictionary should have the structure:
        modbus_address: 'sensor name'
        Where:
        modbus_address should be int + sorted + increasing and should return int from EVSE
        sensor name should be str and not contain the prefix 'sensor.'
        """
        start = entities[0]["modbus_address"]
        end = entities[-1]["modbus_address"]

        length = end - start + 1
        results = await self.__modbus_read(
            address=start, length=length, source="__get_and_process_registers"
        )
        if results is None:
            # Could not read
            self.__log("results is None, abort processing.", level="WARNING")
            return

        for entity in entities:
            entity_name = f"sensor.{entity['ha_entity_name']}"
            register_index = entity["modbus_address"] - start
            new_state = results[register_index]
            if new_state is None:
                self.__log(f"New value 'None' for entity '{entity_name}' ignored.")
                continue

            try:
                new_state = int(float(new_state))
            except ValueError as ve:
                self.__log(
                    f"New value '{new_state}' for entity '{entity_name}' "
                    f"ignored due to ValueError: {ve}."
                )
                continue

            if not (entity["minimum_value"] <= new_state <= entity["maximum_value"]):
                # Ignore and keep current value unless that is None
                if entity["current_value"] is None:
                    # This is very rare: current_value will only be None at startup.
                    # Not setting a value will cause the application to hang, so lets use
                    # the relaxed min/max in the entity supports that.
                    # If that fails assume 'unavailable'.
                    relaxed_min_value = entity.get("relaxed_min_value", None)
                    relaxed_max_value = entity.get("relaxed_max_value", None)
                    if relaxed_min_value is None or relaxed_max_value is None:
                        new_state = "unavailable"
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of range {entity['minimum_value']} "
                            f"- {entity['maximum_value']} but current value is None, so this polled"
                            f" value cannot be ignored, so new_value set to 'unavailable'."
                        )
                    elif relaxed_min_value <= new_state <= relaxed_max_value:
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of min/max range but in relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value}. So, as the current value is None, this this "
                            f"polled value is still used."
                        )
                    else:
                        new_state = "unavailable"
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value} but current value is None, so this polled value"
                            f" cannot be ignored, so new_value set to 'unavailable'."
                        )
                else:
                    # Ignore new value, keep current value
                    self.__log(
                        f"New value {new_state} for entity '{entity_name}' "
                        f"out of range {entity['minimum_value']} - {entity['maximum_value']} "
                        f"so keep current value {entity['current_value']}."
                    )
                    continue

            await self.__update_ha_and_evse_entity(
                evse_entity=entity, new_value=new_state
            )
        return

    async def __update_ha_and_evse_entity(
        self,
        evse_entity,
        new_value=None,
    ):
        """Helper method to update both the evse- and ha-entity at the same time"""
        await self.__update_evse_entity(evse_entity=evse_entity, new_value=new_value)
        entity_id = f"sensor.{evse_entity['ha_entity_name']}"
        await self.__update_ha_entity(entity_id=entity_id, new_value=new_value)

    async def __update_evse_entity(
        self,
        evse_entity: dict,
        new_value,
    ):
        """
        Update evse_entity.
        Should only be called from __update_ha_and_evse_entity.
        :param evse_entity: evse_entity
        :param new_value: new_value, can be "unavailable"
        :return: Nothing
        """
        current_value = evse_entity["current_value"]

        if current_value != new_value:
            evse_entity["current_value"] = new_value
            # Call change_handler if defined
            if "change_handler" in evse_entity.keys():
                str_action = evse_entity["change_handler"]
                # TODO: Find an more elegant way (without 'eval') to do this...
                if str_action == "__handle_charger_state_change":
                    await self.__handle_charger_state_change(
                        new_charger_state=new_value,
                        old_charger_state=current_value,
                    )
                elif str_action == "__handle_soc_change":
                    await self.__handle_soc_change()
                elif str_action == "__handle_charger_error_state_change":
                    # This is the case for the ENTITY_ERROR_1..4. The charger_state
                    # does not necessarily change only (one or more of) these error-states.
                    # So the state is not added to the call.
                    await self.__handle_charger_error_state_change({"dummy": None})
                else:
                    self.__log(f"unknown action: '{str_action}'.", level="WARNING")

    async def __update_ha_entity(
        self,
        entity_id: str,
        new_value=None,
        attributes: dict = {},
    ):
        """
        Generic function for updating the state of an entity in Home Assistant
        If it does not exist, create it.
        If it has attributes, keep them (if not overwrite with empty)

        Args:
            entity_id (str):
              Full entity_id including type, e.g. sensor.charger_state
            new_value (any, optional):
              The value the entity should be written with. Defaults to None, can be "unavailable" or
              "unknown" (treated as unavailable).
            attributes (dict, optional):
              The dict the attributes should be written with. Defaults to {}.
        """
        new_attributes = {}
        if self.hass.entity_exists(entity_id):
            entity_state = await self.hass.get_state(entity_id, attribute="all")
            if entity_state is not None:
                # Even though it exists it's state can still be None
                new_attributes = entity_state.get("attributes", {})
            new_attributes.update(attributes)
        else:
            new_attributes = attributes

        if entity_id.startswith("binary_sensor"):
            if new_value in [None, "unavailable", "unknown", ""]:
                availability = "off"
            else:
                availability = "on"
                if new_value in [True, "true", "on", 1]:
                    new_value = "on"
                else:
                    new_value = "off"

            # A work-around, sighhh...
            # This should be done by parameter availability=False in the set_state call (not as part
            # of the attributes) but that does not work..
            # So, there is an extra sensor with the same name as the original + _availability that
            # is used in the availability template of the original.
            availability_entity_id = f"{entity_id}_availability"
            await self.hass.set_state(
                availability_entity_id,
                state=availability,
            )
            if availability == "on":
                await self.hass.set_state(
                    entity_id,
                    state=new_value,
                    attributes=new_attributes,
                )
        else:
            if new_value is None:
                # A sensor cannot be set to None, results in HA error.
                new_value = "unavailable"
            await self.hass.set_state(
                entity_id,
                state=new_value,
                attributes=new_attributes,
            )

    async def __set_charge_power(
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
                This is used when this method is called from the __get_car_soc Defaults to False.
            source (str, optional):
              For logging purposes.
        """
        self.__log(f"called from {source}, power {charge_power}.")
        if not self._am_i_active:
            self.__log("called while _am_i_active is false, not blocking.")

        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self.__get_car_soc()
            if current_soc in ["unavailable", "unknown"]:
                self.__log(
                    "current SoC is 'unavailable', only expected when car is not connected",
                    level="WARNING",
                )
            elif current_soc <= c.CAR_MIN_SOC_IN_PERCENT:
                # Fail-safe, this should never happen...
                self.__log(
                    f"A discharge is attempted from {source=}, while the current SoC is below the "
                    f"minimum ({c.CAR_MIN_SOC_IN_PERCENT})%. Stopping discharging.",
                    level="WARNING",
                )
                charge_power = 0

        # Clip values to min/max charging current
        if charge_power > c.CHARGER_MAX_CHARGE_POWER:
            self.__log(
                f"Requested charge power {charge_power} Watt too high.", level="WARNING"
            )
            charge_power = c.CHARGER_MAX_CHARGE_POWER
        elif abs(charge_power) > c.CHARGER_MAX_DISCHARGE_POWER:
            self.__log(
                f"Requested discharge power {charge_power} Watt too high.",
                level="WARNING",
            )
            charge_power = -c.CHARGER_MAX_DISCHARGE_POWER

        current_charge_power = await self.__get_charge_power()

        if current_charge_power == charge_power:
            self.__log(
                f"New-charge-power-setting from {source=} is same as "
                f"current-charge-power-setting: {charge_power} W. Not writing to charger."
            )
            return

        res = await self.__modbus_write(
            address=self.CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source=f"set_charge_power, from {source}",
        )

        if not res:
            self.__log(f"Failed to set charge power to {charge_power} Watt.")
            # If negative value result in false, check if grid code is set correct in charger.
        return

    ######################################################################
    #                   POLLING RELATED FUNCTIONS                        #
    ######################################################################

    async def __update_poll_indicator_in_ui(self, reset: bool = False):
        # Toggles the char in the UI to indicate polling activity,
        # as the "last_changed" attribute also changes, an "age" can be shown based on this as well.
        self.poll_update_text = "↺" if self.poll_update_text != "↺" else "↻"
        if reset:
            self.poll_update_text = ""
        await self.__update_ha_entity(
            entity_id="sensor.poll_refresh_indicator", new_value=self.poll_update_text
        )

    async def __set_poll_strategy(self):
        """Poll strategy:
        Should only be called if connection state has really changed.
        Minimal: Car is disconnected, poll for just the charger state every 15 seconds.
        Base: Car is connected, poll for all info every 5 seconds
        When Charge mode is off, is handled by handle_charge_mode
        """
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        if self.try_get_new_soc_in_process:
            # At the end of the process of (forcefully) getting a soc this method is called (again).
            return

        await self.__cancel_polling(reason="setting new polling strategy")

        charger_state = await self.__get_charger_state()
        if charger_state in [None, "unavailable", "unknown"]:
            # Probably initialization is not complete yet, assume not connected
            charger_state = self.DISCONNECTED_STATES[0]
            self.__log(
                "Deciding polling strategy based on state unavailable charger state, "
                "assume disconnected."
            )
        else:
            self.__log(
                f"Deciding polling strategy based on state: {self.CHARGER_STATES[charger_state]}."
            )

        if charger_state in self.DISCONNECTED_STATES:
            self.__log(
                "Minimal polling strategy (lower freq., charger_state register only.)"
            )
            self.poll_timer_handle = await self.hass.run_every(
                self.__minimal_polling, "now", self.MINIMAL_POLLING_INTERVAL_SECONDS
            )
        else:
            self.__log("Base polling strategy (higher freq., all registers).")
            self.poll_timer_handle = await self.hass.run_every(
                self.__base_polling, "now", self.BASE_POLLING_INTERVAL_SECONDS
            )

    async def __cancel_polling(self, reason: str = ""):
        """Stop the polling process by cancelling the polling timer.
           Further reset the polling indicator in the UI.

        Args:
            reason (str, optional): For debugging only
        """
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        self.__log(f"reason: {reason}")
        self.__cancel_timer(self.poll_timer_handle)
        self.poll_timer_handle = None
        await self.__update_poll_indicator_in_ui(reset=True)

    async def __minimal_polling(self, kwargs):
        """Should only be called from set_poll_strategy
        Minimal polling strategy:
        When car is disconnected
        Only poll for charger status to see if car is connected again.
        """
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self.__get_and_process_registers([self.ENTITY_CHARGER_STATE])
        await self.__get_and_process_registers([self.ENTITY_CHARGER_LOCKED])
        await self.__update_poll_indicator_in_ui()

    async def __base_polling(self, kwargs):
        """Should only be called from set_poll_strategy
        Base polling strategy:
        When car is connected
        Poll for soc, state, power, lock etc...
        """
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self.__get_and_process_registers([self.ENTITY_CHARGER_LOCKED])
        await self.__update_poll_indicator_in_ui()

    ######################################################################
    #                   MODBUS RELATED FUNCTIONS                         #
    ######################################################################

    async def __update_charger_connection_state(self, is_alive: bool):
        keep_alive = {"keep_alive": get_local_now()}
        msg = "Successfully connected" if is_alive else "Connection error"
        await self.__update_ha_entity(
            entity_id="sensor.charger_connection_status",
            new_value=msg,
            attributes=keep_alive,
        )

    async def __force_get_register(
        self,
        register: int,
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
        the modbus server has crashed, and we call __handle_un_recoverable_error.

        :param register: The address to read from
        :param min_value_at_forced_get: min acceptable value
        :param max_value_at_forced_get: max acceptable value
        :param min_value_after_forced_get: min acceptable value after the timeout
        :param max_value_after_forced_get: max acceptable value after the timeout
        :return: the read value
        """
        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        # Times in seconds
        total_time = 0
        delay_between_reads = 0.25

        # If the real SoC is not available yet, keep trying for
        # max. self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS
        while True:
            result = None
            try:
                # Only one register is read so count = 1, the charger expects slave to be 1.
                result = await self.client.read_holding_registers(
                    register, count=1, slave=1
                )
            except ModbusException as me:
                self.__log(f"ModbusException {me}", level="WARNING")
                is_unrecoverable = await self.__handle_modbus_exception(
                    source="__force_get_register"
                )
                if is_unrecoverable:
                    return
            else:
                await self.__reset_modbus_exception()

            if result is not None:
                try:
                    result = self.__get_2comp(result.registers[0])
                    if min_value_at_forced_get <= result <= max_value_at_forced_get:
                        # Acceptable result retrieved
                        self.__log(
                            f"After {total_time} sec. value {result} was retrieved."
                        )
                        break
                    # else:
                    #     self.__log(f"{result} out of range {min_value_at_forced_get} - "
                    #                f"{max_value_at_forced_get}, retrying.")
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
                ):
                    if (
                        min_value_after_forced_get
                        <= result
                        <= max_value_after_forced_get
                    ):
                        self.__log(f"after timed out relevant value was {result}.")
                        break

                self.__log("timed out, no relevant value was retrieved.")
                # This does not always trigger a connection exception, but we can assume the
                # connection is down. This normally would result in ModbusExceptions earlier
                # and these would normally trigger __handle_un_recoverable_error already.
                await self.__handle_un_recoverable_error(
                    reason="timeout", source="__force_get_register"
                )
                return None

            await asyncio.sleep(delay_between_reads)
            continue
        # End of while loop

        await self.__update_charger_connection_state(is_alive=True)
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)
        return result

    async def __modbus_write(self, address: int, value: int, source: str) -> bool:
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
            self.__log("called while _am_i_active == False. Not blocking.")

        if value < 0:
            # Modbus cannot handle negative values directly.
            value = self.MAX_USI + value

        result = None
        try:
            # I hate using the word 'slave', this should be 'server' but
            # pyModbus has not changed this yet...
            result = await self.client.write_register(
                address=address,
                value=value,
                slave=1,
            )
        except ModbusException as me:
            self.__log(f"ModbusException {me}", level="WARNING")
            is_unrecoverable = await self.__handle_modbus_exception(
                source="__modbus_write"
            )
            if is_unrecoverable:
                return
        else:
            await self.__reset_modbus_exception()

        if result is None:
            self.__log("Failed to write to modbus server.")
        # Sleep for a while to create time between writes does not work as this is async..
        return result

    async def __modbus_read(
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
            # I hate using the word 'slave', this should be 'server' but pyModbus
            # has not changed this yet...
            result = await self.client.read_holding_registers(
                address=address,
                count=length,
                slave=1,
            )
        except ModbusException as me:
            self.__log(f"ModbusException {me}", level="WARNING")
            is_unrecoverable = await self.__handle_modbus_exception(
                source="__modbus_read"
            )
            if is_unrecoverable:
                return None
        else:
            await self.__reset_modbus_exception()

        if result is None:
            self.__log(f"result is None for address '{address}' and length '{length}'.")
            return None
        return list(map(self.__get_2comp, result.registers))

    async def __handle_bad_modbus_config(self):
        """Function to call when no connection with the modbus server could be made.
        This is only expected at startup.
        A persistent notification will be set pointing out that the configuration might not be ok.
        Polling is canceled as this is pointless without a connection.
        """

        self.v2g_globals.create_persistent_notification(
            title="Error in charger configuration",
            message="Please check if charger is powered, has IP connection and "
            "if Host/Port are correct in configuration.",
            id="no_comm_with_evse",
        )
        await self.__cancel_polling(reason="no modbus connection")

    async def __handle_charger_error_state_change(self, kwargs):
        """Handle errors reported by the charger.
        To be called when:
        - When the charger state changes to or from one of the ERROR_STATES
          Then the new_charger_state is added to the call
        - Any of the error entities ENTITY_ERROR_1..4 change
          Then the new_charger_state is not in the call.
        - After a MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS delay this method calls
          itself with is_final_check = true

        The parameters in the kwargs
        - new_charger_state: used when called due to changed charger_state (was or is error)
        - is_final_check: If True then it is time to call __handle_unrecoverable_error
        :return: nothing
        These are not normal parameters otherwise the run_in cannot be used.
        """
        new_charger_state = kwargs.get("new_charger_state", None)
        is_final_check = kwargs.get("is_final_check", False)
        self.__log(f"{new_charger_state=}, {is_final_check=}")
        has_error = False

        if new_charger_state is None:
            new_charger_state = await self.__get_charger_state()
            self.__log(
                f"Called without charger state, __get_charger_state: {new_charger_state}."
            )

        if new_charger_state in self.ERROR_STATES:
            self.__log("Charger in error state", level="WARNING")
            has_error = True

        for entity in self.CHARGER_ERROR_ENTITIES:
            # None = uninitialised, 0 = no error.
            if entity["current_value"] not in [None, 0]:
                self.__log(
                    f"Charger reports {entity['ha_entity_name']} "
                    f"is {entity['current_value']}",
                    level="WARNING",
                )
                has_error = True

        if has_error:
            if is_final_check:
                await self.__handle_un_recoverable_error(reason="charger reports error")
            elif self.timer_id_check_error_state is None:
                self.timer_id_check_error_state = await self.hass.run_in(
                    self.__handle_charger_error_state_change,
                    delay=self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
                    new_charger_state=None,
                    is_final_check=True,
                )
                return
        else:
            self.__cancel_timer(self.timer_id_check_error_state)
            self.timer_id_check_error_state = None

    async def __handle_modbus_exception(self, source):
        """Modbus (connection) exception occurs regularly with the Wallbox Quasar (e.g. bi-weekly)
        and is usually not self resolving.
        This method checks the severity of the connection problem and notifies the user if needed.

        This method is to be called from __modbus_read and __modbus_write methods.
        Connection exceptions occurs on client.read() and client.write() instead of, as you would
        expect, on client.connect().

        :param source: Only for logging
        :return: Is the exception persistent for longer than the set timeout.
        """
        self.__log("called")
        is_unrecoverable = False
        # The counter is initiated at None.
        # At first successful modbus call this counter is set to 0 by __reset_modbus_exception.
        # Until then do not treat the exception as a problem and do not increment the counter.
        # Most likely the app is still initialising or user is still busy configuring.
        if self.modbus_exception_counter is None:
            self.__log(f"{source}: modbus exception. Configuration (not yet) invalid?")
            await self.__handle_bad_modbus_config()
            is_unrecoverable = False

        # So, there is an exception after initialisation, this still could self recover.
        # We'll wait self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS, until then consider it
        # recoverable.
        if self.modbus_exception_counter == 0:
            self.__log(f"{source}: First modbus exception.")
            self.timer_id_check_modus_exception_state = self.hass.run_in(
                self.__handle_un_recoverable_error,
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

    async def __reset_modbus_exception(self):
        """Reset modbus_exception_counter and cancel timer_id_check_modus_exception_state
        and set the connection status in the UI to is_alive=True
        Works in conjunction with __handle_modbus_exception.
        To be called every time there has been a successful modbus read/write.
        :return: Nothing
        """
        # self.__log("called")
        if self.modbus_exception_counter == 1:
            self.__log("There was an modbus exception, now solved.")
            self.v2g_main_app.reset_charger_communication_fault()
        self.modbus_exception_counter = 0
        self.__cancel_timer(self.timer_id_check_modus_exception_state)
        self.timer_id_check_modus_exception_state = None
        await self.__update_charger_connection_state(is_alive=True)

    async def __handle_un_recoverable_error(
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
        self.__log(f"{source=}, {reason=}.")

        # This method could be called from two timers. Make sure both are canceled so no double
        # notifications get sent.
        self.__cancel_timer(self.timer_id_check_modus_exception_state)
        self.__cancel_timer(self.timer_id_check_error_state)

        await self.__cancel_polling(reason="un_recoverable charger error")
        # The only exception to the rule that _am_i_active should only be set from set_(in)active().
        self._am_i_active = False
        await self.v2g_main_app.handle_none_responsive_charger(
            was_car_connected=await self.is_car_connected()
        )
        await self.__update_charger_connection_state(is_alive=False)

        # The soc and power are not known any more so let's represent this in the app
        await self.__update_ha_and_evse_entity(
            evse_entity=self.ENTITY_CHARGER_CURRENT_POWER, new_value="unavailable"
        )
        await self.__update_ha_and_evse_entity(
            evse_entity=self.ENTITY_CAR_SOC, new_value="unavailable"
        )
        await self.__update_ha_entity(
            entity_id="binary_sensor.is_car_connected",
            new_value="unavailable",
        )

    def __cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if timer_id in [None, ""]:
            return
        if self.hass.timer_running(timer_id):
            silent = True  # Does not really work
            self.hass.cancel_timer(timer_id, silent)

    def __get_2comp(self, number):
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
