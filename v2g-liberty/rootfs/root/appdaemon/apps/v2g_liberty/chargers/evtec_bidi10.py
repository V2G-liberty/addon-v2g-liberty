"""Client to control a EVtec BiDi10 Electric Vehicle Supply Equipment (EVSE)"""

#######################################################################################
#   This file contains the Modbus address information for the EVtec BiDiPro 10.       #
#   This is provided by the EV2Grid as is.                                            #
#   For reference see https://ev2grid.de/didipro                                      #
#   EVtec nor EV2Grid provider of the software and does not provide any type of       #
#   service for the software.                                                         #
#######################################################################################

from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from .v2g_modbus_client import V2GmodbusClient
from .modbus_types import ModbusConfigEntity, RegisterRange
from .base_bidirectional_evse import BidirectionalEVSE


class EVtecBiDi10Client(BidirectionalEVSE):
    """Client to control a EVtec BiDi10 EVSE"""

    ######################################################################
    #                 Modbus addresses for setting values                #
    ######################################################################

    #### NEW FOR BiDiPro10 ###
    # All connector related addresses are on a device_id 2 or higher.
    # TODO: Add a check when a car gets connected. Only CSS and CHAdeMO are bidirectional.
    # Maybe show connector type in UI?
    _CONNECTOR_TYPE_REG: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=12, length=1, data_type="uint16"
        ),
        "minimum_value": 0,
        "maximum_value": 3,
        "current_value": None,
        "change_handler": None,
    }
    _CONNECTOR_STATE_TYPES: dict[int, str] = {
        0: "Type 2",
        1: "CCS",
        2: "CHAdeMO",
        3: "GBT",
    }

    # Charger setting to go to idle state if not receive modbus message within this timeout.
    # Fail-safe in case this software crashes: if timeout passes charger will stop (dis-)charging.
    # New, 0= Disabled, 1 = Enabled. Only on device_id = 1
    _CHARGER_MODBUS_IDLE_TIMEOUT_REG: int = 201
    _CHARGER_MODBUS_IDLE_TIMEOUT_VALUE_REG: int = 202  # New, only on device_id = 1
    # Timeout in seconds. 10 minutes is long, consided the polling frequncy of 5 seconds.
    _CMIT: int = 600  # New. 600 is max.

    # Charger charging can be started/stopped remote (Read/Write)
    # Not implemented: restart and update software
    _SET_ACTION_REGISTER: int = 602  # New, only on device_id = 2 or higher
    _ACTIONS = {"start_charging": 1, "stop_charging": 2}

    # For setting the desired charge power, reading the actual charging power is done
    # through ENTITY_CONNECTOR_CURRENT_POWER
    _CHARGER_SET_CHARGE_POWER_REGISTER: int = 600  # New, only on device_id 2 or higher

    # New, only on device_id = 2 or higher, OR IS IT 31? CHECK!!
    _MAX_CONNECTOR_POWER_W: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=13, length=2, data_type="unit32"
        ),
        "minimum_value": 1,
        "maximum_value": 10000,
        "current_value": None,
        "change_handler": None,
    }

    _MIN_CONNECTOR_POWER_W: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=15, length=2, data_type="unit32"
        ),
        "minimum_value": 1,
        "maximum_value": 10000,
        "current_value": None,
        "change_handler": None,
    }

    _CAR_BATTERY_CAPACITY_WH: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=59, length=2, data_type="unit32"
        ),
        "minimum_value": 5000,
        "maximum_value": 250000,
        "current_value": None,
        "change_handler": None,
    }

    ################################################################################
    #   EVSE Entities                                                              #
    #   These hold the constants for entity (e.g. modbus address, min/max value,   #
    #   and store (cache) the values of the charger.                               #
    #   About the current / previous_value:                                        #
    #    These are initiated with None to indicate they have not been touched yet. #
    ################################################################################

    ENTITY_CHARGER_STATE: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=1, address=100, length=1, data_type="uint16"
        ),
        "minimum_value": 0,
        "maximum_value": 11,
        "current_value": None,
        "change_handler": "__handle_charger_state_change",
    }

    ENTITY_CONNECTOR_CURRENT_POWER: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=9, length=2, data_type="float32"
        ),
        "minimum_value": -10000,
        "maximum_value": 10000,
        "current_value": None,
        "change_handler": "_handle_charge_power_change",
    }

    ENTITY_CONNECTOR_STATE: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=0, length=1, data_type="uint16"
        ),
        "minimum_value": 0,
        "maximum_value": 9,
        "current_value": None,
        "change_handler": "__handle_connector_state_change",
    }
    _CONNECTOR_STATE_TYPES: dict[int, str] = {
        0: "Unavailable",
        1: "Available",
        2: "Occupied",
        3: "Preparing",
        4: "Charging",
        5: "Finishing",
        6: "Suspended EV",
        7: "Suspended EVSE",
        8: "Not ready",
        9: "Faulted",
    }
    ENTITY_CONNECTOR_CHARGE_STATE: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=1, length=1, data_type="uint16"
        ),
        "minimum_value": 0,
        "maximum_value": 7,
        "current_value": None,
        "change_handler": "__handle_connector_state_change",
    }
    CONNECTOR_CHARGE_STATES: dict[int, str] = {
        1: "Connected, waiting for release (by RFID or Lokall)",
        2: "Charging process starts",
        3: "Shop",
        4: "Suspended (loading paused)",
        5: "Charging process successfully completed (vehicle still plugged in)",
        6: "Charging process completed by user (vehicle still plugged in)",
        7: "Charging ended with error (vehicle still connected)",
    }

    ENTITY_CAR_SOC: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=2, address=11, length=1, data_type="uint16"
        ),
        "minimum_value": 2,
        "maximum_value": 97,
        "relaxed_min_value": 1,
        "relaxed_max_value": 100,
        "current_value": None,
        "change_handler": "_handle_soc_change",
    }

    ### New, only on device_id = 1. Error list too long to add here.
    ENTITY_CHARGER_ERROR: ModbusConfigEntity = {
        "modbus_range": RegisterRange(
            device_id=1, address=103, length=1, data_type="uint16"
        ),
        "minimum_value": 0,
        "maximum_value": 65535,
        "current_value": None,
        "change_handler": "_handle_charger_error_state_change",
    }

    CHARGER_POLLING_ENTITIES = [
        ENTITY_CONNECTOR_STATE,
        ENTITY_CONNECTOR_CHARGE_STATE,
        ENTITY_CAR_SOC,
        ENTITY_CHARGER_ERROR,
    ]

    _POLLING_INTERVAL_SECONDS: int = 5

    # How long should an error state be present before it is communicated to the user.
    # After a restart of the charger, errors can be present for up to 5 minutes.
    _MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 300

    def __init__(
        self, hass: Hass, event_bus: EventBus, get_vehicle_by_name_func: callable
    ):
        super().__init__()
        self._hass = hass
        self._eb = event_bus
        self._log = get_class_method_logger(hass.log)

        # function to get the connected vehicle by name from v2g-liberty
        self.get_vehicle_by_name = get_vehicle_by_name_func

        # Modbus specifics
        self._mb_client: V2GmodbusClient | None = None
        self._mb_host: str | None = None
        self._mb_port: int = 5020

        # User limit the max (dis-)charger power.
        self._max_charge_power_w: int | None = None
        self._max_discharge_power_w: int | None = None

        self._connected_car: ElectricVehicle | None = None
        self._actual_charge_power: int | None = None

        # Polling variables
        self._poll_timer_handle: str | None = None

        self._timer_id_check_error_state: str | None = None

        # Variable for checking if the (base) state of the charger has changed. It should not be
        # used anwhere else.
        self._evse_state: int | None = None

        # Block events from firing during charge that is needed to read a SoC.
        self._charging_to_read_soc: bool = False

        self._am_i_active: bool = False

        self._log("EVtecBiDiPro10client initialized.")

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
        connected = mb_client.initialise(host=host, port=port)
        if not connected:
            self._log(
                f"Connecting at {host}:{port} failed, max power: None", level="WARNING"
            )
            return False, None

        result = await mb_client.read_register_ranges(
            [self._MAX_CONNECTOR_POWER_W["modbus_range"]]
        )
        mb_client.terminate()
        max_hardware_power = result[0]
        self._log(
            f"Connecting at {host}:{port} succeeded, max power: {max_hardware_power} W"
        )
        await self._emit_modbus_communication_state(can_communicate=True)
        return True, max_hardware_power

    async def initialise_evse(
        self,
        communication_config: dict,
    ):
        """Initialise EVtec BiDiPro10 EVSE
        To be called from globals.
        """
        self._log(
            f"Initialising EVtecBiDiPro10client with config: {communication_config}"
        )

        host = communication_config.get("host", None)
        if not host:
            self._log(
                "EVtec BiDiPro10 EVSE initialisation failed, no host.",
                level="WARNING",
            )
            raise ValueError("Host required for EVtecBiDiPro10client")
        self._mb_host = host
        self._mb_port = communication_config.get("port", 5020)
        self._mb_client = V2GmodbusClient(self._hass, self._cb_modbus_state)
        connected = await self._mb_client.initialise(
            host=self._mb_host,
            port=self._mb_port,
        )
        if not connected:
            self._log(
                f"EVtec BiDiPro10 EVSE initialisation failed, cannot connect to "
                f"Modbus host {self._mb_host}:{self._mb_port}.",
                level="WARNING",
            )
            return False

        mcp = await self.get_hardware_power_limit()
        await self.set_max_charge_power(mcp)

        self._log(
            f"EVtec BiDiPro10 EVSE initialised with host: {self._mb_host}, "
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
                "The mb_client not initialised (host missing?), cannot complete kick_off. Aborting",
                level="WARNING",
            )
            return
        self._log("Kicking off EVtec BiDiPro10 EVSE client.")

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
        # await self._set_charger_control("give")
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return
        self._log("activated")
        self._am_i_active = True
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        # Eventhough it probably did not stop
        await self._kick_off_polling()

    async def get_hardware_power_limit(self) -> int | None:
        if self._mb_client is None:
            self._log(
                "Modbus client not initialised, cannot get hardware power limit",
                level="WARNING",
            )
            return None
        result = await self._mb_client.read_register_ranges(
            self._MAX_CONNECTOR_POWER_W["modbus_range"]
        )

        if not result:
            self._log(f"No valid read hardware limit {result}", level="WARNING")
            await self._emit_modbus_communication_state(can_communicate=False)
            return None
        result = self._process_number(result[0])
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
            source="start_charging",
        )

    async def stop_charging(self, source: str = None):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self._log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )
        await self._set_charger_action("stop", reason="stop_charging")
        await self._set_charge_power(charge_power=0, source="stop_charging")

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
        # 'bdp' stands for BiDiPro, 'ma' is abbriviation of modbus address
        bdp_version_ma = 101
        bdp_number_of_connectors_ma = 102
        bdp_serial_number_ma = 110
        bdp_model_ma = 130

        length = bdp_model_ma - bdp_version_ma + 1
        try:
            results = await self._mb_client.modbus_read(
                address=bdp_version_ma,
                length=length,
                source="_get_charger_info",
            )
            if results:
                charger_info = (
                    f"EVtec BiDiPro10 EVSE - Version: {results[0]}, "
                    f"Serial number: {results[bdp_serial_number_ma - bdp_version_ma]}, "
                    f"Model: {results[bdp_model_ma - bdp_version_ma]}, "
                    f"Connectors: {results[bdp_number_of_connectors_ma - bdp_version_ma]}."
                )
                self._log(charger_info)
                await self._emit_modbus_communication_state(can_communicate=True)
                return charger_info
        except Exception as e:
            self._log(
                f"EVtec BiDiPro10 EVSE - Failed to get charger info: {e}",
                level="WARNING",
            )

        await self._emit_modbus_communication_state(can_communicate=False)
        return "unknown"

    async def _kick_off_polling(self, reason: str = ""):
        """Start polling

        Args:
            reason (str, optional): For debugging only
        """

        self._cancel_timer(self._poll_timer_handle)
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
        self._cancel_timer(self._poll_timer_handle)
        self._poll_timer_handle = None
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

        if self._mb_client is None:
            self._log("Mobus client not initialised yet, aborting", level="WARNING")
            return

        start = entities[0]["modbus_address"]
        end = entities[-1]["modbus_address"]

        length = end - start + 1
        results = await self._mb_client.modbus_read(
            address=start, length=length, source="_get_and_process_registers"
        )
        if not results:
            # Could not read
            self._log("results is None, abort processing.", level="WARNING")
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
                        self._log(
                            f"New value {new_state} for entity '{entity_name}' "
                            f"out of range {entity['minimum_value']} "
                            f"- {entity['maximum_value']} but current value is None, so this polled"
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

    def _get_base_state(self) -> int:
        """Based on charger info return a 'base evse state'."""

        ############################ CHECK FOR ERROR STATE #########################################
        charger_error = self.ENTITY_CHARGER_ERROR["current_value"]
        if charger_error is not None:
            self._log(f"Charger in error, error_no: {charger_error}.", level="WARNING")
            return 9

        charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        if charger_state == 10:
            self._log(f"Charger faulted.", level="WARNING")
            return 9

        connector_state = self.ENTITY_CONNECTOR_STATE["current_value"]
        if connector_state == 9:
            self._log(f"Connector faulted.", level="WARNING")
            return 9

        connector_charge_state = self.ENTITY_CONNECTOR_STATE["current_value"]
        if connector_charge_state == 7:
            # TODO: Check if this really should translate to base error state.
            self._log(
                "Charging ended with error (vehicle still connected)", level="WARNING"
            )
            return 9

        ############################ Check for Communication error #################################
        if charger_state == 100:
            # 100 is not a state that the charger returns but it is set by this software when
            # modbus communication is not possible.
            self._log("Communication error", level="WARNING")
            return 10

        ############################ Check for Starting up #########################################
        if charger_state == 0:
            self._log("Charger starting up")
            return 0
        if connector_state in [0, 8]:
            # TODO: Or should this be regarded "controlled by other app??"
            self._log("Connector not ready")
            return 0

        # TODO: check if this is a relevant situation for the BiDiPro10
        ############################ Check for Controlled by other app #############################
        # if charger_state in [0, 1] and connector_state == 0:  # Unclear from table, adjust as needed
        #     return 7  # Controlled by other app

        # From here on charger_state can be assumed 1 and error state can be assumed None,
        # not checking those vailues any further.

        ############################ Check for Idle ################################################
        if connector_state in [2, 6, 7] and connector_charge_state in [4, 5, 6]:
            return 2

        ############################ Check for No car connected ####################################
        if connector_state == 1 and connector_charge_state == 0:
            return 1

        # Check for Charging
        if connector_state in [3, 4, 5] and connector_charge_state in [2, 3]:
            charge_power = self.ENTITY_CONNECTOR_CURRENT_POWER["current_value"]
            if charge_power > 0:
                # if charge_power =  positive and loadbalancer not active
                # TODO: Check if loadbalancer is actively reducing charge power, if so return 5
                return 3  # Charging
            elif charge_power < 0:
                # if charge_power =  negative and loadbalancer not active
                # TODO: Check if loadbalancer is actively reducing charge power, if so return 6
                return 4  # Discharging
            else:
                # charge_power = 0, not charging
                # TODO: Check if loadbalancer is actively reducing charge power to 0, if so return 3
                self._log(
                    f"connector state not {connector_charge_state} matching charge "
                    f"power {charge_power}",
                    level="WARNING",
                )
                return 2  # Idle

        # Check for Locked
        if connector_state == 0 and connector_charge_state == 1:
            return 8  # Locked

        # Default: Communication error (unknown state)
        return 10

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
        evse_entity["current_value"] = new_value

        if current_value != new_value:
            # Call change_handler if defined
            # change_handler method must be async and have two parameters: new_value, old_value
            if "change_handler" in evse_entity.keys():
                change_handler_method_name = evse_entity["change_handler"]
                if isinstance(change_handler_method_name, str):
                    try:
                        change_handler_method = getattr(
                            self, change_handler_method_name
                        )
                        await change_handler_method(new_value, current_value)
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
        if (new_power > 0) != (old_power > 0) or (new_power == 0) != (old_power == 0):
            # Power has changed sugnificantly from + to - or 0 to not 0 vv
            self._recalculate_evse_state()

    async def _handle_charger_error_registers_state_change(self, new_error, old_error):
        # This is the case for the ENTITY_ERROR_1..4. The charger_state
        # does not necessarily change only (one or more of) these error-states.
        # So the state is not added to the call.
        self._log(f"new_error: {new_error}, old_error: {old_error}.", level="WARNING")
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

    async def _recalculate_evse_state(self):
        bcs = self._get_base_state()
        if bcs != self._evse_state:
            self._handle_charger_state_change(
                new_evse_state=bcs, old_evse_state=self._evse_state
            )
            self._evse_state = bcs

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

        if new_evse_state in self._DISCONNECTED_STATES:
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
        ev_name = "NissanLeaf"
        ev = self.get_vehicle_by_name(ev_name)
        if ev is None:
            self._log(
                f"Cannot set connected vehicle, no vehicle with name '{ev_name}' found.",
                level="WARNING",
            )
            return False
        else:
            self._connected_car = ev
            return True

    async def _get_charge_power(self) -> int | None:
        state = self.ENTITY_CONNECTOR_CURRENT_POWER["current_value"]
        if state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self.ENTITY_CONNECTOR_CURRENT_POWER])
            state = self.ENTITY_CONNECTOR_CURRENT_POWER["current_value"]
        return state

    async def _get_evse_state(self) -> int:
        """Returns the state accoding to _EVSE_STATES from the base class."""
        charger_state = self.ENTITY_CHARGER_STATE["current_value"]
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self.ENTITY_CHARGER_STATE])
            charger_state = self.ENTITY_CHARGER_STATE["current_value"]
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

    async def _get_car_soc(self, force_renew: bool = True) -> int | None:
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

        ecs = self.ENTITY_CAR_SOC
        soc_value = ecs["current_value"]
        should_be_renewed = False
        if soc_value is None:
            # This can occur if it is queried for the first time and no polling has taken place
            # yet. Then the entity does not exist yet and returns None.
            self._log("current_value is None so should_be_renewed = True")
            should_be_renewed = True

        if force_renew:
            # Needed usually only when car has been disconnected. The polling then does not read SoC
            # and this probably changed and polling might not have picked this up yet.
            self._log("force_renew == True so should_be_renewed = True")
            should_be_renewed = True

        if should_be_renewed:
            self._log("old or invalid SoC in HA Entity: renew")
            soc_address = ecs["modbus_address"]

            # TODO: move to ElectricVehicle class??
            min_value_at_forced_get = ecs["minimum_value"]
            max_value_at_forced_get = ecs["maximum_value"]
            relaxed_min_value = ecs["relaxed_min_value"]
            relaxed_max_value = ecs["relaxed_max_value"]

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
                    evse_entity=ecs, new_value=soc_in_charger
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
                await self._set_charge_power(
                    charge_power=1, skip_min_soc_check=True, source="get_car_soc"
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
            if await self._is_charging_or_discharging():
                self._log("Not performing charger action 'start': Already charging.")
                return
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
        await self._mb_client.modbus_write(
            address=self._SET_ACTION_REGISTER, value=action_value, source=txt
        )
        self._log(f"{txt} {reason}")
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
                self._max_charge_power_w and -self._max_discharge_power_w
            skip_min_soc_check (bool, optional):
                boolean is used when the check for the minimum soc needs to be skipped.
                This is used when this method is called from the __get_car_soc Defaults to False.
            source (str, optional):
              For logging purposes.
        """
        self._log(f"called from {source}, power {charge_power}.")
        if not self._am_i_active:
            self._log(
                "called while _am_i_active is false, not blocking.", level="WARNING"
            )

        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return

        self._log(f"called from {source}, power {charge_power}.")

        ev = await self.get_connected_car()
        if ev is None:
            self._log("No car connected, cannot set charge power.", level="WARNING")
            return
        # Make sure that discharging does not occur below minimum SoC.
        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self._get_car_soc()
            if current_soc is None:
                self._log(
                    "current SoC is 'unavailable', only expected when car is not connected",
                    level="WARNING",
                )
            elif current_soc <= ev.min_soc_percent:
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

        current_charge_power = await self._get_charge_power()

        if current_charge_power == charge_power:
            return

        res = await self._mb_client.modbus_write(
            address=self._CHARGER_SET_CHARGE_POWER_REGISTER,
            value=charge_power,
            source=f"set_charge_power, from {source}",
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
            # await self._mb_client.modbus_write(
            #     address=self._SET_CHARGER_CONTROL_REGISTER,
            #     value=self._CONTROL_TYPES["remote"],
            #     source="_set_charger_control, take control",
            # )
            # await self._mb_client.modbus_write(
            #     address=self.CHARGER_AUTOSTART_ON_CONNECT_REGISTER,
            #     value=self.AUTOSTART_ON_CONNECT_SETTING["disable"],
            #     source="_set_charger_control, set_auto_connect",
            # )
            # await self._mb_client.modbus_write(
            #     address=self.SET_SETPOINT_TYPE_REGISTER,
            #     value=self.SETPOINT_TYPES["power"],
            #     source="_set_charger_control: power",
            # )
            await self._mb_client.modbus_write(
                address=self.CHARGER_MODBUS_IDLE_TIMEOUT_REGISTER,
                value=self._CMIT,
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
            # await self._mb_client.modbus_write(
            #     address=self.SET_CHARGER_CONTROL_REGISTER,
            #     value=self.CONTROL_TYPES["user"],
            #     source="_set_charger_control, give control",
            # )
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

    async def _modbus_communication_lost(self):
        self._log(
            "Persistent Modbus connection problem detected in EVtec BiDiPro10 EVSE.",
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
            evse_entity=self.ENTITY_CONNECTOR_CURRENT_POWER, new_value=None
        )
        await self._update_evse_entity(evse_entity=self.ENTITY_CAR_SOC, new_value=None)
        # Set charger state to error, use quasar state number as the preprocessor will alter to
        # base_evse_state.
        await self._update_evse_entity(
            evse_entity=self.ENTITY_CHARGER_STATE, new_value=10
        )

    async def _modbus_communication_restored(self):
        self._log("Modbus connection to EVtec BiDiPro10 EVSE restored.")

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

        # None = uninitialised, 0 = no error.
        if self.ENTITY_CHARGER_ERROR["current_value"] is not None:
            has_error = True

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
            self._cancel_timer(self._timer_id_check_error_state)
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

    def _cancel_timer(self, timer_id: str):
        """Utility function to silently cancel a timer.
        Born because the "silent" flag in cancel_timer does not work and the
        logs get flooded with useless warnings.

        Args:
            timer_id: timer_handle to cancel
        """
        if timer_id in [None, ""]:
            return
        if self._hass.timer_running(timer_id):
            silent = True  # Does not really work
            self._hass.cancel_timer(timer_id, silent)
