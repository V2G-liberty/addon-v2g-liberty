"""Isolated tests for the dev EVtec BiDiPro charger emulator.

Drive the emulator's scenario, interlock, mirror and encoding logic against a
fake Modbus client (an in-memory register store), without AppDaemon or the mock
container. The emulator instance is built with ``object.__new__`` so we skip
``hass.Hass.__init__`` and set only the attributes the logic needs. Register
semantics follow the hardware-tested EVtec Modbus 2.0 contract.
"""

import asyncio
import dataclasses
import json
from types import SimpleNamespace

import pytest
from dev_tools.charger_emulator_base import (
    PHASE_CHARGING,
    PHASE_DISCHARGING,
    PHASE_EMPTY,
    PHASE_FULL,
    PHASE_IDLE,
)
from dev_tools.charger_emulator_evtec import EVtecChargerEmulator
from dev_tools.charger_scenarios_evtec import (
    CP_COMMUNICATION_TIMEOUT,
    CP_FALLBACK_POWER,
    CP_MODEL,
    CP_NUM_CONNECTORS,
    CP_STATE,
    CP_STATE_IN_USE,
    CP_STATE_READY,
    DEFAULT_CONNECTOR,
    EVTEC_BIDI_PRO_10,
    OFF_BATTERY_CAPACITY,
    OFF_CAR_ID,
    OFF_CHARGED_ENERGY,
    OFF_CONNECTOR_STATE,
    OFF_DISCHARGED_ENERGY,
    OFF_ERROR,
    OFF_INPUT_POWER,
    OFF_LOWER_LIMIT,
    OFF_LOWER_LIMIT_POTENTIAL,
    OFF_POWER,
    OFF_PRESENT_CONSUMPTION,
    OFF_SESSION_STATE,
    OFF_SESSION_TYPE,
    OFF_SOC,
    OFF_SUSPEND_MODE,
    OFF_UPPER_LIMIT,
    SCENARIOS,
    SESSION_DEFAULT,
    SESSION_STATE_CURRENT_DEMAND,
    SESSION_STATE_GRID_FEED_IN,
    SESSION_STATE_READY,
    STATE_BOOT,
    STATE_CHARGING_END,
    STATE_CURRENT_DEMAND,
    STATE_ERROR,
    STATE_GRID_FEED,
    STATE_READY,
    STATE_WAIT_FOR_GRID,
    dec_float32,
    dec_int32,
    dec_int64,
    dec_string,
    enc_float32,
    enc_int32,
    enc_int64,
    enc_string,
    words_at,
)
from pymodbus.exceptions import ModbusException

BASE = DEFAULT_CONNECTOR * 100
MAX = EVTEC_BIDI_PRO_10.hw_max_charge_power_w  # 10000


class FakeModbusClient:
    """Minimal async pymodbus stand-in backed by an in-memory register store.
    Records every write as (address, words) to assert on FC16 grouping."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.connected = True
        self.writes = []
        # Fault injection: "raise" makes every read/write raise (a hung mock),
        # "error" returns an error response, "short" returns too few registers.
        self.fault = None

    async def connect(self):
        self.connected = True
        return True

    async def read_holding_registers(self, address, count=1, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        regs = [self.store.get(address + i, 0) for i in range(count)]
        if self.fault == "short" and regs:
            regs = regs[:-1]
        return SimpleNamespace(registers=regs, isError=lambda: self.fault == "error")

    async def write_register(self, address, value, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        self.store[address] = value
        self.writes.append((address, [value]))
        return SimpleNamespace(isError=lambda: False)

    async def write_registers(self, address, values, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        for i, value in enumerate(values):
            self.store[address + i] = value
        self.writes.append((address, list(values)))
        return SimpleNamespace(isError=lambda: False)

    def close(self):
        pass


def make_emulator(fake, **over):
    e = object.__new__(EVtecChargerEmulator)
    e.logged = []
    e.log = lambda msg="", level="INFO", **k: e.logged.append((level, msg))
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

    profile = over.get("profile", EVTEC_BIDI_PRO_10)
    profile = dataclasses.replace(
        profile, power_jitter_w=over.get("power_jitter_w", profile.power_jitter_w)
    )
    e._base_profile = profile
    e._profile = profile
    e._scenario = SCENARIOS[over.get("scenario", "normal")]
    e._soc = float(over.get("soc", 33))
    e._status_every = 0
    e._ticks_since_status = 0
    e._last_logged_state = None
    e._last_soc_shown = None
    e._init_charger_state()
    return e


def words(store, address, count):
    return [store.get(address + i, 0) for i in range(count)]


def i32(store, address):
    return dec_int32(words(store, address, 2))


def f32(store, address):
    return dec_float32(words(store, address, 2))


def command(setpoint_w: int, suspend_mode: int = 1) -> dict[int, int]:
    """The two command registers as V2G writes them (int32, big-endian)."""
    return {
        **words_at(BASE + OFF_INPUT_POWER, enc_int32(setpoint_w)),
        **words_at(BASE + OFF_SUSPEND_MODE, enc_int32(suspend_mode)),
    }


def warnings_of(e):
    return [msg for level, msg in e.logged if level == "WARNING"]


# --- encoding (sync) -------------------------------------------------------
def test_encoders_roundtrip():
    for v in (-(2**31), -MAX, -1, 0, 1, MAX, 2**31 - 1):
        assert dec_int32(enc_int32(v)) == v
    for v in (0.0, -9200.0, 400.0, 3.5):
        assert dec_float32(enc_float32(v)) == v
    for v in (0, 1, 1 << 39, (1 << 40) - 1):
        assert dec_int64(enc_int64(v)) == v
    assert dec_string(enc_string("cremacharge", 10)) == "cremacharge"
    assert dec_string(enc_string("", 10)) == ""
    assert len(enc_string("x" * 30, 10)) == 10  # truncated to the register count
    # big-endian word order, as MBR.encode() produces and the driver decodes
    assert enc_int32(-1) == [0xFFFF, 0xFFFF]
    assert enc_int32(65536) == [1, 0]


# --- phase derivation (sync) -----------------------------------------------
def test_derive_phase_follows_setpoint_and_clamps_to_windows():
    e = make_emulator(FakeModbusClient())
    assert e._derive_phase(4000, False) == (PHASE_CHARGING, 4000)
    assert e._derive_phase(-4000, False) == (PHASE_DISCHARGING, -4000)
    assert e._derive_phase(0, False) == (PHASE_IDLE, 0)
    assert e._derive_phase(4000, True) == (PHASE_IDLE, 0)
    # requested beyond the accept windows [X+38, X+30] / [X+22, 0] is clamped
    assert e._derive_phase(99999, False) == (PHASE_CHARGING, MAX)
    assert e._derive_phase(-99999, False) == (PHASE_DISCHARGING, -MAX)


def test_derive_phase_tapers_at_soc_limits():
    full = make_emulator(FakeModbusClient(), soc=EVTEC_BIDI_PRO_10.hw_soc_ceiling_pct)
    assert full._derive_phase(5000, False) == (PHASE_FULL, 0)
    empty = make_emulator(FakeModbusClient(), soc=EVTEC_BIDI_PRO_10.hw_soc_floor_pct)
    assert empty._derive_phase(-5000, False) == (PHASE_EMPTY, 0)


def test_discharge_clamped_to_offered_window():
    profile = dataclasses.replace(EVTEC_BIDI_PRO_10, discharge_floor_w=-3000.0)
    e = make_emulator(FakeModbusClient(), profile=profile)
    assert e._derive_phase(-8000, False) == (PHASE_DISCHARGING, -3000)
    assert e._derive_phase(-1000, False) == (PHASE_DISCHARGING, -1000)


# --- interlocks (async: scenario activation seeds the mock) ----------------
@pytest.mark.asyncio
async def test_interlock_session_type_refuses_discharge():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("session_not_bidirectional")
    assert i32(fake.store, BASE + OFF_SESSION_TYPE) == SESSION_DEFAULT
    assert e._derive_phase(-3000, False) == (PHASE_IDLE, 0)
    # the warning is logged once per violation, not on every tick
    e._derive_phase(-3000, False)
    e._derive_phase(-3000, False)
    assert len(warnings_of(e)) == 1
    assert "X+04" in warnings_of(e)[0]
    # charging is still fine and clears the violation; a new one logs again
    assert e._derive_phase(3000, False) == (PHASE_CHARGING, 3000)
    assert e._derive_phase(-3000, False) == (PHASE_IDLE, 0)
    assert len(warnings_of(e)) == 2


@pytest.mark.asyncio
async def test_interlock_v2g_not_offered_refuses_discharge():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("v2g_not_offered")
    assert f32(fake.store, BASE + OFF_LOWER_LIMIT_POTENTIAL) == 0.0  # X+22 >= 0
    assert e._derive_phase(-3000, False) == (PHASE_IDLE, 0)
    assert e._derive_phase(3000, False) == (PHASE_CHARGING, 3000)
    assert any("X+22" in w for w in warnings_of(e))


# --- mirror tick (async) ---------------------------------------------------
@pytest.mark.asyncio
async def test_mirror_tick_charging_follows_setpoint():
    fake = FakeModbusClient(command(6000))
    e = make_emulator(fake, power_jitter_w=0)
    for _ in range(40):
        await e._tick()
    s = fake.store
    assert i32(s, BASE + OFF_CONNECTOR_STATE) == STATE_CURRENT_DEMAND
    assert i32(s, BASE + OFF_SESSION_STATE) == SESSION_STATE_CURRENT_DEMAND
    power = f32(s, BASE + OFF_POWER)
    assert abs(power - round(0.92 * 6000)) <= 5  # settled at ~92%
    assert f32(s, BASE + OFF_PRESENT_CONSUMPTION) == power
    assert i32(s, BASE + OFF_SOC) == round(e._soc * 10)  # per-mille
    assert dec_string(words(s, BASE + OFF_CAR_ID, 10)) == EVTEC_BIDI_PRO_10.car_id
    assert i32(s, CP_STATE) == CP_STATE_IN_USE
    # command registers must be left untouched by the emulator
    assert i32(s, BASE + OFF_INPUT_POWER) == 6000
    assert i32(s, BASE + OFF_SUSPEND_MODE) == 1


@pytest.mark.asyncio
async def test_mirror_tick_discharging_counts_energy():
    fake = FakeModbusClient(command(-6000))
    e = make_emulator(fake, power_jitter_w=0, soc_speedup=100)
    for _ in range(40):
        await e._tick()
    s = fake.store
    assert i32(s, BASE + OFF_CONNECTOR_STATE) == STATE_GRID_FEED
    assert i32(s, BASE + OFF_SESSION_STATE) == SESSION_STATE_GRID_FEED_IN
    assert abs(f32(s, BASE + OFF_POWER) + round(0.92 * 6000)) <= 5
    assert f32(s, BASE + OFF_DISCHARGED_ENERGY) > 0
    assert f32(s, BASE + OFF_CHARGED_ENERGY) == 0
    assert e._soc < 33


@pytest.mark.asyncio
async def test_suspend_mode_is_ignored_like_on_hardware():
    # X+88 = 0 (suspend off) with a non-zero setpoint still delivers power.
    fake = FakeModbusClient(command(4000, suspend_mode=0))
    e = make_emulator(fake, power_jitter_w=0)
    for _ in range(10):
        await e._tick()
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_CURRENT_DEMAND
    assert f32(fake.store, BASE + OFF_POWER) > 0


@pytest.mark.asyncio
async def test_disconnected_tick_reports_no_car():
    fake = FakeModbusClient(command(6000))
    e = make_emulator(fake, car_connected=False)
    await e._tick()
    s = fake.store
    assert i32(s, BASE + OFF_CONNECTOR_STATE) == STATE_READY
    assert i32(s, BASE + OFF_SESSION_STATE) == SESSION_STATE_READY
    assert f32(s, BASE + OFF_POWER) == 0
    assert i32(s, BASE + OFF_SOC) == 0
    assert words(s, BASE + OFF_CAR_ID, 10) == [0] * 10  # empty car id
    assert i32(s, CP_STATE) == CP_STATE_READY


# --- disjoint write guard (async) ------------------------------------------
@pytest.mark.asyncio
async def test_write_guard_rejects_command_registers():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    forbidden = (
        BASE + OFF_INPUT_POWER,
        BASE + OFF_INPUT_POWER + 1,
        BASE + OFF_SUSPEND_MODE,
        BASE + OFF_SUSPEND_MODE + 1,
        3 * 100 + OFF_INPUT_POWER,  # any connector's command registers
        CP_FALLBACK_POWER,
        CP_COMMUNICATION_TIMEOUT,
        CP_COMMUNICATION_TIMEOUT + 1,
    )
    for address in forbidden:
        with pytest.raises(ValueError):
            await e._write(address, 1)
    # a batch with one forbidden address is refused before anything is written
    with pytest.raises(ValueError):
        await e._write_many({BASE + OFF_POWER: 0, BASE + OFF_INPUT_POWER: 0})
    assert fake.writes == []
    # report registers are allowed
    await e._write(BASE + OFF_POWER, 0)


@pytest.mark.asyncio
async def test_write_many_groups_contiguous_runs_into_fc16():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._write_many({BASE + 3: 3, BASE + 1: 1, BASE + 0: 0, BASE + 10: 10})
    assert fake.writes == [(BASE + 0, [0, 1]), (BASE + 3, [3]), (BASE + 10, [10])]


# --- scenarios (async) -----------------------------------------------------
@pytest.mark.asyncio
async def test_seed_identity_windows_and_no_error():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("normal")
    s = fake.store
    assert "crema" in dec_string(words(s, CP_MODEL, 10))
    assert i32(s, CP_NUM_CONNECTORS) == 1
    assert f32(s, BASE + OFF_UPPER_LIMIT) == MAX
    assert f32(s, BASE + OFF_LOWER_LIMIT) == 0.0
    assert f32(s, BASE + OFF_LOWER_LIMIT_POTENTIAL) == -MAX  # < 0: V2G offered
    assert f32(s, BASE + OFF_BATTERY_CAPACITY) == 58000.0
    assert dec_int64(words(s, BASE + OFF_ERROR, 4)) == 0
    assert i32(s, BASE + OFF_SOC) == 330


@pytest.mark.asyncio
async def test_seed_reports_no_car_when_disconnected():
    fake = FakeModbusClient()
    e = make_emulator(fake, car_connected=False)
    await e._apply_scenario("normal")
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_READY
    assert words(fake.store, BASE + OFF_CAR_ID, 10) == [0] * 10


@pytest.mark.asyncio
async def test_scenario_not_recognised():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("not_recognised")
    assert dec_string(words(fake.store, CP_MODEL, 10)) == "acme-evse"


@pytest.mark.asyncio
async def test_scenario_booting_connector_is_frozen():
    fake = FakeModbusClient(command(5000))
    e = make_emulator(fake)
    await e._apply_scenario("booting_connector")
    s = fake.store
    assert i32(s, BASE + OFF_CONNECTOR_STATE) == STATE_BOOT
    # offsets 2/4 are non-zero: discovery on 0/2/4 must still find the slot
    assert i32(s, BASE + OFF_SESSION_STATE) != 0
    assert i32(s, BASE + OFF_SESSION_TYPE) != 0
    await e._tick()
    assert i32(s, BASE + OFF_CONNECTOR_STATE) == STATE_BOOT


@pytest.mark.asyncio
async def test_scenario_error_state_is_frozen():
    fake = FakeModbusClient(command(5000))
    e = make_emulator(fake)
    await e._apply_scenario("error_state")
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_ERROR
    assert f32(fake.store, BASE + OFF_POWER) == 0
    await e._tick()
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_ERROR


@pytest.mark.asyncio
async def test_scenario_internal_error():
    fake = FakeModbusClient()
    e = make_emulator(fake)
    await e._apply_scenario("internal_error")
    assert dec_int64(words(fake.store, BASE + OFF_ERROR, 4)) == 1
    assert f32(fake.store, BASE + OFF_POWER) == 0


# --- resume SoC (async) ----------------------------------------------------
@pytest.mark.asyncio
async def test_resume_soc_from_mock_adopts_valid_permille():
    fake = FakeModbusClient(words_at(BASE + OFF_SOC, enc_int32(500)))
    e = make_emulator(fake, soc=33)
    await e._resume_soc_from_mock()
    assert e._soc == 50.0


@pytest.mark.asyncio
async def test_resume_soc_ignores_invalid_permille():
    for raw in (0, 5, 990):  # idle/unavailable, below 2 %, above 97 %
        fake = FakeModbusClient(words_at(BASE + OFF_SOC, enc_int32(raw)))
        e = make_emulator(fake, soc=33)
        await e._resume_soc_from_mock()
        assert e._soc == 33.0


# --- configurable connector -----------------------------------------------
@pytest.mark.asyncio
async def test_connector_is_configurable():
    profile = dataclasses.replace(EVTEC_BIDI_PRO_10, connector=3)
    fake = FakeModbusClient(words_at(300 + OFF_INPUT_POWER, enc_int32(4000)))
    e = make_emulator(fake, profile=profile, power_jitter_w=0)
    await e._apply_scenario("error_state")  # connector-relative override relocates
    assert i32(fake.store, 300 + OFF_CONNECTOR_STATE) == STATE_ERROR
    await e._apply_scenario("normal")
    for _ in range(5):
        await e._tick()
    assert i32(fake.store, 300 + OFF_CONNECTOR_STATE) == STATE_CURRENT_DEMAND
    assert BASE + OFF_CONNECTOR_STATE not in fake.store


# --- accept window: advertised == enforced ---------------------------------
@pytest.mark.asyncio
async def test_advertised_window_matches_what_is_delivered():
    """X+22 must never offer more than the emulator will actually deliver:
    a real station does not advertise a window it cannot honour."""
    profile = dataclasses.replace(
        EVTEC_BIDI_PRO_10, hw_max_discharge_power_w=3000, discharge_floor_w=-10000.0
    )
    fake = FakeModbusClient()
    e = make_emulator(fake, profile=profile)
    await e._apply_scenario("normal")
    advertised = f32(fake.store, BASE + OFF_LOWER_LIMIT_POTENTIAL)
    assert advertised == -3000.0
    phase, delivered = e._derive_phase(-9000, False)
    assert (phase, delivered) == (PHASE_DISCHARGING, -3000)
    assert delivered == advertised


def test_clamped_setpoint_is_logged_once():
    e = make_emulator(FakeModbusClient())
    # within the window: no warning
    e._derive_phase(4000, False)
    assert warnings_of(e) == []
    # beyond it: clamped, and said out loud once
    assert e._derive_phase(99999, False) == (PHASE_CHARGING, MAX)
    e._derive_phase(99999, False)
    assert len(warnings_of(e)) == 1
    assert "would reject it" in warnings_of(e)[0]


@pytest.mark.asyncio
async def test_suspend_mode_change_is_logged_once():
    """X+88 is a no-op here; say so when it changes, not on every tick."""
    fake = FakeModbusClient(command(0, suspend_mode=1))
    e = make_emulator(fake)
    for _ in range(3):
        await e._read_command()
    assert len([m for _, m in e.logged if "suspend mode" in m]) == 1
    fake.store.update(command(0, suspend_mode=0))
    await e._read_command()
    assert len([m for _, m in e.logged if "suspend mode" in m]) == 2


# --- SoC bounds through a tick ---------------------------------------------
@pytest.mark.asyncio
async def test_tick_at_soc_ceiling_reports_charging_end():
    fake = FakeModbusClient(command(6000))
    e = make_emulator(fake, soc=EVTEC_BIDI_PRO_10.hw_soc_ceiling_pct)
    await e._tick()
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_CHARGING_END
    assert f32(fake.store, BASE + OFF_POWER) == 0
    assert "chargingEnd" in e._phase_name(PHASE_FULL)


@pytest.mark.asyncio
async def test_tick_at_soc_floor_stops_discharging():
    fake = FakeModbusClient(command(-6000))
    e = make_emulator(fake, soc=EVTEC_BIDI_PRO_10.hw_soc_floor_pct)
    await e._tick()
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_WAIT_FOR_GRID
    assert f32(fake.store, BASE + OFF_POWER) == 0


# --- failure paths ---------------------------------------------------------
@pytest.mark.asyncio
async def test_tick_skips_silently_on_read_failure():
    fake = FakeModbusClient(command(6000))
    e = make_emulator(fake)
    await e._apply_scenario("normal")
    before = dict(fake.store)
    for fault in ("raise", "error", "short"):
        fake.fault = fault
        await e._tick()  # must not raise
    fake.fault = None
    assert fake.store == before  # nothing written from a failed read


@pytest.mark.asyncio
async def test_scenario_seed_survives_a_write_failure():
    """Activation runs before the tick loop starts, so a failing write must not
    escape: it would leave the app dead instead of retrying next tick."""
    fake = FakeModbusClient()
    fake.fault = "raise"
    e = make_emulator(fake)
    await e._apply_scenario("normal")  # must not raise
    assert any("could not seed scenario" in m for _, m in e.logged)


@pytest.mark.asyncio
async def test_frozen_scenario_is_not_overwritten_by_a_concurrent_tick():
    """A tick that already passed the mirror check must not write report
    registers on top of a freshly seeded frozen scenario."""
    fake = FakeModbusClient(command(6000))
    e = make_emulator(fake)
    await e._apply_scenario("normal")
    tick = asyncio.create_task(e._tick())
    await e._apply_scenario("error_state")
    await tick
    assert i32(fake.store, BASE + OFF_CONNECTOR_STATE) == STATE_ERROR


# --- the static mock must stay in step with the emulator -------------------
def test_static_mock_json_matches_the_normal_seed():
    """charger-mocks/configs/evtec_bidipro_33pct.json is generated from this
    seed; if they drift, the mock boots into a state the emulator never writes."""
    e = make_emulator(FakeModbusClient(), soc=33)
    seed = {str(a): v for a, v in e._seed_registers().items()}
    with open("/workspaces/charger-mocks/configs/evtec_bidipro_33pct.json") as f:
        doc = json.load(f)
    assert doc["registers"]["holdingRegister"] == seed
