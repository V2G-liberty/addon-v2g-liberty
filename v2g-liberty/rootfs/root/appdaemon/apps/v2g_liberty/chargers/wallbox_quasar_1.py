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

import time
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from apps.v2g_liberty.utils.hass_util import cancel_timer_silently
from .base_bidirectional_evse import BidirectionalEVSE
from .modbus_types import MBR, ModbusConfigEntity
from .v2g_modbus_client import V2GmodbusClient


class WallboxQuasar1Client(BidirectionalEVSE):
    """Client to control a Wallbox Quasar 1 EVSE"""

    ######################################################################
    #                 Modbus registers for setting values                #
    ######################################################################

    # Charger can be controlled by the app = user or by code = remote (Read/Write)
    # For all other settings mentioned here to work, this setting must be remote.
    _MBR_SET_CHARGER_CONTROL = MBR(address=81, data_type="uint16", length=1)
    _CONTROL_TYPES = {"user": 0, "remote": 1}

    # Start charging/discharging on EV-Gun connected (Read/Write)
    # Resets to default (=enabled) when control set to user
    # Must be set to "disabled" when controlled from this code.
    _MBR_CHARGER_AUTOSTART_ON_CONNECT = MBR(address=82, data_type="uint16", length=1)
    _AUTOSTART_ON_CONNECT_SETTING = {"enable": 1, "disable": 0}

    # Control if charger can be set through current setting or power setting (Read/Write)
    # This software uses power only.
    _MBR_SET_SETPOINT_TYPE = MBR(address=83, data_type="uint16", length=1)
    _SETPOINT_TYPES = {"current": 0, "power": 1}

    # Charger setting to go to idle state if not receive modbus message within this timeout.
    # Fail-safe in case this software crashes: if timeout passes charger will stop (dis-)charging.
    _MBR_CHARGER_MODBUS_IDLE_TIMEOUT = MBR(address=88, data_type="uint16", length=1)

    # Timeout in seconds. 15 minutes is long, consided the polling frequncy of 5 seconds.
    _CMIT: int = 900

    # Charger charging can be started/stopped remote (Read/Write)
    # Not implemented: restart and update software
    _MBR_SET_ACTION = MBR(address=257, data_type="uint16", length=1)
    _ACTIONS = {"start_charging": 1, "stop_charging": 2}

    # For setting the desired charge power, reading the actual charging power is done
    # through _MCE_ACTUAL_POWER
    _MBR_CHARGER_SET_CHARGE_POWER = MBR(address=260, data_type="int16", length=1)

    # AC Max Charging Power (by phase) (hardware) setting in charger (Read/Write)
    # (int16) unit W, min_value 1380, max_value 7400
    # Used when set_setpoint_type = power
    _MBR_MAX_AVAILABLE_POWER = MBR(address=514, data_type="int16", length=1)

    # Charger info registers (Read-only)
    _MBR_FIRMWARE_VERSION = MBR(address=1, data_type="uint16", length=1)
    _MBR_SERIAL_NUMBER_HIGH = MBR(address=2, data_type="uint16", length=1)
    _MBR_SERIAL_NUMBER_LOW = MBR(address=3, data_type="uint16", length=1)

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

    ################################################################################################
    # EVSE Entities                                                                                #
    #                                                                                              #
    # These ModbusConfigEntity instances hold constants for each entity (e.g., Modbus address,     #
    # min/max values) and cache the charger's current values.                                      #
    #                                                                                              #
    # - `current_value` is initialized as `None` to indicate it has not been set yet.              #
    # - `pre_processor` (optional): A synchronous method with one parameter: `new_value`.          #
    # - `change_handler` (optional): An asynchronous method with two parameters: `new_value`,      #
    #    `old_value`.                                                                              #
    ################################################################################################

    _MCE_ACTUAL_POWER = ModbusConfigEntity(
        modbus_register=MBR(address=526, data_type="int16", length=1),
        minimum_value=-7400,
        maximum_value=7400,
        current_value=None,
        change_handler="_handle_charge_power_change",
    )

    _MCE_CHARGER_STATE = ModbusConfigEntity(
        modbus_register=MBR(address=537, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=11,
        current_value=None,
        change_handler="_handle_charger_state_change",
        pre_processor="_get_base_state",
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
    _MCE_CAR_SOC = ModbusConfigEntity(
        modbus_register=MBR(address=538, data_type="uint16", length=1),
        minimum_value=2,
        maximum_value=97,
        relaxed_min_value=1,
        relaxed_max_value=100,
        current_value=None,
        change_handler="_handle_soc_change",
    )

    _MCE_ERROR_1 = ModbusConfigEntity(
        modbus_register=MBR(address=539, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_registers_state_change",
    )

    _MCE_ERROR_2 = ModbusConfigEntity(
        modbus_register=MBR(address=540, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_registers_state_change",
    )

    _MCE_ERROR_3 = ModbusConfigEntity(
        modbus_register=MBR(address=541, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_registers_state_change",
    )

    _MCE_ERROR_4 = ModbusConfigEntity(
        modbus_register=MBR(address=542, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=65535,
        current_value=None,
        change_handler="_handle_charger_error_registers_state_change",
    )

    _MCE_CHARGER_LOCKED = ModbusConfigEntity(
        modbus_register=MBR(address=256, data_type="uint16", length=1),
        minimum_value=0,
        maximum_value=1,
        current_value=None,
    )

    CHARGER_ERROR_ENTITIES = [
        _MCE_ERROR_1,
        _MCE_ERROR_2,
        _MCE_ERROR_3,
        _MCE_ERROR_4,
    ]
    CHARGER_POLLING_ENTITIES = [
        _MCE_ACTUAL_POWER,
        _MCE_CHARGER_STATE,
        _MCE_CAR_SOC,
        _MCE_ERROR_1,
        _MCE_ERROR_2,
        _MCE_ERROR_3,
        _MCE_ERROR_4,
    ]

    # Define the mapping from subclass states to base class _EVSE_STATES states
    _EVSE_STATE_MAPPING = {
        0: 1,  # "No car connected" -> base state 1
        1: 3,  # "Charging" -> base state 3
        2: 2,  # "Connected: waiting for car demand" -> base state 2
        3: 7,  # "Connected: controlled by Wallbox App" -> base state 7
        4: 2,  # "Connected: not charging (paused)" -> base state 2
        5: 2,  # "Connected: end of schedule" -> base state 2
        6: 8,  # "No car connected and charger locked" -> base state 8
        7: 9,  # "Error" -> base state 9
        8: 2,  # "Connected: In queue by Power Sharing" -> base state 2
        9: 9,  # "Error: Un-configured Power Sharing System" -> base state 9
        10: 2,  # "Connected: In queue by Power Boost (Home uses all power)" -> base state 2
        11: 4,  # "Discharging" -> base state 4
    }

    _POLLING_INTERVAL_SECONDS: int = 5

    # How long should an error state be present before it is communicated to the user.
    # After a restart of the charger, errors can be present for up to 5 minutes.
    _MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 300

    def __init__(
        self, hass: Hass, event_bus: EventBus, get_vehicle_by_ev_id_func: callable
    ):
        super().__init__()
        self._hass = hass
        self._eb = event_bus
        self._log = get_class_method_logger(hass.log)

        # function to get the connected vehicle by name from v2g-liberty
        self.get_vehicle_by_ev_id = get_vehicle_by_ev_id_func

        # Modbus specifics
        self._mb_client: V2GmodbusClient | None = None
        self._mb_host: str | None = None
        self._mb_port: int = 502

        # User limit the max (dis-)charger power.
        self._max_charge_power_w: int | None = None
        self._max_discharge_power_w: int | None = None

        self._connected_car: ElectricVehicle | None = None
        self._actual_charge_power: int | None = None

        # Polling variables
        self._poll_timer_handle: str | None = None

        self._timer_id_check_error_state: str | None = None

        # Block events from firing during charge that is needed to read a SoC.
        self._charging_to_read_soc: bool = False

        self._am_i_active: bool = False

        self._log("WallboxQuasar1Client initialized.")

    async def get_max_power_pre_init(
        self, host: str, port: int | None = None
    ) -> tuple[bool, int | None]:
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

        mb_client = V2GmodbusClient(self._hass)

        connected, max_hardware_power = await mb_client.adhoc_read_register(
            modbus_address=self._MBR_MAX_AVAILABLE_POWER.address, host=host, port=port
        )

        self._log(
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
        self._log(
            f"Initialising WallboxQuasar1Client with config: {communication_config}"
        )

        host = communication_config.get("host", None)
        if not host:
            self._log(
                "Wallbox Quasar 1 EVSE initialisation failed, no host.",
                level="WARNING",
            )
            raise ValueError("Host required for WallboxQuasar1Client")
        self._mb_host = host
        self._mb_port = communication_config.get("port", 502)
        self._mb_client = V2GmodbusClient(self._hass, self._cb_modbus_state)
        connected = await self._mb_client.initialise(
            host=self._mb_host,
            port=self._mb_port,
        )
        if not connected:
            self._log(
                f"Wallbox Quasar 1 EVSE initialisation failed, cannot connect to "
                f"Modbus host {self._mb_host}:{self._mb_port}.",
                level="WARNING",
            )
            return False

        mcp = await self.get_hardware_power_limit()
        await self.set_max_charge_power(mcp)

        self._log(
            f"Wallbox Quasar 1 EVSE initialised with host: {self._mb_host}, "
            f"Max (dis-)charge power: {self._max_charge_power_w} W"
        )

    ######################################################################
    #                  INITIALISATION RELATED FUNCTIONS                  #
    ######################################################################

    async def kick_off_evse(self):
        """
        This public function is to be called from v2g-liberty once after its own init is complete.
        It performs the initial data retrieval and starts the polling loop."""
        if self._mb_host is None:
            self._log(
                "The mb_client is not initialised (host missing?), cannot complete init. Aborting",
                level="WARNING",
            )
            return
        self._log("Kicking off Wallbox Quasar 1 EVSE client.")

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
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        self._log("made inactive")
        await self.stop_charging()
        await self._set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return
        self._log("activated")
        self._am_i_active = True
        await self._set_charger_control("take")
        await self._get_car_soc(force_renew=True)
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        # Eventhough it probably did not stop
        await self._kick_off_polling()

    async def get_hardware_power_limit(self) -> int | None:
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return None

        results = await self._mb_client.read_registers([self._MBR_MAX_AVAILABLE_POWER])

        if not results or results[0] is None:
            self._log(f"No valid read hardware limit {results}", level="WARNING")
            await self._emit_modbus_communication_state(can_communicate=False)
            return None

        result = self._process_number(results[0])
        await self._emit_modbus_communication_state(can_communicate=True)
        return result

    @property
    def max_charge_power_w(self) -> int | None:
        return self._max_charge_power_w

    @property
    def max_discharge_power_w(self) -> int | None:
        return self._max_discharge_power_w

    async def set_max_charge_power(self, power_in_watt: int):
        """Set the maximum charge power in Watt.
        Used for reducing the hardware limit."""
        hardware_power_limit = await self.get_hardware_power_limit()

        if not isinstance(hardware_power_limit, int):
            self._log(
                "Cannot set max charge power, hardware limit unavailable",
                level="WARNING",
            )
            return False

        if power_in_watt > hardware_power_limit:
            self._log(
                f"Requested max charge power {power_in_watt}W exceeds hardware limit "
                f"{hardware_power_limit}W",
                level="WARNING",
            )
            power_in_watt = hardware_power_limit
        elif power_in_watt < hardware_power_limit:
            self._log(
                f"Max charge power {hardware_power_limit}W is reduced by user "
                f"setting to {power_in_watt}W."
            )
        else:
            self._log(
                f"User requested max power setting {hardware_power_limit}W is equal to hardware"
                f"{hardware_power_limit}W maximum."
            )

        self._max_charge_power_w = power_in_watt
        # TODO: Implement separate min_charge_power and min_discharge_power
        # You would add code to apply this limit to the evse if supported
        return True

    async def set_max_discharge_power(self, power_in_watt: int):
        """Set the maximum discharge power in Watt.
        Used for reducing the hardware limit."""
        hardware_power_limit = await self.get_hardware_power_limit()

        if not isinstance(hardware_power_limit, int):
            self._log(
                "Cannot set max discharge power, hardware limit unavailable",
                level="WARNING",
            )
            return False

        if power_in_watt > hardware_power_limit:
            self._log(
                f"Requested max discharge power {power_in_watt}W exceeds hardware limit "
                f"{hardware_power_limit}W",
                level="WARNING",
            )
            power_in_watt = hardware_power_limit
        elif power_in_watt < hardware_power_limit:
            self._log(
                f"Max discharge power {hardware_power_limit}W is reduced by user "
                f"setting to {power_in_watt}W."
            )
        else:
            self._log(
                f"User requested max power setting {hardware_power_limit}W is equal to hardware"
                f"{hardware_power_limit}W maximum."
            )

        self._max_discharge_power_w = power_in_watt
        # TODO: Implement separate min_charge_power and min_discharge_power
        # You would add code to apply this limit to the evse if supported
        return True

    # TODO: make separate efficiency for charger and car.

    async def get_connected_car(self) -> ElectricVehicle | None:
        """Return the connected car, or None if no car is connected."""
        return self._connected_car

    # TODO: Make propperty, also in abstract class..
    # @property
    # def connected_car(self) -> ElectricVehicle | None:
    #     return self._connected_car

    # TODO:
    # Rename to Occupied or has_car_connected? To distinguish from is_connected on ElectricVehicle.
    async def is_car_connected(self) -> bool:
        """Is the car connected to the charger (is chargeplug in the socket)."""
        state = await self._get_evse_state()
        return state not in self._DISCONNECTED_STATES

    async def start_charging(self, power_in_watt: int, source: str = None):
        """Start charging with specified power in Watt, can be negative.
        A power_in_watt of 0 will result in stop charging.
        Args:
            power_in_watt (int): power_in_watt with a value in Watt, can be negative.
        """

        if not self._am_i_active:
            self._log("Not setting charge_rate: _am_i_active == False.")
            return

        if power_in_watt is None:
            self._log("power_in_watt = None, abort", level="WARNING")
            return

        if not await self.is_car_connected():
            self._log("Not setting charge_rate: No car connected.")
            return

        await self._set_charger_control("take")

        # Get SoC BEFORE starting charger to avoid race condition where _get_car_soc
        # might stop the charger we're about to start
        current_soc = await self._get_car_soc()

        if power_in_watt == 0:
            await self._set_charger_action(
                action="stop",
                reason="called with power = 0",
            )
        else:
            await self._set_charger_action(
                action="start",
                reason=f"called with power = {power_in_watt}W",
            )
        await self._set_charge_power(
            charge_power=power_in_watt,
            current_soc=current_soc,
            source="start_charging",
        )

    async def stop_charging(self, source: str = None):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self._log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )
        await self._set_charger_action("stop", reason="stop_charging")
        fake_soc = 50
        await self._set_charge_power(
            charge_power=0, current_soc=fake_soc, source="stop_charging"
        )

    async def is_charging(self) -> bool:
        """Is the battery being charged (positive power value, soc is increasing)"""
        state = await self._get_evse_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        return state in self._CHARGING_STATES

    async def is_discharging(self) -> bool:
        """Is the battery being discharged (negative power value, soc is decreasing)"""
        state = await self._get_evse_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        return state in self._DISCHARGING_STATES

    ######################################################################
    #                           PRIVATE METHODS                          #
    ######################################################################

    async def _get_charger_info(self) -> str:
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return "unknown"

        try:
            results = await self._mb_client.read_registers(
                [
                    self._MBR_FIRMWARE_VERSION,
                    self._MBR_SERIAL_NUMBER_HIGH,
                    self._MBR_SERIAL_NUMBER_LOW,
                ]
            )
            if results:
                charger_info = (
                    f"Wallbox Quasar 1 EVSE - Firmware version: {results[0]}, "
                    f"Serial number high: {results[1]}, Serial Number Low: {results[2]}."
                )
                self._log(charger_info)
                await self._emit_modbus_communication_state(can_communicate=True)
                return charger_info
        except Exception as e:
            self._log(
                f"Wallbox Quasar 1 EVSE - Failed to get charger info: {e}",
                level="WARNING",
            )

        await self._emit_modbus_communication_state(can_communicate=False)
        return "unknown"

    async def _kick_off_polling(self, reason: str = ""):
        """Start polling

        Args:
            reason (str, optional): For debugging only
        """

        cancel_timer_silently(self._hass, self._poll_timer_handle)
        self._poll_timer_handle = await self._hass.run_every(
            self._get_and_process_evse_data,
            "now",
            self._POLLING_INTERVAL_SECONDS,
        )
        if reason:
            reason = f", reason: {reason}"
        self._log(f"Kicked off polling{reason}.")

    async def _cancel_polling(self, reason: str = ""):
        """Stop the polling process by cancelling the polling timer.
           Further reset the polling indicator in the UI.

        Args:
            reason (str, optional): For debugging only
        """
        self._log(f"reason: {reason}")
        cancel_timer_silently(self._hass, self._poll_timer_handle)
        self._poll_timer_handle = None
        self._eb.emit_event("evse_polled", stop=True)

    async def _get_and_process_evse_data(self, *_args):
        """Retrieve and process all data from EVSE, called from polling timer."""
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._get_and_process_registers([self._MCE_CHARGER_LOCKED])
        self._eb.emit_event("evse_polled", stop=False)

    async def _get_and_process_registers(self, entities: list):
        """This function reads the values from the EVSE via modbus and
        writes these values to corresponding sensors in HA.

        Args:
            entities: List of ModbusConfigEntity objects to read
        """

        if self._mb_client is None:
            self._log("Mobus client not initialised yet, aborting", level="WARNING")
            return

        # Extract MBR objects from entities
        mbr_list = [entity.modbus_register for entity in entities]

        results = await self._mb_client.read_registers(mbr_list)
        if not results:
            # Could not read
            self._log("results is None, abort processing.", level="WARNING")
            await self._emit_modbus_communication_state(can_communicate=False)
            return

        await self._emit_modbus_communication_state(can_communicate=True)

        for i, entity in enumerate(entities):
            entity_name = "now_handled_via_events"
            new_state = results[i]
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
                        new_state = None
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of range {entity.minimum_value} "
                            f"- {entity.maximum_value} but current value is None, so this polled"
                            f" value cannot be ignored, so new_value set to None."
                        )
                    elif relaxed_min_value <= new_state <= relaxed_max_value:
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of min/max range but in relaxed range {relaxed_min_value} "
                            f"- {relaxed_max_value}. So, as the current value is None, this this "
                            f"polled value is still used."
                        )
                    else:
                        new_state = None
                        self._log(
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

    async def _update_evse_entity(self, evse_entity: ModbusConfigEntity, new_value):
        """Update the EVSE entity with the new value.

        Args:
            evse_entity: The ModbusConfigEntity to update.
            new_value: The new value (can be None).
        """
        current_value = evse_entity.current_value

        # Use set_value() which handles type conversion, pre_processor, and validation
        has_changed = evse_entity.set_value(new_value, owner=self)

        if has_changed:
            # Call change_handler if defined
            # change_handler method must be async and have two parameters: new_value, old_value
            if evse_entity.change_handler is not None:
                change_handler_method_name = evse_entity.change_handler
                if isinstance(change_handler_method_name, str):
                    try:
                        change_handler_method = getattr(
                            self, change_handler_method_name
                        )
                        await change_handler_method(
                            evse_entity.current_value, current_value
                        )
                    except AttributeError:
                        self._log(
                            f"Change_handler_method '{change_handler_method_name}' does not exist!",
                            level="WARNING",
                        )
                else:
                    self._log(
                        "change_handler_method_name is not a string!", level="WARNING"
                    )

    async def _handle_charge_power_change(self, new_power, old_power):
        self._eb.emit_event("charge_power_change", new_power=new_power)

    async def _handle_charger_error_registers_state_change(self, new_error, old_error):
        # This is the case for the _MCE_ERROR_1..4. The charger_state
        # does not necessarily change only (one or more of) these error-states.
        # So the state is not added to the call.
        if old_error is not None:
            self._log(
                f"new_error: {new_error}, old_error: {old_error}.", level="WARNING"
            )
        await self._handle_charger_error_state_change({"dummy": None})

    async def _handle_soc_change(self, new_soc, old_soc):
        # Conceptually strange to set the soc on the car where it is just read from, but
        # ElectricVehicle object cannot retrieve the soc by itself, the charger does
        # this for it. Further actions based on soc changes are initiated by the car
        # object.
        if self._connected_car is None:
            success = self.try_set_connected_vehicle()
            if not success:
                self._log(
                    "SoC change detected but no car connected, cannot set SoC on car.",
                    level="WARNING",
                )
                return
        self._connected_car.set_soc(new_soc=new_soc)

    async def _handle_charger_state_change(
        self, new_evse_state: int, old_evse_state: int
    ):
        """Called when _update_evse_entity detects a changed value."""
        self._log(f"called {new_evse_state=}, {old_evse_state=}.")

        if new_evse_state in self._ERROR_STATES or old_evse_state in self._ERROR_STATES:
            # Check if user needs to be notified or if notification process needs to be aborted
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_evse_state, "is_final_check": False}
            )

        if self._charging_to_read_soc:
            return

        charger_state_text = self.get_evse_state_str(new_evse_state)
        self._eb.emit_event(
            "charger_state_change",
            new_charger_state=new_evse_state,
            old_charger_state=old_evse_state,
            new_charger_state_str=charger_state_text,
        )

        if new_evse_state in self._ERROR_STATES:
            # to exclude the other cases to run.
            pass
        elif new_evse_state in self._DISCONNECTED_STATES:
            # Goes to this status when the plug is removed from the car-socket,
            # not when disconnect is requested from the UI.

            self._connected_car = None

            # When disconnected the SoC of the car goes from current soc to None.
            await self._update_evse_entity(
                evse_entity=self._MCE_CAR_SOC, new_value=None
            )

            # To prevent the charger from auto-start charging after the car gets connected again,
            # explicitly send a stop-charging command:
            await self._set_charger_action("stop", reason="car disconnected")
            self._eb.emit_event("is_car_connected", is_car_connected=False)
        elif old_evse_state in self._DISCONNECTED_STATES:
            # new_charger_state must be a connected state, so if the old state was disconnected
            # there was a change in connected state.

            self._log(
                "From disconnected to connected: get connected car and try to get the SoC"
            )
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
        """
        For ISO15118 capable chargers we would get the car info (name) from the charger here.
        For now only one car can be used with V2G Liberty, always a (the same) Nissan Leaf.
        """
        ev_id = "NissanLeaf"
        ev = self.get_vehicle_by_ev_id(ev_id)
        if ev is None:
            self._log(
                f"Cannot set connected vehicle, no vehicle with name '{ev_id}' found.",
                level="WARNING",
            )
            return False
        else:
            self._connected_car = ev
            return True

    async def _get_charge_power(self) -> int | None:
        state = self._MCE_ACTUAL_POWER.current_value
        if state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_ACTUAL_POWER])
            state = self._MCE_ACTUAL_POWER.current_value
        return state

    def _get_base_state(self, quasar_state: int) -> int:
        """'Translate' a 'quasar state' to 'base evse state'."""
        return self._EVSE_STATE_MAPPING.get(
            quasar_state, 0
        )  # Default to "Booting" if unknown

    async def _get_evse_state(self) -> int:
        """Returns the state accoding to _EVSE_STATES from the base class."""
        charger_state = self._MCE_CHARGER_STATE.current_value
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_CHARGER_STATE])
            charger_state = self._MCE_CHARGER_STATE.current_value
        return charger_state

    async def _is_charging_or_discharging(self) -> bool:
        state = await self._get_evse_state()
        if state is None:
            # The connection to the charger probably is not setup yet.
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in self._CHARGING_STATES + self._DISCHARGING_STATES
        self._log(
            f"state: {state} ({self.get_evse_state_str(state)}), charging: {is_charging}."
        )
        return is_charging

    async def _get_car_soc(self, force_renew: bool = False) -> int | None:
        """Checks if a SoC value is new enough to return directly or if it should be updated first.

        :param force_renew (bool):
        This forces the method to get the soc from the car and bypass any cached value.

        :return (int):
        SoC value from 2 to 97 (%) or None.
        If the car is disconnected the charger returns 0 representing None.
        """

        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        if not await self.is_car_connected():
            self._log("no car connected, returning SoC = None")
            return None

        mcs = self._MCE_CAR_SOC
        soc_value = mcs.current_value
        should_be_renewed = False

        if force_renew:
            # Needed usually only when car has been disconnected. The polling then does not read SoC
            # and this probably changed and polling might not have picked this up yet.
            self._log("force_renew == True so should_be_renewed = True")
            should_be_renewed = True
        elif not mcs.is_value_fresh(max_age_seconds=self._POLLING_INTERVAL_SECONDS):
            # Polled value is either missing, invalid, or stale - need to force read
            # Soc could be old in periods where no charging is preformed as the quasar then
            # returns 0 for SoC which is ignored as a invalid value.
            age_str = (
                f"{time.time() - mcs.last_updated:.1f}s"
                if mcs.last_updated
                else "never"
            )
            self._log(f"SoC needs refresh (value: {soc_value}, age: {age_str}).")
            should_be_renewed = True

        if should_be_renewed:
            self._log("old or invalid SoC in HA Entity: renew")

            soc_address = mcs.modbus_register.address

            # TODO: move to ElectricVehicle class??
            min_value_at_forced_get = mcs.minimum_value
            max_value_at_forced_get = mcs.maximum_value
            relaxed_min_value = mcs.relaxed_min_value
            relaxed_max_value = mcs.relaxed_max_value

            if await self._is_charging_or_discharging():
                # self._log("called")
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
                    evse_entity=mcs, new_value=soc_in_charger
                )
            else:
                self._log("start a charge and read the soc until value is valid")
                # When not charging reading a SoC will return a false 0-value. To resolve this start
                # charging (with minimum power) then read a SoC and stop charging.
                # To not send unneeded change events, for the duration of getting an SoC reading,
                # polling is paused.
                # charging_to_read_soc is used to prevent polling to start again from
                # elsewhere and to stop other processes.
                self._charging_to_read_soc = True
                await self._cancel_polling(reason="Charging to force reading soc")
                await self._set_charger_control("take")
                fake_soc = 50
                await self._set_charge_power(
                    charge_power=1,
                    current_soc=fake_soc,
                    skip_min_soc_check=True,
                    source="get_car_soc",
                )
                await self._set_charger_action(
                    "start", reason="Charging to force reading soc"
                )
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
                    charge_power=0,
                    current_soc=fake_soc,
                    skip_min_soc_check=True,
                    source="get_car_soc",
                )
                await self._set_charger_action("stop", reason="After force reading soc")

                # Bit bdouble: should be done in MCE
                # This can occur if charger is in error
                # Convert 0 to None (0 means unavailable for this charger)
                if soc_in_charger == 0:
                    soc_in_charger = None
                # Do before restart polling
                await self._update_evse_entity(
                    evse_entity=mcs, new_value=soc_in_charger
                )
                self._charging_to_read_soc = False
                await self._kick_off_polling(reason="After force reading soc")
            soc_value = soc_in_charger
        self._log(f"returning: '{soc_value}'.")
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

        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        self._log(f"Called with action '{action}', reason: '{reason}'.")

        if not self._am_i_active:
            self._log("called while _am_i_active == False. Not blocking.")

        action_value = ""

        if action == "start":
            if not await self.is_car_connected():
                self._log("Not performing charger action 'start': No car connected.")
                return
            # if await self._is_charging_or_discharging():
            #     self._log("Not performing charger action 'start': Already charging.")
            #     return
            action_value = self._ACTIONS["start_charging"]

        elif action == "stop":
            # Stop needs to be very reliable, so we always perform this action, even if currently
            # not charging.
            action_value = self._ACTIONS["stop_charging"]

        else:
            # Restart not implemented
            self._log(
                f"Unknown option for action: '{action}'.{reason}", level="WARNING"
            )

        txt = f"set_charger_action: {action}"
        await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_SET_ACTION, value=action_value
        )
        self._log(f"{txt} {reason}")
        return

    async def _set_charge_power(
        self,
        charge_power: int,
        current_soc: int,
        skip_min_soc_check: bool = False,
        source: str = None,
    ):
        """Private function to set desired (dis-)charge power in Watt in the charger.
           Check in place not to discharge below the set minimum.
           Setting the charge_power does not imply starting the charge.

        Args:
            charge_power (int):
                Power in Watt, is checked to be between
                self._max_charge_power_w and -self._max_discharge_power_w
            skip_min_soc_check (bool, optional):
                boolean is used when the check for the minimum soc needs to be skipped.
                This is used when this method is called from the _get_car_soc Defaults to False.
            current_soc:
                for comparison with min_soc
            source (str, optional):
              For logging purposes.
        """
        if not self._am_i_active:
            self._log(
                "called while _am_i_active is false, not blocking.", level="WARNING"
            )

        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        self._log(f"called from {source}, power {charge_power}.")

        ev = await self.get_connected_car()
        if ev is None or ev.min_soc_percent is None:
            self._log(
                "No car connected/not initialised yet, cannot set charge power.",
                level="WARNING",
            )
            return

        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            if current_soc <= ev.min_soc_percent:
                # Fail-safe, this should never happen...
                self._log(
                    f"A discharge is attempted from {source=}, while the current SoC is below the "
                    f"minimum ({ev.min_soc_percent})%. Stopping discharging.",
                    level="WARNING",
                )
                charge_power = 0

        # Clip values to min/max charging current
        if charge_power > self._max_charge_power_w:
            self._log(
                f"Requested charge power {charge_power} W too high, reducing.",
                level="WARNING",
            )
            charge_power = self._max_charge_power_w
        elif charge_power < -self._max_discharge_power_w:
            self._log(
                f"Requested discharge power {charge_power} W too high, reducing.",
                level="WARNING",
            )
            charge_power = -self._max_discharge_power_w

        # TODO:
        # Does this check the actual chargepower (not good) of the requested chargepower (expected)?
        current_charge_power = await self._get_charge_power()

        if current_charge_power == charge_power:
            return

        res = await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_CHARGER_SET_CHARGE_POWER,
            value=charge_power,
        )

        if not res:
            self._log(
                f"Failed to set charge power to {charge_power} W.", level="WARNING"
            )
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

        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        if take_or_give_control == "take":
            if not self._am_i_active:
                self._log(
                    "Trying to take control while _am_i_active == False. Not blocking.",
                    level="WARNING",
                )
            await self._mb_client.write_modbus_register(
                modbus_register=self._MBR_SET_CHARGER_CONTROL,
                value=self._CONTROL_TYPES["remote"],
            )
            await self._mb_client.write_modbus_register(
                modbus_register=self._MBR_CHARGER_AUTOSTART_ON_CONNECT,
                value=self._AUTOSTART_ON_CONNECT_SETTING["disable"],
            )
            await self._mb_client.write_modbus_register(
                modbus_register=self._MBR_SET_SETPOINT_TYPE,
                value=self._SETPOINT_TYPES["power"],
            )
            await self._mb_client.write_modbus_register(
                modbus_register=self._MBR_CHARGER_MODBUS_IDLE_TIMEOUT,
                value=self._CMIT,
            )

        elif take_or_give_control == "give":
            # Setting control to user automatically sets:
            # + autostart to enable
            # + set_point to Ampere
            # + idle timeout to 0 (disabled)
            fake_soc = 50
            await self._set_charge_power(
                charge_power=0,
                current_soc=fake_soc,
                source="_set_charger_control, give control",
            )
            await self._mb_client.write_modbus_register(
                modbus_register=self._MBR_SET_CHARGER_CONTROL,
                value=self._CONTROL_TYPES["user"],
            )
            # For the rare case that forced get soc is in action when the car gets disconnected.
            self._charging_to_read_soc = False

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

    ################################ CHARGER ERROR HANDLING #################################

    async def _modbus_communication_lost(self):
        self._log(
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
            was_car_connected=await self.is_car_connected(),
        )

        # The soc and power are not known any more so let's represent this in the app
        await self._update_evse_entity(
            evse_entity=self._MCE_ACTUAL_POWER, new_value=None
        )
        await self._update_evse_entity(evse_entity=self._MCE_CAR_SOC, new_value=None)
        # Set charger state to error, use quasar state number as the preprocessor will alter to
        # base_evse_state.
        await self._update_evse_entity(evse_entity=self._MCE_CHARGER_STATE, new_value=7)

    async def _modbus_communication_restored(self):
        self._log("Modbus connection to Wallbox Quasar 1 EVSE restored.")

        # TODO: check if it is wise to use this same event for both
        # modbus communication lost and charger error
        self._eb.emit_event(
            "charger_error_state_change", persistent_error=False, was_car_connected=None
        )
        # It could be that the charger was switched off to adjust settings, re-check if the
        # current max_charge_power is lower than the hardware power limit (that can have changed).
        self.set_max_charge_power(self._max_charge_power_w)
        self._kick_off_polling()

    async def _emit_modbus_communication_state(self, can_communicate: bool):
        # To be called every time a successful or failed modbus read occurs
        self._eb.emit_event(
            "charger_communication_state_change", can_communicate=can_communicate
        )

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
        - is_final_check: If True then it is time to call __handle_unrecoverable_error
        :return: nothing
        These are not normal parameters otherwise the run_in cannot be used.
        """
        new_charger_state = kwargs.get("new_charger_state", None)
        is_final_check = kwargs.get("is_final_check", False)
        self._log(f"{new_charger_state=}, {is_final_check=}")
        has_error = False

        # if new_charger_state is None:
        #     new_charger_state = await self._get_charger_state()
        #     self._log(
        #         f"Called without charger state, _get_charger_state: {new_charger_state}."
        #     )

        if new_charger_state in self._ERROR_STATES:
            self._log("Charger in error state", level="WARNING")
            has_error = True

        i = 1
        for entity in self.CHARGER_ERROR_ENTITIES:
            # None = uninitialised, 0 = no error.
            if entity.current_value not in [None, 0]:
                self._log(
                    f"Charger reports error_{i} is {entity.current_value}",
                    level="WARNING",
                )
                has_error = True
                break
            i += 1

        if has_error:
            if is_final_check:
                await self._handle_un_recoverable_error(reason="charger reports error")
                # TODO: check if it is wise to use this same event for both
                # modbus communication lost and charger error
                self._eb.emit_event(
                    "charger_error_state_change",
                    persistent_error=True,
                    was_car_connected=await self.is_car_connected(),
                )

            elif self._timer_id_check_error_state is None:
                self._timer_id_check_error_state = await self._hass.run_in(
                    self._handle_charger_error_state_change,
                    delay=self._MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
                    new_charger_state=None,
                    is_final_check=True,
                )
                return
        else:
            cancel_timer_silently(self._hass, self._timer_id_check_error_state)
            self._timer_id_check_error_state = None
            # TODO: check if it is wise to use this same event for both
            # modbus communication lost and charger error
            self._eb.emit_event(
                "charger_error_state_change",
                persistent_error=False,
                was_car_connected=None,
            )

    ################################# UTILITIE METHODS ################################

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
            self._log(
                f"Number named '{number_name}' with value '{number_to_process}' cannot be processed"
                f"due to ValueError: {ve}.",
                level="WARNING",
            )
            return None

        if isinstance(min_value, (int, float)) and isinstance(max_value, (int, float)):
            if min_value <= processed_number <= max_value:
                return processed_number
            else:
                self._log(
                    f"Number named '{number_name}' with value '{number_to_process}' is out of range"
                    f"min '{min_value}' - max '{max_value}'.",
                    level="WARNING",
                )
                return None
        else:
            return processed_number
