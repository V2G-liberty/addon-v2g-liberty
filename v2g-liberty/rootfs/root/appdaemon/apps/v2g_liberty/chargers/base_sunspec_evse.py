"""Base class for SunSpec-compliant EVSE chargers.

This module provides an abstract base class for implementing SunSpec Modbus
chargers. SunSpec is a standardized communication protocol for Distributed
Energy Resources (DER) devices.

Key SunSpec-specific features handled by this base class:
- Scale factors: SunSpec uses integer registers with scale factors (powers of 10)
- Standard models: Common Model, DER AC Measurement, DER Capacity, DER Controls
- State mapping: SunSpec inverter states mapped to base EVSE states

Subclasses must implement charger-specific details like:
- SoC register location (varies by charger)
- Default Modbus port
- Any custom registers or behaviors
"""

from abc import abstractmethod
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.log_wrapper import get_class_method_logger
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from apps.v2g_liberty.utils.hass_util import cancel_timer_silently
from .v2g_modbus_client import V2GmodbusClient
from .modbus_types import ModbusConfigEntity, MBR
from .base_bidirectional_evse import BidirectionalEVSE


class BaseSunSpecEVSE(BidirectionalEVSE):
    """Abstract base class for SunSpec-compliant bidirectional EVSE chargers.

    This class handles SunSpec-specific patterns including:
    - Reading and applying scale factors for power values
    - Standard DER model register definitions
    - State mapping from SunSpec inverter states to base EVSE states

    Subclasses must implement:
    - _get_soc_mce(): Return the charger-specific SoC ModbusConfigEntity
    - _get_default_port(): Return the default Modbus TCP port
    - _get_charger_name(): Return a human-readable charger name
    """

    ################################################################################
    #  SunSpec Scale Factor Registers                                              #
    #  These are read once at initialization and cached for the session.           #
    ################################################################################

    _MBR_W_SF = MBR(address=40163, data_type="int16", length=1)
    _MBR_VA_SF = MBR(address=40164, data_type="int16", length=1)
    _MBR_VAR_SF = MBR(address=40165, data_type="int16", length=1)

    ################################################################################
    #  SunSpec Common Model Registers (40000+)                                     #
    ################################################################################

    _MBR_MANUFACTURER = MBR(address=40003, data_type="string", length=16)
    _MBR_MODEL = MBR(address=40019, data_type="string", length=16)
    _MBR_SERIAL_NUMBER = MBR(address=40051, data_type="string", length=16)

    ################################################################################
    #  SunSpec DER AC Measurement Model (40070+)                                   #
    ################################################################################

    # Operating state of the DER
    _MBR_OPERATING_STATE = MBR(address=40073, data_type="uint16", length=1)

    # Inverter state (enumerated) - used for state mapping
    _MBR_INVERTER_STATE = MBR(address=40074, data_type="uint16", length=1)

    # Grid connection state
    _MBR_CONN_STATE = MBR(address=40075, data_type="uint16", length=1)

    # Total active power (needs W_SF scale factor applied)
    _MBR_ACTUAL_POWER = MBR(address=40080, data_type="int16", length=1)

    ################################################################################
    #  SunSpec DER Capacity Model (40225+)                                         #
    ################################################################################

    _MBR_MAX_CHARGE_RATE = MBR(address=40235, data_type="uint16", length=1)
    _MBR_MAX_DISCHARGE_RATE = MBR(address=40236, data_type="uint16", length=1)

    ################################################################################
    #  SunSpec DER Controls (40291+)                                               #
    ################################################################################

    # Active power setpoint in watts
    _MBR_POWER_SETPOINT = MBR(address=40301, data_type="int16", length=1)

    ################################################################################
    #  SunSpec State Mapping                                                       #
    #  Maps SunSpec InvSt (inverter state) to base EVSE states (0-10)              #
    ################################################################################

    _SUNSPEC_STATE_MAPPING: dict[int, int] = {
        1: 1,   # Off -> No car connected
        2: 0,   # Sleeping (auto-shutdown) -> Starting up
        3: 0,   # Starting -> Starting up
        4: 2,   # Tracking MPPT -> Idle
        5: 5,   # Forced Power Reduction -> Charging (reduced)
        6: 2,   # Shutting down -> Idle
        7: 9,   # Fault -> Error
        8: 2,   # Standby -> Idle
        9: 3,   # Charging -> Charging
        10: 4,  # Discharging -> Discharging
    }

    ################################################################################
    #  Modbus Config Entities (MCE) for polled registers                           #
    ################################################################################

    _MCE_INVERTER_STATE = ModbusConfigEntity(
        modbus_register=_MBR_INVERTER_STATE,
        minimum_value=0,
        maximum_value=20,
        current_value=None,
        pre_processor="_get_base_state",
        change_handler="_handle_inverter_state_change",
    )

    _MCE_ACTUAL_POWER = ModbusConfigEntity(
        modbus_register=_MBR_ACTUAL_POWER,
        minimum_value=-50000,
        maximum_value=50000,
        current_value=None,
        pre_processor="_apply_power_scale_factor",
        change_handler="_handle_charge_power_change",
    )

    _MCE_CONN_STATE = ModbusConfigEntity(
        modbus_register=_MBR_CONN_STATE,
        minimum_value=0,
        maximum_value=10,
        current_value=None,
        change_handler="_handle_conn_state_change",
    )

    # How long should an error state be present before it is communicated to the user.
    _MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 300

    _POLLING_INTERVAL_SECONDS: int = 5

    def __init__(
        self, hass: Hass, event_bus: EventBus, get_vehicle_by_ev_id_func: callable
    ):
        super().__init__()
        self._hass = hass
        self._eb = event_bus
        self._log = get_class_method_logger(hass.log)

        # Function to get the connected vehicle by name from v2g-liberty
        self.get_vehicle_by_ev_id = get_vehicle_by_ev_id_func

        # Modbus specifics
        self._mb_client: V2GmodbusClient | None = None
        self._mb_host: str | None = None
        self._mb_port: int = self._get_default_port()

        # Scale factors (read during initialization)
        self._w_sf: int = 0  # Active power scale factor
        self._va_sf: int = 0  # Apparent power scale factor
        self._var_sf: int = 0  # Reactive power scale factor

        # User limit the max (dis-)charger power.
        self._max_charge_power_w: int | None = None
        self._max_discharge_power_w: int | None = None

        # Hardware limits (read during initialization)
        self._hardware_max_charge_power_w: int | None = None
        self._hardware_max_discharge_power_w: int | None = None

        self._connected_car: ElectricVehicle | None = None
        self._actual_charge_power: int | None = None

        # Polling variables
        self._poll_timer_handle: str | None = None
        self._timer_id_check_error_state: str | None = None

        # Variable for checking if the (base) state of the charger has changed.
        self._evse_state: int | None = None

        self._am_i_active: bool = False

        self._log(f"{self._get_charger_name()} client initialized.")

    ################################################################################
    #  Abstract methods - must be implemented by subclasses                        #
    ################################################################################

    @abstractmethod
    def _get_soc_mce(self) -> ModbusConfigEntity:
        """Return the charger-specific SoC ModbusConfigEntity.

        SunSpec doesn't define a standard SoC register for EV chargers,
        so each charger implementation must define its own.

        Returns:
            ModbusConfigEntity: The MCE for reading car SoC.
        """
        raise NotImplementedError("Subclasses must implement _get_soc_mce()")

    @abstractmethod
    def _get_default_port(self) -> int:
        """Return the default Modbus TCP port for this charger.

        Returns:
            int: Default Modbus TCP port number.
        """
        raise NotImplementedError("Subclasses must implement _get_default_port()")

    @abstractmethod
    def _get_charger_name(self) -> str:
        """Return a human-readable name for this charger type.

        Returns:
            str: Charger name for logging and UI display.
        """
        raise NotImplementedError("Subclasses must implement _get_charger_name()")

    ################################################################################
    #  Scale factor methods                                                        #
    ################################################################################

    async def _read_scale_factors(self) -> bool:
        """Read and cache SunSpec scale factors from the charger.

        Scale factors are powers of 10 used to convert integer register
        values to actual floating-point values. Must be called during
        initialization BEFORE reading any power values.

        Returns:
            bool: True if scale factors were read successfully.
        """
        if self._mb_client is None:
            self._log("Cannot read scale factors: Modbus client not initialized",
                      level="WARNING")
            return False

        results = await self._mb_client.read_registers([
            self._MBR_W_SF,
            self._MBR_VA_SF,
            self._MBR_VAR_SF,
        ])

        if not results or None in results:
            self._log("Failed to read scale factors, using defaults (0)",
                      level="WARNING")
            return False

        self._w_sf = results[0]
        self._va_sf = results[1]
        self._var_sf = results[2]

        self._log(f"Scale factors read: W_SF={self._w_sf}, "
                  f"VA_SF={self._va_sf}, VAR_SF={self._var_sf}")
        return True

    def _apply_power_scale_factor(self, raw_value: int) -> int | None:
        """Pre-processor: Apply W_SF scale factor to raw register value.

        This is called as a pre_processor for the _MCE_ACTUAL_POWER entity.

        Args:
            raw_value: Raw integer value from the Modbus register.

        Returns:
            int | None: Scaled power value in Watts, or None if input is None.
        """
        if raw_value is None:
            return None
        # Scale factor is a power of 10, e.g., -2 means multiply by 0.01
        scaled = raw_value * (10 ** self._w_sf)
        return int(scaled)

    def _remove_power_scale_factor(self, watts: int) -> int:
        """Remove scale factor from watts for writing to register.

        Args:
            watts: Power value in Watts.

        Returns:
            int: Value to write to the Modbus register.
        """
        if watts is None:
            return 0
        # Reverse the scale factor
        return int(watts / (10 ** self._w_sf))

    ################################################################################
    #  State mapping                                                               #
    ################################################################################

    def _get_base_state(self, sunspec_state: int) -> int:
        """Translate a SunSpec inverter state to base EVSE state.

        This is called as a pre_processor for the _MCE_INVERTER_STATE entity.

        Args:
            sunspec_state: SunSpec InvSt enumeration value.

        Returns:
            int: Base EVSE state (0-10).
        """
        return self._SUNSPEC_STATE_MAPPING.get(sunspec_state, 0)  # Default to "Starting up"

    ################################################################################
    #  Polling entities                                                            #
    ################################################################################

    def _get_polling_entities(self) -> list[ModbusConfigEntity]:
        """Return all MCEs to poll every cycle.

        Includes base SunSpec entities plus charger-specific SoC entity.

        Returns:
            list[ModbusConfigEntity]: List of entities to poll.
        """
        return [
            self._MCE_INVERTER_STATE,
            self._MCE_ACTUAL_POWER,
            self._MCE_CONN_STATE,
            self._get_soc_mce(),
        ]

    ################################################################################
    #  Pre-initialization (for UI validation)                                      #
    ################################################################################

    async def get_max_power_pre_init(
        self, host: str, port: int | None = None
    ) -> tuple[bool, int | None]:
        """Test connection and get max power before full initialization.

        This method is used by the UI to validate connection settings
        before saving them.

        Args:
            host: IP address or hostname of the charger.
            port: Modbus TCP port (uses default if None).

        Returns:
            tuple[bool, int | None]: (success, max_power_watts or None)
        """
        if port is None:
            port = self._get_default_port()

        mb_client = V2GmodbusClient(self._hass)
        connected = await mb_client.initialise(host=host, port=port)
        if not connected:
            self._log(f"Connection to {host}:{port} failed", level="WARNING")
            return False, None

        # Read scale factor first
        sf_results = await mb_client.read_registers([self._MBR_W_SF])
        w_sf = sf_results[0] if sf_results and sf_results[0] is not None else 0

        # Read max charge rate
        results = await mb_client.read_registers([self._MBR_MAX_CHARGE_RATE])
        mb_client.terminate()

        if not results or results[0] is None:
            self._log("Failed to read max charge rate", level="WARNING")
            return False, None

        # Apply scale factor
        max_power = int(results[0] * (10 ** w_sf))

        self._log(f"Connection to {host}:{port} succeeded, max power: {max_power} W")
        await self._emit_modbus_communication_state(can_communicate=True)
        return True, max_power

    ################################################################################
    #  Initialization                                                              #
    ################################################################################

    async def initialise_evse(self, communication_config: dict):
        """Initialize the SunSpec EVSE charger.

        Args:
            communication_config: Dict containing 'host' and optionally 'port'.

        Raises:
            ValueError: If host is not provided.
        """
        self._log(f"Initializing {self._get_charger_name()} with config: "
                  f"{communication_config}")

        host = communication_config.get("host", None)
        if not host:
            self._log(f"{self._get_charger_name()} initialization failed, no host.",
                      level="WARNING")
            raise ValueError(f"Host required for {self._get_charger_name()}")

        self._mb_host = host
        self._mb_port = communication_config.get("port", self._get_default_port())

        self._mb_client = V2GmodbusClient(self._hass, self._cb_modbus_state)
        can_connect = await self._mb_client.initialise(
            host=self._mb_host,
            port=self._mb_port,
        )

        if not can_connect:
            self._log(f"{self._get_charger_name()} initialization failed, "
                      f"cannot connect to {self._mb_host}:{self._mb_port}.",
                      level="WARNING")
            return False

        # Read scale factors FIRST before any power readings
        await self._read_scale_factors()

        # Read hardware power limits
        await self._read_hardware_power_limits()

        # Set initial max power to hardware limits
        if self._hardware_max_charge_power_w:
            await self.set_max_charge_power(self._hardware_max_charge_power_w)
        if self._hardware_max_discharge_power_w:
            await self.set_max_discharge_power(self._hardware_max_discharge_power_w)

        self._log(f"{self._get_charger_name()} initialized with host: {self._mb_host}, "
                  f"Max charge: {self._max_charge_power_w} W, "
                  f"Max discharge: {self._max_discharge_power_w} W")
        return True

    async def _read_hardware_power_limits(self):
        """Read hardware power limits from the charger."""
        if self._mb_client is None:
            return

        results = await self._mb_client.read_registers([
            self._MBR_MAX_CHARGE_RATE,
            self._MBR_MAX_DISCHARGE_RATE,
        ])

        if results:
            if results[0] is not None:
                self._hardware_max_charge_power_w = int(results[0] * (10 ** self._w_sf))
            if results[1] is not None:
                self._hardware_max_discharge_power_w = int(results[1] * (10 ** self._w_sf))

            self._log(f"Hardware limits: charge={self._hardware_max_charge_power_w} W, "
                      f"discharge={self._hardware_max_discharge_power_w} W")

    async def kick_off_evse(self):
        """Start the EVSE after initialization is complete.

        Called from v2g-liberty after its own init is complete.
        """
        if self._mb_host is None:
            self._log("Modbus client not initialized, cannot kick off.",
                      level="WARNING")
            return

        self._log(f"Kicking off {self._get_charger_name()} client.")

        # Emit charger info
        self._eb.emit_event(
            "update_charger_info", charger_info=await self._get_charger_info()
        )

        # Initial read of all polling entities
        await self._get_and_process_registers(self._get_polling_entities())

        # Force read SoC
        await self._get_car_soc(force_renew=True)

        # Start polling
        await self._kick_off_polling()

    async def _get_charger_info(self) -> str:
        """Read and return charger identification info."""
        try:
            results = await self._mb_client.read_registers([
                self._MBR_MANUFACTURER,
                self._MBR_MODEL,
                self._MBR_SERIAL_NUMBER,
            ])
            if results:
                charger_info = (
                    f"{self._get_charger_name()} - "
                    f"Manufacturer: {results[0]}, "
                    f"Model: {results[1]}, "
                    f"Serial: {results[2]}"
                )
                self._log(charger_info)
                await self._emit_modbus_communication_state(can_communicate=True)
                return charger_info
        except Exception as e:
            self._log(f"Failed to get charger info: {e}", level="WARNING")

        await self._emit_modbus_communication_state(can_communicate=False)
        return "unknown"

    ################################################################################
    #  Public methods / properties                                                 #
    ################################################################################

    async def set_inactive(self):
        """Called when charge_mode in UI is switched to Stop."""
        if self._mb_client is None:
            self._log("Modbus client not initialized, aborting", level="WARNING")
            return
        self._log("Set inactive")
        await self.stop_charging()
        self._am_i_active = False

    async def set_active(self):
        """Called when charge_mode in UI is switched to Automatic or Boost."""
        if self._mb_client is None:
            self._log("Modbus client not initialized, aborting", level="WARNING")
            return
        self._log("Set active")
        self._am_i_active = True
        await self._get_and_process_registers(self._get_polling_entities())
        await self._kick_off_polling()

    async def get_hardware_power_limit(self) -> int | None:
        """Get the hardware charge power limit in Watts."""
        if self._hardware_max_charge_power_w is not None:
            return self._hardware_max_charge_power_w

        # Try to read it
        await self._read_hardware_power_limits()
        return self._hardware_max_charge_power_w

    @property
    def max_charge_power_w(self) -> int | None:
        return self._max_charge_power_w

    @property
    def max_discharge_power_w(self) -> int | None:
        return self._max_discharge_power_w

    async def set_max_charge_power(self, power_in_watt: int):
        """Set the maximum charge power in Watts."""
        hardware_limit = await self.get_hardware_power_limit()

        if not isinstance(hardware_limit, int):
            self._log("Cannot set max charge power, hardware limit unavailable",
                      level="WARNING")
            return False

        if power_in_watt > hardware_limit:
            self._log(f"Requested max charge power {power_in_watt}W exceeds "
                      f"hardware limit {hardware_limit}W", level="WARNING")
            power_in_watt = hardware_limit
        elif power_in_watt < hardware_limit:
            self._log(f"Max charge power {hardware_limit}W reduced by user "
                      f"setting to {power_in_watt}W")

        self._max_charge_power_w = power_in_watt
        return True

    async def set_max_discharge_power(self, power_in_watt: int):
        """Set the maximum discharge power in Watts."""
        hardware_limit = self._hardware_max_discharge_power_w

        if not isinstance(hardware_limit, int):
            self._log("Cannot set max discharge power, hardware limit unavailable",
                      level="WARNING")
            return False

        if power_in_watt > hardware_limit:
            self._log(f"Requested max discharge power {power_in_watt}W exceeds "
                      f"hardware limit {hardware_limit}W", level="WARNING")
            power_in_watt = hardware_limit
        elif power_in_watt < hardware_limit:
            self._log(f"Max discharge power {hardware_limit}W reduced by user "
                      f"setting to {power_in_watt}W")

        self._max_discharge_power_w = power_in_watt
        return True

    async def get_connected_car(self) -> ElectricVehicle | None:
        """Return the connected car, or None if no car is connected."""
        return self._connected_car

    async def is_car_connected(self) -> bool:
        """Is the car connected to the charger."""
        state = await self._get_evse_state()
        return state not in self._DISCONNECTED_STATES

    async def start_charging(self, power_in_watt: int, source: str = None):
        """Start charging with specified power in Watts.

        Args:
            power_in_watt: Power in Watts, positive for charging, negative for discharging.
            source: Source of the request for logging.
        """
        if not self._am_i_active:
            self._log("Not setting charge rate: not active.")
            return

        if power_in_watt is None:
            self._log("power_in_watt = None, abort", level="WARNING")
            return

        if not await self.is_car_connected():
            self._log("Not setting charge rate: No car connected.")
            return

        await self._set_charge_power(charge_power=power_in_watt, source="start_charging")

    async def stop_charging(self, source: str = None):
        """Stop charging."""
        if not self._am_i_active:
            self._log("Called while not active. Not blocking call for reliability.")

        await self._set_charge_power(charge_power=0, source="stop_charging")

    async def is_charging(self) -> bool:
        """Is the battery being charged."""
        state = await self._get_evse_state()
        if state is None:
            self._log("Charger state is None, assume not charging.")
            return False
        return state in self._CHARGING_STATES

    async def is_discharging(self) -> bool:
        """Is the battery being discharged."""
        state = await self._get_evse_state()
        if state is None:
            self._log("Charger state is None, assume not discharging.")
            return False
        return state in self._DISCHARGING_STATES

    ################################################################################
    #  Private methods                                                             #
    ################################################################################

    async def _kick_off_polling(self, reason: str = ""):
        """Start the polling timer."""
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
        """Stop the polling process."""
        self._log(f"Cancelling polling, reason: {reason}")
        cancel_timer_silently(self._hass, self._poll_timer_handle)
        self._poll_timer_handle = None
        self._eb.emit_event("evse_polled", stop=True)

    async def _get_and_process_evse_data(self, *_args):
        """Retrieve and process all data from EVSE, called from polling timer."""
        await self._get_and_process_registers(self._get_polling_entities())
        self._eb.emit_event("evse_polled", stop=False)

    async def _get_and_process_registers(self, entities: list[ModbusConfigEntity]):
        """Read values from EVSE via Modbus and update entity states."""
        if self._mb_client is None:
            self._log("Modbus client not initialized, aborting", level="WARNING")
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
            raw_value = results[i]
            await self._update_mce(entity, raw_value)

    async def _update_mce(self, mce: ModbusConfigEntity, new_value):
        """Update an MCE and trigger change handler if value changed."""
        old_value = mce.current_value
        mce.set_value(new_value=new_value, owner=self)
        new_value = mce.current_value

        # Trigger change handler if value changed
        if old_value != new_value and mce.change_handler:
            try:
                handler = getattr(self, mce.change_handler)
                await handler(new_value, old_value)
            except Exception as e:
                self._log(f"Change handler '{mce.change_handler}' failed: {e}",
                          level="WARNING")

    async def _get_evse_state(self) -> int | None:
        """Get the current EVSE state (base state, 0-10)."""
        charger_state = self._MCE_INVERTER_STATE.current_value
        if charger_state is None:
            await self._get_and_process_registers([self._MCE_INVERTER_STATE])
            charger_state = self._MCE_INVERTER_STATE.current_value
        return charger_state

    async def _get_car_soc(self, force_renew: bool = False) -> int | None:
        """Get the car's state of charge."""
        if self._mb_client is None:
            self._log("Modbus client not initialized", level="WARNING")
            return None

        if not await self.is_car_connected():
            self._log("No car connected, returning SoC = None")
            return None

        soc_mce = self._get_soc_mce()
        soc_value = soc_mce.current_value

        if soc_value is None:
            self._log("SoC not yet available from polling")

        return soc_value

    async def _set_charge_power(self, charge_power: int, source: str = None):
        """Set the desired charge power in Watts."""
        self._log(f"Called from {source}, power {charge_power}.")

        if not self._am_i_active:
            self._log("Called while not active, not blocking.", level="WARNING")

        if self._mb_client is None:
            self._log("Modbus client not initialized", level="WARNING")
            return

        ev = await self.get_connected_car()
        if ev is None:
            self._log("No car connected, cannot set charge power.", level="WARNING")
            return

        # Check minimum SoC for discharge
        if charge_power < 0:
            current_soc = await self._get_car_soc()
            if current_soc is None:
                self._log("Current SoC unavailable", level="WARNING")
            elif current_soc <= ev.min_soc_percent:
                self._log(f"Discharge attempted below minimum SoC ({ev.min_soc_percent}%), "
                          f"stopping discharge.", level="WARNING")
                charge_power = 0

        # Clip to max power limits
        if charge_power > self._max_charge_power_w:
            self._log(f"Requested charge power {charge_power}W too high, reducing.",
                      level="WARNING")
            charge_power = self._max_charge_power_w
        elif charge_power < -self._max_discharge_power_w:
            self._log(f"Requested discharge power {charge_power}W too high, reducing.",
                      level="WARNING")
            charge_power = -self._max_discharge_power_w

        # Convert to register value (remove scale factor)
        register_value = self._remove_power_scale_factor(charge_power)

        res = await self._mb_client.write_modbus_register(
            modbus_register=self._MBR_POWER_SETPOINT,
            value=register_value
        )

        if not res:
            self._log(f"Failed to set charge power to {charge_power}W", level="WARNING")

    ################################################################################
    #  Change handlers                                                             #
    ################################################################################

    async def _handle_charge_power_change(self, new_power, old_power):
        """Handle actual power change."""
        self._eb.emit_event("charge_power_change", new_power=new_power)

    async def _handle_soc_change(self, new_soc, old_soc):
        """Handle SoC change - set on connected car."""
        if self._connected_car is None:
            self._log("No car connected, cannot set SoC.", level="WARNING")
        else:
            self._connected_car.set_soc(new_soc=new_soc)

    async def _handle_conn_state_change(self, new_state, old_state):
        """Handle connection state change."""
        self._log(f"Connection state changed: {old_state} -> {new_state}")
        # SunSpec ConnSt: 0=disconnected, 1=connected, 2=operating
        is_connected = new_state in (1, 2)
        was_connected = old_state in (1, 2) if old_state is not None else False

        if is_connected and not was_connected:
            self._log("Car connected")
            self._eb.emit_event("is_car_connected", is_car_connected=True)
        elif not is_connected and was_connected:
            self._log("Car disconnected")
            self._connected_car = None
            await self._update_mce(mce=self._get_soc_mce(), new_value=None)
            self._eb.emit_event("is_car_connected", is_car_connected=False)

    async def _handle_inverter_state_change(self, new_state, old_state):
        """Handle inverter state change."""
        self._log(f"Inverter state changed: {old_state} -> {new_state}")

        if new_state in self._ERROR_STATES or old_state in self._ERROR_STATES:
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_state, "is_final_check": False}
            )

        charger_state_text = self.get_evse_state_str(new_state)
        self._eb.emit_event(
            "charger_state_change",
            new_charger_state=new_state,
            old_charger_state=old_state,
            new_charger_state_str=charger_state_text,
        )

    ################################################################################
    #  Modbus state handling                                                       #
    ################################################################################

    async def _cb_modbus_state(self, persistent_problem: bool):
        """Callback for modbus_client to report communication problems."""
        if persistent_problem:
            await self._modbus_communication_lost()
        else:
            await self._modbus_communication_restored()

    async def _modbus_communication_lost(self):
        """Handle persistent Modbus communication loss."""
        self._log(f"Persistent Modbus connection problem in {self._get_charger_name()}.",
                  level="WARNING")

        self._am_i_active = False
        await self._cancel_polling(reason="modbus communication lost")

        self._eb.emit_event(
            "charger_error_state_change",
            persistent_error=True,
            was_car_connected=await self.is_car_connected(),
        )

        await self._update_mce(mce=self._MCE_ACTUAL_POWER, new_value=None)
        await self._update_mce(mce=self._get_soc_mce(), new_value=None)
        await self._update_mce(mce=self._MCE_INVERTER_STATE, new_value=7)  # Fault state

    async def _modbus_communication_restored(self):
        """Handle Modbus communication restoration."""
        self._log(f"Modbus connection to {self._get_charger_name()} restored.")

        self._eb.emit_event(
            "charger_error_state_change",
            persistent_error=False,
            was_car_connected=None,
        )

        await self.set_max_charge_power(self._max_charge_power_w)
        await self._kick_off_polling()

    async def _emit_modbus_communication_state(self, can_communicate: bool):
        """Emit communication state event."""
        self._eb.emit_event(
            "charger_communication_state_change",
            can_communicate=can_communicate,
        )

    ################################################################################
    #  Error handling                                                              #
    ################################################################################

    async def _handle_charger_error_state_change(self, kwargs):
        """Handle charger error state changes."""
        new_charger_state = kwargs.get("new_charger_state", None)
        is_final_check = kwargs.get("is_final_check", False)
        self._log(f"Error state check: {new_charger_state=}, {is_final_check=}")

        has_error = new_charger_state in self._ERROR_STATES if new_charger_state else False

        if has_error:
            if is_final_check:
                self._log("Persistent charger error", level="WARNING")
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
        else:
            cancel_timer_silently(self._hass, self._timer_id_check_error_state)
            self._timer_id_check_error_state = None
            self._eb.emit_event(
                "charger_error_state_change",
                persistent_error=False,
                was_car_connected=None,
            )

    def try_set_connected_vehicle(self, ev_id: str) -> bool:
        """Try to set the connected vehicle by ID."""
        ev = self.get_vehicle_by_ev_id(ev_id)
        if ev is None:
            self._log(f"Cannot set connected vehicle, no vehicle with ID '{ev_id}'.",
                      level="WARNING")
            return False
        self._connected_car = ev
        return True
