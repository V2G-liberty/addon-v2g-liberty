"""The reaction engine in shadow: on every meter sample the setpoint the
rules give, published as a sensor, band exits logged, nothing written to the
charger. The clock is injected, so nothing here waits.

Slot 1 of schedule_with_reaction.json: the plan charges 4.4 kW on a planned
surplus of 4.4 kW (reference -4.4 kW), react 1, band [1.4, 7.4] kW. The
planned meter reading is therefore 0 W; whatever the meter shows beyond the
charger's own power is the surprise.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.recourse import engine as eng
from apps.v2g_liberty.recourse.engine import ReactionEngine
from apps.v2g_liberty.recourse.rules import parse_reaction

FIXTURES = Path(__file__).parent / "fixtures" / "fm"
CET = timezone(timedelta(hours=1))
NOON = datetime(2026, 12, 10, 12, 0, tzinfo=CET)  # start of the fixture
PLAN_W = 4400


class Clock:
    def __init__(self):
        self.now = NOON

    def __call__(self):
        return self.now

    def tick(self, seconds: float):
        self.now += timedelta(seconds=seconds)


def rules(name="schedule_with_reaction"):
    schedule = json.loads((FIXTURES / f"{name}.json").read_text())
    return parse_reaction(schedule, sensor_id=65)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def one_phase(monkeypatch):
    monkeypatch.setattr(c, "GRID_PHASES", 1, raising=False)


@pytest.fixture
def engine(clock, one_phase):
    hass = AsyncMock()
    hass.set_state = AsyncMock()
    hass.run_every = AsyncMock(return_value="tick-handle")
    return ReactionEngine(hass, MagicMock(), now=clock)


async def start(engine, name="schedule_with_reaction"):
    await engine.initialize()
    await engine.install(rules(name))
    engine._on_charge_power_change(PLAN_W)  # the charger follows its plan


async def sample(engine, net_w: int, phase: int = 1):
    await engine._on_grid_sample(phase, net_w)


def sensor(engine):
    kwargs = engine.hass.set_state.call_args.kwargs
    return kwargs["state"], kwargs["attributes"]


def writes(engine) -> int:
    return engine.hass.set_state.call_count


class TestSetpoint:
    @pytest.mark.asyncio
    async def test_without_rules_the_sensor_is_unavailable(self, engine):
        await engine.initialize()
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["reason"] == "no rules installed"
        assert attributes["mode"] == "shadow"

    @pytest.mark.asyncio
    async def test_a_shortfall_of_sun_lowers_the_setpoint(self, engine):
        """B1: the house exports 1.4 kW less than planned, so the meter reads
        the charger's 4.4 kW minus 1.4 kW. Surprise +3 kW: charge 1.4 kW."""
        await start(engine)
        await sample(engine, PLAN_W - 1400)
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["plan_w"] == PLAN_W
        assert attributes["surprise_w"] == 3000
        assert attributes["outside_band"] is False
        assert attributes["band_w"] == [[1400, 7400]]

    @pytest.mark.asyncio
    async def test_extra_sun_raises_it(self, engine):
        await start(engine)
        await sample(engine, PLAN_W - 6400)
        state, _ = sensor(engine)
        assert state == 6400

    @pytest.mark.asyncio
    async def test_a_follow_the_plan_slot_ignores_the_house(self, engine, clock):
        """Slot 3: car reserved, react 0, band [[0, 0]]. A 5 kW surprise
        changes nothing and is no band exit."""
        clock.tick(10 * 60)
        await start(engine)
        engine._on_charge_power_change(0)
        await sample(engine, 5000)
        state, attributes = sensor(engine)
        assert state == 0
        assert attributes["react"] == 0.0
        assert attributes["outside_band"] is False

    @pytest.mark.asyncio
    async def test_the_charger_power_is_taken_out_of_the_meter(self, engine):
        """The meter sees the charger too; only the rest is the house."""
        await start(engine)
        engine._on_charge_power_change(2000)  # the charger lags its plan
        await sample(engine, 2000 - 4400)  # the house exports as planned
        state, attributes = sensor(engine)
        assert attributes["surprise_w"] == 0
        assert state == PLAN_W

    @pytest.mark.asyncio
    async def test_an_unavailable_charger_power_is_ignored(self, engine):
        await start(engine)
        engine._on_charge_power_change("unavailable")
        await sample(engine, PLAN_W - 1400)
        _, attributes = sensor(engine)
        assert attributes["charger_w"] == PLAN_W


class TestCoalescing:
    @pytest.mark.asyncio
    async def test_small_changes_within_five_seconds_are_held(self, engine, clock):
        await start(engine)
        await sample(engine, 2000)  # setpoint 2400
        before = writes(engine)
        for step in range(1, 5):
            clock.tick(1)
            await sample(engine, 2000 + 10 * step)  # setpoint drifts by 10 W
        assert writes(engine) == before
        clock.tick(1)  # five seconds since the last write
        await sample(engine, 2040)
        assert writes(engine) == before + 1

    @pytest.mark.asyncio
    async def test_a_jump_is_published_at_once(self, engine, clock):
        await start(engine)
        await sample(engine, 2000)  # setpoint 2400
        before = writes(engine)
        clock.tick(1)
        await sample(engine, 2100)  # setpoint 2300: a jump of 100 W
        assert writes(engine) == before + 1

    @pytest.mark.asyncio
    async def test_an_unchanged_verdict_is_not_rewritten(self, engine, clock):
        await start(engine)
        await sample(engine, 3000)
        before = writes(engine)
        clock.tick(10)
        await sample(engine, 3000)
        assert writes(engine) == before


class TestBandExit:
    @pytest.mark.asyncio
    async def test_an_exit_is_logged_once_after_the_dwell(self, engine, clock, caplog):
        """Surprise +4 kW asks 0.4 kW, in the dead zone under the band: the
        band clips to 1.4 kW. Thirty seconds of that is one band exit."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            await sample(engine, PLAN_W - 400)
            state, attributes = sensor(engine)
            assert state == 1400
            assert attributes["outside_band"] is True
            assert attributes["band_exits"] == 0
            clock.tick(eng.DWELL_S - 1)
            await sample(engine, PLAN_W - 401)
            assert "Band exit" not in caplog.text
            clock.tick(2)
            await sample(engine, PLAN_W - 402)
            assert caplog.text.count("Band exit #1") == 1
            assert sensor(engine)[1]["band_exits"] == 1
            clock.tick(10)
            await sample(engine, PLAN_W - 403)
            assert caplog.text.count("Band exit #") == 1  # not logged again
            await sample(engine, PLAN_W - 1400)  # back inside
            assert "Band exit over after" in caplog.text
        assert sensor(engine)[1]["outside_band"] is False
        assert sensor(engine)[1]["band_exits"] == 1

    @pytest.mark.asyncio
    async def test_a_short_excursion_is_no_exit(self, engine, clock, caplog):
        await start(engine)
        with caplog.at_level(logging.INFO):
            await sample(engine, PLAN_W - 400)
            clock.tick(10)
            await sample(engine, PLAN_W - 1400)
        assert "Band exit" not in caplog.text
        assert sensor(engine)[1]["band_exits"] == 0

    @pytest.mark.asyncio
    async def test_flicker_inside_the_band_is_no_exit(self, engine, clock):
        """B6: clouds every second, the surprise jumps between 1 and 3 kW.
        The setpoint follows; nothing leaves the band."""
        await start(engine)
        for second in range(60):
            clock.tick(1)
            await sample(engine, PLAN_W - (3400 if second % 2 else 1400))
        assert sensor(engine)[1]["band_exits"] == 0
        assert sensor(engine)[0] in (1400, 3400)


class TestMeter:
    @pytest.mark.asyncio
    async def test_without_a_meter_the_engine_follows_the_plan(
        self, engine, clock, caplog
    ):
        await start(engine)
        await sample(engine, PLAN_W - 1400)
        assert sensor(engine)[0] == 1400
        clock.tick(eng.STALE_AFTER_S + 1)
        with caplog.at_level(logging.WARNING):
            await engine._tick()
        assert "No grid sample" in caplog.text
        state, attributes = sensor(engine)
        assert state == PLAN_W
        assert attributes["meter_stale"] is True
        await sample(engine, PLAN_W - 1400)
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["meter_stale"] is False

    @pytest.mark.asyncio
    async def test_it_waits_for_a_sample_of_every_phase(self, engine, monkeypatch):
        monkeypatch.setattr(c, "GRID_PHASES", 3, raising=False)
        await start(engine)
        await sample(engine, 1000, phase=1)
        await sample(engine, 1000, phase=2)
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["reason"] == "waiting for a sample of every phase"
        await sample(engine, 1000, phase=3)  # meter 3000 W over three phases
        state, attributes = sensor(engine)
        assert attributes["meter_w"] == 3000
        assert state == 1400


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_outside_the_schedule_there_is_no_setpoint(self, engine, clock):
        clock.tick(25 * 60)
        await start(engine)
        await sample(engine, 0)
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["reason"] == "outside the schedule"

    @pytest.mark.asyncio
    async def test_clear_makes_the_sensor_unavailable(self, engine):
        await start(engine)
        await sample(engine, PLAN_W - 1400)
        assert sensor(engine)[0] == 1400
        await engine.clear()
        assert sensor(engine)[0] == "unavailable"
        assert engine.rules is None
        before = writes(engine)
        await sample(engine, PLAN_W - 1400)
        assert writes(engine) == before

    @pytest.mark.asyncio
    async def test_new_rules_replace_the_old(self, engine):
        await start(engine)
        await engine.install(rules("schedule_night_k0"))
        assert engine.rules.rules[0].react == 0.0

    @pytest.mark.asyncio
    async def test_shadow_never_touches_the_charger(self, engine, clock):
        """The engine has no driver to call; the only things it asks of
        AppDaemon are the sensor write and its stale-check timer."""
        await start(engine)
        for second in range(10):
            clock.tick(1)
            await sample(engine, PLAN_W - 400)  # a band exit in the making
        assert not hasattr(engine, "evse_client_app")
        used = {call[0] for call in engine.hass.method_calls}
        assert used <= {"set_state", "run_every"}
