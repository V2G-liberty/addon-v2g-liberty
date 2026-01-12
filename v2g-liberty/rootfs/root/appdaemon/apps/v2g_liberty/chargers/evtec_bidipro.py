"""Client to control a EVtec BiDi10 Electric Vehicle Supply Equipment (EVSE)"""

#######################################################################################
#   This file contains the Modbus address information for the EVtec BiDiPro 10.       #
#   This is provided by the EV2Grid as is.                                            #
#   For reference see https://ev2grid.de/bidipro                                      #
#   EVtec nor EV2Grid provider of the software and does not provide any type of       #
#   service for the software.                                                         #
#######################################################################################

from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from apps.v2g_liberty.utils.hass_util import cancel_timer_silently
from .v2g_modbus_client import V2GmodbusClient
from .modbus_types import ModbusConfigEntity, MBR
from .base_bidirectional_evse import BidirectionalEVSE


class EVtecBiDiProClient(BidirectionalEVSE):
    """Client to control a EVtec BiDi EVSE"""

    ################################################################################
    #  ModBus Registers (MBR)                                                      #
    #  For "read once at boot" registers and "write registers".                    #
    ################################################################################

    _MBR_EVSE_VERSION = MBR(address=0, data_type="string", length=16)
    _MBR_EVSE_SERIAL_NUMBER = MBR(address=16, data_type="string", length=10)
    _MBR_EVSE_MODEL = MBR(address=26, data_type="string", length=10)
    _MBR_CONNECTOR_TYPE = MBR(address=114, data_type="int32", length=2)
    _MBR_MAX_CHARGE_POWER = MBR(address=130, data_type="float32", length=2)
    # Not used yet..
    # _MBR_MIN_CHARGE_POWER: MBR = {"address": 138, "data_type": "float32", "length": 2}
    _CONNECTOR_TYPES: dict[int, str] = {
        0: "Type 2",
        1: "CCS",
        2: "CHAdeMO",
        3: "GBT",
    }

    _MBR_CAR_BATTERY_CAPACITY_WH = MBR(address=158, data_type="float32", length=2)
    _MBR_CAR_MIN_BATTERY_CAPACITY_WH = MBR(address=162, data_type="float32", length=2)

    # Charger setting to go to idle state if no Modbus message is received within this timeout.
    _MBR_IDLE_TIMEOUT_SEC = MBR(address=42, data_type="int32", length=2)

    _CMIT: int = 600  # Communication Idle Timeout in seconds, 600 is max.

    # EVTEC calls this Suspend mode. It is expected that the EVSE in this mode
    # uses less energy?? CHECK!
    _MBR_SET_ACTION = MBR(address=188, data_type="int32", length=2)

    _ACTIONS = {"start_charging": 1, "stop_charging": 0}

    # For setting the desired charge power, reading the actual charging power is done
    # through _MCE_ACTUAL_POWER
    _MBR_SET_CHARGE_POWER = MBR(address=186, data_type="int32", length=2)

    ################################################################################
    #   Modbus Config Entities (MCE)                                               #
    #   These hold the constants for entity (e.g. MBR, min/max value,              #
    #   and store (cache) the values of the charger.                               #
    #   The current_value defaults to None to indicate it has not been touched yet.#
    ################################################################################

    _MCE_CAR_ID = ModbusConfigEntity(
        modbus_register=MBR(address=176, data_type="string", length=10),
        current_value=None,
        change_handler="_handle_car_id_change",
    )

    _MCE_CAR_SOC = ModbusConfigEntity(
        modbus_register=MBR(address=112, data_type="float32", length=2),
        minimum_value=2,
        maximum_value=97,
        relaxed_min_value=1,
        relaxed_max_value=100,
        current_value=None,
        change_handler="_handle_soc_change",
    )

    _MCE_ACTUAL_POWER = ModbusConfigEntity(
        modbus_register=MBR(address=110, data_type="float32", length=2),
        minimum_value=-20000,
        maximum_value=20000,
        current_value=None,
        change_handler="_handle_charge_power_change",
    )

    # The address is the BiDiPro connector state, this represents the EVSE state best.
    # The stored value is the _base_state
    _MCE_EVSE_STATE = ModbusConfigEntity(
        modbus_register=MBR(address=100, data_type="int32", length=2),
        minimum_value=0,
        maximum_value=12,
        current_value=None,
        pre_processor="_get_base_state",
        change_handler="__handle_evse_state_change",
    )

    # Mapping of _MCE_CONNECTOR_STATE to BASE_EVSE_STATE
    _BASE_STATE_MAPPING: dict[int, int] = {
        0: 0,  # Booting
        1: 1,  # No car connected
        2: 9,  # Error
        3: 7,  # OCPP, external control
        4: 1,  # contract authorization -> No car connected
        5: 1,  # plugged in + power check -> No car connected
        6: 1,  # initialize connection + safety checks -> No car connected
        7: 3,  # Charge
        8: 4,  # Discharge
        9: 7,  # OCPP, external control
        10: 2,  # Connected & idle
        11: 1,  # No car connected
        12: 9,  # Error
    }

    _MCE_ERROR = ModbusConfigEntity(
        modbus_register=MBR(address=154, data_type="64int", length=4),
        current_value=None,
        change_handler="_handle_charger_error_state_change",
    )

    # How long should an error state be present before it is communicated to the user.
    # After a restart of the charger, errors can be present for up to 5 minutes.
    _MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 300

    _POLLING_ENTITIES = [
        _MCE_EVSE_STATE,
        _MCE_ACTUAL_POWER,
        _MCE_CAR_SOC,
        _MCE_ERROR,
    ]

    _POLLING_INTERVAL_SECONDS: int = 5

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

        self._am_i_active: bool = False

        self._log("EVtecBiDiProclient initialized.")

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

        result = await mb_client.read_registers([self._MBR_MAX_CHARGE_POWER])

        ####### DEBUGGING CODE #######
        self._log(f"get_max_power_pre_init extended result={result}.")

        result2 = await mb_client.modbus_read(address=130, length=2)
        self._log(f"get_max_power_pre_init Simple result={result2}.")

        mb_client.terminate()
        max_hardware_power = result[0]
        self._log(
            f"Connecting at {host}:{port} succeeded, max power: {max_hardware_power} W"
        )
        await self._emit_modbus_communication_state(can_communicate=True)
        return True, max_hardware_power

    ######################################################################
    #                  INITIALISATION RELATED METHODS                    #
    ######################################################################

    async def initialise_evse(
        self,
        communication_config: dict,
    ):
        """Initialise EVtec BiDiPro EVSE
        To be called from globals.
        """
        self._log(
            f"Initialising EVtecBiDiProclient with config: {communication_config}"
        )

        host = communication_config.get("host", None)
        if not host:
            self._log(
                "EVtec BiDiPro EVSE initialisation failed, no host.",
                level="WARNING",
            )
            raise ValueError("Host required for EVtecBiDiProclient")
        self._mb_host = host
        self._mb_port = communication_config.get("port", 5020)
        self._mb_client = V2GmodbusClient(self._hass, self._cb_modbus_state)
        can_connect = await self._mb_client.initialise(
            host=self._mb_host,
            port=self._mb_port,
        )
        if not can_connect:
            self._log(
                f"EVtec BiDiPro EVSE initialisation failed, cannot connect to "
                f"Modbus host {self._mb_host}:{self._mb_port}.",
                level="WARNING",
            )
            return False

        mcp = await self.get_hardware_power_limit()
        await self.set_max_charge_power(mcp)

        result = self._mb_client.read_registers([self._MBR_CONNECTOR_TYPE])
        connector_type = self._CONNECTOR_TYPES.get(result[0], "Unknown")
        if connector_type != "CSS":
            # For now, just a warning, how to handle this correctly?
            self._log(f"Unexpected connector type: {connector_type}.", level="WARNING")

        self._log(
            f"EVtec BiDiPro EVSE initialised with host: {self._mb_host}, "
            f"Max (dis-)charge power: {self._max_charge_power_w} W"
        )

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
        self._log("Kicking off EVtec BiDiPro EVSE client.")

        self._eb.emit_event(
            "update_charger_info", charger_info=await self._get_charger_info()
        )

        await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_IDLE_TIMEOUT_SEC,
            value=self._CMIT,
        )

        # We always at least need all the information to get started
        # This also creates the entities in HA that many modules depend upon.
        await self._get_and_process_registers(self._POLLING_ENTITIES)

        # SoC is essential for many decisions, so we need to get it as soon as possible.
        # As at init there most likely is no charging in progress this will be the first
        # opportunity to do a poll.
        await self._get_car_soc(force_renew=True)

        await self._kick_off_polling()

    ######################################################################
    #                  PUBLIC METHODS / PROPERTIES                       #
    ######################################################################

    async def get_new_ev_details(self) -> dict[str, object] | None:
        """
        Retrieve details for a newly connected EV that has not yet been registered
        as a V2G Liberty vehicle.

        This method checks whether a car is currently connected, determines whether
        it is already known to the system, and reads key battery parameters from
        the Modbus interface.

        Returns:
            dict[str, object] | None:
                A dictionary containing:
                - ``ev_id`` (str): Identifier of the connected EV.
                - ``battery_capacity_wh`` (int | None): Full battery capacity in Wh,
                or ``None`` if unavailable.
                - ``min_battery_capacity_wh`` (int | None): Minimum usable battery
                capacity in Wh, or ``None`` if unavailable.
                - ``is_new`` (bool): ``True`` if the EV is not yet registered.

                Returns ``None`` if no car is currently connected.

        Raises:
            Any exception raised by ``_mb_client.read_registers`` or
            ``_process_number`` will propagate unless handled internally.
        """
        if not self.is_car_connected:
            return None

        ev_id = self._MCE_CAR_ID.current_value
        if ev_id in [0, None]:
            return None

        ev = self.get_vehicle_by_ev_id(ev_id=ev_id)
        is_new = ev is None

        result = await self._mb_client.read_registers(
            [
                self._MBR_CAR_BATTERY_CAPACITY_WH,
                self._MBR_CAR_MIN_BATTERY_CAPACITY_WH,
            ]
        )

        if result is None:
            bc = None
            mbc = None
        else:
            bc = self._process_number(result[0])
            mbc = self._process_number(result[1])

        return {
            "ev_id": ev_id,
            "battery_capacity_wh": bc,
            "min_battery_capacity_wh": mbc,
            "is_new": is_new,
        }

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop
        Do not cancel polling, the information is still relevant.
        """
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return
        self._log("made inactive")
        await self.stop_charging()
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost"""
        if self._mb_client is None:
            self._log("Modbus client not initialised, aborting", level="WARNING")
            return
        self._log("activated")
        self._am_i_active = True
        await self._get_and_process_registers(self._POLLING_ENTITIES)
        # Eventhough it probably did not stop
        await self._kick_off_polling()

    async def get_hardware_power_limit(self) -> int | None:
        if self._mb_client is None:
            self._log(
                "Modbus client not initialised, cannot get hardware power limit",
                level="WARNING",
            )
            return None
        result = await self._mb_client.read_registers([self._MBR_MAX_CHARGE_POWER])

        if not result:
            self._log("No valid read hardware limit", level="WARNING")
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

    def _get_base_state(self, bidipro_state: int) -> int:
        """'Translate' a 'bidipro state' to 'base evse state'."""
        return self._BASE_STATE_MAPPING.get(
            bidipro_state, 0
        )  # Default to "Booting" if unknown

    async def _get_charger_info(self) -> str:
        try:
            results = await self._mb_client.read_registers(
                [
                    self._MBR_EVSE_VERSION,
                    self._MBR_EVSE_SERIAL_NUMBER,
                    self._MBR_EVSE_MODEL,
                ]
            )
            if results:
                charger_info = (
                    f"EVtec BiDiPro EVSE - Version: {results[0]}, "
                    f"Serial number: {results[1]}, "
                    f"Model: {results[2]}, "
                )
                self._log(charger_info)
                await self._emit_modbus_communication_state(can_communicate=True)
                return charger_info
        except Exception as e:
            self._log(
                f"EVtec BiDiPro EVSE - Failed to get charger info: {e}",
                level="WARNING",
            )

        await self._emit_modbus_communication_state(can_communicate=False)
        return "unknown"

    async def _kick_off_polling(self, reason: str = ""):
        """Start polling

        Args:
            reason (str, optional): For debugging only
        """

        cancel_timer_silently(self._hass,self._poll_timer_handle)
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
        cancel_timer_silently(self._hass,self._poll_timer_handle)
        self._poll_timer_handle = None
        self._eb.emit_event("evse_polled", stop=True)

    async def _get_and_process_evse_data(self, *_args):
        """Retrieve and process all data from EVSE, called from polling timer."""
        # These needs to be in different lists because the
        # modbus addresses in between them do not exist in the EVSE.
        await self._get_and_process_registers(self._POLLING_ENTITIES)
        # await self._get_and_process_registers([self.ENTITY_CHARGER_LOCKED])
        self._eb.emit_event("evse_polled", stop=False)

    async def _get_and_process_registers(self, entities: list[ModbusConfigEntity]):
        """Read values from the EVSE via Modbus and update entity states."""

        if self._mb_client is None:
            self._log("Modbus client not initialised yet, aborting", level="WARNING")
            return

        if not entities:
            self._log("Entities empty, aborting", level="WARNING")
            return

        # Extract MBRs in the same order as entities
        mbrs = [entity.modbus_register for entity in entities]

        # Read all registers in one batch
        results = await self._mb_client.read_registers(modbus_registers=mbrs)

        if not results:
            self._log("Could not read registers, abort processing.", level="WARNING")
            await self._emit_modbus_communication_state(can_communicate=False)
            return

        await self._emit_modbus_communication_state(can_communicate=True)

        # Process each entity with its corresponding result
        for i, entity in enumerate(entities):
            raw_value = results[i]  # Already decoded by MBR.decode()
            self._update_mce(entity, raw_value)

    async def _update_mce(self, mce: ModbusConfigEntity, new_value):
        old_value = mce.current_value
        mce.set_value(new_value=new_value, owner=self)
        new_value = mce.current_value

        # Trigger change handler if value changed
        if old_value != new_value and mce.change_handler:
            try:
                handler = getattr(self, mce.change_handler)
                await handler(new_value, old_value)
            except Exception as e:
                self._log(
                    f"Change_handler '{mce.change_handler}' not called. Exception: {e}.",
                    level="WARNING",
                )

    async def _handle_charge_power_change(self, new_power, old_power):
        self._eb.emit_event("charge_power_change", new_power=new_power)

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
            self._log(
                "No car connected, cannot set changed SoC on car.", level="WARNING"
            )
        else:
            self._connected_car.set_soc(new_soc=new_soc)

    async def _handle_car_id_change(self, new_id, old_id):
        self._log(f"called {new_id=}, {old_id=}.")
        # 0 = no car connected?
        if new_id == 0 or new_id is None:
            # Handeling disconnected state is done in _handle_evse_state_change()
            self._connected_car = None
        else:
            self._connected_car = self.get_vehicle_by_ev_id(new_id)

    async def _handle_evse_state_change(self, new_evse_state: int, old_evse_state: int):
        """Called when _update_evse_entity detects a changed value."""
        self._log(f"called {new_evse_state=}, {old_evse_state=}.")

        if new_evse_state in self._ERROR_STATES or old_evse_state in self._ERROR_STATES:
            # Check if user needs to be notified or if notification process needs to be aborted
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_evse_state, "is_final_check": False}
            )

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
            await self._update_mce(mce=self._MCE_CAR_SOC, new_value=None)

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
            success = self.try_set_connected_vehicle("NissanLeaf")
            if success:
                await self._get_car_soc(force_renew=True)
            self._eb.emit_event("is_car_connected", is_car_connected=True)
        else:
            # From one connected state to an other connected state: not a change that this method
            # needs to react upon.
            pass

        return

    def try_set_connected_vehicle(self, ev_id: str):
        """
        For ISO15118 capable chargers we would get the car info (name) from the charger here.
        For now only one car can be used with V2G Liberty, always a (the same) Nissan Leaf.
        """
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

    async def _get_evse_state(self) -> int:
        """Returns the state accoding to _EVSE_STATES from the base class."""
        charger_state = self._MCE_EVSE_STATE.current_value
        if charger_state is None:
            # This can be the case before initialisation has finished.
            await self._get_and_process_registers([self._MCE_EVSE_STATE])
            charger_state = self._MCE_EVSE_STATE.current_value
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

        ecs = self._MCE_CAR_SOC
        soc_value = ecs.current_value
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
            soc_address = ecs.modbus_address

            # TODO: move to ElectricVehicle class??
            min_value_at_forced_get = ecs.minimum_value
            max_value_at_forced_get = ecs.maximum_value
            relaxed_min_value = ecs.relaxed_min_value
            relaxed_max_value = ecs.relaxed_max_value

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
            await self._update_mce(mce=ecs, new_value=soc_in_charger)
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
        await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_SET_ACTION, value=action_value
        )
        self._log(f"{txt} {reason}")
        return

    async def _set_charge_power(self, charge_power: int, source: str = None):
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
        if charge_power < 0:
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
        # TODO: Check for min (dis)charge power
        current_charge_power = await self._get_charge_power()

        if current_charge_power == charge_power:
            return

        res = await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_SET_CHARGE_POWER, value=charge_power
        )

        if not res:
            self._log(
                f"Failed to set charge power to {charge_power} W.", level="WARNING"
            )
            # If negative value (always) fails, check if grid code is set correct in charger.

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
            "Persistent Modbus connection problem detected in EVtec BiDiPro EVSE.",
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
        await self._update_mce(mce=self._MCE_ACTUAL_POWER, new_value=None)
        await self._update_mce(mce=self._MCE_CAR_SOC, new_value=None)
        # Set charger state to error, use quasar state number as the preprocessor will alter to
        # base_evse_state.
        await self._update_mce(mce=self._MCE_EVSE_STATE, new_value=12)

    async def _modbus_communication_restored(self):
        self._log("Modbus connection to EVtec BiDiPro EVSE restored.")

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
        if self._MCE_ERROR.current_value is not None:
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
            cancel_timer_silently(self._hass,self._timer_id_check_error_state)
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

