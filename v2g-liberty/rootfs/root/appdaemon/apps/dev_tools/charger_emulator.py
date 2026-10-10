"""Wallbox Quasar charger emulator for the dev environment.

Makes the static Quasar mock (``quasar-mock:5020``) dynamic by acting as a
second Modbus client to it: every tick it reads the command registers V2G
Liberty writes (setpoint 260, action 257, control 81) and writes back
realistic report registers (actual power 526, state 537, SoC 538, errors
539-542, identity 1-3, max power 514). This unblocks automatic charger-phase
detection and lets scenarios be played without real hardware.

Producer/consumer with V2G (strict): the emulator writes ONLY report registers
and never touches V2G's command registers {81, 82, 83, 88, 257, 260}. Because
the mock uses one shared datastore, a write there would clobber V2G's control.

Control surface (create-once HA entities, change via Developer Tools > States):
- ``input_select.emulator_charger_scenario`` — pick a scenario.
- ``input_boolean.emulator_car_connected`` — connect/disconnect the car.
- ``input_number.emulator_soc`` — jump the SoC.

The charger-independent machinery (tick loop, ramp, SoC, control entities) is
in ``charger_emulator_base``; this module maps it onto the Quasar registers.

Dev-only: this app is not included in the production Docker image.
"""

from dev_tools.charger_emulator_base import (
    PHASE_CHARGING,
    PHASE_DISCHARGING,
    PHASE_DISCONNECTED,
    PHASE_EMPTY,
    PHASE_FULL,
    PHASE_IDLE,
    BaseChargerEmulator,
)
from dev_tools.charger_scenarios import (
    DEFAULT_SCENARIO,
    EMULATOR_WRITE_REGISTERS,
    QUASAR_1,
    REG_ACTION,
    REG_ACTUAL_POWER,
    REG_ERROR_1,
    REG_ERROR_2,
    REG_ERROR_3,
    REG_ERROR_4,
    REG_FIRMWARE,
    REG_LOCKED,
    REG_MAX_POWER,
    REG_SERIAL_HIGH,
    REG_SERIAL_LOW,
    REG_SETPOINT,
    REG_SOC,
    REG_STATE,
    SCENARIOS,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_DISCONNECTED,
    STATE_PAUSED,
    STATE_WAITING,
    int16_to_uint16,
    uint16_to_int16,
)

_STOP_ACTION = 2

# Profile fields that can be overridden from apps.yaml.
_PROFILE_ARGS = (
    "hw_max_charge_power_w",
    "hw_max_discharge_power_w",
    "hw_soc_floor_pct",
    "hw_soc_ceiling_pct",
    "battery_capacity_kwh",
    "power_jitter_w",
)

# Delivery phase -> Quasar charger state (register 537).
_PHASE_STATES = {
    PHASE_DISCONNECTED: STATE_DISCONNECTED,
    PHASE_IDLE: STATE_PAUSED,
    PHASE_CHARGING: STATE_CHARGING,
    PHASE_DISCHARGING: STATE_DISCHARGING,
    PHASE_FULL: STATE_WAITING,
    PHASE_EMPTY: STATE_WAITING,
}

_STATE_NAMES = {
    STATE_DISCONNECTED: "disconnected",
    STATE_CHARGING: "charging",
    STATE_WAITING: "waiting",
    STATE_PAUSED: "paused",
    STATE_DISCHARGING: "discharging",
}


class ChargerEmulator(BaseChargerEmulator):
    """AppDaemon dev app that drives the static Quasar mock dynamically."""

    SCENARIOS = SCENARIOS
    DEFAULT_SCENARIO = DEFAULT_SCENARIO
    BASE_PROFILE = QUASAR_1
    PROFILE_ARGS = _PROFILE_ARGS
    DEFAULT_HOST = "quasar-mock"
    LOG_TAG = "Charger emulator"

    # --- Charger-specific hooks --------------------------------------------
    def _battery_capacity_wh(self) -> float:
        return self._profile.battery_capacity_kwh * 1000.0

    def _seed_registers(self) -> dict[int, int]:
        seed = {
            REG_LOCKED: 0,
            REG_MAX_POWER: self._profile.hw_max_charge_power_w,
            REG_FIRMWARE: self._profile.firmware,
            REG_SERIAL_HIGH: self._profile.serial_high,
            REG_SERIAL_LOW: self._profile.serial_low,
            REG_ERROR_1: 0,
            REG_ERROR_2: 0,
            REG_ERROR_3: 0,
            REG_ERROR_4: 0,
        }
        if self._scenario.mirror and not self._car_connected:
            seed.update(self._disconnected_registers())
        else:
            # Connected and idle; frozen scenarios also start from a connected car.
            seed[REG_STATE] = STATE_PAUSED
            seed[REG_ACTUAL_POWER] = 0
            seed[REG_SOC] = round(self._soc)
        return seed

    def _disconnected_registers(self) -> dict[int, int]:
        return {REG_STATE: STATE_DISCONNECTED, REG_ACTUAL_POWER: 0, REG_SOC: 0}

    async def _read_command(self) -> tuple[int, bool] | None:
        raw_setpoint = await self._read(REG_SETPOINT)
        action = await self._read(REG_ACTION)
        if raw_setpoint is None or action is None:
            return None
        return uint16_to_int16(raw_setpoint), action == _STOP_ACTION

    def _derive_state_and_power(self, setpoint: int, action: int) -> tuple[int, int]:
        """Quasar view of the delivery phase: (state 537, power to deliver)."""
        phase, power = self._derive_phase(setpoint, action == _STOP_ACTION)
        return _PHASE_STATES[phase], power

    def _report_registers(self, phase: str, actual: int) -> dict[int, int]:
        return {
            REG_ACTUAL_POWER: int16_to_uint16(actual),
            REG_STATE: _PHASE_STATES[phase],
            REG_SOC: round(self._soc),
        }

    def _phase_name(self, phase: str) -> str:
        state = _PHASE_STATES[phase]
        return _STATE_NAMES.get(state, str(state))

    def _may_write(self, address: int) -> bool:
        return address in EMULATOR_WRITE_REGISTERS

    async def _resume_soc_from_mock(self):
        """Adopt the mock's current SoC (register 538) if it is a valid value,
        so a hot reload or restart continues instead of resetting to the default.
        """
        if not await self._ensure_connected():
            return
        raw = await self._read(REG_SOC)
        if raw is not None and 2 <= raw <= 97:
            self._soc = float(raw)
