"""Client to control a Wallbox Quasar 1 Electric Vehicle Supply Equipment (EVSE)"""

#######################################################################################
#   This file contains the Modbus address information for the Wallbox Quasar 1 EVSE.  #
#   This is provided by the Wallbox Chargers S.L. as is.                              #
#   For reference see https://wallbox.com/en_uk/quasar-dc-charger                     #
#   Wallbox is not provider of the software and does not provide any type of service  #
#   for the software.                                                                 #
#   Wallbox will not be responsible for any damage or malfunction generated on        #
#   the Charger by the Software.                                                      #
#######################################################################################

from .v2g_modbus_client import V2GmodbusClient
from .base_bidirectional_evse import BidirectionalEVSE
from v2g_liberty import constants as c
from appdaemon.plugins.hass.hassapi import Hass
from v2g_liberty.event_bus import EventBus
from v2g_liberty.log_wrapper import get_class_method_logger
from v2g_liberty.evs.electric_vehicle import ElectricVehicle


class WallboxQuasar1Client(BidirectionalEVSE):
    """Client to control a Wallbox Quasar 1 EVSE"""

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

    # Timeout in seconds. 15 minutes is long, consided the polling frequncy of 5 seconds.
    CMIT: int = 900

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

    # TODO: Move to the abstract UnidirectionalEVSE class,
    # CHARGER_STATES, DISCONNECTED_STATES..ERROR_STATES.

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
    CHARGER_STATES: dict[int, str] = {
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

    ################################################################################
    #   EVSE Entities                                                              #
    #   These hold the constants for entity (e.g. modbus address, min/max value,   #
    #   and store (cache) the values of the charger.                               #
    #   About the current / previous_value:                                        #
    #    These are initiated with None to indicate they have not been touched yet. #
    ################################################################################

    ENTITY_CHARGER_CURRENT_POWER = {
        "modbus_address": 526,
        "minimum_value": -7400,
        "maximum_value": 7400,
        "current_value": None,
        "change_handler": "_handle_charge_power_change",
    }
    ENTITY_CHARGER_STATE = {
        "modbus_address": 537,
        "minimum_value": 0,
        "maximum_value": 11,
        "current_value": None,
        "change_handler": "__handle_charger_state_change",
    }
    ENTITY_CAR_SOC = {
        "modbus_address": 538,
        "minimum_value": 2,
        "maximum_value": 97,
        "relaxed_min_value": 1,
        "relaxed_max_value": 100,
        "current_value": None,
        "change_handler": "_handle_soc_change",
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

    ENTITY_ERROR_1 = {
        "modbus_address": 539,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "_handle_charger_error_state_change",
    }
    ENTITY_ERROR_2 = {
        "modbus_address": 540,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "_handle_charger_error_state_change",
    }
    ENTITY_ERROR_3 = {
        "modbus_address": 541,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "_handle_charger_error_state_change",
    }
    ENTITY_ERROR_4 = {
        "modbus_address": 542,
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "_handle_charger_error_state_change",
    }

    ENTITY_CHARGER_LOCKED = {
        "modbus_address": 256,
        "minimum_value": 0,
        "maximum_value": 1,
        "current_value": None,
        "ha_entity_name": "charger_locked",
    }

    CHARGER_ERROR_ENTITIES = [
        ENTITY_ERROR_1,
        ENTITY_ERROR_2,
        ENTITY_ERROR_3,
        ENTITY_ERROR_4,
    ]
    CHARGER_POLLING_ENTITIES = [
        ENTITY_CHARGER_CURRENT_POWER,
        ENTITY_CHARGER_STATE,
        ENTITY_CAR_SOC,
        ENTITY_ERROR_1,
        ENTITY_ERROR_2,
        ENTITY_ERROR_3,
        ENTITY_ERROR_4,
    ]

    # The Quasar 1 hardware does not accept a setting lower than 6A => 6A*230V = 1380W
    CHARGE_POWER_LOWER_LIMIT: int = 1380
    # The Quasar 1 hardware does not accept a setting higher than 32A => 32A*230V = 7400W
    CHARGE_POWER_UPPER_LIMIT: int = 7400

    POLLING_INTERVAL_SECONDS: int = 5

    # After a restart of the charger, errors can be present for up to 5 minutes.
    MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 300

    def __init__(self, hass: Hass, event_bus: EventBus, get_vehicle_by_name_func: callable):
        super().__init__()
        self.hass = hass
        self._eb = event_bus
        self.__log = get_class_method_logger(hass.log)

        # function to get the connected vehicle by name from v2g-liberty
        self.get_vehicle_by_name = get_vehicle_by_name_func

        #Modbus specifics
        self._mb_client: V2GmodbusClient | None = None
        self._mb_host: str | None = None
        self._mb_port: int = 502

        # Is the default that is normal for the Quasar 1. Can be overruled by user settings.
        self.evse_efficiency: float = 0.85

        # User limit the max (dis-)charger power.
        self.evse_max_charge_power_w: int | None = None
        self.evse_max_discharge_power_w: int | None = None


        self._connected_car: ElectricVehicle | None = None
        self.evse_actual_charge_power: int | None = None

        # Polling variables
        self.poll_timer_handle: str | None= None

        self._timer_id_check_error_state: str | None = None

        # Block events from firing during charge that is needed to read a SoC.
        self.charging_to_read_soc: bool = False

        self._am_i_active: bool = False

        self.__log("WallboxQuasar1Client initialized.")

    async def get_max_power_pre_init(self, host: str, port: int | None = None) -> tuple[bool, int | None]:
        """Tests the Modbus TCP connection to the EVSE charger before full initialization.

        This method is designed to validate communication settings (host and port)
        prior to calling `initialise_EVSE`. It establishes a temporary connection,
        reads the maximum available power (in Watts), and returns it. This can be called
        from the UI (via globals) even if the module is not yet initialized.

        Args:
            host (str): IP address or hostname of the EVSE charger.
            port (int): Modbus TCP port of the EVSE charger

        Returns:
            tuple[bool, int | None]:
            - Element 1: boolean indicating connection success.
            - Element 2: the maximum available power (int in Watts) if successful, otherwise None.
        """

        mb_client = V2GmodbusClient(self.hass)

        connected, max_hardware_power = await mb_client.adhoc_read_register(
            modbus_address=self.MAX_AVAILABLE_POWER_REGISTER, host=host, port=port
        )

        self.__log(
            f"Pre-init connection to Wallbox Quasar 1 at {host}:{port} "
            f"{'succeeded' if connected else 'failed'}, "
            f"max power: {max_hardware_power if connected else 'N/A'} W"
        )
        await self._emit_modbus_communication_state(can_communicate=connected)
        return connected, max_hardware_power


    async def initialise_evse(
        self,
        communication_config: dict,
    ):
        """Initialise Wallbox Quasar 1 EVSE
        To be called from globals.
        """
        self.__log(f"Initialising WallboxQuasar1Client with config: {communication_config}")

        host = communication_config.get("host", None)
        if not host:
            self.__log(
                "Wallbox Quasar 1 EVSE initialisation failed, no host.",
                level="WARNING",
            )
            raise ValueError("Host required for WallboxQuasar1Client")
        self._mb_host = host
        self._mb_port = communication_config.get("port", 502)
        self._mb_client = V2GmodbusClient(self.hass, self._cb_modbus_state)
        connected = await self._mb_client.initialise(
            host=self._mb_host,
            port=self._mb_port,
        )
        if not connected:
            self.__log(
                f"Wallbox Quasar 1 EVSE initialisation failed, cannot connect to "
                f"Modbus host {self._mb_host}:{self._mb_port}.",
                level="WARNING",
            )
            return False

        mcp = await self.get_hardware_power_limit()
        await self.set_max_charge_power(mcp)

        self.__log(
            f"Wallbox Quasar 1 EVSE initialised with host: {self._mb_host}, "
            f"Max (dis-)charge power: {self.evse_max_charge_power_w} W"
        )

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################

    async def kick_off_evse(self):
        """
        This public function is to be called from v2g-liberty once after its own init is complete.
        It performs the initial data retrieval and starts the polling loop."""
        if self._mb_host is None:
            self.__log(
                "The mb_client is not initialised (host missing?), cannot complete init. Aborting",
                level="WARNING"
            )
            return
        self.__log("Kicking off Wallbox Quasar 1 EVSE client.")

        self._eb.emit_event(
            "update_charger_info", charger_info=await self._get_charger_info()
        )

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)

        # SoC is essential for many decisions, so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first
        # opportunity to do a poll.
        await self._get_car_soc(force_renew=True)

        await self._kick_off_polling()

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop
        Do not cancel polling, the information is still relevant.
        """
        if self._mb_client is None:
            self.__log("Modbus client not initialised, aborting", level="WARNING")
            return
        self.__log("made inactive")
        await self.stop_charging()
        await self._set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if self._mb_client is None:
            self.__log("Modbus client not initialised, aborting", level="WARNING")
            return
        self.__log("activated")
        self._am_i_active = True
        await self._set_charger_control("take")
        await self._get_car_soc(force_renew=True)
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        # Eventhough it probably did not stop
        await self._kick_off_polling()

    async def get_hardware_power_limit(self) -> int | None:
        if self._mb_client is None:
            self.__log(
                "Modbus client not initialised, cannot get hardware power limit", level="WARNING"
            )
            return None
        result = await self._mb_client.modbus_read(
            self.MAX_AVAILABLE_POWER_REGISTER, length=1, source="get_hardware_power_limit"
        )

        if not result:
            self.__log(
                f"No valid read hardware limit {result}", level="WARNING"
            )
            await self._emit_modbus_communication_state(can_communicate=False)
            return None
        result = self._process_number(result[0])
        await self._emit_modbus_communication_state(can_communicate=True)
        return result

    async def set_max_charge_power(self, power_in_watt: int):
        """Set the maximum charge power in Watt.
        Used for reducing the hardware limit."""
        hardware_power_limit = await self.get_hardware_power_limit()

        if not isinstance(hardware_power_limit, int):
            self.__log(
                "Cannot set max charge power, hardware limit unavailable", level="WARNING",
            )
            return False

        if power_in_watt > hardware_power_limit:
            self.__log(
                f"Requested max charge power {power_in_watt}W exceeds hardware limit "
                f"{hardware_power_limit}W", level="WARNING",
            )
            power_in_watt = hardware_power_limit
        elif power_in_watt < hardware_power_limit:
            self.__log(
                f"Max charge power {hardware_power_limit}W is reduced by user "
                f"setting to {power_in_watt}W."
            )
        else:
            self.__log(
                f"User requested max power setting {hardware_power_limit}W is equal to hardware"
                f"{hardware_power_limit}W maximum."
            )

        self.evse_max_charge_power_w = power_in_watt
        self.evse_max_discharge_power_w = power_in_watt
        #TODO: Implement separate max_discharge_power, min_charge_power and min_discharge_power
        # You would add code to apply this limit to the evse if supported
        return True


    async def set_charging_efficiency(self, efficiency_percent: int):
        """Set the EVSE roundtrip charging efficiency.
        Args:
        Efficiency percent (int): Must be between 10 and 100.
        If not set the hardware limit will be used.
        """
        if efficiency_percent < 10 or efficiency_percent > 100:
            self.__log(
                f"Efficiency percent {efficiency_percent} is out of bounds (10-100).",
                level="WARNING",
            )
            return False
        self.evse_efficiency = round(efficiency_percent / 100, 2)
        self.__log(f"Set EVSE charging efficiency to {self.evse_efficiency}.")
        return True

    async def get_charging_efficiency(self) -> float:
        """Get the EVSE roundtrip charging efficiency as a float between 0.1 and 1.0"""
        return self.evse_efficiency



    async def get_connected_car(self) -> ElectricVehicle | None:
        """Return the connected car, or None if no car is connected."""
        return self._connected_car
    #TODO: Make propperty, also in abstract class..
    # @property
    # def connected_car(self) -> ElectricVehicle | None:
    #     return self._connected_car

    # TODO:
    # Rename to Occupied or has_car_connected? To distinguish from is_connected on ElectricVehicle.
    async def is_car_connected(self) -> bool:
        """Is the car connected to the charger (is chargeplug in the socket)."""
        state = await self._get_charger_state()
        return state not in self.DISCONNECTED_STATES

    async def start_charging(self, power_in_watt: int, source: str = None):
        """Start charging with specified power in Watt, can be negative.
        A power_in_watt of 0 will result in stop charging.
        Args:
            power_in_watt (int): power_in_watt with a value in Watt, can be negative.
        """

        if not self._am_i_active:
            self.__log("Not setting charge_rate: _am_i_active == False.")
            return

        if power_in_watt is None:
            self.__log("power_in_watt = None, abort", level="WARNING")
            return

        if not await self.is_car_connected():
            self.__log("Not setting charge_rate: No car connected.")
            return

        await self._set_charger_control("take")
        if power_in_watt == 0:
            await self._set_charger_action(
                action="stop",
                reason=f"called with power = 0",
            )
        else:
            await self._set_charger_action(
                action="start",
                reason=f"called with power = {power_in_watt}W",
            )

        await self._set_charge_power(
            charge_power=power_in_watt,
            source="start_charging",
        )

    async def stop_charging(self, source: str = None):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self.__log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )
        await self._set_charger_action("stop", reason="stop_charging")
        await self._set_charge_power(charge_power=0, source="stop_charging")

    async def is_charging(self) -> bool:
        """Is the battery being charged (positive power value, soc is increasing)"""
        state = await self._get_charger_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self.__log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        return state == self.CHARGING_STATE

    async def is_discharging(self) -> bool:
        """Is the battery being discharged (negative power value, soc is decreasing)"""
        state = await self._get_charger_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self.__log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        return state == self.DISCHARGING_STATE


    ######################################################################
    #                           PRIVATE METHODS                          #
    ######################################################################

    async def _get_charger_info(self) -> str:
        firmware_version_modbus_address = 1
        # serial_number_high_modbus_address = 2
        serial_number_low_modbus_address = 3

        length = serial_number_low_modbus_address - firmware_version_modbus_address + 1
        try:
            results = await self._mb_client.modbus_read(
                address=firmware_version_modbus_address,
                length=length,
                source="_get_charger_info",
            )
            if results:
                charger_info = (
                    f"Wallbox Quasar 1 EVSE - Firmware version: {results[0]}, "
                    f"Serial number high: {results[1]}, Serial Number Low: {results[2]}."
                )
                self.__log(charger_info)
                await self._emit_modbus_communication_state(can_communicate=True)
                return charger_info
        except Exception as e:
            self.__log(f"Wallbox Quasar 1 EVSE - Failed to get charger info: {e}", level="WARNING")

        await self._emit_modbus_communication_state(can_communicate=False)
        return "unknown"

    async def _kick_off_polling(self, reason: str = ""):
        """Start polling

        Args:
            reason (str, optional): For debugging only
        """

        self._cancel_timer(self.poll_timer_handle)
        self.poll_timer_handle = await self.hass.run_every(
            self._get_and_process_evse_data,
            "now",
            self.POLLING_INTERVAL_SECONDS,
        )
        if reason:
            reason = f", reason: {reason}"
        self.__log(f"Kicked off polling{reason}.")

    async def _cancel_polling(self, reason: str = ""):
        """Stop the polling process by cancelling the polling timer.
           Further reset the polling indicator in the UI.

        Args:
            reason (str, optional): For debugging only
        """
        self.__log(f"reason: {reason}")
        self._cancel_timer(self.poll_timer_handle)
        self.poll_timer_handle = None
        self._eb.emit_event("evse_polled", stop=True)

    async def _get_and_process_evse_data(self, *_args):
        """Retrieve and process all data from EVSE, called from polling timer."""
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._get_and_process_registers([self.ENTITY_CHARGER_LOCKED])
        self._eb.emit_event("evse_polled", stop=False)

    async def _get_and_process_registers(self, entities: list):
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
        results = await self._mb_client.modbus_read(
            address=start, length=length, source="_get_and_process_registers"
        )
        if not results:
            # Could not read
            self.__log("results is None, abort processing.", level="WARNING")
            await self._emit_modbus_communication_state(can_communicate=False)
            return

        await self._emit_modbus_communication_state(can_communicate=True)

        for entity in entities:
            # TODO: remove entity_name in this method.
            entity_name = entity.get("ha_entity_name", "now_handled_via_events")
            entity_name = f"sensor.{entity_name}"
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
                        new_state = None
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of range {entity['minimum_value']} "
                            f"- {entity['maximum_value']} but current value is None, so this polled"
                            f" value cannot be ignored, so new_value set to None."
                        )
                    elif relaxed_min_value <= new_state <= relaxed_max_value:
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of min/max range but in relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value}. So, as the current value is None, this this "
                            f"polled value is still used."
                        )
                    else:
                        new_state = None
                        self.__log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value} but current value is None, so this polled value"
                            f" cannot be ignored, so new_value set to None."
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
        evse_entity: dict,
        new_value,
    ):
        """
        Update evse_entity.
        :param evse_entity: evse_entity
        :param new_value: new_value, can be None
        :return: Nothing
        """
        current_value = evse_entity["current_value"]

        if current_value != new_value:
            evse_entity["current_value"] = new_value
            # Call change_handler if defined
            if "change_handler" in evse_entity.keys():
                str_action = evse_entity["change_handler"]
                # TODO: Find an more elegant way (without 'eval') to do this, e.g. callable?
                if str_action == "__handle_charger_state_change":
                    await self._handle_charger_state_change(
                        new_charger_state=new_value,
                        old_charger_state=current_value,
                    )
                elif str_action == "_handle_soc_change":
                    # Conceptually strange to set the soc on the car where it is just read from, but
                    # ElectricVehicle object cannot retrieve the soc by itself, the charger does
                    # this for it. Further actions based on soc changes are initiated by the car
                    # object.
                    if self._connected_car is None:
                        success = self.try_set_connected_vehicle()
                        if not success:
                            self.__log(
                                "SoC change detected but no car connected, cannot set SoC on car.",
                                level="WARNING",
                            )
                            return
                    self._connected_car.set_soc(new_soc=new_value)
                elif str_action == "_handle_charger_error_state_change":
                    # This is the case for the ENTITY_ERROR_1..4. The charger_state
                    # does not necessarily change only (one or more of) these error-states.
                    # So the state is not added to the call.
                    await self._handle_charger_error_state_change({"dummy": None})
                elif str_action == "_handle_charge_power_change":
                    self._eb.emit_event("charge_power_change", new_power=new_value)
                else:
                    self.__log(f"unknown action: '{str_action}'.", level="WARNING")

    async def _handle_charger_state_change(
        self, new_charger_state: int, old_charger_state: int
    ):
        """Called when _update_evse_entity detects a changed value."""
        self.__log(f"called {new_charger_state=}, {old_charger_state=}.")

        if (
            new_charger_state in self.ERROR_STATES
            or old_charger_state in self.ERROR_STATES
        ):
            # Check if user needs to be notified or if notification process needs to be aborted
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_charger_state, "is_final_check": False}
            )

        if self.charging_to_read_soc:
            return

        charger_state_text = self.CHARGER_STATES.get(new_charger_state, None)
        self._eb.emit_event(
            "charger_state_change",
            new_charger_state=new_charger_state,
            old_charger_state=old_charger_state,
            new_charger_state_str=charger_state_text,
        )

        if new_charger_state in self.DISCONNECTED_STATES:
            # Goes to this status when the plug is removed from the car-socket,
            # not when disconnect is requested from the UI.


            self._connected_car = None

            # When disconnected the SoC of the car goes from current soc to None.
            await self._update_evse_entity(
                evse_entity=self.ENTITY_CAR_SOC, new_value=None
            )

            # To prevent the charger from auto-start charging after the car gets connected again,
            # explicitly send a stop-charging command:
            await self._set_charger_action("stop", reason="car disconnected")
            self._eb.emit_event("is_car_connected", is_car_connected=False)
        elif old_charger_state in self.DISCONNECTED_STATES:
            # new_charger_state must be a connected state, so if the old state was disconnected
            # there was a change in connected state.

            self.__log("From disconnected to connected: get connected car and try to get the SoC")
            success = self.try_set_connected_vehicle()
            if success:
                await self._get_car_soc(force_renew=True)
            self._eb.emit_event("is_car_connected", is_car_connected=True)
        else:
            # From one connected state to an other connected state: not a change that this method
            # needs to react upon.
            pass

        return

    def try_set_connected_vehicle(self):
        # For ISO15118 capable chargers we would get the car info (name) from the charger here.
        # For now only one car can be used with V2G Liberty, always a (the same) Nissan Leaf.
        ev_name = "NissanLeaf"
        ev = self.get_vehicle_by_name(ev_name)
        if ev is None:
            self.__log(
                f"Cannot set connected vehicle, no vehicle with name '{ev_name}' found.",
                level="WARNING",
            )
            return False
        else:
            self._connected_car = ev
            return True

    async def _get_charge_power(self) -> int | None:
        state = self.ENTITY_CHARGER_CURRENT_POWER["current_value"]
        if state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self.ENTITY_CHARGER_CURRENT_POWER])
            state = self.ENTITY_CHARGER_CURRENT_POWER["current_value"]
        return state

    async def _get_charger_state(self) -> int | None:
        charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self.ENTITY_CHARGER_STATE])
            charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        return charger_state

    async def _is_charging_or_discharging(self) -> bool:
        state = await self._get_charger_state()
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

    async def _get_car_soc(self, force_renew: bool = True) -> int | None:
        """Checks if a SoC value is new enough to return directly or if it should be updated first.

        :param force_renew (bool):
        This forces the method to get the soc from the car and bypass any cached value.

        :return (int):
        SoC value from 2 to 97 (%) or None.
        If the car is disconnected the charger returns 0 representing None.
        """
        if not await self.is_car_connected():
            self.__log("no car connected, returning SoC = None")
            return None

        ecs = self.ENTITY_CAR_SOC
        soc_value = ecs["current_value"]
        should_be_renewed = False
        if soc_value is None:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            self.__log("current_value is None so should_be_renewed = True")
            should_be_renewed = True

        if force_renew:
            # Needed usually only when car has been disconnected. The polling then does not read SoC
            # and this probably changed and polling might not have picked this up yet.
            self.__log("force_renew == True so should_be_renewed = True")
            should_be_renewed = True

        if should_be_renewed:
            self.__log("old or invalid SoC in HA Entity: renew")
            soc_address = ecs["modbus_address"]

            # TODO: move to ElectricVehicle class??
            min_value_at_forced_get = ecs["minimum_value"]
            max_value_at_forced_get = ecs["maximum_value"]
            relaxed_min_value = ecs["relaxed_min_value"]
            relaxed_max_value = ecs["relaxed_max_value"]

            if await self._is_charging_or_discharging():
                # self.__log("called")
                soc_in_charger = await self._mb_client.force_get_register(
                    register=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                # Rare but possible. None can also occur if charger is in error
                if soc_in_charger == 0:
                    soc_in_charger = None
                await self._update_evse_entity(
                    evse_entity=ecs, new_value=soc_in_charger
                )
            else:
                self.__log("start a charge and read the soc until value is valid")
                # When not charging reading a SoC will return a false 0-value. To resolve this start
                # charging (with minimum power) then read a SoC and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading,
                # polling is paused.
                # charging_to_read_soc is used to prevent polling to start again from
                # elsewhere and to stop other processes.
                self.charging_to_read_soc = True
                await self._cancel_polling(reason="Charging to force reading soc")
                await self._set_charger_control("take")
                await self._set_charge_power(
                    charge_power=1, skip_min_soc_check=True, source="get_car_soc"
                )
                await self._set_charger_action("start", reason="Charging to force reading soc")
                # Reading the actual SoC
                soc_in_charger = await self._mb_client.force_get_register(
                    register=soc_address,
                    min_value_at_forced_get=min_value_at_forced_get,
                    max_value_at_forced_get=max_value_at_forced_get,
                    min_value_after_forced_get=relaxed_min_value,
                    max_value_after_forced_get=relaxed_max_value,
                )
                # Setting things back to inactive as it was before SoC reading started.
                await self._set_charge_power(
                    charge_power=0, skip_min_soc_check=True, source="get_car_soc"
                )  # This also sets action to stop
                await self._set_charger_action("stop", reason="After force reading soc")
                # This should can occure if charger is in error
                if soc_in_charger in [None, 0]:
                    soc_in_charger = "unavailable"
                # Do before restart polling
                await self._update_evse_entity(
                    evse_entity=ecs, new_value=soc_in_charger
                )
                self.charging_to_read_soc = False
                await self._kick_off_polling(reason="After force reading soc")
            soc_value = soc_in_charger
        self.__log(f"returning: '{soc_value}'.")
        return soc_value

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
        self.__log(f"Called with action '{action}', reason: '{reason}'.")

        if not self._am_i_active:
            self.__log("called while _am_i_active == False. Not blocking.")

        action_value = ""

        if action == "start":
            if not await self.is_car_connected():
                self.__log("Not performing charger action 'start': No car connected.")
                return
            if await self._is_charging_or_discharging():
                self.__log("Not performing charger action 'start': Already charging.")
                return
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
        await self._mb_client.modbus_write(
            address=self.SET_ACTION_REGISTER, value=action_value, source=txt
        )
        self.__log(f"{txt} {reason}")
        return

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
                This is used when this method is called from the __get_car_soc Defaults to False.
            source (str, optional):
              For logging purposes.
        """
        self.__log(f"called from {source}, power {charge_power}.")
        if not self._am_i_active:
            self.__log("called while _am_i_active is false, not blocking.", level="WARNING")

        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self._get_car_soc()
            if current_soc is None:
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
        if charge_power > self.evse_max_charge_power_w:
            self.__log(
                f"Requested charge power {charge_power} W too high, reducing.", level="WARNING"
            )
            charge_power = self.evse_max_charge_power_w
        elif charge_power < -self.evse_max_discharge_power_w:
            self.__log(
                f"Requested discharge power {charge_power} W too high, reducing.",
                level="WARNING",
            )
            charge_power = -self.evse_max_discharge_power_w

        current_charge_power = await self._get_charge_power()

        if current_charge_power == charge_power:
            return

        res = await self._mb_client.modbus_write(
            address=self.CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source=f"set_charge_power, from {source}",
        )

        if not res:
            self.__log(f"Failed to set charge power to {charge_power} W.", level="WARNING")
            # If negative value (always) fails, check if grid code is set correct in charger.

        return

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
        if take_or_give_control == "take":
            if not self._am_i_active:
                self.__log(
                    "Trying to take control while _am_i_active == False. Not blocking.",
                    level="WARNING"
                )
            await self._mb_client.modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["remote"],
                source="_set_charger_control, take control",
            )
            await self._mb_client.modbus_write(
                address=self.CHARGER_AUTOSTART_ON_CONNECT_REGISTER,
                value=self.AUTOSTART_ON_CONNECT_SETTING["disable"],
                source="_set_charger_control, set_auto_connect",
            )
            await self._mb_client.modbus_write(
                address=self.SET_SETPOINT_TYPE_REGISTER,
                value=self.SETPOINT_TYPES["power"],
                source="_set_charger_control: power",
            )
            await self._mb_client.modbus_write(
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
                charge_power=0, source="_set_charger_control, give control"
            )
            await self._mb_client.modbus_write(
                address=self.SET_CHARGER_CONTROL_REGISTER,
                value=self.CONTROL_TYPES["user"],
                source="_set_charger_control, give control",
            )
            # For the rare case that forced get soc is in action when the car gets disconnected.
            self.charging_to_read_soc = False

        else:
            raise ValueError(
                f"Unknown option for take_or_give_control: {take_or_give_control}"
            )

        return

    ################################ MODBUS STATE HANDLING #################################

    async def _cb_modbus_state(self, persistent_problem: bool):
        # callback for modbus_client to report persistent communication problems
        if persistent_problem:
            await self._modbus_communication_lost()
        else:
            # Only occurs after communication was first lost
            await self._modbus_communication_restored()

    async def _modbus_communication_lost(self):
        self.__log(
            "Persistent Modbus connection problem detected in Wallbox Quasar 1 EVSE.",
            level="WARNING",
        )

        # The only exception to the rule that _am_i_active should only be set from set_(in)active().
        self._am_i_active = False

        await self._cancel_polling(reason="modbus communication lost")

        # TODO: check if it is wise to use this same event for both
        # modbus communication lost and charger error
        self._eb.emit_event(
            "charger_error_state_change",
            persistent_error=True,
            was_car_connected=await self.is_car_connected()
        )

        # The soc and power are not known any more so let's represent this in the app
        await self.__update_evse_entity(
            evse_entity=self.ENTITY_CHARGER_CURRENT_POWER, new_value=None
        )
        await self.__update_evse_entity(
            evse_entity=self.ENTITY_CAR_SOC, new_value=None
        )

    async def _modbus_communication_restored(self):
        self.__log("Modbus connection to Wallbox Quasar 1 EVSE restored.")

        # TODO: check if it is wise to use this same event for both
        # modbus communication lost and charger error
        self._eb.emit_event(
            "charger_error_state_change",
            persistent_error=False,
            was_car_connected=None
        )
        # It could be that the charger was switched off to adjust settings. Re-check if the
        # hardware power limit has changed:
        self.set_max_charge_power(self.evse_max_charge_power_w)

        self._kick_off_polling()

    async def _emit_modbus_communication_state(self, can_communicate: bool):
        # To be called every time a successful or failed modbus read occurs
        self._eb.emit_event(
            "charger_communication_state_change", can_communicate=can_communicate
        )


    ################################ CHARGER ERROR HANDLING #################################

    async def _handle_charger_error_state_change(self, kwargs):
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

        # if new_charger_state is None:
        #     new_charger_state = await self._get_charger_state()
        #     self.__log(
        #         f"Called without charger state, _get_charger_state: {new_charger_state}."
        #     )

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
                break

        if has_error:
            if is_final_check:
                await self._handle_un_recoverable_error(reason="charger reports error")
                # TODO: check if it is wise to use this same event for both
                # modbus communication lost and charger error
                self._eb.emit_event(
                    "charger_error_state_change",
                    persistent_error=True,
                    was_car_connected=await self.is_car_connected()
                )

            elif self._timer_id_check_error_state is None:
                self._timer_id_check_error_state = await self.hass.run_in(
                    self._handle_charger_error_state_change,
                    delay=self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
                    new_charger_state=None,
                    is_final_check=True,
                )
                return
        else:
            self._cancel_timer(self._timer_id_check_error_state)
            self._timer_id_check_error_state = None
            # TODO: check if it is wise to use this same event for both
            # modbus communication lost and charger error
            self._eb.emit_event(
                "charger_error_state_change",
                persistent_error=False,
                was_car_connected=None
            )


    ################################# UTILITIES ################################

    def _process_number(
        self,
        number_to_process: int | float | str,
        min_value: int | float = None,
        max_value: int | float = None,
        number_name: str = None,
    ) -> int | None:

        if number_to_process is None:
            return None

        try:
            processed_number = int(float(number_to_process))
        except ValueError as ve:
            self.__log(
                f"Number named '{number_name}' with value '{number_to_process}' cannot be processed"
                f"due to ValueError: {ve}.",
                level="WARNING",
            )
            return None

        if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
            if min_value <= processed_number <= max_value:
                return processed_number
            else:
                self.__log(
                    f"Number named '{number_name}' with value '{number_to_process}' is out of range"
                    f"min '{min_value}' - max '{max_value}'.",
                    level="WARNING",
                )
                return None
        else:
            return processed_number

    def _cancel_timer(self, timer_id: str):
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
