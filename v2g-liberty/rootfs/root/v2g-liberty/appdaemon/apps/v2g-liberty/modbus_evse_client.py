from datetime import datetime, timedelta
import asyncio
import adbase as ad
import time
import constants as c
import pymodbus.client as modbusClient
from pymodbus import ModbusException
from pymodbus.exceptions import ConnectionException

import appdaemon.plugins.hass.hassapi as hass


class ModbusEVSEclient(hass.Hass):
    """ This class communicates with the EVSE via modbus.
        In V2G Liberty this is to be the only class to communicate with the EVSE.
        It does this mainly by polling the EVSE for states and values in an 
        asynchronous way, as the charger might not always react instantly.

        Values of the EVSE (like charger status or car SoC) are then written to
        Home Assistant entities for other modules to use / subscribe to.

        This class should not interact with (react to) the UI or other AppDaemon apps directly.

    """
     # Max value of an “unsigned short integer” 2^16, used for negative values in modbus.
    MAX_USI = 65536
    HALF_MAX_USI = MAX_USI/2

    ######################################################################
    # Entities reading+validating modbus value and writing to HA entity  #
    ######################################################################
    CHARGER_POLLING_ENTITIES:list

    ENTITY_CHARGER_CURRENT_POWER = {
        'modbus_address' : 526,
        'minimum_value': -7400,
        'maximum_value': 7400,
        'ha_entity_name': 'charger_real_charging_power'
    }
    ENTITY_CHARGER_STATE = {
        'modbus_address' : 537,
        'minimum_value': 0,
        'maximum_value': 11,
        'ha_entity_name': 'charger_charger_state'
    }
    ENTITY_CAR_SOC = {
        'modbus_address' : 538,
        'minimum_value': 2,
        'maximum_value': 100,
        'ha_entity_name': 'charger_connected_car_state_of_charge'
    } #0% is very unlikely, 1% is sometimes returned while the true value is (much) higher
    ENTITY_ERROR_1 = {
        'modbus_address' : 539,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'unrecoverable_errors_register_high'
    }
    ENTITY_ERROR_1 = {
        'modbus_address' : 539,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'unrecoverable_errors_register_high'
    }
    ENTITY_ERROR_2 = {
        'modbus_address' : 540,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'unrecoverable_errors_register_low'
    }
    ENTITY_ERROR_3 = {
        'modbus_address' : 541,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'recoverable_errors_register_high'
    }
    ENTITY_ERROR_4 = {
        'modbus_address' : 542,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'recoverable_errors_register_low'
    }

    ENTITY_CHARGER_LOCKED = {
        'modbus_address' : 256,
        'minimum_value': 0,
        'maximum_value': 1,
        'ha_entity_name': 'charger_locked'
    }

    # To be read once at init
    CHARGER_INFO_ENTITIES: list

    ENTITY_FIRMWARE_VERSION = {
        'modbus_address' : 1,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'firmware_version'
    }

    ENTITY_SERIAL_NUMBER_HIGH = {
        'modbus_address' : 2,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'serial_number_high'
    }

    ENTITY_SERIAL_NUMBER_LOW = {
        'modbus_address' : 3,
        'minimum_value': 0,
        'maximum_value': 65535,
        'ha_entity_name': 'serial_number_low'
    }



    ######################################################################
    #                 Modbus addresses for setting values                #
    ######################################################################

    # Charger can be controlled by the app = user or by code = remote (Read/Write)
    # For all other settings mentioned here to work, this setting must be remote.
    SET_CHARGER_CONTROL_REGISTER:int = 81
    CONTROL_TYPES = {
        'user': 0,
        'remote': 1
    }

    # Start charging/discharging on EV-Gun connected (Read/Write)
    # Resets to default (=enabled) when control set to user
    # Must be set to "disabled" when controlled from this code.
    CHARGER_AUTOSTART_ON_CONNECT_REGISTER:int = 82
    AUTOSTART_ON_CONNECT_SETTING = {
        'enable': 1,
        'disable': 0
    }

    # Control if charger can be set through current setting or power setting (Read/Write)
    # This software uses power only.
    SET_SETPOINT_TYPE_REGISTER:int = 83
    SETPOINT_TYPES = {
        'current': 0,
        'power': 1
    }

    # Charger setting to go to idle state if not receive modbus message within this timeout.
    # Fail-safe in case this software crashes: if timeout passes charger will stop (dis-)charging.
    CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER: int = 88
    CMIT: int = 1800 # Timeout in seconds. Half an hour is long, polling communicates every 5 or 15 seconds.

    # Charger charging can be started/stopped remote (Read/Write)
    # Not implemented: restart and update software
    SET_ACTION_REGISTER:int = 257
    ACTIONS = {
        'start_charging': 1,
        'stop_charging': 2
    }

    # For setting the desired charge power, reading the actual charging power is done
    # through ENTITY_CHARGER_CURRENT_POWER
    CHARGER_SET_CHARGE_POWER_REGISTER: int = 260

    # AC Max Charging Power (by phase) (hardware) setting in charger (Read/Write)
    # (int16) unit W, min -7400, max 7400
    # Used when set_setpoint_type = power
    MAX_CHARGE_POWER_REGISTER:int = 514

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
        0: 'No car connected',
        1: 'Charging',
        2: 'Connected: waiting for car demand',
        3: 'Connected: controlled by EVSE App',
        4: 'Connected: not charging (paused)',
        5: 'Connected: end of schedule',
        6: 'No car connected and charger locked',
        7: 'Error',
        8: 'Connected: In queue by Power Sharing',
        9: 'Error: Un-configured Power Sharing System',
        10: 'Connected: In queue by Power Boost (Home uses all available power)',
        11: 'Discharging'
    }

    DISCONNECTED_STATE: int = 0
    CHARGING_STATE: int = 1
    DISCHARGING_STATE: int = 11
    AVAILABILITY_STATES = [1, 2, 4, 5, 11]

    # Modbus variables
    client: modbusClient
    WAIT_AFTER_MODBUS_WRITE_IN_MS: int = 2500
    WAIT_AFTER_MODBUS_READ_IN_MS: int = 50
    modbus_connection_in_use: bool = False

    # For sending notifications to the user.
    v2g_main_app: object
    v2g_globals: object

    # Handle for polling_timer, needed for cancelling polling.
    poll_timer_handle: object
    BASE_POLLING_INTERVAL_SECONDS: int = 5
    MINIMAL_POLLING_INTERVAL_SECONDS:int = 15
    # For indication to the user if/how fast polling is in progress
    poll_update_text: str = ""

    try_get_new_soc_in_process: bool = False

    # How old may data retrieved from HA entities be before it is renewed from the EVSE
    STATE_MAX_AGE_IN_SECONDS:int = 15


    async def initialize(self):
        self.log("Initializing ModbusEVSEclient")
        self.CHARGER_POLLING_ENTITIES = [
            self.ENTITY_CHARGER_CURRENT_POWER,
            self.ENTITY_CHARGER_STATE,
            self.ENTITY_CAR_SOC,
            self.ENTITY_ERROR_1,
            self.ENTITY_ERROR_2,
            self.ENTITY_ERROR_3,
            self.ENTITY_ERROR_4
        ]

        self.CHARGER_INFO_ENTITIES = [
            self.ENTITY_FIRMWARE_VERSION,
            self.ENTITY_SERIAL_NUMBER_HIGH,
            self.ENTITY_SERIAL_NUMBER_LOW
        ]

        self.poll_timer_handle = None

        self.v2g_main_app = await self.get_app("v2g_liberty")
        self.v2g_globals = await self.get_app("v2g-globals")

        host = self.args["wallbox_host"]
        port = self.args["wallbox_port"]
        self.log(f"Configuring Modbus EVSE client at {host}:{port}")
        self.client = modbusClient.AsyncModbusTcpClient(
            host=host,
            port=port,
            timeout=5,
            retries=6,
            reconnect_delay=1,
            reconnect_delay_max=10,
            retry_on_empty=True,
        )

        # Reacting to charge mode changes is to be done by the V2G Liberty module that calls set_(in)_active
        self.listen_state(self.__handle_charger_state_change, "sensor.charger_charger_state", attribute="all")

        self.log("Completed Initializing ModbusEVSEclient")


    ######################################################################
    #                     PUBLIC FUNCTIONAL METHODS                      #
    ######################################################################

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        await self.__set_charger_action("stop", reason="stop called externally")
        await self.__set_charge_power(charge_power = 0)


    async def start_charge_with_power(self, kwargs: dict, *args, **fnc_kwargs):
        """ Function to start a charge session with a given power in Watt.
            To be called from v2g-liberty module.

        Args:
            kwargs (dict):
                The kwargs dict should contain a "charge_power" key with a value in Watt.
        """
        # Check for automatic mode should be done by V2G Liberty app

        charge_power_in_watt = round(kwargs["charge_power"])
        if not await self.is_car_connected():
            self.log("Not setting charge_rate: No car connected.")
            return
        await self.__set_charger_control("take")
        if charge_power_in_watt == 0:
            await self.__set_charger_action("stop", reason="start_charge_with_power externally called with power = 0")
        else:
            await self.__set_charger_action("start", reason="start_charge_with_power externally called with power <> 0")

        await self.__set_charge_power(charge_power = charge_power_in_watt)


    async def set_inactive(self):
        """To be called when charge_mode in UI is switched to Stop"""
        self.log("evse: set_inactive called")
        await self.__cancel_polling(reason="Set inactive called")
        await self.__set_charger_control("give")


    async def set_active(self):
        """To be called when charge_mode in UI is switched to Automatic or Boost"""
        self.log("evse: set_active called")
        await self.__set_charger_control("take")
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self.__get_car_soc()
        await self.__set_poll_strategy()


    def is_available_for_automated_charging(self) -> bool:
        """Whether or not the car and EVSE are available for automated charging.
        To simplify things for the caller, this is implemented as a synchronous function.
        This means the state is retrieved from HA instead of the charger and as a result 
        can be as old as the maximum polling interval.
        """
        state = self.get_state("sensor.charger_charger_state")
        if state is None:
            self.log("not relevant data yet for availability...")
            return False
        state = int(float(state[0]))
        return state in self.AVAILABILITY_STATES


    async def is_car_connected(self) -> bool:
        """Indicates if currently a car is connected to the charger."""
        is_connected = await self.__get_charger_state() != self.DISCONNECTED_STATE
        self.log(f"is_car_connected called, returning: {is_connected}")
        return is_connected


    async def is_charging(self) -> bool:
        """Indicates if currently the connected car is charging (not discharging)"""
        return await self.__get_charger_state() == self.CHARGING_STATE


    async def is_discharging(self) -> bool:
        """Indicates if currently the connected car is discharging (not charging)"""
        return await self.__get_charger_state() == self.DISCHARGING_STATE


    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################


    async def complete_init(self):
        """ This public function is to be called from v2g-liberty once after its own init is complete.
        This timing is essential as the following code possibly needs v2g-liberty for notifications etc.
        """
        # See if the max charge power in setting in the charger matches the setting from the app/user
        max_charge_power = await self.__get_max_charge_power()
        if max_charge_power > 0:
            self.v2g_globals.check_max_power_settings(max_charge_power)


        # So the status page can show if communication with charge is ok.
        for entity in self.CHARGER_INFO_ENTITIES:
            #Reset values
            entity_name = f"sensor.{entity['ha_entity_name']}"
            await self.__update_state(entity=entity_name, state="unknown")
        await self.__get_and_process_registers(self.CHARGER_INFO_ENTITIES)

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)

        # SoC is essential for many decisions so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first 
        # opportunity to do a poll.
        await self.__get_car_soc()


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

        if take_or_give_control == "take":
            await self.__modbus_write(
                address = self.SET_CHARGER_CONTROL_REGISTER,
                value = self.CONTROL_TYPES['remote'],
                source="__set_charger_control, take_control"
            )
            await self.__modbus_write(
                address = self.CHARGER_AUTOSTART_ON_CONNECT_REGISTER, 
                value = self.AUTOSTART_ON_CONNECT_SETTING['disable'], 
                source="__set_charger_control, set_auto_connect"
            )
            await self.__modbus_write (
                address = self.SET_SETPOINT_TYPE_REGISTER, 
                value = self.SETPOINT_TYPES["power"], 
                source = "__set_charger_control: power"
            )
            await self.__modbus_write (
                address = self.CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER, 
                value = self.CMIT,
                source = "__set_charger_control: Modbus idle timeout"
            )

        elif take_or_give_control == "give":
            # Setting control to user automatically sets:
            # + autostart to enable
            # + set_point to Ampere
            # + idle timeout to 0 (disabled)
            await self.__set_charge_power(charge_power = 0)
            await self.__modbus_write(
                address = self.SET_CHARGER_CONTROL_REGISTER, 
                value = self.CONTROL_TYPES['user'],  
                source="__set_charger_control, give_control"
            )
        else:
            raise ValueError(f"Unknown option for take_or_give_control: {take_or_give_control}")
        return

    ######################################################################
    #                    PRIVATE CALLBACK FUNCTIONS                      #
    ######################################################################

    async def __handle_charger_state_change(self, entity, attribute, old, new, kwargs):
        """Has a sister function in V2G Liberty that also handles this from there"""
        # This should not happen as polling is stopped, just to be safe..
        self.log("__handle_charger_state_change called")
        if self.try_get_new_soc_in_process:
            self.log("__handle_charger_state_change try_get_new_soc_in_process = True, stop handling")
            return

        if new is None:
            self.log("__handle_charger_state_change, new state = None, stop handling")
            return
        new_charger_state = int(float(new["state"]))
        self.log(f"__handle_charger_state_change, new = {new_charger_state}.")

        # TODO: Move to v2g-liberty.py?
        # YES, because:
        # + It is only for the UI, the functional names are not perse related to a charger (type).
        # + this is the only place where self.CHARGER_STATES is used!
        # Also make a text version of the state available in the UI
        charger_state_text = self.CHARGER_STATES[new_charger_state]
        if charger_state_text is not None:
            await self.__update_state(entity="input_text.charger_state", state=charger_state_text)
            self.log(f"__handle_charger_state_change, set state in text for UI = {charger_state_text}.")

        if old is not None:
            old_charger_state = int(float(old["state"]))
        else:
            old_charger_state = None

        #self.log(f"Handling charger_state change new_state: {new_charger_state} ({type(new_charger_state)}), old_state: {old_state} ({type(old_state)})")
        if old_charger_state == new_charger_state:
            self.log("__handle_charger_state_change new = old, stop handling")
            return

        # **** Handle disconnect:
        # Goes to this status when the plug is removed from the socket (not when disconnect is requested from the UI)
        if new_charger_state == self.DISCONNECTED_STATE:
            #self.log(f"Connection state has changed, so check new polling strategy charger_state change new_state: {new_charger_state}, old_state: {old_state}")
            # The connected state has changed to disconnected.
            await self.__set_charger_action("stop", reason="__handle_charge_state_change: disconnected")
            await self.__set_poll_strategy()
            return

        # **** Handle connected:
        if old_charger_state == self.DISCONNECTED_STATE:
            self.log('From disconnected to connected: try to refresh the SoC')
            await self.__get_car_soc()
            await self.__set_poll_strategy()
            return

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
        action_value = ""
        if not await self.is_car_connected():
            self.log(f"Not performing charger action '{action}': No car connected.{reason}")
            return False

        if action == "start":
            if await self.__is_charging_or_discharging():
                self.log(f"Not performing charger action 'start': already charging.{reason}")
                return True
            action_value = self.ACTIONS["start_charging"]
        elif action == "stop":
            # Stop needs to be very reliable, so we always perform this action, even if currently not charging.
            action_value = self.ACTIONS["stop_charging"]
        else:
            # Restart not implemented
            raise ValueError(f"Unknown option for action: '{action}'.{reason}")
        txt = f"set_charger_action: {action}"
        await self.__modbus_write(address = self.SET_ACTION_REGISTER, value = action_value, source=txt)
        self.log(f"{txt}{reason}")
        return


    async def __is_charging_or_discharging(self) -> bool:
        state = await self.__get_charger_state()
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self.log(f"__is_charging_or_discharging, state: {state} ({self.CHARGER_STATES[state]}), charging: {is_charging}.")
        return is_charging


    async def __get_car_soc(self) -> int:
        """ Checks if a SoC value is new enough to return directly or if it should be updated first.
            Should return int values between 1 and 100 (%).
            If the car is disconnected the latest known value will be returned (and thus be old).
        """
        # self.log("__get_car_soc called")
        if not await self.is_car_connected():
            self.log("__get_car_soc called, no car connected, returning SoC = 0")
            return 0

        entity_name = f"sensor.{self.ENTITY_CAR_SOC['ha_entity_name']}"
        state = await self.get_state(entity_name, attribute="all")
        should_be_renewed = False
        if state is None or not isinstance(state, dict) or "state" not in state:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            should_be_renewed = True
        elif state['state'] in ["0", 0]:
            self.log(f", State = {state['state']} ({type(state['state'])}): renew soc from charger.")
            should_be_renewed = True
        elif "last_changed" not in state:
            should_be_renewed = True
        else:
            lc = datetime.fromisoformat(state['last_changed'])
            nu = await self.get_now()
            should_be_renewed = (nu - lc).total_seconds() > self.STATE_MAX_AGE_IN_SECONDS
        self.log(f"__get_car_soc called, SoC state in HA: {state}. Needs to be renewed: {should_be_renewed}.")

        if should_be_renewed:
            self.log("__get_car_soc: old or invalid SoC in HA Entity: renew")
            soc_address = self.ENTITY_CAR_SOC['modbus_address']
            MIN_EXPECTED_SOC_PERCENT = self.ENTITY_CAR_SOC['minimum_value']
            MAX_EXPECTED_SOC_PERCENT = self.ENTITY_CAR_SOC['maximum_value']
            if await self.__is_charging_or_discharging():
                self.log("__get_car_soc: is (dis)charging")
                soc_in_charger = await self.__force_get_register(
                    register = soc_address, 
                    min = MIN_EXPECTED_SOC_PERCENT, 
                    max = MAX_EXPECTED_SOC_PERCENT
                )
                await self.__update_state(entity=entity_name, state=soc_in_charger)
            else:
                self.log("__get_car_soc: try get new soc")
                # Not charging so getting a SoC will return 0, so start charging with minimum power
                # then get an SoC reading and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading, polling is paused.
                self.try_get_new_soc_in_process = True #Prevent polling to start again from elsewhere.
                self.log(f"__get_car_soc, try_get_new_soc_in_process set to True")
                await self.__cancel_polling(reason="try get new soc")
                # Make sure charging with 1W starts so a SoC can be read.
                await self.__set_charger_control("take")
                await self.__set_charge_power(charge_power = 1, skip_min_soc_check = True)
                await self.__set_charger_action("start", reason="try_get_new_soc")
                # Reading the actual SoC
                soc_in_charger = await self.__force_get_register(
                    register = soc_address, 
                    min = MIN_EXPECTED_SOC_PERCENT, 
                    max = MAX_EXPECTED_SOC_PERCENT
                )
                # Setting things back to normal
                await self.__set_charge_power(charge_power = 0, skip_min_soc_check = True) # This also sets action to stop
                await self.__set_charger_action("stop", reason="try_get_new_soc")
                await self.__update_state(entity=entity_name, state=soc_in_charger) # Do before restart polling
                self.try_get_new_soc_in_process = False
                self.log(f"__get_car_soc, try_get_new_soc_in_process set to False")
                await self.__set_poll_strategy()
            state = soc_in_charger
        else:
            state = int(float(state['state']))
        self.car_soc = state
        self.log(f"__get_car_soc returning: '{state}'.")
        return state

    async def __get_charger_state(self) -> int:
        #self.log("__get_charger_state")
        state = await self.get_state("sensor.charger_charger_state", attribute="all")
        should_be_renewed = False
        if state is None or not isinstance(state, dict) or "last_changed" not in state:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            should_be_renewed = True
        else:
            # Check if charger_status is not too old
            lc = datetime.fromisoformat(state['last_changed'])
            nu = await self.get_now()
            should_be_renewed = (nu - lc).total_seconds() > self.STATE_MAX_AGE_IN_SECONDS
        if should_be_renewed:
            await self.__get_and_process_registers([self.ENTITY_CHARGER_STATE])
            state = await self.get_state("sensor.charger_charger_state", attribute="all")

        state = int(float(state['state']))
        # self.log(f"Returning charger_state: '{state}' ({type(state)}).")
        return state

    async def __get_charge_power(self) -> int:
        #self.log("__get_charge_power")
        state = await self.get_state("sensor.charger_real_charging_power", attribute="all")
        should_be_renewed = False
        if state is None or not isinstance(state, dict) or "last_changed" not in state:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            should_be_renewed = True
        else:
            # Check if charger_status ois not too old
            lc = datetime.fromisoformat(state['last_changed'])
            nu = await self.get_now()
            should_be_renewed = (nu - lc).total_seconds() > self.STATE_MAX_AGE_IN_SECONDS

        if should_be_renewed:
            # Charger_status older than STATUS_MAX_AGE_IN_SECONDS seconds, so refresh:
            await self.__get_and_process_registers([self.ENTITY_CHARGER_CURRENT_POWER])
            state = await self.get_state("sensor.charger_real_charging_power", attribute="all")

        state = int(float(state['state']))
        self.log(f"Returning charge_power: '{state}' ({type(state)}).")
        return state


    async def __get_max_charge_power(self):
        """Read max charge power from charger."""
        registers = await self.__modbus_read(
            address=self.MAX_CHARGE_POWER_REGISTER,
            length=1,
            source = "__get_max_charge_power"
        )
        if registers is not None:
            registers = registers[0]
        return registers


    async def __get_and_process_registers(self, entities:list):
        """ This function reads the values from the EVSE via modbus and 
        writes these values to corresponding sensors in HA.

        The registers dictionary should have the structure:
        modbus_address: 'sensor name'
        Where:
        modbus_address should be int + sorted + increasing and should return int from EVSE
        sensor name should be str and not contain the prefix 'sensor.'
        """
        start = entities[0]['modbus_address']
        end = entities[-1]['modbus_address']

        length = end - start + 1
        results = await self.__modbus_read(address=start, length=length, source="__get_and_process_registers")
        for entity in entities:
            entity_name = f"sensor.{entity['ha_entity_name']}"
            register_index = entity['modbus_address'] - start
            new_state = results[register_index]
            if new_state is None:
                self.log(f"__get_and_process_registers: New value 'None' for entity '{entity_name}' ignored.")
                continue

            try:
                new_state = int(float(new_state))
            except:
                self.log(f"__get_and_process_registers: New value '{new_state}' for entity '{entity_name}', not type == int : ignored.")
                continue
            if not entity['minimum_value'] <= new_state <= entity['maximum_value']:
                # self.log(f"__get_and_process_registers: New value '{new_state}' for entity '{entity_name}' out of range {entity['minimum_value']} - {entity['maximum_value']} ignored.")
                continue

            # self.log(f"New value '{new_state}' for entity '{entity_name}'.")
            await self.__update_state(entity_name, state = new_state)
        return


    async def __update_state(self, entity, state=None, attributes=None):
        """ Generic function for updating the state of an entity in Home Assistant
            If it does not exist, create it.
            If it has attributes, keep them (if not overwrite with empty)

        Args:
            entity (str): entity_id
            state (any, optional): The value the entity should be written with. Defaults to None.
            attributes (any, optional): The value (can be dict) the attributes should be written 
            with. Defaults to None.
        """
        new_state = state
        new_attributes = None
        if self.entity_exists(entity):
            current_attributes = (await self.get_state(entity, attribute="all"))
            if current_attributes is not None:
                new_attributes = current_attributes["attributes"]
                if attributes is not None:
                    new_attributes.update(attributes)
        else:
            new_attributes = attributes

        if new_attributes is not None:
            await self.set_state(entity, state=new_state, attributes=new_attributes)
        else:
            await self.set_state(entity, state=new_state)

    async def __set_charge_power(self, charge_power: int, skip_min_soc_check: bool = False):
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
        """
        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self.__get_car_soc()
            if current_soc <= c.CAR_MIN_SOC_IN_PERCENT:
                # Fail-safe, this should never happen...
                self.log(f"A discharge is attempted while the current SoC is below the "
                         f"minimum ({c.CAR_MIN_SOC_IN_PERCENT})%. Stopping discharging.")
                charge_power = 0

        # Clip values to min/max charging current
        if charge_power > c.CHARGER_MAX_CHARGE_POWER:
            self.log(f"Requested charge power {charge_power} Watt too high.")
            charge_power = c.CHARGER_MAX_CHARGE_POWER
        elif abs(charge_power) > c.CHARGER_MAX_DISCHARGE_POWER:
            self.log(f"Requested discharge power {charge_power} Watt too high.")
            charge_power = -c.CHARGER_MAX_DISCHARGE_POWER

        current_charge_power = await self.__get_charge_power()

        if current_charge_power == charge_power:
            self.log(f'New-charge-power-setting is same as current-charge-power-setting: {charge_power} Watt. '
                     f'Not writing to charger.')
            return

        res = await self.__modbus_write(
            address=self.CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source="set_charge_power"
        )

        if not res:
            self.log(f"Failed to set charge power to {charge_power} Watt.")
            # If negative value result in false, check if grid code is set correct in charger.
        return


    ######################################################################
    #                   POLLING RELATED FUNCTIONS                        #
    ######################################################################

    async def __update_poll_indicator_in_ui(self, reset: bool = False):
        # This can be shown directly as text in the UI (or use ⏲ ⟳ 🔃 🔄?) but,
        # as the "last_changed" attribute also changes, an "age" could be shown based on this as well
        self.poll_update_text = "↺" if self.poll_update_text != "↺" else "↻"
        if reset:
            self.poll_update_text = "" 
        await self.__update_state(entity="input_text.poll_refresh_indicator", state=self.poll_update_text)


    async def __set_poll_strategy(self):
        """ Poll strategy:
            Should only be called if connection state has really changed.
            Minimal: Car is disconnected, poll for just the charger state every 15 seconds.
            Base: Car is connected, poll for all info every 5 seconds
            When Charge mode is off, is handled by handle_charge_mode
        """
        await self.__cancel_polling(reason="setting new polling strategy")

        if self.try_get_new_soc_in_process:
            return

        charger_state = await self.__get_charger_state()
        self.log(f"Deciding polling strategy based on state: {self.CHARGER_STATES[charger_state]}.")
        if charger_state == self.DISCONNECTED_STATE:
            self.log("Minimal polling strategy (lower freq., state register only.)")
            self.poll_timer_handle = await self.run_every(self.__minimal_polling, "now", self.MINIMAL_POLLING_INTERVAL_SECONDS)
        else:
            self.log("Base polling strategy (higher freq., all registers).")
            self.poll_timer_handle = await self.run_every(self.__base_polling, "now", self.BASE_POLLING_INTERVAL_SECONDS)


    async def __cancel_polling(self, reason: str = ""):
        """Stop the polling process by cancelling the polling timer.
           Further reset the polling indicator in the UI.

        Args:
            reason (str, optional): For debugging only
        """

        self.log(f"__cancel_polling, reason: {reason}")
        if self.timer_running(self.poll_timer_handle):
            await self.cancel_timer(self.poll_timer_handle, True)
            # To be really sure..
            self.poll_timer_handle = None
        else:
            self.log("__cancel_polling: No timer to cancel")
        await self.__update_poll_indicator_in_ui(reset=True)


    async def __minimal_polling(self, kwargs):
        """ Should only be called from set_poll_strategy
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
            Poll for soc, state, power, lock etc..
        """
        # These needs to be in different lists because the 
        # modbus addresses in between them do not exist in the EVSE.
        await self.__get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self.__get_and_process_registers([self.ENTITY_CHARGER_LOCKED])
        await self.__update_poll_indicator_in_ui()



    ######################################################################
    #                   MODBUS RELATED FUNCTIONS                         #
    ######################################################################


    async def __handle_modbus_failure(self, exception: object = None):
        """Handle a failure of modbus (communication) that does not seem to auto restore:
            - Cancel polling
            - Notify the user via v2g-liberty module that a restart of the modbus server might be needed.
              This also set charge mode to stop. 

        Args:
            exception (object, optional): The exception that caused the problem for logging purposes.
        """
        self.log(f"Modbus failure. Exception: {exception}")
        await self.__cancel_polling(reason="Modbus failure")
        await self.v2g_main_app.notify_user_of_charger_needs_restart()
        return


    async def __handle_no_modbus_connection(self):
        """Function to call when no connection with the modbus server could be made.
        This is only expected at startup.
        A persistent notification will be set pointing out that the configuration might not be ok.
        Polling is canceled as this is pointless without a connection. 
        """
        #Try to reset the connection
        # try to restart the client

        #TODO: Handle only this way at initialisation
        # Use following code somehow:
        # if not self.client.connected:
        #     await self.__handle_no_modbus_connection()
        #     return False

        self.log("__connect_to_modbus_server Could not connect to modbus server, wrong host/port, is it online?")
        self.v2g_globals.create_persistent_notification(
            title="Error in charger configuration",
            message="Please check if charger is powered, has IP connection and if Host/Port are correct in configuration.",
            id="no_comm_with_evse"
        )
        await self.__cancel_polling(reason="no modbus connection")


    async def __connect_to_modbus_server(self, source):
        """Utility function to connect to modbus_server
           Guards that there is always maximum one connection with the modbus server at the time.
           Connecting to the modbus server should exclusively be done through this function.
           Works in conjunction with __disconnect_from_modbus_server

        Args:
            source (str): For debugging

        Raises:
            ce: Modbus connection error
            exc: ModbusException

        Returns:
            bool: If connection was successful
        """

        # Times in seconds
        STEP = 0.25
        TIMEOUT = 15
        time_used = 0
        while self.modbus_connection_in_use:
            await asyncio.sleep(STEP)
            time_used += STEP
            if time_used >= TIMEOUT:
                self.log("__connect_to_modbus_server, timeout: connection with server in use by other process.")
                return False
        if time_used > 5:
            # Only log if time_used is somewhat longer. Can occur while getting an SoC is in progress.
            self.log(f"__connect_to_modbus_sever: {source} waited {time_used} seconds to get a connection.")
        self.modbus_connection_in_use = True

        try:
            await self.client.connect()
        except ConnectionException as ce:
            self.log(f"__connect_to_modbus_server: Connection exception while connecting to server '{ce}'.")
            #TODO: Maybe try close + connect again?
            await self.__handle_no_modbus_connection()
            raise ce
        except ModbusException as exc:
            self.log(f"__connect_to_modbus_server: Modbus exception while connecting to server: '{exc}'.")
            raise exc

        return True


    async def __disconnect_from_modbus_server(self, delay_after_disconnect: int = 0):
        """ Utility function to disconnect from modbus server
            Releases lock on "one connection at the time", that is why disconnection from 
            the modbus server should exclusively be done through this function.
            Works in conjunction with __connect_to_modbus_server

        Args:
            delay_after_disconnect (int, optional): 
            Delay for releasing the lock (after disconnect) in milliseconds.
            Defaults to 0.

        Raises:
            exc: ModbusException
        """
        try:
            self.client.close()
        except ModbusException as exc:
            self.log(f"Error disconnecting from Modbus server {exc}")
            self.modbus_connection_in_use = False
            raise exc
        if delay_after_disconnect > 0:
            await asyncio.sleep(delay_after_disconnect/1000)
        self.modbus_connection_in_use = False
        return


    async def __force_get_register(self, register: int, min: int, max: int) -> int:
        """ When a 'realtime' reading from the modbus server is needed that is expected 
        to be between min/max. We keep trying to get a reading that is within min - max range.

        Of course there is a timeout. If this is reached we expect the modbus server to have crashed
        and we notify the user.

        """

        # Times in seconds
        total_time = 0
        MAX_TOTAL_TIME = 120 
        DELAY_BETWEEN_READS = 0.25

        res = await self.__connect_to_modbus_server(source="__force_get_register")
        if not res:
            self.log(" __force_get_register: No connection!!!")

        # If the real SoC is not available yet, keep trying for max. two minutes
        while True:
            result = None
            try:
                # Only one register is read so count = 1, the charger expects slave to be 1.
                # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
                result = await self.client.read_holding_registers(register, count=1, slave=1)
            except ConnectionException as ce:
                self.log(f"__force_get_register, no connection: ({ce}). Close and open connection to see it this helps..")
                #TODO: check if this is n't doing any harm..
                await self.client.close()
                await self.client.connect()
                pass
            except ModbusException as exc:
                self.log(f"__force_get_register, Received ModbusException({exc}) from library")
                pass

            if result is not None:
                try:
                    result = int(float(result.registers[0]))
                    if result > self.HALF_MAX_USI:
                        # This represents a negative value.
                        result = result - self.MAX_USI
                    if min <= result <= max:
                        # Acceptable result retrieved
                        break
                except TypeError:
                    pass
            total_time += DELAY_BETWEEN_READS

            # We need to stop at some point
            if total_time > MAX_TOTAL_TIME:
                txt=f"__force_get_register timed out, no relevant value was retrieved."
                self.log(txt)
                await self.__handle_modbus_failure(txt)
                break

            await asyncio.sleep(DELAY_BETWEEN_READS)
            # self.log(f"__force_get_register, waited {total_time} seconds so far.")
            continue
        # End of while loop
        
        await self.__disconnect_from_modbus_server(self.WAIT_AFTER_MODBUS_READ_IN_MS)

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
        if value < 0:
            # Modbus cannot handle negative values directly.
            value = self.MAX_USI + value

        await self.__connect_to_modbus_server(source=source)
        try:
            # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
            result = await self.client.write_register(address, value, slave=1)
        except ModbusException as exc:
            self.log(f"__modbus_write, Received ModbusException({exc}) from library")
            await self.__disconnect_from_modbus_server()
            raise exc

        if not result:
            self.log(f"__modbus_write, Failed to write to modbus server ({result})")

        await self.__disconnect_from_modbus_server(self.WAIT_AFTER_MODBUS_WRITE_IN_MS/1000)
        return result


    async def __modbus_read(self, address: int, length: int = 1, source: str = "unknown"):
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
        await self.__connect_to_modbus_server(source=source)
        try:
            # I hate using the word 'slave', this should be 'server' but pyModbus has not changed this yet
            result = await self.client.read_holding_registers(address, length, slave=1)
        except ModbusException as exc:
            self.log(f"__modbus_write, Received ModbusException({exc}) from library")
            raise exc
        await self.__disconnect_from_modbus_server(self.WAIT_AFTER_MODBUS_READ_IN_MS/1000)

        return list(map(self.__get_2comp, result.registers))


    def __get_2comp(self, number):
        """ Util function to covert a modbus read value to in with two's complement values
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
