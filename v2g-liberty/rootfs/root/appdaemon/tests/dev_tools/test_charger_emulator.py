"""Isolated tests for the dev charger emulator.

Drive the emulator's scenario, mirror, ramp and SoC-taper logic against a fake
Modbus client (an in-memory register store), without AppDaemon or the mock
container. The emulator instance is built with ``object.__new__`` so we skip
``hass.Hass.__init__`` and set only the attributes the logic needs.
"""

import dataclasses
from types import SimpleNamespace

import pytest

from dev_tools.charger_emulator import ChargerEmulator
from dev_tools.charger_scenarios import (
    QUASAR_1,
    REG_ACTION,
    REG_ACTUAL_POWER,
    REG_ERROR_1,
    REG_FIRMWARE,
    REG_MAX_POWER,
    REG_SERIAL_HIGH,
    REG_SETPOINT,
    REG_SOC,
    REG_STATE,
    SCENARIOS,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_DISCONNECTED,
    STATE_ERROR,
    STATE_PAUSED,
    STATE_WAITING,
    int16_to_uint16,
    uint16_to_int16,
)

MAX = QUASAR_1.hw_max_charge_power_w  # 5600


class FakeModbusClient:
    """Minimal async pymodbus stand-in backed by an in-memory register store."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.connected = True

    async def connect(self):
        self.connected = True
        return True

    async def read_holding_registers(self, address, count=1, device_id=1):
        regs = [self.store.get(address + i, 0) for i in range(count)]
        return SimpleNamespace(registers=regs, isError=lambda: False)

    async def write_register(self, address, value, device_id=1):
        self.store[address] = value
        return SimpleNamespace(isError=lambda: False)

    def close(self):
        pass


def make_emulator(fake, **over):
    e = object.__new__(ChargerEmulator)
    e.log = lambda *a, **k: None
    e._client = fake
    e._host = "test"
    e._port = 5020
    e._interval = over.get("interval", 0.5)
    e._soc_speedup = over.get("soc_speedup", 1.0)
    e._ramp_up_seconds = over.get("ramp_up_seconds", 15)
    e._ramp_down_seconds = over.get("ramp_down_seconds", 2)
    e._power_target_fraction = over.get("power_target_fraction", 0.92)
    e._actual_power = 0.0
    e._running = True
    e._connection_ok = True
    e._car_connected = over.get("car_connected", True)

    profile = over.get("profile", QUASAR_1)
    if "power_jitter_w" in over:
        profile = dataclasses.replace(profile, power_jitter_w=over["power_jitter_w"])
    e._base_profile = profile
    e._profile = profile
    e._scenario = SCENARIOS[over.get("scenario", "normal")]
    e._soc = float(over.get("soc", 33))
    e._status_every = 0
    e._ticks_since_status = 0
    e._last_logged_state = None
    e._last_soc_shown = None
    return e


def _decoded(store, addr):
    return uint16_to_int16(store.get(addr, 0))


# --- encoding (sync) -------------------------------------------------------
def test_encoding_roundtrip():
    for v in (-MAX, -1000, -1, 0, 1, 1000, MAX):
        assert uint16_to_int16(int16_to_uint16(v)) == v
    assert int16_to_uint16(-1000) == 64536


# --- state derivation (sync) -----------------------------------------------
def test_derive_state_and_power():
    e = make_emulator(FakeModbusClient())
    assert e._derive_state_and_power(3000, 1) == (STATE_CHARGING, 3000)
    assert e._derive_state_and_power(-3000, 1) == (STATE_DISCHARGING, -3000)
    assert e._derive_state_and_power(0, 1) == (STATE_PAUSED, 0)
    assert e._derive_state_and_power(3000, 2) == (STATE_PAUSED, 0)  # stop action
    # requested above hardware max is capped
    assert e._derive_state_and_power(9999, 1) == (STATE_CHARGING, MAX)
    assert e._derive_state_and_power(-9999, 1) == (STATE_DISCHARGING, -MAX)


def test_derive_taper_at_soc_limits():
    full = make_emulator(FakeModbusClient(), soc=QUASAR_1.hw_soc_ceiling_pct)
    assert full._derive_state_and_power(5000, 1) == (STATE_WAITING, 0)
    empty = make_emulator(FakeModbusClient(), soc=QUASAR_1.hw_soc_floor_pct)
    assert empty._derive_state_and_power(-5000, 1) == (STATE_WAITING, 0)


# --- power ramp (sync) -----------------------------------------------------
def test_ramp_timing_up_slow_down_fast():
    e = make_emulator(FakeModbusClient(), power_jitter_w=0)
    target = round(0.92 * MAX)

    up_ticks = 0
    while round(e._actual_power) < target:
        e._ramp_power(MAX)
        up_ticks += 1
        assert up_ticks < 200
    # 0 -> 92% of max should take roughly ramp_up_seconds (15 s @ 0.5 s = 30 ticks)
    assert 24 <= up_ticks <= 34

    down_ticks = 0
    while round(e._actual_power) > 0:
        e._ramp_power(0)
        down_ticks += 1
        assert down_ticks < 200
    # down is much faster (2 s @ 0.5 s = 4 ticks)
    assert down_ticks <= 6
    assert down_ticks < up_ticks


def test_ramp_hard_clamp_never_exceeds_max():
    # fraction 1.0 + jitter would push above max without the clamp.
    e = make_emulator(FakeModbusClient(), power_jitter_w=200, power_target_fraction=1.0)
    peak = max(e._ramp_power(MAX) for _ in range(200))
    assert peak <= MAX
    e2 = make_emulator(
        FakeModbusClient(), power_jitter_w=200, power_target_fraction=1.0
    )
    trough = min(e2._ramp_power(-MAX) for _ in range(200))
    assert trough >= -MAX


# --- mirror tick (async) ---------------------------------------------------
@pytest.mark.asyncio
async def test_mirror_tick_charging_follows_setpoint():
    fake = FakeModbusClient({REG_SETPOINT: 5600, REG_ACTION: 1})
    e = make_emulator(fake, power_jitter_w=0)
    for _ in range(40):
        await e._tick()
    assert fake.store[REG_STATE] == STATE_CHARGING
    actual = _decoded(fake.store, REG_ACTUAL_POWER)
    assert 0 < actual <= MAX
    assert abs(actual - round(0.92 * MAX)) <= 5  # settled at ~92%
    # command registers must be left untouched by the emulator
    assert fake.store[REG_SETPOINT] == 5600
    assert fake.store[REG_ACTION] == 1


@pytest.mark.asyncio
async def test_mirror_tick_discharging():
    fake = FakeModbusClient({REG_SETPOINT: int16_to_uint16(-5600), REG_ACTION: 1})
    e = make_emulator(fake, power_jitter_w=0)
    for _ in range(40):
        await e._tick()
    assert fake.store[REG_STATE] == STATE_DISCHARGING
    actual = _decoded(fake.store, REG_ACTUAL_POWER)
    assert -MAX <= actual < 0


@pytest.mark.asyncio
async def test_mirror_tick_paused_when_setpoint_zero():
    fake = FakeModbusClient({REG_SETPOINT: 0, REG_ACTION: 1})
    e = make_emulator(fake)
    await e._tick()
    assert fake.store[REG_STATE] == STATE_PAUSED
    assert _decoded(fake.store, REG_ACTUAL_POWER) == 0


@pytest.mark.asyncio
async def test_disconnected_tick_writes_zero_state():
    fake = FakeModbusClient({REG_SETPOINT: 5600, REG_ACTION: 1})
    e = make_emulator(fake, car_connected=False)
    await e._tick()
    assert fake.store[REG_STATE] == STATE_DISCONNECTED
    assert fake.store[REG_ACTUAL_POWER] == 0
    assert fake.store[REG_SOC] == 0


@pytest.mark.asyncio
async def test_soc_ramps_up_while_charging():
    fake = FakeModbusClient({REG_SETPOINT: 5600, REG_ACTION: 1})
    e = make_emulator(fake, power_jitter_w=0, soc_speedup=100, soc=50)
    for _ in range(60):
        await e._tick()
    assert e._soc > 50
    assert e._soc <= QUASAR_1.hw_soc_ceiling_pct


# --- disjoint write guard (async) ------------------------------------------
@pytest.mark.asyncio
async def test_write_guard_rejects_command_registers():
    e = make_emulator(FakeModbusClient())
    for cmd in (REG_SETPOINT, REG_ACTION, 81, 82, 83, 88):
        with pytest.raises(ValueError):
            await e._write(cmd, 1)
    # report registers are allowed
    await e._write(REG_ACTUAL_POWER, 100)


# --- scenarios (async) -----------------------------------------------------
@pytest.mark.asyncio
async def test_scenario_error_state():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("error_state")
    assert fake.store[REG_STATE] == STATE_ERROR
    assert fake.store[REG_ACTUAL_POWER] == 0
    # frozen: a tick must not overwrite the error state back to charging
    fake.store[REG_SETPOINT] = 5600
    fake.store[REG_ACTION] = 1
    await e._tick()
    assert fake.store[REG_STATE] == STATE_ERROR


@pytest.mark.asyncio
async def test_scenario_internal_error():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("internal_error")
    assert fake.store[REG_ERROR_1] == 1234
    assert fake.store[REG_ACTUAL_POWER] == 0


@pytest.mark.asyncio
async def test_scenario_wrong_fingerprint():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("wrong_fingerprint")
    assert fake.store[REG_FIRMWARE] == 9999
    assert fake.store[REG_SERIAL_HIGH] == 0


@pytest.mark.asyncio
async def test_scenario_reduced_max_power_caps_delivery():
    fake = FakeModbusClient({REG_SETPOINT: 5600, REG_ACTION: 1})
    e = make_emulator(fake, power_jitter_w=0)
    await e._apply_scenario("reduced_max_power")
    assert fake.store[REG_MAX_POWER] == 3700
    for _ in range(60):
        await e._tick()
    actual = _decoded(fake.store, REG_ACTUAL_POWER)
    assert actual <= 3700


# --- resume SoC (async) ----------------------------------------------------
@pytest.mark.asyncio
async def test_resume_soc_from_mock_adopts_valid_value():
    fake = FakeModbusClient({REG_SOC: 50})
    e = make_emulator(fake, soc=33)
    await e._resume_soc_from_mock()
    assert e._soc == 50.0


@pytest.mark.asyncio
async def test_resume_soc_ignores_invalid_value():
    fake = FakeModbusClient({REG_SOC: 0})  # 0 = idle/unavailable, not a real SoC
    e = make_emulator(fake, soc=33)
    await e._resume_soc_from_mock()
    assert e._soc == 33.0
