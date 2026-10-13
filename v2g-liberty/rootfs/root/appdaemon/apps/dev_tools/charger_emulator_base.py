"""Shared core of the dev charger emulators (Wallbox Quasar, EVtec BiDiPro).

A charger emulator makes a static Modbus mock (``oitc/modbus-server``) dynamic
by acting as a second Modbus client to it: every tick it reads the command
registers V2G Liberty writes and writes back realistic report registers, so
V2G sees a charger that responds. The charger-independent part lives here —
the HA control entities, the tick loop, the asymmetric power ramp, SoC
integration with a hardware taper and the Modbus plumbing. A subclass supplies
the register model of one charger type through the hooks in the
"Charger-specific hooks" section.

Producer/consumer with V2G (strict): the emulator writes ONLY report registers
and never touches V2G's command registers. Because the mock uses one shared
datastore, a write there would clobber V2G's control. Each subclass declares
what it may write via ``_may_write``; ``_write`` refuses everything else.

Dev-only: not included in the production Docker image.
"""

import asyncio
import random
from dataclasses import replace
from typing import ClassVar

import appdaemon.plugins.hass.hassapi as hass
import pymodbus.client as modbus_client
from pymodbus.exceptions import ModbusException

# Symbolic delivery phases. The base class reasons in these; a subclass maps
# them onto its charger's state codes in ``_report_registers``.
PHASE_DISCONNECTED = "disconnected"
PHASE_IDLE = "idle"  # connected, delivering 0 W (setpoint 0 or stop requested)
PHASE_CHARGING = "charging"
PHASE_DISCHARGING = "discharging"
PHASE_FULL = "full"  # at the hardware SoC ceiling: a charge request tapers to 0
PHASE_EMPTY = "empty"  # at the hardware SoC floor: a discharge request tapers to 0


class BaseChargerEmulator(hass.Hass):
    """AppDaemon dev app that drives a static charger mock dynamically.

    Subclasses set the class attributes below and implement the hooks.
    """

    # --- Subclass configuration ---------------------------------------------
    SCENARIOS: ClassVar[dict] = {}
    DEFAULT_SCENARIO: str = "normal"
    BASE_PROFILE = None  # dataclass instance with the hardware constants
    PROFILE_ARGS: tuple[str, ...] = ()  # profile fields overridable from apps.yaml
    DEFAULT_HOST: str = "charger-mock"
    LOG_TAG: str = "Charger emulator"

    # Control surface (create-once HA entities; interactive with the dev
    # package, else change via Developer Tools > States).
    _SCENARIO_ENTITY = "input_select.emulator_charger_scenario"
    _CONNECT_ENTITY = "input_boolean.emulator_car_connected"
    _SOC_ENTITY = "input_number.emulator_soc"
    _SCENARIO_FRIENDLY_NAME = "Charger emulator scenario"
    _CONNECT_FRIENDLY_NAME = "Charger emulator: car connected"

    async def initialize(self):
        self.log("")
        self.log(
            f"#################### {self.LOG_TAG.upper()} (RE)STARTED ####################"
        )
        self._host = self.args.get("charger_host", self.DEFAULT_HOST)
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

        profile_kwargs = {k: self.args[k] for k in self.PROFILE_ARGS if k in self.args}
        self._base_profile = replace(self.BASE_PROFILE, **profile_kwargs)

        self._client = None
        self._running = True
        self._connection_ok = None  # tri-state, to log transitions only
        self._car_connected = True
        self._scenario = self.SCENARIOS[self.DEFAULT_SCENARIO]
        self._profile = self._base_profile
        self._soc = float(self._scenario.start_soc)
        self._init_charger_state()

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
        # jump the SoC back to the default (the mock keeps its registers).
        await self._resume_soc_from_mock()
        await self._apply_scenario(self._scenario.name)
        await self._sync_soc_entity()
        self._task = asyncio.create_task(self._run_loop())
        self.log(
            f"{self.LOG_TAG} started against {self._host}:{self._port} "
            f"(tick {self._interval}s, scenario '{self._scenario.name}', "
            f"soc_speedup {self._soc_speedup}x, "
            f"ramp up/down {self._ramp_up_seconds}/{self._ramp_down_seconds}s, "
            f"target {self._power_target_fraction}, "
            f"max {self._profile.hw_max_charge_power_w}W, "
            f"battery {self._battery_capacity_wh() / 1000:g}kWh)"
        )

    def terminate(self):
        self._running = False

    # --- Charger-specific hooks --------------------------------------------
    def _init_charger_state(self):
        """Initialise any extra per-instance state a subclass keeps."""

    def _battery_capacity_wh(self) -> float:
        raise NotImplementedError

    def _seed_registers(self) -> dict[int, int]:
        """Registers written on scenario activation: identity, limits, no error,
        and the start state matching the scenario/connection."""
        raise NotImplementedError

    def _disconnected_registers(self) -> dict[int, int]:
        """Registers written each tick while the car is disconnected."""
        raise NotImplementedError

    async def _read_command(self) -> tuple[int, bool] | None:
        """Read V2G's command: (setpoint_w, stop_requested), or None on a read
        failure (the tick is then skipped)."""
        raise NotImplementedError

    def _report_registers(self, phase: str, actual: int) -> dict[int, int]:
        """Registers written each tick while connected: state, power, SoC, …"""
        raise NotImplementedError

    def _may_write(self, address: int) -> bool:
        """Disjoint write-set guard: True only for report registers."""
        raise NotImplementedError

    async def _resume_soc_from_mock(self):
        """Adopt the mock's current SoC if it holds a valid value."""
        raise NotImplementedError

    def _phase_name(self, phase: str) -> str:
        """Name of a phase in the status log (charger-specific wording)."""
        return phase

    # --- Control surface ----------------------------------------------------
    async def _init_control_entities(self):
        """Adopt the scenario/connect HA input entities if present (dev package),
        else create virtual fallbacks so the emulator still works standalone."""
        scenario = await self.get_state(self._SCENARIO_ENTITY)
        if scenario not in self.SCENARIOS:
            scenario = self.DEFAULT_SCENARIO
            self.set_state(
                self._SCENARIO_ENTITY,
                state=scenario,
                attributes={
                    "options": list(self.SCENARIOS),
                    "friendly_name": self._SCENARIO_FRIENDLY_NAME,
                },
            )
        self._scenario = self.SCENARIOS[scenario]

        connected = await self.get_state(self._CONNECT_ENTITY)
        if connected not in ("on", "off"):
            connected = "on"
            self.set_state(
                self._CONNECT_ENTITY,
                state=connected,
                attributes={"friendly_name": self._CONNECT_FRIENDLY_NAME},
            )
        self._car_connected = connected == "on"

    async def _on_scenario_change(self, entity, attribute, old, new, kwargs):
        if new in self.SCENARIOS:
            self.log(f"{self.LOG_TAG} scenario -> '{new}'")
            await self._apply_scenario(new)

    async def _on_connect_change(self, entity, attribute, old, new, kwargs):
        self._car_connected = new == "on"
        self.log(f"{self.LOG_TAG} car connected -> {self._car_connected}")

    async def _on_soc_set(self, entity, attribute, old, new, kwargs):
        """Jump the emulated SoC to a value set via the SoC input_number."""
        try:
            value = float(new)
        except (TypeError, ValueError):
            return
        # Ignore our own echo (the tick pushes the live SoC onto this entity).
        if abs(value - self._soc) < 1.0:
            return
        self._soc = value
        self._last_soc_shown = round(value)
        self.log(f"{self.LOG_TAG} SoC set to {round(self._soc)}%")

    async def _sync_soc_entity(self):
        """Reflect the current SoC on the SoC input_number so the slider
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
    @property
    def _scenario_lock(self) -> asyncio.Lock:
        """Serialises scenario activation against the tick loop.

        Both run on the same event loop but both await Modbus I/O, so without
        this a tick that already passed the ``mirror`` check could write report
        registers on top of a freshly seeded frozen scenario. Created lazily so
        tests can drive the logic on an instance built with ``object.__new__``.
        """
        lock = getattr(self, "_scenario_lock_obj", None)
        if lock is None:
            lock = self._scenario_lock_obj = asyncio.Lock()
        return lock

    async def _apply_scenario(self, name: str):
        async with self._scenario_lock:
            self._scenario = self.SCENARIOS[name]
            self._profile = replace(
                self._base_profile, **self._scenario.profile_overrides
            )
            self._actual_power = 0.0

            if not await self._ensure_connected():
                return

            seed = self._seed_registers()
            # Scenario-specific overrides (error state/register, wrong identity) win.
            seed.update(self._scenario_registers())
            try:
                await self._write_many(seed)
            except (ModbusException, OSError) as e:
                # Activation runs from initialize() before the tick loop starts, so
                # an unhandled write failure would leave the app dead instead of
                # retrying on the next tick.
                self.log(
                    f"{self.LOG_TAG} could not seed scenario '{name}': {e}",
                    level="WARNING",
                )

    def _scenario_registers(self) -> dict[int, int]:
        """The scenario's raw register overrides, keyed by absolute address."""
        return dict(self._scenario.registers)

    # --- Tick loop --------------------------------------------------------
    async def _run_loop(self):
        while self._running:
            try:
                await self._tick()
            except Exception as e:  # noqa: BLE001 - dev tool: keep the loop alive
                self.log(f"{self.LOG_TAG} tick error: {e}", level="WARNING")
            await asyncio.sleep(self._interval)

    async def _tick(self):
        async with self._scenario_lock:
            await self._tick_locked()

    async def _tick_locked(self):
        if not await self._ensure_connected():
            return
        # Frozen scenarios hold the registers seeded on activation.
        if not self._scenario.mirror:
            return

        if not self._car_connected:
            self._actual_power = 0.0
            await self._write_many(self._disconnected_registers())
            self._log_status(PHASE_DISCONNECTED, 0, 0)
            return

        command = await self._read_command()
        if command is None:
            return
        setpoint, stop_requested = command

        phase, requested = self._derive_phase(setpoint, stop_requested)
        actual = self._ramp_power(requested)
        self._integrate_soc(actual)

        await self._write_many(self._report_registers(phase, actual))
        await self._sync_soc_entity()
        self._log_status(phase, setpoint, actual)

    def _log_status(self, phase: str, setpoint: int, actual: int):
        """Log a throttled status line: on phase change or every N seconds."""
        if self._status_every == 0:
            return
        self._ticks_since_status += 1
        if (
            phase != self._last_logged_state
            or self._ticks_since_status >= self._status_every
        ):
            self._ticks_since_status = 0
            self._last_logged_state = phase
            self.log(
                f"status: state={self._phase_name(phase)} setpoint={setpoint}W "
                f"actual={actual}W soc={round(self._soc)}%"
            )

    # --- Physics ----------------------------------------------------------
    def _derive_phase(self, setpoint: int, stop_requested: bool) -> tuple[str, int]:
        """Map V2G's request onto a delivery phase and the power to deliver,
        clamped to the hardware max and tapered at the SoC bounds."""
        p = self._profile
        if stop_requested or setpoint == 0:
            return PHASE_IDLE, 0
        if setpoint > 0:
            if self._soc >= p.hw_soc_ceiling_pct:  # full: taper off
                return PHASE_FULL, 0
            return PHASE_CHARGING, min(setpoint, p.hw_max_charge_power_w)
        if self._soc <= p.hw_soc_floor_pct:  # empty: taper off
            return PHASE_EMPTY, 0
        return PHASE_DISCHARGING, max(setpoint, -p.hw_max_discharge_power_w)

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
        self._soc += actual_power_w * dt_hours / self._battery_capacity_wh() * 100.0
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
                self.log(f"{self.LOG_TAG} connected to {self._host}:{self._port}")
            else:
                self.log(
                    f"{self.LOG_TAG} lost connection to {self._host}:{self._port} {detail}",
                    level="WARNING",
                )

    async def _read(self, address: int):
        """Read one holding register; None on any failure."""
        words = await self._read_words(address, 1)
        return None if words is None else words[0]

    async def _read_words(self, address: int, count: int):
        """Read ``count`` consecutive holding registers; None on any failure."""
        try:
            result = await self._client.read_holding_registers(
                address=address, count=count, device_id=1
            )
        except (ModbusException, OSError):
            return None
        if result is None or result.isError() or len(result.registers) < count:
            return None
        return list(result.registers[:count])

    async def _write_many(self, registers: dict[int, int]):
        for address, value in registers.items():
            await self._write(address, value)

    async def _write(self, address: int, value: int):
        if not self._may_write(address):
            raise ValueError(f"Emulator may not write command register {address}")
        await self._client.write_register(
            address=address, value=int(value) & 0xFFFF, device_id=1
        )
