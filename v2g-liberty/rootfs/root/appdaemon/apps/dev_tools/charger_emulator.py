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

Dev-only: this app is not included in the production Docker image.
"""

import asyncio
import random
from dataclasses import replace

import appdaemon.plugins.hass.hassapi as hass
import pymodbus.client as modbus_client
from pymodbus.exceptions import ModbusException

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

_STATE_NAMES = {
    STATE_DISCONNECTED: "disconnected",
    STATE_CHARGING: "charging",
    STATE_WAITING: "waiting",
    STATE_PAUSED: "paused",
    STATE_DISCHARGING: "discharging",
}


class ChargerEmulator(hass.Hass):
    """AppDaemon dev app that drives the static Quasar mock dynamically."""

    _SCENARIO_ENTITY = "input_select.emulator_charger_scenario"
    _CONNECT_ENTITY = "input_boolean.emulator_car_connected"
    _SOC_ENTITY = "input_number.emulator_soc"

    async def initialize(self):
        self.log("")
        self.log(
            "#################### CHARGER EMULATOR (RE)STARTED ####################"
        )
        self._host = self.args.get("charger_host", "quasar-mock")
        self._port = int(self.args.get("charger_port", 5020))
        self._interval = float(self.args.get("update_interval", 0.5))
        # Accelerate the SoC ramp for testing (1.0 = real time).
        self._soc_speedup = float(self.args.get("soc_speedup", 1.0))
        # Power ramp (asymmetric): increasing the magnitude is slow
        # (ramp_up_seconds to go full-scale), decreasing toward zero is fast
        # (ramp_down_seconds). The delivered power settles at this fraction of the
        # requested power and is hard-clamped so it never exceeds the hardware max.
        self._ramp_up_seconds = float(self.args.get("ramp_up_seconds", 15))
        self._ramp_down_seconds = float(self.args.get("ramp_down_seconds", 2))
        self._power_target_fraction = float(
            self.args.get("power_target_fraction", 0.92)
        )
        self._actual_power = 0.0

        profile_kwargs = {k: self.args[k] for k in _PROFILE_ARGS if k in self.args}
        self._base_profile = replace(QUASAR_1, **profile_kwargs)

        self._client = None
        self._running = True
        self._connection_ok = None  # tri-state, to log transitions only
        self._car_connected = True
        self._scenario = SCENARIOS[DEFAULT_SCENARIO]
        self._profile = self._base_profile
        self._soc = float(self._scenario.start_soc)

        # Throttled status heartbeat to the emulator log (0 = off).
        self._status_log_seconds = float(self.args.get("status_log_seconds", 10))
        self._status_every = (
            max(1, round(self._status_log_seconds / self._interval))
            if self._status_log_seconds > 0
            else 0
        )
        self._ticks_since_status = 0
        self._last_logged_state = None
        self._last_soc_shown = None

        # Control surface: prefer real HA input entities (from the dev package,
        # interactive in the dev dashboard); fall back to virtual set_state
        # entities (changed via Developer Tools > States) when absent.
        await self._init_control_entities()
        self.listen_state(self._on_scenario_change, self._SCENARIO_ENTITY)
        self.listen_state(self._on_connect_change, self._CONNECT_ENTITY)
        self.listen_state(self._on_soc_set, self._SOC_ENTITY)

        # Resume from the mock's current SoC so an app reload/restart doesn't
        # jump the SoC back to the default (the mock keeps register 538).
        await self._resume_soc_from_mock()
        await self._apply_scenario(self._scenario.name)
        await self._sync_soc_entity()
        self._task = asyncio.create_task(self._run_loop())
        self.log(
            f"Charger emulator started against {self._host}:{self._port} "
            f"(tick {self._interval}s, scenario '{self._scenario.name}', "
            f"soc_speedup {self._soc_speedup}x, "
            f"ramp up/down {self._ramp_up_seconds}/{self._ramp_down_seconds}s, "
            f"target {self._power_target_fraction}, "
            f"max {self._profile.hw_max_charge_power_w}W, "
            f"battery {self._profile.battery_capacity_kwh}kWh)"
        )

    def terminate(self):
        self._running = False

    async def _init_control_entities(self):
        """Adopt the scenario/connect HA input entities if present (dev package),
        else create virtual fallbacks so the emulator still works standalone."""
        scenario = await self.get_state(self._SCENARIO_ENTITY)
        if scenario not in SCENARIOS:
            scenario = DEFAULT_SCENARIO
            self.set_state(
                self._SCENARIO_ENTITY,
                state=scenario,
                attributes={
                    "options": list(SCENARIOS),
                    "friendly_name": "Charger emulator scenario",
                },
            )
        self._scenario = SCENARIOS[scenario]

        connected = await self.get_state(self._CONNECT_ENTITY)
        if connected not in ("on", "off"):
            connected = "on"
            self.set_state(
                self._CONNECT_ENTITY,
                state=connected,
                attributes={"friendly_name": "Charger emulator: car connected"},
            )
        self._car_connected = connected == "on"

    # --- Control-surface callbacks ----------------------------------------
    async def _on_scenario_change(self, entity, attribute, old, new, kwargs):
        if new in SCENARIOS:
            self.log(f"Charger emulator scenario -> '{new}'")
            await self._apply_scenario(new)

    async def _on_connect_change(self, entity, attribute, old, new, kwargs):
        self._car_connected = new == "on"
        self.log(f"Charger emulator car connected -> {self._car_connected}")

    async def _on_soc_set(self, entity, attribute, old, new, kwargs):
        """Jump the emulated SoC to a value set via input_number.emulator_soc."""
        try:
            value = float(new)
        except (TypeError, ValueError):
            return
        # Ignore our own echo (the tick pushes the live SoC onto this entity).
        if abs(value - self._soc) < 1.0:
            return
        self._soc = value
        self._last_soc_shown = round(value)
        self.log(f"Charger emulator SoC set to {round(self._soc)}%")

    async def _sync_soc_entity(self):
        """Reflect the current SoC on input_number.emulator_soc so the slider
        tracks it. Only writes on a changed (rounded) value to limit churn."""
        rounded = round(self._soc)
        if rounded == self._last_soc_shown:
            return
        self._last_soc_shown = rounded
        try:
            await self.call_service(
                "input_number/set_value",
                entity_id=self._SOC_ENTITY,
                value=rounded,
            )
        except Exception as e:  # noqa: BLE001 - entity absent without the dev package
            self.log(f"Could not sync SoC entity: {e}", level="DEBUG")

    # --- Scenario activation ----------------------------------------------
    async def _apply_scenario(self, name: str):
        self._scenario = SCENARIOS[name]
        self._profile = replace(self._base_profile, **self._scenario.profile_overrides)
        self._actual_power = 0.0

        if not await self._ensure_connected():
            return

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
        if self._scenario.mirror and self._car_connected:
            seed[REG_STATE] = STATE_PAUSED
            seed[REG_ACTUAL_POWER] = 0
            seed[REG_SOC] = round(self._soc)
        elif self._scenario.mirror:
            seed[REG_STATE] = STATE_DISCONNECTED
            seed[REG_ACTUAL_POWER] = 0
            seed[REG_SOC] = 0
        else:
            seed[REG_STATE] = STATE_PAUSED
            seed[REG_ACTUAL_POWER] = 0
            seed[REG_SOC] = round(self._soc)

        # Scenario-specific overrides (error state/register, wrong identity) win.
        seed.update(self._scenario.registers)
        await self._write_many(seed)

    async def _resume_soc_from_mock(self):
        """Adopt the mock's current SoC (register 538) if it is a valid value,
        so a hot reload or restart continues instead of resetting to the default.
        """
        if not await self._ensure_connected():
            return
        raw = await self._read(REG_SOC)
        if raw is not None and 2 <= raw <= 97:
            self._soc = float(raw)

    # --- Tick loop --------------------------------------------------------
    async def _run_loop(self):
        while self._running:
            try:
                await self._tick()
            except Exception as e:  # noqa: BLE001 - dev tool: keep the loop alive
                self.log(f"Charger emulator tick error: {e}", level="WARNING")
            await asyncio.sleep(self._interval)

    async def _tick(self):
        if not await self._ensure_connected():
            return
        # Frozen scenarios hold the registers seeded on activation.
        if not self._scenario.mirror:
            return

        if not self._car_connected:
            self._actual_power = 0.0
            await self._write_many(
                {REG_STATE: STATE_DISCONNECTED, REG_ACTUAL_POWER: 0, REG_SOC: 0}
            )
            self._log_status(STATE_DISCONNECTED, 0, 0)
            return

        raw_setpoint = await self._read(REG_SETPOINT)
        action = await self._read(REG_ACTION)
        if raw_setpoint is None or action is None:
            return
        setpoint = uint16_to_int16(raw_setpoint)

        state, requested = self._derive_state_and_power(setpoint, action)
        actual = self._ramp_power(requested)
        self._integrate_soc(actual)

        await self._write_many(
            {
                REG_ACTUAL_POWER: int16_to_uint16(actual),
                REG_STATE: state,
                REG_SOC: round(self._soc),
            }
        )
        await self._sync_soc_entity()
        self._log_status(state, setpoint, actual)

    def _log_status(self, state: int, setpoint: int, actual: int):
        """Log a throttled status line: on state change or every N seconds."""
        if self._status_every == 0:
            return
        self._ticks_since_status += 1
        if (
            state != self._last_logged_state
            or self._ticks_since_status >= self._status_every
        ):
            self._ticks_since_status = 0
            self._last_logged_state = state
            name = _STATE_NAMES.get(state, str(state))
            self.log(
                f"status: state={name} setpoint={setpoint}W actual={actual}W "
                f"soc={round(self._soc)}%"
            )

    def _derive_state_and_power(self, setpoint: int, action: int):
        p = self._profile
        if action == _STOP_ACTION or setpoint == 0:
            return STATE_PAUSED, 0
        if setpoint > 0:
            target = min(setpoint, p.hw_max_charge_power_w)
            if self._soc >= p.hw_soc_ceiling_pct:  # full: taper off
                return STATE_WAITING, 0
            return STATE_CHARGING, target
        target = max(setpoint, -p.hw_max_discharge_power_w)
        if self._soc <= p.hw_soc_floor_pct:  # empty: taper off
            return STATE_WAITING, 0
        return STATE_DISCHARGING, target

    def _ramp_power(self, requested: int) -> int:
        """Ramp the delivered power toward a fraction of the requested power.

        Increasing the magnitude (drawing/feeding more) is slow — takes
        ``ramp_up_seconds`` to cover the full range; decreasing toward zero is
        fast (``ramp_down_seconds``). Applies to every power change, not just a
        cold start. Varies slightly around the target and is hard-clamped to the
        hardware max so it never exceeds it.
        """
        p = self._profile
        target = self._power_target_fraction * requested
        if target != 0:
            target += random.uniform(-p.power_jitter_w, p.power_jitter_w)

        cur = self._actual_power
        if cur == 0:
            increasing = target != 0
        elif (cur > 0) == (target > 0):  # same direction
            increasing = abs(target) > abs(cur)
        else:  # crossing zero: first head back toward zero
            increasing = False
        ramp_seconds = self._ramp_up_seconds if increasing else self._ramp_down_seconds

        max_step = p.hw_max_charge_power_w * self._interval / ramp_seconds
        delta = target - cur
        self._actual_power = cur + max(-max_step, min(max_step, delta))
        self._actual_power = max(
            -p.hw_max_discharge_power_w,
            min(p.hw_max_charge_power_w, self._actual_power),
        )
        return round(self._actual_power)

    def _integrate_soc(self, actual_power_w: int):
        p = self._profile
        dt_hours = self._interval * self._soc_speedup / 3600.0
        self._soc += (
            actual_power_w * dt_hours / (p.battery_capacity_kwh * 1000.0) * 100.0
        )
        self._soc = min(max(self._soc, p.hw_soc_floor_pct), p.hw_soc_ceiling_pct)

    # --- Modbus helpers ---------------------------------------------------
    async def _ensure_connected(self) -> bool:
        if self._client is None:
            self._client = modbus_client.AsyncModbusTcpClient(
                host=self._host, port=self._port, timeout=3
            )
        if not self._client.connected:
            try:
                await self._client.connect()
            except (ModbusException, OSError) as e:
                self._log_connection(False, str(e))
                return False
        ok = self._client.connected
        self._log_connection(ok)
        return ok

    def _log_connection(self, ok: bool, detail: str = ""):
        if ok != self._connection_ok:
            self._connection_ok = ok
            if ok:
                self.log(f"Charger emulator connected to {self._host}:{self._port}")
            else:
                self.log(
                    f"Charger emulator lost connection to {self._host}:{self._port} {detail}",
                    level="WARNING",
                )

    async def _read(self, address: int):
        try:
            result = await self._client.read_holding_registers(
                address=address, count=1, device_id=1
            )
        except (ModbusException, OSError):
            return None
        if result is None or result.isError():
            return None
        return result.registers[0]

    async def _write_many(self, registers: dict[int, int]):
        for address, value in registers.items():
            await self._write(address, value)

    async def _write(self, address: int, value: int):
        if address not in EMULATOR_WRITE_REGISTERS:
            raise ValueError(f"Emulator may not write command register {address}")
        await self._client.write_register(
            address=address, value=int(value) & 0xFFFF, device_id=1
        )
