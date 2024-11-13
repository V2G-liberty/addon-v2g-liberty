from datetime import datetime
import asyncio
import constants as c
from v2g_globals import get_local_now
import pymodbus.client as modbusClient
from pymodbus.exceptions import ModbusException, ModbusIOException, ConnectionException

from appdaemon.plugins.hass.hassapi import Hass


class ModbusEVSEclient:
    """This class communicates with the EVSE via modbus.
    In V2G Liberty this is to be the only class to communicate with the EVSE.
    It does this mainly by polling the EVSE for states and values in an
    asynchronous way, as the charger might not always react instantly.

    Values of the EVSE (like charger status or car SoC) are then written to
    Home Assistant entities for other modules to use / subscribe to.

    This class should not interact with (react to) the UI or other AppDaemon apps directly.
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

    ######################################################################
    # Entities reading+validating modbus value and writing to HA entity  #
    ######################################################################
    CHARGER_POLLING_ENTITIES: list

    ENTITY_CHARGER_CURRENT_POWER = {
        "modbus_address": 526,
        "minimum_value": -7400,
        "maximum_value": 7400,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "charger_real_charging_power",
    }
    ENTITY_CHARGER_STATE = {
        "modbus_address": 537,
        "minimum_value": 0,
        "maximum_value": 11,
        "current_value": None,
        "previous_value": None,
        "change_handler": "__handle_charger_state_change",
        "ha_entity_name": "charger_charger_state",
    }
    ENTITY_CAR_SOC = {
        "modbus_address": 538,
        "minimum_value": 2,
        "maximum_value": 100,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "charger_connected_car_state_of_charge",
    }
    # About the minimum value of 2%:
    #  - 0% represents "unknown" and is very unlikely to be real value, so it is ignored.
    #  - The Quasar sometimes returns 1% while the true value is (much) higher resulting in strange spikes in graph and
    #    charge behaviour. As 1% will seldom be the real value, we chose to set the minimum value to 2%.
    #    We realise this has a drawback: when returning with 1% the only way to charge is to set it to "Max charge now".

    ENTITY_ERROR_1 = {
        "modbus_address": 539,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "unrecoverable_errors_register_high",
    }
    ENTITY_ERROR_2 = {
        "modbus_address": 540,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "unrecoverable_errors_register_low",
    }
    ENTITY_ERROR_3 = {
        "modbus_address": 541,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "recoverable_errors_register_high",
    }
    ENTITY_ERROR_4 = {
        "modbus_address": 542,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "recoverable_errors_register_low",
    }

    ENTITY_CHARGER_LOCKED = {
        "modbus_address": 256,
        "minimum_value": 0,
        "maximum_value": 1,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "charger_locked",
    }

    # To be read once at init
    CHARGER_INFO_ENTITIES: list

    ENTITY_FIRMWARE_VERSION = {
        "modbus_address": 1,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "firmware_version",
    }

    ENTITY_SERIAL_NUMBER_HIGH = {
        "modbus_address": 2,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "serial_number_high",
    }

    ENTITY_SERIAL_NUMBER_LOW = {
        "modbus_address": 3,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "previous_value": None,
        "ha_entity_name": "serial_number_low",
    }

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
    CMIT: int = 1800  # Timeout in seconds. Half an hour is long, polling communicates every 5 or 15 seconds.

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
    # 2: Connected and waiting for car demand; sometimes shortly goes to this status when action = start
    # 3: Connected and waiting for next schedule; this occurs when a charging session is scheduled via the Wallbox app.
    #    As we control the charger we override this setting
    # 4: Connected and paused by user; goes to this status when action = stop or when gun is connected and auto start = disabled
    # 7: In error; the charger sometimes returns error first minutes after restart
    # 10: Connected and in queue by Power Boost
    # 11: Connected and discharging. This status is reached when the power or current setting is set to a negative value and the action = start
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

    DISCONNECTED_STATE: int = 0
    CHARGING_STATE: int = 1
    DISCHARGING_STATE: int = 11
    AVAILABILITY_STATES = [1, 2, 4, 5, 11]

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
    connection_failure_counter: int = -1
    dtm_connection_failure_since: datetime
    MAX_CONNECTION_FAILURE_DURATION_IN_SECONDS: int = 300

    # For (un)blocking of calls and keeping the client in-active when it should
    # Set only(!) by set_inactive and set_active.
    _am_i_active: bool = None

    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass

    async def initialize(self):
        self.hass.log("Initializing ModbusEVSEclient")
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

        await self.initialise_charger("initialize")

        self.hass.log("Completed Initializing ModbusEVSEclient")

    ######################################################################
    #                     PUBLIC FUNCTIONAL METHODS                      #
    ######################################################################

    async def initialise_charger(self, v2g_args=None):
        self.hass.log(
            f"Configuring Modbus EVSE client at {c.CHARGER_HOST_URL}:{c.CHARGER_PORT}, reason: {v2g_args}"
        )

        # Remove old client if needed.
        if self.client is not None:
            if self.client.connected:
                self.client.close()

        self.client = modbusClient.AsyncModbusTcpClient(
            host=c.CHARGER_HOST_URL,
            port=c.CHARGER_PORT,
            retry_on_empty=True,
        )
        await self.client.connect()
        self.connection_failure_counter = 0
        if self.client.connected:
            await self.__process_min_max_charge_power()
            return True
        else:
            return False

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self.hass.log(
                "stop_charging called while _am_i_active == False. Not blocking call to make stop reliable."
            )

        await self.__set_charger_action("stop", reason="stop_charging")
        await self.__set_charge_power(charge_power=0, source="stop_charging")

    async def start_charge_with_power(self, kwargs: dict, *args, **fnc_kwargs):
        """Function to start a charge session with a given power in Watt.
            To be called from v2g-liberty module.

        Args:
            kwargs (dict):
                kwargs should contain a "charge_power" key with a value in Watt.
                kwargs can contain a "source" for debugging.
        """
        # Check for automatic mode should be done by V2G Liberty app
        source = kwargs.get("source", "unknown source")
        if not self._am_i_active:
            self.hass.log("Not setting charge_rate: _am_i_active == False.")
            return

        if not await self.is_car_connected():
            self.hass.log("Not setting charge_rate: No car connected.")
            return

        charge_power_in_watt = int(float(kwargs["charge_power"]))
        await self.__set_charger_control("take")
        if charge_power_in_watt == 0:
            await self.__set_charger_action(
                action="stop",
                reason=f"start_charge_with_power called from {source} with power = 0",
            )
        else:
            await self.__set_charger_action(
                action="start",
                reason=f"start_charge_with_power called from {source} with {charge_power_in_watt=}",
            )
        await self.__set_charge_power(
            charge_power=charge_power_in_watt,
            source=f"{source} => start_charge_with_power",
        )

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop"""
        self.hass.log("evse: set_inactive called")
        await self.stop_charging()
        await self.__set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        self.hass.log("evse: set_active called")
        self._am_i_active = True
        await self.__set_charger_control("take")
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self.__get_car_soc()
        await self.__set_poll_strategy()

    def is_available_for_automated_charging(self) -> bool:
        """Whether the car and EVSE are available for automated charging.
        To simplify things for the caller, this is implemented as a synchronous function.
        This means the state is retrieved from HA instead of the charger and as a result
        can be as old as the maximum polling interval.
        """
        if not self._am_i_active:
            self.hass.log(
                "is_available_for_automated_charging called while _am_i_active == False. Returning False."
            )
            return False

        # The method self.__get_charger_state() cannot be used as it is async and this
        # method should not be as it is called from sync code (data_monitor.py).
        return self.ENTITY_CHARGER_STATE["current_value"] in self.AVAILABILITY_STATES

    async def is_car_connected(self) -> bool:
        """Indicates if currently a car is connected to the charger."""
        if not self._am_i_active:
            self.hass.log(
                "is_car_connected called while _am_i_active == False. Not blocking."
            )

        is_connected = await self.__get_charger_state() != self.DISCONNECTED_STATE
        self.hass.log(f"is_car_connected called, returning: {is_connected}")
        return is_connected

    async def is_charging(self) -> bool:
        """Indicates if currently the connected car is charging (not discharging)"""
        if not self._am_i_active:
            self.hass.log(
                "is_charging called while _am_i_active == False. Not blocking."
            )

        return await self.__get_charger_state() == self.CHARGING_STATE

    async def is_discharging(self) -> bool:
        """Indicates if currently the connected car is discharging (not charging)"""
        if not self._am_i_active:
            self.hass.log(
                "is_discharging called while _am_i_active == False. Not blocking."
            )

        return await self.__get_charger_state() == self.DISCHARGING_STATE

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################

    async def complete_init(self):
        """This public function is to be called from v2g-liberty once after its own init is complete.
        This timing is essential as the following code possibly needs v2g-liberty for notifications etc.
        """

        # So the status page can show if communication with charge is ok.
        for entity in self.CHARGER_INFO_ENTITIES:
            # Reset values
            entity_name = f"sensor.{entity['ha_entity_name']}"
            await self.__update_state(entity_id=entity_name, state="unknown")
        await self.__get_and_process_registers(self.CHARGER_INFO_ENTITIES)

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)

        # SoC is essential for many decisions, so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first
        # opportunity to do a poll.
        await self.__get_car_soc()

    async def __process_min_max_charge_power(self):
        """Reads the maximum charge power setting from the charger."""
        self.hass.log("__get_min_max_charge_power")
        if not self._am_i_active:
            self.hass.log(
                "__process_min_max_charge_power called while _am_i_active == False. Not blocking."
            )

        max_available_power_by_charger = await self.__force_get_register(
            register=self.MAX_AVAILABLE_POWER_REGISTER,
            min_value=self.CHARGE_POWER_LOWER_LIMIT,
            max_value=self.CHARGE_POWER_UPPER_LIMIT,
        )
        await self.v2g_globals.process_max_power_settings(
            min_acceptable_charge_power=self.CHARGE_POWER_LOWER_LIMIT,
            max_available_charge_power=max_available_power_by_charger,
        )

    async def __set_charger_control(self, take_or_give_control: str):
        """Set charger control: take control from the user or give control back to the user (the EVSE app).

        This is a private function. The V2G Liberty module should use the function set_active() and set_inactive().

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
            self.hass.log(
                "__set_charger_control called while _am_i_active == False. Not blocking."
            )

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

    async def __handle_charger_state_change(self):
        """
        Called when __update_entity detects a changed value.
        Has a sister function in V2G Liberty that reacts to HA entity change.
        """
        self.hass.log("__handle_charger_state_change called")
        if not self._am_i_active:
            self.hass.log(
                "__handle_charger_state_change called while _am_i_active == False. Not blocking."
            )

        new_charger_state = await self.__get_charger_state()

        # TODO: Move setting the charger_state_text to v2g-liberty.py?
        # YES, because:
        # + It is only for the UI, the functional names are not per se related to a charger (type).
        # + this is the only place where self.CHARGER_STATES is used!
        # NO, because:
        # + self.try_get_new_soc_in_process is not available there...

        # Also make a text version of the state available in the UI
        charger_state_text = self.CHARGER_STATES[new_charger_state]
        if charger_state_text is not None and not self.try_get_new_soc_in_process:
            # self.try_get_new_soc_in_process should not happen as polling is stopped, just to be safe...
            await self.__update_state(
                entity_id="input_text.charger_state", state=charger_state_text
            )
            self.hass.log(
                f"__handle_charger_state_change, set state in text for UI = {charger_state_text}."
            )

        if new_charger_state == self.DISCONNECTED_STATE:
            # **** Handle disconnect
            # Goes to this status when the plug is removed from the car-socket,
            # not when disconnect is requested from the UI.
            # To prevent the charger from auto-start charging after the car gets connected again,
            # explicitly send a stop-charging command:
            await self.__set_charger_action(
                "stop", reason="__handle_charge_state_change: disconnected"
            )
            await self.__set_poll_strategy()
        elif self.ENTITY_CHARGER_STATE["previous_value"] == self.DISCONNECTED_STATE:
            # new_charger_state must be a connected state, so if the old state was disconnected
            # **** Handle connected
            self.hass.log("From disconnected to connected: try to refresh the SoC")
            await self.__get_car_soc(do_not_use_cache=True)
            await self.__set_poll_strategy()
        else:
            # Not a change that this method needs to react upon.
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
            self.hass.log(
                "__set_charger_action called while _am_i_active == False. Not blocking."
            )

        action_value = ""
        if not await self.is_car_connected():
            self.hass.log(
                f"Not performing charger action '{action}': No car connected.{reason}"
            )
            return False

        if action == "start":
            if await self.__is_charging_or_discharging():
                self.hass.log(
                    f"Not performing charger action 'start': already charging.{reason}"
                )
                return True
            action_value = self.ACTIONS["start_charging"]
        elif action == "stop":
            # Stop needs to be very reliable, so we always perform this action, even if currently not charging.
            action_value = self.ACTIONS["stop_charging"]
        else:
            # Restart not implemented
            raise ValueError(f"Unknown option for action: '{action}'.{reason}")
        txt = f"set_charger_action: {action}"
        await self.__modbus_write(
            address=self.SET_ACTION_REGISTER, value=action_value, source=txt
        )
        self.hass.log(f"{txt}{reason}")
        return

    async def __is_charging_or_discharging(self) -> bool:
        if not self._am_i_active:
            self.hass.log(
                "__is_charging_or_discharging called while _am_i_active == False. Not blocking."
            )

        state = await self.__get_charger_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self.hass.log(
                f"__is_charging_or_discharging, charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self.hass.log(
            f"__is_charging_or_discharging, state: {state} ({self.CHARGER_STATES[state]}), "
            f"charging: {is_charging}."
        )
        return is_charging

    async def __get_car_soc(self, do_not_use_cache: bool = False) -> int:
        """Checks if a SoC value is new enough to return directly or if it should be updated first.

        :param do_not_use_cache (bool): This forces the method to get the soc from the car and bypass any cached value.

        :return (int): SoC value from 2 to 100 (%)
                       If the car is disconnected a 0 value is returned, representing "unknown".
        """
        # self.hass.log("__get_car_soc called")
        if not self._am_i_active:
            self.hass.log(
                "__get_car_soc called while _am_i_active == False. Not blocking."
            )

        if not await self.is_car_connected():
            self.hass.log("__get_car_soc called, no car connected, returning SoC = 0")
            return 0

        ecs = self.ENTITY_CAR_SOC
        state = ecs["current_value"]
        should_be_renewed = False
        if state is None or state == 0:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            self.hass.log(
                "__get_car_soc: current_value is None or 0 so should_be_renewed = True"
            )
            should_be_renewed = True

        if do_not_use_cache:
            # Needed usually only when car has been disconnected. The polling then does not read SoC and this probably
            # changed and polling might not have picked this up yet.
            self.hass.log(
                "__get_car_soc: do_not_use_cache == True so should_be_renewed = True"
            )
            should_be_renewed = True

        if should_be_renewed:
            self.hass.log("__get_car_soc: old or invalid SoC in HA Entity: renew")
            soc_address = ecs["modbus_address"]
            MIN_EXPECTED_SOC_PERCENT = ecs["minimum_value"]
            MAX_EXPECTED_SOC_PERCENT = ecs["maximum_value"]
            entity_name = f"sensor.{ecs['ha_entity_name']}"
            if await self.__is_charging_or_discharging():
                self.hass.log("__get_car_soc: is (dis)charging")
                soc_in_charger = await self.__force_get_register(
                    register=soc_address,
                    min_value=MIN_EXPECTED_SOC_PERCENT,
                    max_value=MAX_EXPECTED_SOC_PERCENT,
                )
                await self.__update_state(entity_id=entity_name, state=soc_in_charger)
            else:
                self.hass.log(
                    "__get_car_soc: starting a charge and reading the soc until a valid value is returned."
                )
                # Not charging so reading a SoC will return a false 0-value. To resolve this start charging
                # (with minimum power) then read a SoC and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading, polling is paused.
                self.try_get_new_soc_in_process = (
                    True  # Prevent polling to start again from elsewhere.
                )
                self.hass.log(f"__get_car_soc, try_get_new_soc_in_process set to True")
                await self.__cancel_polling(reason="try get new soc")
                # Make sure charging with 1W starts so a SoC can be read.
                await self.__set_charger_control("take")
                await self.__set_charge_power(
                    charge_power=1, skip_min_soc_check=True, source="get_car_soc"
                )
                await self.__set_charger_action("start", reason="try_get_new_soc")
                # Reading the actual SoC
                soc_in_charger = await self.__force_get_register(
                    register=soc_address,
                    min_value=MIN_EXPECTED_SOC_PERCENT,
                    max_value=MAX_EXPECTED_SOC_PERCENT,
                )
                # Setting things back to normal
                await self.__set_charge_power(
                    charge_power=0, skip_min_soc_check=True, source="get_car_soc"
                )  # This also sets action to stop
                await self.__set_charger_action("stop", reason="try_get_new_soc")

                await self.__update_entity(entity=ecs, value=soc_in_charger)
                await self.__update_state(
                    entity_id=entity_name, state=soc_in_charger
                )  # Do before restart polling
                self.try_get_new_soc_in_process = False
                self.hass.log(f"__get_car_soc, try_get_new_soc_in_process set to False")
                await self.__set_poll_strategy()
            state = soc_in_charger

        self.hass.log(f"__get_car_soc returning: '{state}'.")
        return state

    async def __get_charger_state(self) -> int:
        self.hass.log("__get_charger_state")
        if not self._am_i_active:
            self.hass.log(
                "__get_charger_state called while _am_i_active == False. Not blocking."
            )

        charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self.__get_and_process_registers([self.ENTITY_CHARGER_STATE])
            charger_state = self.ENTITY_CHARGER_STATE["current_value"]

        return charger_state

    async def __get_charge_power(self) -> int:
        if not self._am_i_active:
            self.hass.log(
                "__get_charge_power called while _am_i_active == False. Not blocking."
            )

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
            self.hass.log(
                f"__get_and_process_registers: results is None, abort processing."
            )
            return
        for entity in entities:
            entity_name = f"sensor.{entity['ha_entity_name']}"
            register_index = entity["modbus_address"] - start
            new_state = results[register_index]
            if new_state is None:
                self.hass.log(
                    f"__get_and_process_registers: New value 'None' for entity '{entity_name}' ignored."
                )
                continue

            try:
                new_state = int(float(new_state))
            except:
                self.hass.log(
                    f"__get_and_process_registers: New value '{new_state}' for entity '{entity_name}', "
                    f"not type == int : ignored."
                )
                continue
            if not entity["minimum_value"] <= new_state <= entity["maximum_value"]:
                # self.hass.log(f"__get_and_process_registers: New value '{new_state}' for entity '{entity_name}' out of range {entity['minimum_value']} - {entity['maximum_value']} ignored.")
                continue

            await self.__update_entity(entity=entity, value=new_state)
            await self.__update_state(entity_id=entity_name, state=new_state)
        return

    async def __update_entity(self, entity: dict, value):
        # self.hass.log(f"__update_entity called for {entity['ha_entity_name']} with value '{value}'.")
        current_value = entity["current_value"]
        if current_value != value:
            entity["current_value"] = value
            entity["previous_value"] = current_value
            # call handler if defined
            if "change_handler" in entity.keys():
                str_action = entity["change_handler"]
                # TODO: Find an more elegant way (without 'eval') to do this...
                if str_action == "__handle_charger_state_change":
                    await self.__handle_charger_state_change()
                else:
                    self.hass.log(f"__update_entity unknown action: '{str_action}'.")

    async def __update_state(self, entity_id, state=None, attributes=None):
        """Generic function for updating the state of an entity in Home Assistant
            If it does not exist, create it.
            If it has attributes, keep them (if not overwrite with empty)

        Args:
            entity_id (str): full entity_id incl. type, e.g. sensor.charger_state
            state (any, optional): The value the entity should be written with. Defaults to None.
            attributes (any, optional): The value (can be dict) the attributes should be written
            with. Defaults to None.
        """
        if state is None:
            self.hass.log("__update_state called with state is None, aborting.")
            return

        new_attributes = None
        if self.hass.entity_exists(entity_id):
            current_attributes = await self.hass.get_state(entity_id, attribute="all")
            if current_attributes is not None:
                new_attributes = current_attributes["attributes"]
                if attributes is not None:
                    new_attributes.update(attributes)
        else:
            new_attributes = attributes

        if new_attributes is not None:
            await self.hass.set_state(entity_id, state=state, attributes=new_attributes)
        else:
            await self.hass.set_state(entity_id, state=state)

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
        self.hass.log(
            f"__set_charge_power called from {source=}, while {self._am_i_active=}. Not blocking."
        )

        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self.__get_car_soc()
            if current_soc <= c.CAR_MIN_SOC_IN_PERCENT:
                # Fail-safe, this should never happen...
                self.hass.log(
                    f"A discharge is attempted from {source=}, while the current SoC is below the "
                    f"minimum ({c.CAR_MIN_SOC_IN_PERCENT})%. Stopping discharging."
                )
                charge_power = 0

        # Clip values to min/max charging current
        if charge_power > c.CHARGER_MAX_CHARGE_POWER:
            self.hass.log(f"Requested charge power {charge_power} Watt too high.")
            charge_power = c.CHARGER_MAX_CHARGE_POWER
        elif abs(charge_power) > c.CHARGER_MAX_DISCHARGE_POWER:
            self.hass.log(f"Requested discharge power {charge_power} Watt too high.")
            charge_power = -c.CHARGER_MAX_DISCHARGE_POWER

        current_charge_power = await self.__get_charge_power()

        if current_charge_power == charge_power:
            self.hass.log(
                f"New-charge-power-setting from {source=} is same as current-charge-power-setting: {charge_power} "
                f"Watt. Not writing to charger."
            )
            return

        res = await self.__modbus_write(
            address=self.CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source=f"set_charge_power, from {source}",
        )

        if not res:
            self.hass.log(f"Failed to set charge power to {charge_power} Watt.")
            # If negative value result in false, check if grid code is set correct in charger.
        return

    ######################################################################
    #                   POLLING RELATED FUNCTIONS                        #
    ######################################################################

    async def __update_poll_indicator_in_ui(self, reset: bool = False):
        # Toggles the char in the UI to indicate polling activity,
        # as the "last_changed" attribute also changes, an "age" could be shown based on this as well.
        self.poll_update_text = "↺" if self.poll_update_text != "↺" else "↻"
        if reset:
            self.poll_update_text = ""
        await self.__update_state(
            entity_id="input_text.poll_refresh_indicator", state=self.poll_update_text
        )

    async def __set_poll_strategy(self):
        """Poll strategy:
        Should only be called if connection state has really changed.
        Minimal: Car is disconnected, poll for just the charger state every 15 seconds.
        Base: Car is connected, poll for all info every 5 seconds
        When Charge mode is off, is handled by handle_charge_mode
        """
        if not self._am_i_active:
            self.hass.log(
                "__set_poll_strategy called while _am_i_active == False. Not blocking."
            )

        if self.try_get_new_soc_in_process:
            # At the end of the process of (forcefully) getting a soc this function is called (again).
            return

        await self.__cancel_polling(reason="setting new polling strategy")

        charger_state = await self.__get_charger_state()
        self.hass.log(
            f"Deciding polling strategy based on state: {self.CHARGER_STATES[charger_state]}."
        )
        if charger_state == self.DISCONNECTED_STATE:
            self.hass.log(
                "Minimal polling strategy (lower freq., charger_state register only.)"
            )
            self.poll_timer_handle = await self.hass.run_every(
                self.__minimal_polling, "now", self.MINIMAL_POLLING_INTERVAL_SECONDS
            )
        else:
            self.hass.log("Base polling strategy (higher freq., all registers).")
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
            self.hass.log(
                "__cancel_polling called while _am_i_active == False. Not blocking."
            )

        self.hass.log(f"__cancel_polling, reason: {reason}")
        if self.hass.timer_running(self.poll_timer_handle):
            await self.hass.cancel_timer(self.poll_timer_handle, True)
            # To be really sure...
            self.poll_timer_handle = None
        else:
            self.hass.log("__cancel_polling: No timer to cancel")
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

    async def __update_charger_connection_state(self, is_alive: bool = True):
        keep_alive = {"keep_alive": get_local_now()}
        msg = "Successfully connected" if is_alive else "Connection error"
        await self.__update_state(
            entity_id="input_text.charger_connection_status",
            state=msg,
            attributes=keep_alive,
        )

    async def __force_get_register(
        self, register: int, min_value: int, max_value: int
    ) -> int:
        """When a 'realtime' reading from the modbus server is needed that is expected
        to be between min_value/max_value. We keep trying to get a reading that is within min_value - max_value range.

        Of course there is a timeout. If this is reached we expect the modbus server to have crashed,
        and we notify the user.

        """
        if not self._am_i_active:
            self.hass.log(
                "__force_get_register called while _am_i_active == False. Not blocking."
            )

        # Times in seconds
        total_time = 0
        MAX_TOTAL_TIME = 120
        DELAY_BETWEEN_READS = 0.25

        # If the real SoC is not available yet, keep trying for max. two minutes
        while True:
            result = None
            try:
                # Only one register is read so count = 1, the charger expects slave to be 1.
                # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
                result = await self.client.read_holding_registers(
                    register, count=1, slave=1
                )
            except (ConnectionException, ModbusIOException) as exc:
                is_unrecoverable = await self.__handle_modbus_connection_exception(
                    exc, source="__force_get_register"
                )
                if is_unrecoverable:
                    break
            except ModbusException as me:
                self.hass.log(
                    f"__force_get_register, Received ModbusException '{me}' from library"
                )
                pass

            if result is not None:
                try:
                    result = self.__get_2comp(result.registers[0])
                    if min_value <= result <= max_value:
                        # Acceptable result retrieved
                        self.hass.log(
                            f"__force_get_register. After {total_time} sec. value {result} was retrieved."
                        )
                        break
                    else:
                        self.hass.log(
                            f"__force_get_register. Value '{result}' not valid, retrying."
                        )
                except TypeError:
                    pass
            total_time += DELAY_BETWEEN_READS

            # We need to stop at some point
            if total_time > MAX_TOTAL_TIME:
                self.hass.log(
                    f"__force_get_register timed out. After {total_time} sec. no relevant value was retrieved."
                )
                # This does not always trigger a connection exception, but we can assume the connection is down.
                await self.__modbus_un_recoverable_error(
                    reason="timeout", source="__force_get_register"
                )
                break

            await asyncio.sleep(DELAY_BETWEEN_READS)
            # self.hass.log(f"__force_get_register, waited {total_time} seconds so far.")
            continue
        # End of while loop

        await self.__update_charger_connection_state()
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)
        return result

    async def __modbus_write(self, address: int, value: int, source: str) -> bool:
        """Generic modbus write function.
           Writing to the modbus server should exclusively be done through this function

        Args:
            address (int): the register / address to write to
            value (int): the value to write
            source (str): only for debugging

        Raises:
            exc: Modbus exception

        Returns:
            bool: if write was successful
        """

        if not self._am_i_active:
            self.hass.log(
                "__modbus_write called while _am_i_active == False. Not blocking."
            )

        if value < 0:
            # Modbus cannot handle negative values directly.
            value = self.MAX_USI + value

        is_unrecoverable = None
        result = None
        try:
            # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
            result = await self.client.write_register(address, value, slave=1)
        except (ConnectionException, ModbusIOException) as exc:
            is_unrecoverable = await self.__handle_modbus_connection_exception(
                exc, "__modbus_write"
            )
            if is_unrecoverable:
                return
        except ModbusException as me:
            self.hass.log(
                f"__modbus_write, Received ModbusException({me}) from library"
            )
            raise me

        if is_unrecoverable is None:
            # No connection exception, thus a successful read, and thus communication.
            await self.__reset_modbus_connection_exception()

        if result is None:
            self.hass.log(f"__modbus_write, Failed to write to modbus server.")

        await self.__update_charger_connection_state()
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_WRITE_IN_MS / 1000)
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

        is_unrecoverable = None
        result = None
        try:
            # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
            result = await self.client.read_holding_registers(address, length, slave=1)
        except (ConnectionException, ModbusIOException) as exc:
            self.hass.log(
                f"__modbus_read, Received ConnectionException, ModbusIOException from library"
            )
            is_unrecoverable = await self.__handle_modbus_connection_exception(
                exc, "__modbus_read"
            )
            if is_unrecoverable:
                return
        except ModbusException as me:
            self.hass.log(f"__modbus_read, Received ModbusException({me}) from library")
            raise me

        if is_unrecoverable is None:
            # No connection exception, thus a successful read, and thus communication.
            await self.__reset_modbus_connection_exception()

        if result is None:
            self.hass.log(
                f"__modbus_read: result is None for address '{address}' and length '{length}'."
            )
            return

        await self.__update_charger_connection_state()
        await asyncio.sleep(self.WAIT_AFTER_MODBUS_READ_IN_MS / 1000)

        return list(map(self.__get_2comp, result.registers))

    async def __reset_modbus_connection_exception(self):
        # Works in conjunction with __handle_modbus_connection_exception.
        # To be called when there has been a successful read/write.
        # self.hass.log("__reset_modbus_connection_exception called.")
        if self.connection_failure_counter > 0:
            self.hass.log(
                f"__reset_modbus_connection_exception, there was an charger_communication_fault, now solved."
            )
            self.v2g_main_app.reset_charger_communication_fault()
        self.connection_failure_counter = 0
        self.dtm_connection_failure_since = None

    async def __handle_no_modbus_connection(self):
        """Function to call when no connection with the modbus server could be made.
        This is only expected at startup.
        A persistent notification will be set pointing out that the configuration might not be ok.
        Polling is canceled as this is pointless without a connection.
        """

        # TODO:
        # Should be handled in a UI-flow with steps, not in persistent notification

        self.v2g_globals.create_persistent_notification(
            title="Error in charger configuration",
            message="Please check if charger is powered, has IP connection and if Host/Port are correct in configuration.",
            id="no_comm_with_evse",
        )
        await self.__cancel_polling(reason="no modbus connection")

    async def __handle_modbus_connection_exception(self, connection_exception, source):
        # Modbus' connection exception occurs regularly with the Wallbox Quasar (e.g. bi-weekly) and
        # is usually not self resolving. This method checks the severity of the connection problem and
        # notifies the user if needed.
        #
        # This method is to be called from __modbus_read and __modbus_write methods as connection exceptions
        # occurs on client.read() and client.write() instead of -as you would expect- on client.connect().
        # This method is called from __modbus_connect but not reset.
        #
        # This variable is initiated at -1. At first successful connection this counter is set to 0.
        # Until then do not trigger this counter, as most likely the user is still busy configuring.
        self.hass.log("__handle_modbus_connection_exception called.")

        if self.connection_failure_counter < 0:
            self.hass.log(
                f"{source}: Connection exception. Configuration (not yet) invalid?"
            )
            await self.__handle_no_modbus_connection()
            return

        if self.connection_failure_counter == 0:
            self.hass.log(
                f"{source}: First occurrence of connection exception. Exception: {connection_exception}."
            )
            self.dtm_connection_failure_since = get_local_now()
        else:
            # self.connection_failure_counter > 0:
            self.hass.log(
                f"{source}: Recurring connection exception. Exception: {connection_exception}."
            )

        duration = (get_local_now() - self.dtm_connection_failure_since).total_seconds()
        if duration > self.MAX_CONNECTION_FAILURE_DURATION_IN_SECONDS:
            reason = f"Connection problems for {duration} sec."
            await self.__modbus_un_recoverable_error(
                reason=reason, source="__handle_modbus_connection_exception"
            )

        self.connection_failure_counter += 1

        return False

    async def __modbus_un_recoverable_error(
        self, reason: str = None, source: str = None
    ):
        self.hass.log(f"__modbus_un_recoverable_error | {source=}, {reason=}.")
        await self.__cancel_polling(reason="un_recoverable modbus error")
        # The only exception to the rule that _am_i_active should only be set from "set_(in)active()"
        self._am_i_active = False
        await self.__cancel_polling()
        await self.v2g_main_app.notify_user_of_charger_needs_restart(
            was_car_connected=await self.is_car_connected()
        )
        await self.__update_charger_connection_state(False)

    def __get_2comp(self, number):
        """Util function to covert a modbus read value to in with two's complement values
            into negative int numbers.

        Args:
            number: value to convert, normally int, but can be other type.

        Returns:
            int: With negative values if applicable
        """
        try:
            number = int(float(number))
        except:
            pass
        if number > self.HALF_MAX_USI:
            # This represents a negative value.
            number = number - self.MAX_USI
        return number
