"""The fuse guard keeps the charger within the capacity of its phase(s),
and only in the direction that causes an overload.

The proxy load balancer it replaces watched one summed sensor without a
sign and clamped charging on any overload, including the export overload a
discharging car causes on a sunny day, where clamping the charge limit does
nothing. These tests pin the sign matrix, the clip at command time against
the instantaneous headroom, the slow restore with hysteresis and cooldown,
the stale-meter fallback and the multi-phase arithmetic. The clock is
injected, so nothing here waits.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.grid_connection import fuse_guard as fg
from apps.v2g_liberty.grid_connection.fuse_guard import FuseGuard

T0 = datetime(2026, 11, 19, 12, 0, 0, tzinfo=timezone(timedelta(hours=1)))
CAP = 25 * 230  # 5750 W per phase
MAX = 7400
BLIND = int(CAP * fg.BLIND_SHARE)  # 4025 W: a 1-phase charger without a meter


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def tick(self, seconds: float):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def one_phase(monkeypatch):
    """A 1-phase grid with a Quasar: the common case."""
    monkeypatch.setattr(c, "GRID_PHASES", 1, raising=False)
    monkeypatch.setattr(c, "GRID_CAPACITY_PER_PHASE", 25, raising=False)
    monkeypatch.setattr(
        c, "GRID_CONSUMPTION_ENTITIES", ["sensor.cons_l1"], raising=False
    )
    monkeypatch.setattr(
        c, "GRID_PRODUCTION_ENTITIES", ["sensor.prod_l1"], raising=False
    )
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", None, raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", MAX, raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_DISCHARGE_POWER", MAX, raising=False)


@pytest.fixture
def three_phase(monkeypatch):
    """A 3-phase grid with a 1-phase charger on L2."""
    monkeypatch.setattr(c, "GRID_PHASES", 3, raising=False)
    monkeypatch.setattr(c, "GRID_CAPACITY_PER_PHASE", 25, raising=False)
    monkeypatch.setattr(
        c, "GRID_CONSUMPTION_ENTITIES", [f"sensor.cons_l{p}" for p in (1, 2, 3)]
    )
    monkeypatch.setattr(
        c, "GRID_PRODUCTION_ENTITIES", [f"sensor.prod_l{p}" for p in (1, 2, 3)]
    )
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [2], raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", MAX, raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_DISCHARGE_POWER", MAX, raising=False)


def _make_guard(clock, get_evse_client=None):
    hass = AsyncMock()
    hass.set_state = AsyncMock()
    hass.run_every = AsyncMock(return_value="tick-handle")
    bus = MagicMock()
    return FuseGuard(hass, bus, now=clock, get_evse_client=get_evse_client)


@pytest.fixture
def guard(clock):
    """A guard whose driver knows no minimum power: limits land anywhere."""
    return _make_guard(clock)


QUASAR_MIN_W = 1380  # 6 A, the Quasar's smallest setting in either direction


@pytest.fixture
def quasar_guard(clock):
    """A guard with a Quasar behind it: nothing between 0 W and 6 A."""
    driver = SimpleNamespace(
        min_charge_power_w=QUASAR_MIN_W, min_discharge_power_w=QUASAR_MIN_W
    )
    return _make_guard(clock, get_evse_client=lambda: driver)


def _events(guard, name="fuse_guard_limit_change"):
    return [
        call.kwargs
        for call in guard.event_bus.emit_event.call_args_list
        if call.args and call.args[0] == name
    ]


def _sensor(guard):
    """The last state and attributes written to the limit sensor."""
    for call in reversed(guard.hass.set_state.await_args_list):
        if call.args and call.args[0] == fg.SENSOR_FUSE_GUARD_LIMIT:
            return call.kwargs["state"], call.kwargs["attributes"]
    return None, None


async def _sample(guard, clock, net_w, phase=1, advance=1.0):
    await guard._on_grid_sample(phase=phase, net_power_w=net_w)
    clock.tick(advance)


# ── Active or not ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_without_grid_sensors_everything_passes(guard, one_phase, monkeypatch):
    monkeypatch.setattr(c, "GRID_CONSUMPTION_ENTITIES", [], raising=False)
    await guard.initialize()

    assert not guard.is_active()
    assert (await guard.clip(MAX)).allowed_w == MAX
    assert _sensor(guard)[0] == "unavailable"


@pytest.mark.asyncio
async def test_without_a_charger_phase_on_three_phases_everything_passes(
    guard, three_phase, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", None, raising=False)

    assert not guard.is_active()
    assert (await guard.clip(MAX)).allowed_w == MAX


def test_a_one_phase_grid_needs_no_charger_phase(one_phase):
    assert FuseGuard.charger_phases() == [1]


def test_charger_phases_accept_a_bare_number_and_reject_junk(three_phase, monkeypatch):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", 3, raising=False)
    assert FuseGuard.charger_phases() == [3]
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [3, 1, 1], raising=False)
    assert FuseGuard.charger_phases() == [1, 3]
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [True, 4], raising=False)
    assert FuseGuard.charger_phases() is None


@pytest.mark.asyncio
async def test_before_the_first_sample_the_blind_bound_applies(guard, one_phase):
    """No sample yet is a stale meter: the guard cannot see the household, so
    the charger gets BLIND_SHARE of its phase's capacity, never nothing and
    never the full maximum (7400 W is 32 A on a 25 A phase)."""
    result = await guard.clip(MAX + 1000)

    assert result.allowed_w == BLIND
    assert result.phase is None
    assert guard.meter_is_stale
    assert guard.charge_limit_w == BLIND


@pytest.mark.asyncio
async def test_the_blind_bound_never_exceeds_the_configured_maximum(
    guard, three_phase, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [1, 2, 3], raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 11000, raising=False)

    assert (await guard.clip(11000)).allowed_w == 11000  # 70 % of 17250 > 11000


# ── The sign matrix ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_import_overload_while_charging_lowers_only_the_charge_limit(
    guard, clock, one_phase
):
    guard._on_charge_power_change(5000)
    await _sample(guard, clock, net_w=6000)  # other load 1000 W

    assert guard.charge_limit_w == CAP - 1000
    assert guard.discharge_limit_w == MAX
    (event,) = _events(guard)
    assert event["charge_limit_w"] == CAP - 1000
    assert event["discharge_limit_w"] == MAX
    assert event["phase"] == 1
    assert "import overload" in event["reason"]


@pytest.mark.asyncio
async def test_import_overload_while_idle_blocks_charging_not_discharging(
    guard, clock, one_phase
):
    guard._on_charge_power_change(0)
    await _sample(guard, clock, net_w=6000)

    assert guard.charge_limit_w == 0
    assert guard.discharge_limit_w == MAX


@pytest.mark.asyncio
async def test_export_overload_while_discharging_lowers_only_the_discharge_limit(
    guard, clock, one_phase
):
    guard._on_charge_power_change(-5000)
    await _sample(guard, clock, net_w=-6000)  # PV feeds in 1000 W besides the car

    assert guard.discharge_limit_w == CAP - 1000
    assert guard.charge_limit_w == MAX
    assert "export overload" in _events(guard)[0]["reason"]


@pytest.mark.asyncio
async def test_export_overload_while_charging_leaves_charging_free(
    guard, clock, one_phase
):
    """The old balancer clamped charging here, which made the export worse.
    Charging stays free; discharging, which would add to the export, is
    bounded by what the phase has left."""
    guard._on_charge_power_change(3000)
    await _sample(guard, clock, net_w=-6000)  # PV makes 9000 W on this phase

    assert guard.charge_limit_w == MAX
    assert guard.discharge_limit_w == 0
    assert "export overload" in _events(guard)[0]["reason"]


@pytest.mark.asyncio
async def test_import_overload_while_discharging_leaves_discharging_free(
    guard, clock, one_phase
):
    guard._on_charge_power_change(-2000)
    await _sample(guard, clock, net_w=6000)

    assert guard.discharge_limit_w == MAX
    assert guard.charge_limit_w == 0


# ── Clip at command time ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_request_is_clipped_to_the_headroom_before_any_overload(
    guard, clock, one_phase
):
    """The proxy let the charger overload the phase and then cut it back.
    Here the request never gets there."""
    await _sample(guard, clock, net_w=3000)  # household 3000 W, car idle
    guard.event_bus.emit_event.reset_mock()  # the first sample says "meter back"

    result = await guard.clip(MAX, source="schedule")

    assert result.is_clipped
    assert result.allowed_w == CAP - 3000
    assert result.phase == 1
    assert guard.charge_limit_w == CAP - 3000
    assert _events(guard) == []  # the caller has the answer already
    assert _sensor(guard)[0] == CAP - 3000


@pytest.mark.asyncio
async def test_a_discharge_request_is_clipped_against_the_export_capacity(
    guard, clock, one_phase
):
    await _sample(guard, clock, net_w=-2000)  # PV exports 2000 W, car idle

    result = await guard.clip(-MAX)

    assert result.allowed_w == -(CAP - 2000)


@pytest.mark.asyncio
async def test_a_request_that_fits_passes_unchanged(guard, clock, one_phase):
    await _sample(guard, clock, net_w=1000)

    result = await guard.clip(4000)

    assert not result.is_clipped
    assert result.allowed_w == 4000
    assert guard.charge_limit_w == MAX


@pytest.mark.asyncio
async def test_zero_is_never_clipped(guard, clock, one_phase):
    await _sample(guard, clock, net_w=9000)

    assert (await guard.clip(0)).allowed_w == 0


@pytest.mark.asyncio
async def test_the_commanded_power_counts_until_the_driver_catches_up(
    guard, clock, one_phase
):
    """Right after a command the meter shows the new power while the driver
    still reports the old one. Taking the measured value then would turn the
    charger's own ramp-up into household load."""
    await _sample(guard, clock, net_w=1000)
    await guard.clip(4000)  # commanded 4000, measured still 0
    clock.tick(2)
    await _sample(guard, clock, net_w=5000)  # meter: 1000 household + 4000 car

    assert guard._headroom(clock()).charge_w == CAP - 1000

    clock.tick(fg.SETTLE_S)  # past the settle window, measured still 0
    assert guard._headroom(clock()).charge_w == CAP - 5000


@pytest.mark.asyncio
async def test_a_clip_does_not_turn_the_old_charger_power_into_household_load(
    guard, clock, one_phase
):
    """Seen in the emulator: the sample that follows a clip still carries
    the old charger power, the meter being slower than the command. Counting
    only the new, lower command as the charger's share made the rest look
    like household load and clipped a second time, to 0 W."""
    guard._on_charge_power_change(7000)
    await _sample(guard, clock, net_w=7300, advance=0.02)  # 300 household
    assert guard.charge_limit_w == CAP - 300

    # The same meter reading arrives again 20 ms later (production entity).
    await _sample(guard, clock, net_w=7300, advance=0.02)

    assert guard.charge_limit_w == CAP - 300  # not 0
    assert len(_events(guard)) == 1


@pytest.mark.asyncio
async def test_a_discharge_clip_keeps_the_old_export_as_the_chargers(
    guard, clock, one_phase
):
    guard._on_charge_power_change(-7000)
    await _sample(guard, clock, net_w=-6700, advance=0.02)  # 300 household
    assert guard.discharge_limit_w == CAP + 300

    await _sample(guard, clock, net_w=-6700, advance=0.02)

    assert guard.discharge_limit_w == CAP + 300
    assert len(_events(guard)) == 1


# ── Restore: slow, with hysteresis and cooldown ──────────────────────────


async def _lowered_to(guard, clock, limit_w):
    """Bring the guard to a lowered charge limit by an import overload."""
    other = CAP - limit_w
    guard._on_charge_power_change(limit_w + 500)
    await _sample(guard, clock, net_w=other + limit_w + 500)
    assert guard.charge_limit_w == limit_w
    guard.event_bus.emit_event.reset_mock()
    guard._on_charge_power_change(limit_w)  # the charger followed


@pytest.mark.asyncio
async def test_headroom_must_hold_for_the_delay_before_the_limit_rises(
    guard, clock, one_phase
):
    await _lowered_to(guard, clock, 4750)
    clock.tick(fg.COOLDOWN_S)  # cooldown over, so only the delay counts

    # Household drops by 500 W: headroom 5250, the limit 4750. The restore
    # stops one ampere under the headroom.
    for _ in range(fg.RESTORE_DELAY_S):
        await _sample(guard, clock, net_w=4750 + 500)
        assert guard.charge_limit_w == 4750
    await _sample(guard, clock, net_w=4750 + 500)

    assert guard.charge_limit_w == 5250 - fg.HYSTERESIS_W
    (event,) = _events(guard)
    assert event["charge_limit_w"] == 5250 - fg.HYSTERESIS_W
    assert "headroom back" in event["reason"]


@pytest.mark.asyncio
async def test_a_dip_in_the_headroom_restarts_the_delay(guard, clock, one_phase):
    await _lowered_to(guard, clock, 4750)
    clock.tick(fg.COOLDOWN_S)

    for _ in range(fg.RESTORE_DELAY_S - 2):
        await _sample(guard, clock, net_w=5250)
    await _sample(guard, clock, net_w=5750)  # back at the capacity for a second
    for _ in range(fg.RESTORE_DELAY_S - 2):
        await _sample(guard, clock, net_w=5250)

    assert guard.charge_limit_w == 4750


@pytest.mark.asyncio
async def test_no_restore_within_the_hysteresis(guard, clock, one_phase):
    await _lowered_to(guard, clock, 4750)
    clock.tick(fg.COOLDOWN_S)

    for _ in range(3 * fg.RESTORE_DELAY_S):
        # Car 4750 + household 800: headroom 4950, only 200 W above the
        # limit, so inside the one-ampere margin.
        await _sample(guard, clock, net_w=4750 + 800)

    assert guard.charge_limit_w == 4750
    assert _events(guard) == []


@pytest.mark.asyncio
async def test_no_restore_during_the_cooldown(guard, clock, one_phase):
    await _lowered_to(guard, clock, 4750)

    # Nothing but the car left on the phase, but the lowering was a moment ago.
    for _ in range(fg.RESTORE_DELAY_S + 2):
        await _sample(guard, clock, net_w=4750)

    assert guard.charge_limit_w == 4750

    clock.tick(fg.COOLDOWN_S)
    await _sample(guard, clock, net_w=4750)
    assert guard.charge_limit_w == CAP - fg.HYSTERESIS_W


@pytest.mark.asyncio
async def test_a_full_restore_reaches_the_maximum_despite_the_hysteresis(
    guard, clock, one_phase
):
    """A limit just under the maximum must not stay there forever because
    the step up is smaller than the hysteresis: once the headroom clears the
    maximum by the margin, the restore goes all the way."""
    await _lowered_to(guard, clock, MAX - 400)
    clock.tick(fg.COOLDOWN_S)

    for _ in range(fg.RESTORE_DELAY_S + 1):
        await _sample(guard, clock, net_w=0)  # nothing but the car

    assert guard.charge_limit_w == MAX
    assert guard._limit[fg.CHARGE] is None


@pytest.mark.asyncio
async def test_lowering_is_immediate_and_a_repeat_is_silent(guard, clock, one_phase):
    guard._on_charge_power_change(0)
    await _sample(guard, clock, net_w=6000)
    await _sample(guard, clock, net_w=6100)
    await _sample(guard, clock, net_w=6200)

    assert guard.charge_limit_w == 0
    assert len(_events(guard)) == 1


@pytest.mark.asyncio
async def test_a_lowering_of_less_than_one_ampere_is_not_worth_a_write(
    guard, clock, one_phase
):
    """Seen in the emulator: four lowerings within 90 ms, the last one by
    3 W, each a Modbus write. Under one ampere per phase the capacity in
    whole amperes, the mains voltage and the Quasar's ampere steps make the
    difference meaningless (see STEP_A)."""
    await _lowered_to(guard, clock, 4750)

    await _sample(guard, clock, net_w=4750 + 1000 + 100)  # household +100 W
    assert guard.charge_limit_w == 4750
    assert _events(guard) == []

    await _sample(guard, clock, net_w=4750 + 1000 + 300)  # household +300 W
    assert guard.charge_limit_w == 4750 - 300
    assert len(_events(guard)) == 1


@pytest.mark.asyncio
async def test_lowering_to_zero_ignores_the_step(guard, clock, one_phase):
    await _lowered_to(guard, clock, 200)

    await _sample(guard, clock, net_w=200 + 5700)  # headroom 50 W: stays
    assert guard.charge_limit_w == 200

    await _sample(guard, clock, net_w=200 + 5800)  # headroom gone: to 0
    assert guard.charge_limit_w == 0


@pytest.mark.asyncio
async def test_the_step_is_one_ampere_on_every_charger_phase(
    guard, clock, three_phase, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [1, 2, 3], raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 11000, raising=False)
    guard._on_charge_power_change(9000)  # 3000 W on each phase
    await _sample(guard, clock, net_w=4000, phase=1)
    await _sample(guard, clock, net_w=4000, phase=2)
    await _sample(guard, clock, net_w=6000, phase=3)
    assert guard.charge_limit_w == (CAP - 3000) * 3  # 8250

    await _sample(guard, clock, net_w=6150, phase=3)  # 450 W less headroom
    assert guard.charge_limit_w == 8250  # under 3 x 230 W

    await _sample(guard, clock, net_w=6300, phase=3)  # 900 W less headroom
    assert guard.charge_limit_w == (CAP - 3300) * 3


# ── The charger's dead zone ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_clip_that_lands_in_the_dead_zone_goes_to_zero(
    quasar_guard, clock, one_phase
):
    """Headroom 600 W on a charger that does nothing under 1380 W: written
    as 600 W the Quasar would round up to 6 A, the phase would stay over
    capacity and the guard, whose headroom then equals its own limit,
    would never lower again. Stopping is the honest answer."""
    await _sample(quasar_guard, clock, net_w=CAP - 600)  # household, car idle

    result = await quasar_guard.clip(MAX, source="schedule")

    assert result.allowed_w == 0
    assert result.phase == 1
    assert quasar_guard.charge_limit_w == 0
    assert _sensor(quasar_guard)[0] == 0


@pytest.mark.asyncio
async def test_a_discharge_clip_in_the_dead_zone_goes_to_zero_too(
    quasar_guard, clock, one_phase
):
    await _sample(quasar_guard, clock, net_w=-(CAP - 600))  # PV exports

    result = await quasar_guard.clip(-MAX)

    assert result.allowed_w == 0
    assert quasar_guard.discharge_limit_w == 0


@pytest.mark.asyncio
async def test_an_overload_lowers_through_the_dead_zone_to_zero(
    quasar_guard, clock, one_phase
):
    quasar_guard._on_charge_power_change(QUASAR_MIN_W)  # charging at 6 A
    await _sample(quasar_guard, clock, net_w=CAP + 780)  # household 5150 W

    assert quasar_guard.charge_limit_w == 0
    (event,) = _events(quasar_guard)
    assert event["charge_limit_w"] == 0


@pytest.mark.asyncio
async def test_without_a_known_minimum_the_limit_lands_on_the_headroom(
    guard, clock, one_phase
):
    """No driver, no minimum: the same overload leaves a 600 W limit."""
    guard._on_charge_power_change(QUASAR_MIN_W)
    await _sample(guard, clock, net_w=CAP + 780)

    assert guard.charge_limit_w == 600


@pytest.mark.asyncio
async def test_a_restore_does_not_stop_in_the_dead_zone(quasar_guard, clock, one_phase):
    quasar_guard._on_charge_power_change(QUASAR_MIN_W)
    await _sample(quasar_guard, clock, net_w=CAP + 780)
    assert quasar_guard.charge_limit_w == 0
    quasar_guard._on_charge_power_change(0)  # the car stopped
    clock.tick(fg.COOLDOWN_S)
    quasar_guard.event_bus.emit_event.reset_mock()

    # Household 4300 W: headroom 1450 W, one ampere under it is 1220 W,
    # under 6 A. The limit stays at 0 W rather than land there.
    for _ in range(fg.RESTORE_DELAY_S + 2):
        await _sample(quasar_guard, clock, net_w=4300)
    assert quasar_guard.charge_limit_w == 0
    assert _events(quasar_guard) == []

    # Household 4000 W: headroom 1750 W, so 1520 W is a setting the Quasar
    # can follow.
    for _ in range(fg.RESTORE_DELAY_S + 1):
        await _sample(quasar_guard, clock, net_w=4000)
    assert quasar_guard.charge_limit_w == 1750 - fg.HYSTERESIS_W


@pytest.mark.asyncio
async def test_a_blind_bound_in_the_dead_zone_is_zero(
    quasar_guard, one_phase, monkeypatch
):
    """A 6 A phase: 70 % of it is 966 W, under the Quasar's 6 A."""
    monkeypatch.setattr(c, "GRID_CAPACITY_PER_PHASE", 6, raising=False)

    result = await quasar_guard.clip(MAX)

    assert result.allowed_w == 0
    assert quasar_guard.charge_limit_w == 0


# ── Stale meter ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_silent_meter_falls_back_to_the_blind_bound(guard, clock, one_phase):
    await _lowered_to(guard, clock, 2000)
    clock.tick(fg.STALE_AFTER_S + 1)

    await guard._tick()

    assert guard.meter_is_stale
    assert guard.charge_limit_w == BLIND
    (event,) = _events(guard)
    assert event["reason"] == "meter stale"
    assert event["charge_limit_w"] == BLIND
    assert _sensor(guard)[1]["meter_stale"] is True
    assert (await guard.clip(MAX)).allowed_w == BLIND
    assert (await guard.clip(-MAX)).allowed_w == -BLIND


@pytest.mark.asyncio
async def test_the_first_sample_after_a_silence_makes_the_guard_live_again(
    guard, clock, one_phase
):
    await _sample(guard, clock, net_w=1000)
    clock.tick(fg.STALE_AFTER_S + 1)
    await guard._tick()
    guard.event_bus.emit_event.reset_mock()

    await _sample(guard, clock, net_w=1000)

    assert not guard.meter_is_stale
    # The blind bound lifts, so the main app must hear it and re-apply.
    (event,) = _events(guard)
    assert event["reason"] == "meter back"
    assert event["charge_limit_w"] == MAX
    assert _sensor(guard)[1]["meter_stale"] is False


@pytest.mark.asyncio
async def test_the_stale_check_runs_on_a_timer(guard, one_phase):
    await guard.initialize()

    guard.hass.run_every.assert_awaited_once()
    args = guard.hass.run_every.await_args.args
    assert args[0] == guard._tick
    assert args[2] == fg.STALE_CHECK_EVERY_S


# ── More phases ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_overload_on_another_phase_is_not_the_chargers_problem(
    guard, clock, three_phase
):
    await _sample(guard, clock, net_w=7000, phase=1)

    assert guard.charge_limit_w == MAX
    assert [e["reason"] for e in _events(guard)] == ["meter back"]  # no limit moved

    await _sample(guard, clock, net_w=7000, phase=2)
    assert guard.charge_limit_w == 0


@pytest.mark.asyncio
async def test_a_three_phase_charger_is_bound_by_its_weakest_phase(
    guard, clock, three_phase, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [1, 2, 3], raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 11000, raising=False)
    await _sample(guard, clock, net_w=1000, phase=1)
    await _sample(guard, clock, net_w=3000, phase=2)
    await _sample(guard, clock, net_w=500, phase=3)

    result = await guard.clip(11000)

    assert result.allowed_w == (CAP - 3000) * 3
    assert result.phase == 2


@pytest.mark.asyncio
async def test_a_three_phase_charger_shares_its_power_over_the_phases(
    guard, clock, three_phase, monkeypatch
):
    monkeypatch.setattr(c, "CHARGER_CONNECTED_TO_PHASE", [1, 2, 3], raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 11000, raising=False)
    guard._on_charge_power_change(9000)  # 3000 W on each phase
    await _sample(guard, clock, net_w=4000, phase=1)
    await _sample(guard, clock, net_w=4000, phase=2)
    await _sample(guard, clock, net_w=6000, phase=3)  # 3000 W household on L3

    assert guard.charge_limit_w == (CAP - 3000) * 3


# ── Settings and output ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_grid_settings_forget_old_samples_but_keep_the_limits(
    guard, clock, one_phase
):
    """Seen in the emulator: saving the grid dialog while clipped released
    the limit, and the next event sent the full 10 kW into a connection that
    had just been set to 6 A. The limits now stay until the samples say
    otherwise."""
    await _lowered_to(guard, clock, 2000)

    await guard._on_grid_settings_changed()

    assert guard.charge_limit_w == 2000
    assert guard._net_w == {}
    assert _events(guard) == []


@pytest.mark.asyncio
async def test_a_larger_capacity_restores_through_the_normal_delay(
    guard, clock, one_phase, monkeypatch
):
    await _lowered_to(guard, clock, 2000)
    monkeypatch.setattr(c, "GRID_CAPACITY_PER_PHASE", 35, raising=False)
    await guard._on_grid_settings_changed()

    for _ in range(fg.RESTORE_DELAY_S + fg.COOLDOWN_S + 2):
        await _sample(guard, clock, net_w=2000 + 300)  # 2000 car, 300 household

    assert guard.charge_limit_w == MAX
    assert _events(guard)[-1]["reason"] == "charge headroom back"


@pytest.mark.asyncio
async def test_a_guard_that_becomes_inactive_releases_its_limits_and_says_so(
    guard, clock, one_phase, monkeypatch
):
    await _lowered_to(guard, clock, 2000)
    monkeypatch.setattr(c, "GRID_CONSUMPTION_ENTITIES", [], raising=False)

    await guard._on_grid_settings_changed()

    assert guard.charge_limit_w == MAX
    assert _events(guard)[-1]["reason"] == "fuse guard inactive"
    assert _sensor(guard)[0] == "unavailable"


@pytest.mark.asyncio
async def test_the_sensor_carries_both_limits_and_the_binding_phase(
    guard, clock, one_phase
):
    guard._on_charge_power_change(5000)
    await _sample(guard, clock, net_w=6000)

    state, attributes = _sensor(guard)
    assert state == CAP - 1000
    assert attributes["discharge_limit_w"] == MAX
    assert attributes["limiting_phase"] == 1
    assert attributes["meter_stale"] is False
    assert attributes["capacity_per_phase_w"] == CAP


@pytest.mark.asyncio
async def test_the_sensor_is_only_written_on_a_change(guard, clock, one_phase):
    await _sample(guard, clock, net_w=1000)
    writes = guard.hass.set_state.await_count
    await _sample(guard, clock, net_w=1100)
    await _sample(guard, clock, net_w=1200)

    assert guard.hass.set_state.await_count == writes
