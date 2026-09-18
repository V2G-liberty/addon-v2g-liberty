"""EVtec BiDiPro 10 charger driver.

Phase 3 of the 359 migration: the EVtec BiDiPro (ECP4 / "Modbus 2.0" register
map) on the ``chargers/`` package structure introduced in phase 2a.
Structurally it mirrors ``wallbox_quasar_1``: the same public charger API, the
same events with the same kwargs and ordering, and the same exception /
grace-timer state machine and polling orchestration kept on the charger. What
differs is the register model, which follows the hardware-tested contract of a
production proxy against a live BiDiPro:

- The map is nine 100-register objects on one Modbus unit: the ChargePoint at
  absolute addresses 0..43 and connector ``X`` at base ``X*100``. The active
  connector is discovered at connection time (any of the int32 at offsets
  0/2/4 non-zero, lowest wins) and every connector register is bound to that
  base. A block read never crosses an object boundary.
- Multi-register values are big-endian: int32/float32 (2 registers), uint64
  (4), strings (2 characters per register).
- The setpoint is ``X+86`` (int32 W, negative = discharge). Suspend mode
  (``X+88``) is documented as the on/off gate but does nothing on the real
  charger; it is written for parity with the tested proxy, but a stop is always
  ``X+86 = 0``.
- Two interlocks are honoured before a discharge is commanded: the charge
  session type (``X+04``) must be v2xDynamic (3) or bidirectional (5), and the
  station must offer V2G right now (``X+22`` < 0). "Not offered" is not an
  error. Setpoints are clamped into the accept windows ``[X+22, 0]`` and
  ``[X+38, X+30]``; the station rejects values outside them.
- SoC (``X+12``, per-mille) is reported reliably by polling, so there is no
  "1 W SoC refresh dance"; ``try_get_new_soc_in_process`` never becomes True.
- The error register (``X+54``) is a 40-bit bitmask decoded unsigned; 0 means
  no error.

States are the raw connector states (0..12). Like the Quasar driver, this class
provides its own DISCONNECTED_STATES / ERROR_STATES / CHARGER_STATES for the
consumers (data_monitor, ha_ui_manager). Connector states 4..6 (authorising,
plugged, initialising) are physically connected but not yet controllable, so
they count as "no car" until the session is up — as the 359 driver did.
"""

import time
from typing import ClassVar

from appdaemon.plugins.hass.hassapi import Hass
from pymodbus.exceptions import ModbusException

from .. import constants as c
from ..event_bus import EventBus
from ..log_wrapper import get_class_method_logger
from ..notifier_util import Notifier
from ..timer_utils import cancel_timer_silent, set_oneshot_timer
from .recovery_probe import RecoveryProbe
from .base_bidirectional_evse import BidirectionalEVSE
from .modbus_types import MBR, ModbusConfigEntity
from .v2g_modbus_client import V2GmodbusClient

# --- Register map (ECP4 / Modbus 2.0) -----------------------------------------
CONNECTOR_STRIDE = 100
FIRST_CONNECTOR = 1
LAST_CONNECTOR = 10

# ChargePoint object, absolute addresses.
CP_VERSION = 0  # string, 16 registers
CP_SERIAL = 16  # string, 10 registers
CP_MODEL = 26  # string, 10 registers; contains "crema" on supported firmware
CP_COMMUNICATION_TIMEOUT = 42  # int32, seconds; the charger idles without traffic

# Connector object, offsets relative to X*100.
OFF_CONNECTOR_STATE = 0  # int32, enum below
OFF_SESSION_STATE = 2  # int32, CCS session state (used for discovery only)
OFF_SESSION_TYPE = 4  # int32, enum below; 3 and 5 permit discharge
OFF_POWER = 10  # float32, W, signed (measured)
OFF_SOC = 12  # int32, per-mille
OFF_CONNECTOR_TYPE = 14  # int32: 0 type2, 1 ccs, 2 chademo, 3 gbt
OFF_LOWER_LIMIT_POTENTIAL = 22  # float32, W; < 0 means V2G offered, discharge floor
OFF_UPPER_LIMIT = 30  # float32, W; charge ceiling
OFF_LOWER_LIMIT = 38  # float32, W; charge floor
OFF_ERROR = 54  # uint64, 40-bit bitmask, 0 = no error
OFF_CAR_ID = 76  # string, 10 registers; empty = no car (EvccId)
OFF_INPUT_POWER = 86  # int32, W, signed — THE setpoint (written)
OFF_SUSPEND_MODE = 88  # int32, 1 on / 0 off — documented gate, no-op (written)

SESSION_TYPE_V2X_DYNAMIC = 3
SESSION_TYPE_BIDIRECTIONAL = 5
BIDIRECTIONAL_SESSION_TYPES = (SESSION_TYPE_V2X_DYNAMIC, SESSION_TYPE_BIDIRECTIONAL)


class EVtecBiDiProClient(BidirectionalEVSE):
    """Class to communicate with the EVtec BiDiPro 10 EVSE via Modbus TCP.

    Polls the EVSE for states and values; the values (charger state, car SoC,
    power) are emitted onto the event_bus for other modules to use.
    """

    CHARGER_TYPE = "evtec-bidi-pro-10"

    event_bus: EventBus = None

    #######################################################################################
    #   This file contains the Modbus address information for the EVtec BiDiPro 10.       #
    #   This is provided by EV2Grid as is (https://ev2grid.de/bidipro).                    #
    #   EVtec nor EV2Grid is provider of the software and neither provides any type of     #
    #   service for the software.                                                          #
    #######################################################################################

    # Connector state (X+00). Raw values; the texts are what the UI shows.
    CHARGER_STATES: ClassVar[dict[int, str]] = {
        0: "Starting up",
        1: "No car connected",
        2: "Error: charger unavailable",
        3: "Connected: reserved by another system",
        4: "Connected: authorising",
        5: "Connected: plugged in, preparing",
        6: "Connected: initialising session",
        7: "Charging",
        8: "Discharging",
        9: "Connected: session stopped",
        10: "Connected: not charging (idle)",
        11: "Connected: charging ended",
        12: "Error",
    }
    # 4..6 are plugged in but not yet controllable: treat as "no car" until the
    # session is up, exactly as the original driver did against the hardware.
    DISCONNECTED_STATES: ClassVar[list[int]] = [0, 1, 4, 5, 6]
    CHARGING_STATE: int = 7
    DISCHARGING_STATE: int = 8
    AVAILABILITY_STATES: ClassVar[list[int]] = [7, 8, 9, 10, 11]
    ERROR_STATES: ClassVar[list[int]] = [2, 12]

    # The hardware limit read from the charger (X+30) is unreliable, e.g. 0 W
    # while the car is not yet charge-ready, so a fixed default is reported.
    DEFAULT_HARDWARE_POWER_LIMIT_W: int = 10000
    # Seconds without Modbus traffic after which the charger idles (fail-safe
    # when this software stops). 600 is the maximum the charger accepts.
    COMMUNICATION_TIMEOUT_S: int = 600

    # Holds the last requested charge power written to X+86, used for the
    # deviation comparison and to skip a duplicate write.
    requested_charge_power: int = 0
    _is_power_deviating: bool = False

    # For handling a non-responsive charger (direct call, not on the bus).
    v2g_main_app: object

    poll_timer_handle: object
    BASE_POLLING_INTERVAL_SECONDS: int = 5
    MINIMAL_POLLING_INTERVAL_SECONDS: int = 15

    # Kept for API compatibility: the EVtec reports SoC reliably, so there is
    # no forced-read dance and this never becomes True.
    try_get_new_soc_in_process: bool = False

    # For tracking Modbus failure in the charger. None until the first
    # successful connection: until then a failure is most likely a
    # configuration in progress, not a crashed charger.
    modbus_exception_counter: int = None
    timer_id_check_modus_exception_state: str = None
    timer_id_check_error_state: str = None
    MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS: int = 60
    # After an un-recoverable error the recovery probe re-checks the charger
    # this often; a probe blocks for at most the Modbus timeout (10 s).
    RECOVERY_PROBE_INTERVAL_SECONDS: int = 60

    # Set only(!) by set_inactive and set_active.
    _am_i_active: bool = None

    hass: Hass = None
    notifier: Notifier = None

    def __init__(self, hass: Hass, event_bus: EventBus, notifier: Notifier):
        """Initialise EVtecBiDiProClient.
        Configuration and connecting the Modbus client is done separately in
        initialise_charger.
        """
        super().__init__()
        self.hass = hass
        self._log = get_class_method_logger(module_name="evtec_bidipro")

        self.event_bus = event_bus
        self.notifier = notifier

        # Raw Modbus transport; the exception/grace-timer state machine stays here.
        self._mb_client = V2GmodbusClient(hass)
        # Re-checks the charger after an un-recoverable error, when polling has
        # stopped and nothing else would notice it coming back.
        self._recovery_probe = RecoveryProbe(
            hass,
            self._log,
            self.RECOVERY_PROBE_INTERVAL_SECONDS,
            check=self._charger_is_healthy,
            on_recovered=self._handle_charger_recovered,
        )

        self.poll_timer_handle = None
        # Which refusal, if any, the rest of the app has been told about.
        self._refusing_discharge_because: str | None = None
        # Set by shutdown(): stops a poll that was already in flight from
        # re-arming timers on a driver that is no longer the active one.
        self._is_shut_down = False
        self._connector: int | None = None
        # Bind a provisional connector so the entities exist before discovery;
        # initialise_charger re-binds to the discovered one.
        self._bind_connector(FIRST_CONNECTOR)

    # ChargePoint registers (absolute, connector independent).
    _MBR_EVSE_VERSION = MBR(address=CP_VERSION, data_type="string", length=16)
    _MBR_EVSE_SERIAL = MBR(address=CP_SERIAL, data_type="string", length=10)
    _MBR_EVSE_MODEL = MBR(address=CP_MODEL, data_type="string", length=10)
    _MBR_COMMUNICATION_TIMEOUT = MBR(
        address=CP_COMMUNICATION_TIMEOUT, data_type="int32", length=2
    )

    def _bind_connector(self, connector: int):
        """Create the connector entities at base ``connector * 100``.

        Instance-level, so a test can bind a fixed connector and discovery can
        re-bind without touching class state.
        """
        self._connector = connector
        base = connector * CONNECTOR_STRIDE

        def at(offset: int, data_type: str, length: int) -> MBR:
            return MBR(address=base + offset, data_type=data_type, length=length)

        ################################################################################
        #   EVSE Config Entities (MCE): register + limits + change handler, caching   #
        #   the current value. Pre-processors convert the raw decoded value first.    #
        ################################################################################
        self._MCE_CHARGER_STATE = ModbusConfigEntity(
            modbus_register=at(OFF_CONNECTOR_STATE, "int32", 2),
            minimum_value=0,
            maximum_value=12,
            current_value=None,
            change_handler="_handle_charger_state_change",
        )
        self._MCE_SESSION_TYPE = ModbusConfigEntity(
            modbus_register=at(OFF_SESSION_TYPE, "int32", 2),
            minimum_value=0,
            maximum_value=5,
            current_value=None,
        )
        self._MCE_ACTUAL_POWER = ModbusConfigEntity(
            modbus_register=at(OFF_POWER, "float32", 2),
            minimum_value=-20000,
            maximum_value=20000,
            current_value=None,
            pre_processor="_round_to_int",
            change_handler="_handle_charge_power_change",
        )
        # Per-mille from the charger, percent for the app. The 2..97 window with
        # a relaxed 1..100 at start-up mirrors the Quasar driver: a 0 while
        # connected is a glitch to ignore, and above 97 the car is not charged.
        self._MCE_CAR_SOC = ModbusConfigEntity(
            modbus_register=at(OFF_SOC, "int32", 2),
            minimum_value=2,
            maximum_value=97,
            relaxed_min_value=1,
            relaxed_max_value=100,
            current_value=None,
            pre_processor="_soc_permille_to_percent",
            change_handler="_handle_soc_change",
        )
        self._MCE_LOWER_LIMIT_POTENTIAL = ModbusConfigEntity(
            modbus_register=at(OFF_LOWER_LIMIT_POTENTIAL, "float32", 2),
            current_value=None,
        )
        self._MCE_UPPER_LIMIT = ModbusConfigEntity(
            modbus_register=at(OFF_UPPER_LIMIT, "float32", 2),
            current_value=None,
        )
        self._MCE_LOWER_LIMIT = ModbusConfigEntity(
            modbus_register=at(OFF_LOWER_LIMIT, "float32", 2),
            current_value=None,
        )
        self._MCE_ERROR = ModbusConfigEntity(
            modbus_register=at(OFF_ERROR, "int64", 4),
            current_value=None,
            pre_processor="_error_unsigned",
            change_handler="_handle_charger_error_state_change",
        )
        # Write registers and on-demand reads.
        self._MBR_CONNECTOR_TYPE = at(OFF_CONNECTOR_TYPE, "int32", 2)
        self._MBR_CAR_ID = at(OFF_CAR_ID, "string", 10)
        self._MBR_SET_CHARGE_POWER = at(OFF_INPUT_POWER, "int32", 2)
        self._MBR_SUSPEND_MODE = at(OFF_SUSPEND_MODE, "int32", 2)

        # One contiguous span X+0..X+57 — never across the object boundary.
        self.CHARGER_POLLING_ENTITIES = [
            self._MCE_CHARGER_STATE,
            self._MCE_SESSION_TYPE,
            self._MCE_ACTUAL_POWER,
            self._MCE_CAR_SOC,
            self._MCE_LOWER_LIMIT_POTENTIAL,
            self._MCE_UPPER_LIMIT,
            self._MCE_LOWER_LIMIT,
            self._MCE_ERROR,
        ]
        self.CHARGER_ERROR_ENTITIES = [self._MCE_ERROR]
        self.CHARGER_WINDOW_ENTITIES = [
            self._MCE_SESSION_TYPE,
            self._MCE_LOWER_LIMIT_POTENTIAL,
            self._MCE_UPPER_LIMIT,
            self._MCE_LOWER_LIMIT,
        ]

    ######################################################################
    #                     PUBLIC FUNCTIONAL METHODS                      #
    ######################################################################

    async def test_charger_connection(self, host, port) -> tuple[str, int | None]:
        """Test the connection and validate the charger before initialisation.
        To be called from the UI (via globals); works while not initialised.

        Returns (status, max_available_power) with status one of
        "connection_failed", "not_recognised", "no_active_plug", "success".
        A car does not have to be connected to configure the charger.
        """
        self._log(f"Testing EVtec BiDiPro at {host}:{port}")
        mb_client = V2GmodbusClient(self.hass)
        if not await mb_client.initialise(host=host, port=port):
            mb_client.terminate()
            return "connection_failed", None
        try:
            model = (await mb_client.read_registers([self._MBR_EVSE_MODEL]))[0]
            if not model or "crema" not in str(model).lower():
                self._log(
                    f"Connected at {host}:{port} but charger not recognised: "
                    f"model '{model}', expected to contain 'crema'.",
                    level="WARNING",
                )
                return "not_recognised", None
            connector = await self._discover_connector(mb_client)
        except ModbusException as me:
            self._log(
                f"ModbusException while testing {host}:{port}: {me}", level="WARNING"
            )
            return "connection_failed", None
        finally:
            mb_client.terminate()

        if connector is None:
            self._log(
                f"Connected at {host}:{port}, charger recognised, but no configured "
                f"connector found in {FIRST_CONNECTOR}..{LAST_CONNECTOR}.",
                level="WARNING",
            )
            return "no_active_plug", None
        self._log(
            f"Connection test succeeded: model '{model}', connector {connector}, "
            f"max power {self.DEFAULT_HARDWARE_POWER_LIMIT_W} W."
        )
        return "success", self.DEFAULT_HARDWARE_POWER_LIMIT_W

    async def initialise_charger(self, v2g_args=None):
        """Initialise the charger: connect, discover and bind the connector,
        set the communication timeout. Activating the polling is done in set_active.
        Returns (connected, max_available_power).
        """
        self._mb_client.terminate()

        connected = await self._mb_client.initialise(
            host=c.CHARGER_HOST_URL, port=c.CHARGER_PORT
        )
        if not connected:
            return False, None

        self.modbus_exception_counter = 0

        try:
            connector = await self._discover_connector(self._mb_client)
        except ModbusException as me:
            self._log(
                f"ModbusException during connector discovery: {me}", level="WARNING"
            )
            await self._handle_modbus_exception(source="initialise_charger")
            return False, None
        if connector is None:
            self._log(
                f"No configured connector found in {FIRST_CONNECTOR}..{LAST_CONNECTOR}.",
                level="WARNING",
            )
            return False, None
        self._bind_connector(connector)
        self._log(
            f"Bound connector {connector} (base address {connector * CONNECTOR_STRIDE})."
        )

        await self._modbus_write_mbr(
            self._MBR_COMMUNICATION_TIMEOUT,
            self.COMMUNICATION_TIMEOUT_S,
            source="initialise_charger: communication timeout",
        )
        self._log(f"Returning max. power: {self.DEFAULT_HARDWARE_POWER_LIMIT_W}.")
        return True, self.DEFAULT_HARDWARE_POWER_LIMIT_W

    async def stop_charging(self):
        """Stop charging if it is in process and set charge power to 0."""
        if not self._am_i_active:
            self._log(
                "called while _am_i_active == False. Not blocking call to make stop reliable."
            )
        await self._set_charger_action("stop", reason="stop_charging")
        await self._set_charge_power(charge_power=0, source="stop_charging")

    async def start_charge_with_power(self, charge_power: int, source: str = "unknown"):
        """Start a charge session with a given power in Watt (negative = discharge).
        To be called from the v2g-liberty module.
        """
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

        if charge_power == 0:
            await self._set_charger_action(
                action="stop", reason=f"called from {source} with power = 0"
            )
        else:
            await self._set_charger_action(
                action="start", reason=f"called from {source} with {charge_power=}"
            )

        await self._set_charge_power(
            charge_power=charge_power, source=f"{source} => start_charge_with_power"
        )

    async def set_inactive(self):
        """To be called when charge_mode in UI is (switched to) Stop.
        Polling continues, the information is still relevant.
        """
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("made inactive")
        await self.stop_charging()
        self._am_i_active = False

    async def set_active(self):
        """To be called when charge_mode in UI is (switched to) Automatic or Boost."""
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("activated")
        # A manual switch back to Automatic is a recovery too.
        await self._recovery_probe.cancel()
        self._am_i_active = True
        await self._get_car_soc(do_not_use_cache=True)
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._set_poll_strategy()

    async def shutdown(self):
        """Release the charger: stop polling and timers, close the connection.
        Used when the configured charger type changes at runtime.
        """
        self._log("shutting down")
        self._is_shut_down = True
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        self.timer_id_check_modus_exception_state = None
        await cancel_timer_silent(self.hass, self.timer_id_check_error_state)
        self.timer_id_check_error_state = None
        await self._recovery_probe.cancel()
        await self._cancel_polling(reason="shutdown")
        self._am_i_active = False
        self._mb_client.terminate()
        # Closing the socket makes an in-flight read raise; cancel once more so
        # a timer armed by that exception cannot outlive this driver.
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        self.timer_id_check_modus_exception_state = None
        await cancel_timer_silent(self.hass, self.timer_id_check_error_state)
        self.timer_id_check_error_state = None

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
        return int(round((soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM), 0))

    def is_available_for_automated_charging(self) -> bool:
        """Whether the car and EVSE are available for automated charging.
        Synchronous on purpose (called from sync code in data_monitor): uses the
        polled state, which can be as old as the polling interval.
        """
        if not self._am_i_active:
            self._log("called while _am_i_active == False. Returning False.")
            return False
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
        """To be called from v2g-liberty once after its own init is complete;
        the following code might need v2g-liberty for notifications etc.
        """
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting", level="WARNING")
            return
        self._log("kicking off")

        self.event_bus.emit_event(
            "update_charger_info", charger_info=await self._get_charger_info()
        )

        # All the information to get started; this also creates the entities
        # in HA that many modules depend upon.
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        await self._get_car_soc(do_not_use_cache=True)

    async def _get_charger_info(self) -> str:
        results = await self._modbus_read_mbrs(
            [self._MBR_EVSE_VERSION, self._MBR_EVSE_SERIAL, self._MBR_EVSE_MODEL],
            source="_get_charger_info",
        )
        if results is None or any(r is None for r in results):
            return "unknown"
        return (
            f"EVtec BiDiPro 10 - Firmware version: {results[0]}, "
            f"Serial number: {results[1]}, Model: {results[2]}, "
            f"Connector: {self._connector}."
        )

    async def _discover_connector(self, mb_client: V2GmodbusClient) -> int | None:
        """Find the configured connector: scan 1..10 and treat a slot as
        configured when any of the three int32 at offsets 0, 2, 4 is non-zero
        (a slot that is configured but still booting has state 0). The lowest
        configured connector is bound. Propagates ModbusException.
        """
        for connector in range(FIRST_CONNECTOR, LAST_CONNECTOR + 1):
            base = connector * CONNECTOR_STRIDE
            probes = [
                MBR(address=base + offset, data_type="int32", length=2)
                for offset in (OFF_CONNECTOR_STATE, OFF_SESSION_STATE, OFF_SESSION_TYPE)
            ]
            values = await mb_client.read_registers(probes)
            if any(value not in (None, 0) for value in values):
                self._log(f"Connector {connector} is configured: {values}.")
                return connector
        return None

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
            await self._handle_charger_error_state_change(
                {"new_charger_state": new_charger_state, "is_final_check": False}
            )

        charger_state_text = self.CHARGER_STATES.get(new_charger_state, None)
        self.event_bus.emit_event(
            "charger_state_change",
            new_charger_state=new_charger_state,
            old_charger_state=old_charger_state,
            new_charger_state_str=charger_state_text,
        )

        if new_charger_state in self.DISCONNECTED_STATES:
            # The plug was removed (or the session is not up yet): the SoC goes
            # from the current value to unavailable.
            await self._update_evse_entity(
                evse_entity=self._MCE_CAR_SOC, new_value="unavailable"
            )
            # Make sure a stale setpoint cannot resume when the car reconnects.
            await self._set_charger_action("stop", reason="car disconnected")
            # Whatever the charger refused applied to the session that just
            # ended; do not keep telling the user about it.
            self._report_discharge_refusal(None)
            await self._set_poll_strategy()
            self.event_bus.emit_event("is_car_connected", is_car_connected=False)
        elif old_charger_state in self.DISCONNECTED_STATES or old_charger_state is None:
            self._log("From disconnected to connected: refresh the SoC")
            await self._get_car_soc(do_not_use_cache=True)
            await self._set_poll_strategy()
            self.event_bus.emit_event("is_car_connected", is_car_connected=True)
        else:
            # From one connected state to another: nothing to react upon.
            pass

    ######################################################################
    #                    PRIVATE FUNCTIONAL METHODS                      #
    ######################################################################

    async def _set_charger_action(self, action: str, reason: str = ""):
        """Set action to start/stop charging.

        "start" writes suspend mode on (X+88 = 1): documented as the output
        gate, a no-op on the real charger, kept for parity with the tested
        proxy. "stop" writes the setpoint to 0 (X+86 = 0) — the only thing that
        actually stops power flow — and suspend mode off. Stop is always
        performed, so it is reliable even when the state says "not charging".
        """
        self._log(f"Called with action '{action}', reason: '{reason}'.", level="DEBUG")

        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if action == "start":
            if not await self.is_car_connected():
                self._log("Not performing charger action 'start': No car connected.")
                return
            if await self._is_charging_or_discharging():
                self._log("Not performing charger action 'start': Already charging.")
                return
            await self._modbus_write_mbr(
                self._MBR_SUSPEND_MODE, 1, source=f"set_charger_action: start {reason}"
            )
        elif action == "stop":
            await self._modbus_write_mbr(
                self._MBR_SET_CHARGE_POWER,
                0,
                source=f"set_charger_action: stop {reason}",
            )
            self.requested_charge_power = 0
            await self._modbus_write_mbr(
                self._MBR_SUSPEND_MODE, 0, source=f"set_charger_action: stop {reason}"
            )
        else:
            self._log(
                f"Unknown option for action: '{action}'.{reason}", level="WARNING"
            )
            return
        self._log(f"set_charger_action: {action} {reason}", level="DEBUG")

    async def _is_charging_or_discharging(self) -> bool:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        state = await self._get_charger_state()
        if state is None:
            self._log(
                "charger state is None (not setup yet?). Assume not (dis-)charging."
            )
            return False
        is_charging = state in [self.CHARGING_STATE, self.DISCHARGING_STATE]
        self._log(
            f"state: {state} ({self.CHARGER_STATES.get(state)}), charging: {is_charging}."
        )
        return is_charging

    async def _get_car_soc(self, do_not_use_cache: bool = False):
        """Return the SoC in percent (2..97) or "unavailable".

        The EVtec reports the SoC reliably through the polled register, so a
        refresh is just a read of that register — no charge/stop dance.
        :param do_not_use_cache: read now and re-broadcast the value to listeners.
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if not await self.is_car_connected():
            self._log("no car connected, returning SoC = 'unavailable'")
            return "unavailable"

        ecs = self._MCE_CAR_SOC
        if ecs.current_value is None or do_not_use_cache:
            self._log("renew SoC from charger")
            await self._get_and_process_registers([ecs], force_emit=do_not_use_cache)
            if ecs.current_value is None:
                await self._update_evse_entity(
                    evse_entity=ecs,
                    new_value="unavailable",
                    force_emit=do_not_use_cache,
                )
        self._log(f"returning: '{ecs.current_value}'.")
        return ecs.current_value

    async def _get_charger_state(self) -> int:
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        charger_state = self._MCE_CHARGER_STATE.current_value
        if charger_state is None:
            # Before initialisation has finished.
            await self._get_and_process_registers([self._MCE_CHARGER_STATE])
            charger_state = self._MCE_CHARGER_STATE.current_value
        return charger_state

    async def _get_and_process_registers(
        self, entities: list, force_emit: bool = False
    ):
        """Read the entities from the EVSE (one span per call) and emit/cache
        the values for the corresponding sensors in HA.
        """
        results = await self._modbus_read_mbrs(
            [entity.modbus_register for entity in entities],
            source="_get_and_process_registers",
        )
        if results is None:
            self._log("results is None, abort processing.", level="WARNING")
            return
        if all(result is None for result in results):
            # The device answered with an error response: no data at all.
            self._log(
                "Modbus error response, no data. Aborting processing.", level="WARNING"
            )
            is_unrecoverable = await self._handle_modbus_exception(
                source="_get_and_process_registers (error response)"
            )
            if is_unrecoverable:
                await self._handle_un_recoverable_error(
                    reason="persistent modbus error responses",
                    source="_get_and_process_registers",
                )
            return

        for entity, raw_value in zip(entities, results):
            entity_name = f"register_{entity.modbus_register.address}"
            if raw_value is None:
                self._log(f"New value 'None' for entity '{entity_name}' ignored.")
                continue

            if entity.pre_processor is not None:
                try:
                    new_state = getattr(self, entity.pre_processor)(raw_value)
                except (TypeError, ValueError) as e:
                    self._log(
                        f"New value '{raw_value}' for entity '{entity_name}' "
                        f"ignored, pre-processing failed: {e}."
                    )
                    continue
            else:
                new_state = raw_value

            out_of_range = (
                entity.minimum_value is not None
                and entity.maximum_value is not None
                and not (entity.minimum_value <= new_state <= entity.maximum_value)
            )
            # "unavailable" is a sentinel, not a usable reading: treat it like
            # an empty cache, so the relaxed window can still rescue a value.
            # Without this, a car reconnecting above the normal maximum (or at
            # 1 %) is dropped on every poll and stays "unavailable" for good.
            has_usable_value = isinstance(entity.current_value, (int, float))
            if out_of_range:
                if not has_usable_value:
                    # At start-up, or after a disconnect. Not setting a value
                    # would hang the app, so use the relaxed range if the
                    # entity has one.
                    relaxed_min = entity.relaxed_min_value
                    relaxed_max = entity.relaxed_max_value
                    if relaxed_min is None or relaxed_max is None:
                        self._log(
                            f"New value {new_state} for '{entity_name}' out of range "
                            f"{entity.minimum_value}-{entity.maximum_value} and no "
                            f"current value: set to 'unavailable'."
                        )
                        new_state = "unavailable"
                    elif relaxed_min <= new_state <= relaxed_max:
                        self._log(
                            f"New value {new_state} for '{entity_name}' out of range "
                            f"but within relaxed {relaxed_min}-{relaxed_max}: used."
                        )
                    else:
                        self._log(
                            f"New value {new_state} for '{entity_name}' out of relaxed "
                            f"range {relaxed_min}-{relaxed_max} and no usable "
                            f"current value: set to 'unavailable'."
                        )
                        new_state = "unavailable"
                else:
                    # Keep the current value; e.g. a 0 SoC while idle.
                    continue

            entity.last_updated = time.time()
            await self._update_evse_entity(
                evse_entity=entity, new_value=new_state, force_emit=force_emit
            )

    async def _update_evse_entity(
        self, evse_entity: ModbusConfigEntity, new_value, force_emit: bool = False
    ):
        """Cache a validated value and fire its change handler when it changed
        (or when force_emit re-broadcasts it). Stores the "unavailable" sentinel
        verbatim, like the Quasar driver.
        """
        current_value = evse_entity.current_value

        if current_value != new_value or force_emit:
            evse_entity.current_value = new_value
            handler = evse_entity.change_handler
            if handler == "_handle_charger_state_change":
                await self._handle_charger_state_change(
                    new_charger_state=new_value, old_charger_state=current_value
                )
            elif handler == "_handle_soc_change":
                await self._handle_soc_change(new_soc=new_value, old_soc=current_value)
            elif handler == "_handle_charger_error_state_change":
                await self._handle_charger_error_state_change({"dummy": None})
            elif handler == "_handle_charge_power_change":
                await self._handle_charge_power_change(new_power=new_value)
            elif handler is not None:
                self._log(f"unknown action: '{handler}'.", level="WARNING")

    # --- pre-processors (raw decoded register value -> entity value) ---
    def _round_to_int(self, value) -> int:
        return round(float(value))

    def _soc_permille_to_percent(self, value) -> int:
        """330 per-mille -> 33 %. Integer, as the consumers expect."""
        return round(float(value) / 10.0)

    def _error_unsigned(self, value) -> int:
        """The error bitmask is unsigned (40 defined bits); int64 decodes signed."""
        return int(value) & 0xFFFFFFFFFFFFFFFF

    async def _refresh_windows_if_stale(self):
        """The interlock/window registers are polled every base interval; when
        the values are older than that (e.g. minimal polling), read them now."""
        max_age = self.BASE_POLLING_INTERVAL_SECONDS * 3
        if not all(e.is_value_fresh(max_age) for e in self.CHARGER_WINDOW_ENTITIES):
            await self._get_and_process_registers(self.CHARGER_WINDOW_ENTITIES)

    # Refusal reasons, emitted on `discharge_refused` so the UI can explain
    # what the user can do about each. Deliberately codes, not sentences: the
    # remedy is decided in main_app, which knows about users; the driver does
    # not.
    REFUSED_SESSION_NOT_BIDIRECTIONAL = "session_not_bidirectional"
    REFUSED_V2G_NOT_OFFERED = "v2g_not_offered"
    REFUSED_WINDOW_UNKNOWN = "window_unknown"

    def _report_discharge_refusal(self, reason: str | None, source: str = ""):
        """Tell the rest of the app that a discharge was refused, or (reason
        None) that it no longer is. Refusing is silent otherwise: the charger
        reports no error and the UI shows a car that simply never discharges."""
        self._refusing_discharge_because = reason
        self.event_bus.emit_event(
            "discharge_refused",
            reason=reason,
            is_manual="__start_max_discharge_now" in source,
        )

    def _clear_refusal_if_resolved(self):
        """Drop a standing refusal as soon as its cause is gone.

        Refusing is tied to a request -- the interlocks only run when someone
        asks to discharge -- but *clearing* must not be, or the warning
        outlives the problem: a charger that starts offering V2G again would
        keep being reported as refusing until the next discharge happens to be
        asked for, which can be hours. The registers this depends on are read
        every base poll anyway.

        Only clears; never raises a refusal on its own. Warning someone about a
        discharge nobody asked for would be its own kind of noise.
        """
        reason = self._refusing_discharge_because
        if reason is None:
            return

        session_type = self._MCE_SESSION_TYPE.current_value
        floor = self._MCE_LOWER_LIMIT_POTENTIAL.current_value
        still_applies = {
            self.REFUSED_SESSION_NOT_BIDIRECTIONAL: (
                session_type not in BIDIRECTIONAL_SESSION_TYPES
            ),
            self.REFUSED_WINDOW_UNKNOWN: floor is None,
            self.REFUSED_V2G_NOT_OFFERED: floor is not None and floor >= 0,
        }.get(reason, False)

        if not still_applies:
            self._log(f"Discharge is possible again (was refused: {reason}).")
            self._report_discharge_refusal(None)

    async def _apply_discharge_interlocks(self, charge_power: int, source: str) -> int:
        """Discharge only when the session type permits it and the station
        offers V2G right now; then clamp into the offered window [X+22, 0].
        Returns the (possibly zeroed or clamped) power.
        """
        await self._refresh_windows_if_stale()
        session_type = self._MCE_SESSION_TYPE.current_value
        floor = self._MCE_LOWER_LIMIT_POTENTIAL.current_value

        if session_type not in BIDIRECTIONAL_SESSION_TYPES:
            self._log(
                f"Discharge of {charge_power}W requested from {source=} but the charge "
                f"session type (X+04) is {session_type}, not v2xDynamic (3) or "
                f"bidirectional (5). Not discharging.",
                level="WARNING",
            )
            self._report_discharge_refusal(
                self.REFUSED_SESSION_NOT_BIDIRECTIONAL, source
            )
            return 0
        if floor is None:
            self._log(
                f"Discharge of {charge_power}W requested from {source=} but the offered "
                f"window (X+22) is unknown. Not discharging.",
                level="WARNING",
            )
            self._report_discharge_refusal(self.REFUSED_WINDOW_UNKNOWN, source)
            return 0
        if floor >= 0:
            # Not an error: the station simply does not offer V2G right now.
            self._log(
                f"Discharge of {charge_power}W requested from {source=} but V2G is not "
                f"offered right now (X+22 = {floor:g} W). Not discharging."
            )
            self._report_discharge_refusal(self.REFUSED_V2G_NOT_OFFERED, source)
            return 0

        # Accepted: whatever was blocking discharge is no longer in the way.
        self._report_discharge_refusal(None, source)
        clamped = max(charge_power, int(floor))
        if clamped != charge_power:
            self._log(
                f"Requested discharge power {charge_power}W is below the offered floor "
                f"{floor:g}W (X+22); clamped to {clamped}W."
            )
        return clamped

    async def _clamp_to_charge_window(self, charge_power: int, source: str) -> int:
        """Clamp a charge request into the accept window [X+38, X+30]. When the
        window is not sane (upper limit unknown or 0 W, e.g. car not yet
        charge-ready) the configured maximum is trusted instead."""
        await self._refresh_windows_if_stale()
        upper = self._MCE_UPPER_LIMIT.current_value
        lower = self._MCE_LOWER_LIMIT.current_value
        if not isinstance(upper, (int, float)) or upper <= 0:
            return charge_power
        clamped = min(charge_power, int(upper))
        if isinstance(lower, (int, float)) and 0 < lower <= upper and clamped < lower:
            if lower > c.CHARGER_MAX_CHARGE_POWER:
                # Raising the request to the station's floor would exceed the
                # user's reduced-power setting, which is there to protect their
                # grid connection. Not charging is the safe answer.
                self._log(
                    f"Station's charge floor {lower:g}W (X+38) exceeds the configured "
                    f"maximum {c.CHARGER_MAX_CHARGE_POWER}W; not charging.",
                    level="WARNING",
                )
                return 0
            clamped = int(lower)
        if clamped != charge_power:
            self._log(
                f"Requested charge power {charge_power}W from {source=} is outside the "
                f"accept window [{lower}, {upper}] W (X+38, X+30); clamped to {clamped}W."
            )
        return clamped

    async def _set_charge_power(
        self,
        charge_power: int,
        skip_min_soc_check: bool = False,
        source: str | None = None,
    ):
        """Set the desired (dis)charge power in Watt (X+86). Does not by itself
        start a session. Checks: not below the minimum SoC when discharging,
        within the configured maxima, and the two V2G interlocks / windows.
        """
        self._log(f"called from {source}, power {charge_power}.", level="DEBUG")
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        if not skip_min_soc_check and charge_power < 0:
            current_soc = await self._get_car_soc()
            if current_soc in ["unavailable", "unknown"]:
                self._log(
                    "current SoC is 'unavailable', only expected when car is not connected",
                    level="WARNING",
                )
            elif current_soc <= c.CAR_MIN_SOC_IN_PERCENT:
                self._log(
                    f"A discharge is attempted from {source=}, while the current SoC is below "
                    f"the minimum ({c.CAR_MIN_SOC_IN_PERCENT})%. Stopping discharging.",
                    level="WARNING",
                )
                charge_power = 0

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

        if charge_power < 0:
            charge_power = await self._apply_discharge_interlocks(charge_power, source)
        elif charge_power > 0:
            charge_power = await self._clamp_to_charge_window(charge_power, source)

        if self.requested_charge_power == charge_power:
            self._log(
                f"New-charge-power-setting from {source=} is same as "
                f"current-charge-power-setting: {charge_power} W. Not writing to charger.",
                level="DEBUG",
            )
            return

        res = await self._modbus_write_mbr(
            self._MBR_SET_CHARGE_POWER,
            int(charge_power),
            source=f"set_charge_power, from {source}",
        )
        self.requested_charge_power = charge_power
        if not res:
            self._log(
                f"Failed to set charge power to {charge_power} Watt.", level="WARNING"
            )

    ######################################################################
    #                   POLLING RELATED FUNCTIONS                        #
    ######################################################################

    async def _set_poll_strategy(self):
        """Minimal: car disconnected, poll the state every 15 seconds.
        Base: car connected, poll everything every 5 seconds.
        """
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")

        await self._cancel_polling(reason="setting new polling strategy")

        charger_state = await self._get_charger_state()
        if charger_state in [None, "unavailable", "unknown"]:
            charger_state = self.DISCONNECTED_STATES[0]
            self._log(
                "Deciding polling strategy based on state unavailable charger state, "
                "assume disconnected."
            )
        else:
            self._log(
                f"Deciding polling strategy based on state: "
                f"{self.CHARGER_STATES.get(charger_state)}."
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
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")
        self._log(f"reason: {reason}")
        await cancel_timer_silent(self.hass, self.poll_timer_handle)
        self.poll_timer_handle = None
        self.event_bus.emit_event("evse_polled", stop=True)

    async def _minimal_polling(self, kwargs):
        """Car disconnected: only poll the state to see if a car connects."""
        if self._recovery_probe.is_armed:
            # We gave up on this charger, so polling was cancelled -- but
            # AppDaemon had already queued these callbacks. Each would block
            # for the full Modbus timeout on a dead socket, filling the log
            # and pushing the probe's own tick minutes behind. Skip them; the
            # probe is what decides whether the charger is back.
            return
        await self._get_and_process_registers([self._MCE_CHARGER_STATE])
        self.event_bus.emit_event("evse_polled", stop=False)

    async def _base_polling(self, kwargs):
        """Car connected: poll state, session type, power, SoC, windows, error."""
        if self._recovery_probe.is_armed:
            # We gave up on this charger, so polling was cancelled -- but
            # AppDaemon had already queued these callbacks. Each would block
            # for the full Modbus timeout on a dead socket, filling the log
            # and pushing the probe's own tick minutes behind. Skip them; the
            # probe is what decides whether the charger is back.
            return
        await self._get_and_process_registers(self.CHARGER_POLLING_ENTITIES)
        self._clear_refusal_if_resolved()
        self.event_bus.emit_event("evse_polled", stop=False)

    ######################################################################
    #                   MODBUS RELATED FUNCTIONS                         #
    ######################################################################

    async def _update_charger_communication_state(self, can_communicate: bool):
        self.event_bus.emit_event(
            "charger_communication_state_change", can_communicate=can_communicate
        )

    async def _modbus_read_mbrs(self, mbrs: list, source: str = "unknown"):
        """Read and decode a list of MBRs (one span per device). Returns the
        decoded values (None per MBR on an error response) or None when the
        read raised. Reading is done exclusively through this function.
        """
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting.", level="WARNING")
            return None
        try:
            results = await self._mb_client.read_registers(mbrs)
        except ModbusException as me:
            self._log(f"ModbusException {me}", level="WARNING")
            await self._handle_modbus_exception(source=f"{source} (read)")
            return None
        await self._reset_modbus_exception()
        return results

    async def _modbus_write_mbr(self, mbr: MBR, value, source: str) -> bool:
        """Write a value to an MBR. Writing is done exclusively through this function."""
        if not self._am_i_active:
            self._log("Called while inactive, not blocking.", level="DEBUG")
        if not self._mb_client.is_initialised:
            self._log("Client not initialised, aborting.", level="WARNING")
            return False
        try:
            result = await self._mb_client.write_modbus_register(
                modbus_register=mbr, value=value
            )
        except ModbusException as me:
            self._log(f"ModbusException {me}", level="WARNING")
            await self._handle_modbus_exception(source=f"{source} (write)")
            return False
        await self._reset_modbus_exception()
        if not result:
            self._log(f"Failed to write {value} to address {mbr.address} ({source}).")
        return bool(result)

    async def _handle_bad_modbus_config(self):
        """No connection with the Modbus server could be made; only expected at
        start-up. Post a sticky memo and stop polling."""
        self.notifier.post_sticky_memo(
            title="Error in charger configuration",
            message="Please check if charger is powered, has IP connection and "
            "if Host/Port are correct in configuration.",
            memo_id="no_comm_with_evse",
        )
        await self._cancel_polling(reason="no modbus connection")

    async def _handle_charger_error_state_change(self, kwargs):
        """Handle errors reported by the charger: a state in ERROR_STATES or a
        non-zero error bitmask (X+54). Called on the state change, on an error
        register change (kwargs without a state) and by the one-shot timer
        with is_final_check = True.
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
                    f"Charger reports error bitmask at register "
                    f"{entity.modbus_register.address}: {entity.current_value:#x}",
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
            else:
                self._log(
                    "Error still present, waiting for check_error_state timeout..."
                )
        else:
            self._log("Reset check_error_state timer, no error anymore.")
            await cancel_timer_silent(self.hass, self.timer_id_check_error_state)
            self.timer_id_check_error_state = None

    async def _handle_modbus_exception(self, source) -> bool:
        """Grade a Modbus (connection) exception: before the first successful
        connection it is a configuration problem; after that it is recoverable
        for MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS, then un-recoverable.
        Returns whether the exception is persistent beyond the timeout.
        """
        if self._is_shut_down:
            self._log("shut down, ignoring modbus exception.", level="DEBUG")
            return False
        self._log("called")
        is_unrecoverable = False
        if self.modbus_exception_counter is None:
            self._log(f"{source}: modbus exception. Configuration (not yet) invalid?")
            await self._handle_bad_modbus_config()
            is_unrecoverable = False

        if self.modbus_exception_counter == 0:
            self._log(f"{source}: First modbus exception.")
            self.timer_id_check_modus_exception_state = await set_oneshot_timer(
                self.hass,
                self.timer_id_check_modus_exception_state,
                self._handle_un_recoverable_error,
                delay=self.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS,
                reason="no Modbus response",
                source=source,
            )
            self.modbus_exception_counter = 1
            is_unrecoverable = False
        else:
            # Repeated: the timer above decides; no timer any more means the
            # time to see this as recoverable has run out.
            is_unrecoverable = self.timer_id_check_modus_exception_state in [None, ""]

        return is_unrecoverable

    async def _reset_modbus_exception(self):
        """After every successful read/write: reset the counter and timer and
        report the connection as alive."""
        if self.modbus_exception_counter == 1:
            self._log("There was an modbus exception, now solved.")
            if self._recovery_probe.is_armed:
                # We have given up on this charger and the probe owns the way
                # back. A poll that was already queued when polling was
                # cancelled must not clear the error card by itself: that
                # hides the problem while the charge mode is still Stop and
                # nothing is polling.
                self._log(
                    "Un-recoverable error still standing; "
                    "leaving recovery to the probe."
                )
            else:
                await self.v2g_main_app.reset_charger_communication_fault()
        self.modbus_exception_counter = 0
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        self.timer_id_check_modus_exception_state = None
        await self._update_charger_communication_state(can_communicate=True)

    async def _handle_un_recoverable_error(
        self, reason: str | None = None, source: str | None = None
    ):
        """The charger is considered non-responsive (error state or error
        bitmask for too long, or Modbus exceptions for too long): stop polling,
        deactivate, notify the user via v2g_main_app and mark SoC/power unknown.
        A manual restart of charger and V2G Liberty is needed.
        """
        # Called directly with a reason, and scheduled as a one-shot timer.
        # AppDaemon delivers a timer's kwargs as a single positional dict, which
        # lands in `reason`; unpack it so the reason stays a reason.
        if isinstance(reason, dict):
            source = reason.get("source", source)
            reason = reason.get("reason")
        if self._is_shut_down:
            self._log("shut down, not handling un-recoverable error.", level="DEBUG")
            return
        self._log(f"{source=}, {reason=}.")
        await cancel_timer_silent(self.hass, self.timer_id_check_modus_exception_state)
        await cancel_timer_silent(self.hass, self.timer_id_check_error_state)

        await self._cancel_polling(reason="un_recoverable charger error")
        # The only exception to the rule that _am_i_active is set from set_(in)active().
        self._am_i_active = False
        await self.v2g_main_app.handle_none_responsive_charger(
            was_car_connected=await self.is_car_connected(),
            reason=reason,
        )
        await self._update_charger_communication_state(can_communicate=False)

        await self._update_evse_entity(
            evse_entity=self._MCE_ACTUAL_POWER, new_value="unavailable"
        )
        await self._update_evse_entity(
            evse_entity=self._MCE_CAR_SOC, new_value="unavailable"
        )

        # From here on nothing polls, so nothing would notice the charger
        # coming back; the probe does.
        await self._recovery_probe.arm()

    # ── Recovery after an un-recoverable error ─────────────────────────

    async def _charger_is_healthy(self) -> bool:
        """Recovery-probe check, straight through the transport so it never
        touches the exception state machine. Healthy means all of: reachable,
        not in an error state, and no error bits. The last two matter for the
        charger that stayed reachable but reported a fault -- there a live
        connection proves nothing.
        """
        if not self._mb_client.connected:
            await self._mb_client.connect()
        mbrs = [self._MCE_CHARGER_STATE.modbus_register] + [
            entity.modbus_register for entity in self.CHARGER_ERROR_ENTITIES
        ]
        state, *errors = await self._mb_client.read_registers(mbrs)
        if state is None or state in self.ERROR_STATES:
            return False
        # None is an error response, not "no error".
        return all(error == 0 for error in errors)

    async def _handle_charger_recovered(self):
        """The probe found the charger back: resume polling and let the main
        app clear the problem and restore the charge mode."""
        self._log("Charger reachable and without error again; resuming.")
        self.modbus_exception_counter = 0
        await self._update_charger_communication_state(can_communicate=True)
        # Re-broadcast the state, changed or not. The main app wrote "Error"
        # into the UI and only a charger_state_change replaces it; a charger
        # that comes back in the state it failed in would otherwise never emit
        # one. Not by clearing the cache: None -> connected reads as a
        # reconnect and rings the reconnect monitor.
        await self._get_and_process_registers([self._MCE_CHARGER_STATE])
        if self._MCE_CHARGER_STATE.current_value is not None:
            await self._update_evse_entity(
                self._MCE_CHARGER_STATE,
                self._MCE_CHARGER_STATE.current_value,
                force_emit=True,
            )
        # Polling resumes whatever the charge mode is; the main app's mode
        # change (if it restores Automatic) brings set_active() after this.
        await self._set_poll_strategy()
        await self.v2g_main_app.handle_charger_recovered()
