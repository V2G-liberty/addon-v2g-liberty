"""The reaction engine in shadow: on every meter sample the setpoint the
rules give, published as a sensor, band exits logged, nothing written to the
charger. The clock is injected, so nothing here waits.

Slot 1 of schedule_with_reaction.json: the plan charges 4.4 kW on a planned
surplus of 4.4 kW (reference -4.4 kW), react 1, band [1.4, 7.4] kW. The
planned meter reading is therefore 0 W; whatever the meter shows beyond the
charger's own power is the deviation.
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
        the charger's 4.4 kW minus 1.4 kW. Deviation +3 kW: charge 1.4 kW."""
        await start(engine)
        await sample(engine, PLAN_W - 1400)
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["plan_w"] == PLAN_W
        assert attributes["deviation_w"] == 3000
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
        """Slot 3: car reserved, react 0, band [[0, 0]]. A 5 kW deviation
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
        assert attributes["deviation_w"] == 0
        assert state == PLAN_W

    @pytest.mark.asyncio
    async def test_an_unavailable_charger_power_is_ignored(self, engine):
        await start(engine)
        engine._on_charge_power_change("unavailable")
        await sample(engine, PLAN_W - 1400)
        _, attributes = sensor(engine)
        assert attributes["charger_w"] == PLAN_W


class TestWrites:
    """Home Assistant records every write, so the sensor is written sparingly."""

    @pytest.mark.asyncio
    async def test_a_drifting_setpoint_is_written_once_a_minute(self, engine, clock):
        await start(engine)
        await sample(engine, 2000)  # setpoint 2400
        before = writes(engine)
        for second in range(1, 59):
            clock.tick(1)
            await sample(engine, 2000 + 10 * (second % 4))  # drifts by tens of watts
        assert writes(engine) == before
        clock.tick(2)  # a minute since the last write
        await sample(engine, 2010)
        assert writes(engine) == before + 1

    @pytest.mark.asyncio
    async def test_a_jump_is_written_after_the_minimum_gap(self, engine, clock):
        await start(engine)
        await sample(engine, 2000)  # setpoint 2400
        before = writes(engine)
        clock.tick(1)
        await sample(engine, 2100)  # setpoint 2300: a jump of 100 W, but too soon
        assert writes(engine) == before
        clock.tick(4)
        await sample(engine, 2100)
        assert writes(engine) == before + 1
        assert sensor(engine)[0] == 2300

    @pytest.mark.asyncio
    async def test_a_flag_change_is_written_at_once(self, engine, clock):
        await start(engine)
        await sample(engine, 2000)
        before = writes(engine)
        clock.tick(1)
        await sample(engine, PLAN_W - 400)  # below the band: outside_band flips
        assert writes(engine) == before + 1
        assert sensor(engine)[1]["outside_band"] is True

    @pytest.mark.asyncio
    async def test_an_unchanged_verdict_is_not_rewritten(self, engine, clock):
        """No deviation, so no drift either: nothing in the sensor changes."""
        await start(engine)
        await sample(engine, 0)
        before = writes(engine)
        clock.tick(120)
        await sample(engine, 0)
        assert writes(engine) == before

    @pytest.mark.asyncio
    async def test_a_steady_deviation_is_rewritten_per_drift_step(self, engine, clock):
        """The setpoint stays 1.4 kW under a plan of 4.4 kW, so the drift
        grows by 3 kW times the time: 0.1 kWh per two minutes. A step of
        0.1 kWh is worth a write; the steps are centred on zero, so the first
        comes past -0.05 kWh, the next past -0.15 kWh."""
        await start(engine)
        await sample(engine, 3000)
        before = writes(engine)
        clock.tick(60)
        await sample(engine, 3000)
        assert writes(engine) == before  # -0.05 kWh: still zero's step
        clock.tick(60)
        await sample(engine, 3000)
        assert writes(engine) == before + 1
        assert sensor(engine)[1]["drift_kwh"] == -0.1
        clock.tick(60)
        await sample(engine, 3000)
        assert writes(engine) == before + 1  # -0.15 kWh: the same step
        clock.tick(60)
        await sample(engine, 3000)
        assert writes(engine) == before + 2
        assert sensor(engine)[1]["drift_kwh"] == -0.2

    @pytest.mark.asyncio
    async def test_a_follow_the_plan_night_writes_nothing(self, engine, clock):
        """Slot 3 follows the plan: the setpoint and the flags stay whatever
        the house does, and the attributes alone are not worth a write."""
        clock.tick(10 * 60)
        await start(engine)
        engine._on_charge_power_change(0)
        await sample(engine, 500)
        before = writes(engine)
        for second in range(1, 240):
            clock.tick(1)
            await sample(engine, 500 + 300 * (second % 5))
        assert writes(engine) == before


class TestBandExit:
    @pytest.mark.asyncio
    async def test_an_exit_is_logged_once_after_the_dwell(self, engine, clock, caplog):
        """Deviation +4 kW asks 0.4 kW, below the band of slot 1: the band
        clips to 1.4 kW. Thirty seconds of that is one band exit."""
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
            assert "Band exit over" not in caplog.text  # not for one sample
            clock.tick(eng.EXIT_END_DWELL_S)
            await sample(engine, PLAN_W - 1400)
            assert "Band exit over after 41 s" in caplog.text
        assert sensor(engine)[1]["outside_band"] is False
        assert sensor(engine)[1]["band_exits"] == 1

    @pytest.mark.asyncio
    async def test_one_sample_inside_does_not_end_an_exit(self, engine, clock, caplog):
        """The half-updated reading between a phase's consumption and
        production updates sits inside the band for a few milliseconds.
        It neither ends the exit nor starts a fresh dwell."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(eng.DWELL_S + 1):
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            assert caplog.text.count("Band exit #1") == 1
            await sample(engine, PLAN_W - 1400)  # one reading inside
            clock.tick(1)
            for _ in range(eng.DWELL_S + 5):
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            assert "Band exit over" not in caplog.text
            assert caplog.text.count("Band exit #") == 1
            assert sensor(engine)[1]["band_exits"] == 1
            await sample(engine, PLAN_W - 1400)  # inside for good
            clock.tick(eng.EXIT_END_DWELL_S)
            await sample(engine, PLAN_W - 1400)
        assert "Band exit over after 67 s" in caplog.text

    @pytest.mark.asyncio
    async def test_at_a_ten_second_meter_one_reading_inside_is_ten_seconds_inside(
        self, engine, clock, caplog
    ):
        """A DSMR 4 meter reports every ten seconds, so one reading inside
        stands for ten seconds inside and ends the excursion; the next
        reading outside starts a new one, with its own dwell."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(4):  # 0, 10, 20, 30 s outside
                await sample(engine, PLAN_W - 400)
                clock.tick(10)
            assert caplog.text.count("Band exit #1") == 1
            await sample(engine, PLAN_W - 1400)  # 40 s: inside
            clock.tick(10)
            await sample(engine, PLAN_W - 400)  # 50 s: outside again
        # 1000 W held over four readings of ten seconds: 40 s, 11.1 Wh
        assert "Band exit over after 40 s, 11.1 Wh past the band." in caplog.text
        assert sensor(engine)[1]["band_exits"] == 1
        assert sensor(engine)[1]["outside_band"] is True

    @pytest.mark.asyncio
    async def test_the_half_updated_reading_at_a_ten_second_meter_is_a_flicker(
        self, engine, clock, caplog
    ):
        """The grid emulator, like a meter whose phase arrives as two
        entity updates, gives a reading inside the band milliseconds before
        the real one every ten seconds. Neither ends the exit nor counts
        energy, so the exit's energy is that of the readings outside."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(7):  # 0 .. 60 s, one flicker per tick
                await sample(engine, PLAN_W - 1400)  # half-updated: inside
                clock.tick(0.03)
                await sample(engine, PLAN_W - 400)  # real: 1000 W past the band
                clock.tick(9.97)
            assert caplog.text.count("Band exit #") == 1
            assert "Band exit over" not in caplog.text
            assert sensor(engine)[1]["short_excursions"] == 0
            await sample(engine, PLAN_W - 1400)  # 70 s: inside for good
            clock.tick(eng.EXIT_END_DWELL_S)
            await sample(engine, PLAN_W - 1400)
        # 1000 W held over seven spells of 9.97 s outside; the 0.03 s flickers count nothing
        expected_wh = round(1000 * 7 * 9.97 / 3600, 1)
        assert (
            f"Band exit over after 70 s, {expected_wh} Wh past the band." in caplog.text
        )

    @pytest.mark.asyncio
    async def test_a_silent_meter_ends_an_excursion_at_its_last_reading(
        self, engine, clock, caplog
    ):
        """Five seconds past the band, then no sample for ten minutes, then
        one inside: a short excursion of five seconds and 1.4 Wh, not one of
        605 s with the 1000 W held over the gap, and no exit either."""
        await start(engine)
        with caplog.at_level(logging.DEBUG):
            for _ in range(6):  # 0 .. 5 s outside
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            clock.tick(10 * 60)
            await engine._tick()  # the meter is stale now
            attributes = sensor(engine)[1]
            assert attributes["meter_stale"] is True
            assert attributes["short_excursions"] == 1
            assert "Excursion past the band of 5 s, 1.4 Wh" in caplog.text
            await sample(engine, PLAN_W - 1400)  # inside, meter back
        assert "Band exit" not in caplog.text
        assert sensor(engine)[1]["band_exits"] == 0

    @pytest.mark.asyncio
    async def test_a_silent_meter_ends_an_exit_at_its_last_reading(
        self, engine, clock, caplog
    ):
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(31):  # 0 .. 30 s outside: an exit
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            assert caplog.text.count("Band exit #1") == 1
            clock.tick(10 * 60)
            await engine._tick()
        assert "Band exit over after 30 s, 8.3 Wh past the band." in caplog.text
        assert sensor(engine)[1]["last_exit_wh"] == 8.3

    @pytest.mark.asyncio
    async def test_a_lost_charger_ends_an_exit_at_its_last_reading(
        self, engine, clock, caplog
    ):
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(31):
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            await engine._on_charger_communication_state_change(can_communicate=False)
        assert "Band exit over after 30 s, 8.3 Wh past the band." in caplog.text
        assert sensor(engine)[1]["reason"] == "charger power unknown"

    @pytest.mark.asyncio
    async def test_a_flicker_inside_does_not_split_a_short_excursion(
        self, engine, clock
    ):
        await start(engine)
        for _ in range(10):
            await sample(engine, PLAN_W - 400)
            clock.tick(1)
        await sample(engine, PLAN_W - 1400)  # one reading inside
        clock.tick(1)
        for _ in range(10):
            await sample(engine, PLAN_W - 400)
            clock.tick(1)
        await sample(engine, PLAN_W - 1400)
        clock.tick(eng.EXIT_END_DWELL_S)
        await sample(engine, PLAN_W - 1400)
        attributes = sensor(engine)[1]
        assert attributes["short_excursions"] == 1
        assert attributes["band_exits"] == 0

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
        """B6: clouds every second, the deviation jumps between 1 and 3 kW.
        The setpoint follows; nothing leaves the band."""
        await start(engine)
        for second in range(60):
            clock.tick(1)
            await sample(engine, PLAN_W - (3400 if second % 2 else 1400))
        assert sensor(engine)[1]["band_exits"] == 0
        assert sensor(engine)[0] in (1400, 3400)

    @pytest.mark.asyncio
    async def test_the_dead_zone_is_no_exit(self, engine, clock, caplog):
        """Slot 2: band [-7.4, -1.4] / [0, 0] / [1.4, 7.4] kW. A deviation of
        +3.7 kW asks 0.7 kW, in the dead zone; nearest() rounds it onto the
        band, to the plan's side, and that rounding is never a band exit."""
        clock.tick(5 * 60)
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(eng.DWELL_S + 10):
                clock.tick(1)
                await sample(engine, PLAN_W - 700)  # deviation +3700
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["outside_band"] is False
        assert attributes["band_exits"] == 0
        assert "Band exit" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_hair_beyond_the_edge_is_not_outside(self, engine, clock, caplog):
        """Float noise on a reference served in MW, or meter noise, is no
        band exit: the raw setpoint has to be more than EXIT_TOLERANCE_W
        past the edge before it counts."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(eng.DWELL_S + 5):
                clock.tick(1)
                await sample(engine, PLAN_W - 1400 + 30)  # raw 1370 W, 30 W under
            state, attributes = sensor(engine)
            assert (state, attributes["outside_band"]) == (1400, False)
            assert "Band exit" not in caplog.text
            for _ in range(eng.DWELL_S + 5):
                clock.tick(1)
                await sample(engine, PLAN_W - 1400 + 60)  # raw 1340 W, 60 W under
            assert sensor(engine)[1]["outside_band"] is True
            assert caplog.text.count("Band exit #1") == 1

    @pytest.mark.asyncio
    async def test_short_excursions_are_counted(self, engine, clock):
        """What a hysteresis at the band edge would absorb: excursions that
        end before the dwell are counted, not logged as exits."""
        await start(engine)
        for _ in range(3):
            await sample(engine, PLAN_W - 400)  # 1000 W past the band
            clock.tick(10)
            await sample(engine, PLAN_W - 1400)  # back on the edge
            clock.tick(10)
        await sample(engine, PLAN_W - 1400)  # closes the third one
        attributes = sensor(engine)[1]
        assert attributes["short_excursions"] == 3
        assert attributes["band_exits"] == 0
        assert attributes["last_exit_wh"] is None

    @pytest.mark.asyncio
    async def test_the_energy_past_the_band_is_logged_with_the_exit(
        self, engine, clock, caplog
    ):
        """1000 W past the band for 61 s is 16.9 Wh, the last reading held
        until the setpoint came inside: the number an energy threshold in
        front of the retrigger is set from."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            for _ in range(61):
                await sample(engine, PLAN_W - 400)
                clock.tick(1)
            assert "for 30 s now, 8.3 Wh past the band" in caplog.text
            await sample(engine, PLAN_W - 1400)
            clock.tick(eng.EXIT_END_DWELL_S)
            await sample(engine, PLAN_W - 1400)
        assert "Band exit over after 61 s, 16.9 Wh past the band." in caplog.text
        assert sensor(engine)[1]["last_exit_wh"] == 16.9


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
        assert attributes["deviation_w"] is None
        await sample(engine, PLAN_W - 1400)
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["meter_stale"] is False

    @pytest.mark.asyncio
    async def test_the_first_sample_is_not_a_return_of_the_meter(
        self, engine, clock, caplog
    ):
        """The engine starts without a sample, so the first one would read
        as the meter coming back; it only is after a stale spell."""
        with caplog.at_level(logging.INFO):
            await start(engine)
            await sample(engine, PLAN_W - 1400)
        assert "Meter back" not in caplog.text
        clock.tick(eng.STALE_AFTER_S + 1)
        await engine._tick()
        with caplog.at_level(logging.INFO):
            await sample(engine, PLAN_W - 1400)
        assert "Meter back" in caplog.text

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


class TestChargerPower:
    @pytest.mark.asyncio
    async def test_every_write_carries_every_key_so_nothing_stale_survives(
        self, engine
    ):
        """AppDaemon merges the attributes into the entity's, which keeps the
        name, unit, device class and icon the package gives the sensor. So
        the engine never replaces them, and writes every key of its own on
        every write, None where it does not apply: the reason of the
        unavailable spell must not sit next to a valid setpoint."""
        await engine.initialize()
        await engine.install(rules())
        await sample(engine, 3000)
        assert sensor(engine)[1]["reason"] == "charger power unknown"
        engine._on_charge_power_change(PLAN_W)
        await sample(engine, 3000)
        state, attributes = sensor(engine)
        assert state == 1400
        assert attributes["reason"] is None
        for call in engine.hass.set_state.call_args_list:
            assert "replace" not in call.kwargs
            assert set(call.kwargs["attributes"]) == set(eng.ATTRIBUTE_KEYS)

    @pytest.mark.asyncio
    async def test_no_setpoint_until_the_driver_reports_the_charger(self, engine):
        await engine.initialize()
        await engine.install(rules())
        await sample(engine, 3000)
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["reason"] == "charger power unknown"
        engine._on_charge_power_change(PLAN_W)
        await sample(engine, 3000)
        assert sensor(engine)[0] == 1400

    @pytest.mark.asyncio
    async def test_a_lost_charger_takes_the_setpoint_away(self, engine, caplog):
        """Modbus drops while charging at 4.4 kW: no event brings a new power,
        so the last one would make the house look 4.4 kW off."""
        await start(engine)
        await sample(engine, 3000)
        assert sensor(engine)[0] == 1400
        with caplog.at_level(logging.INFO):
            await engine._on_charger_communication_state_change(can_communicate=False)
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["reason"] == "charger power unknown"
        assert "Charger unreachable" in caplog.text
        await engine._on_charger_communication_state_change(can_communicate=True)
        assert sensor(engine)[0] == "unavailable"  # unknown until reported
        engine._on_charge_power_change(PLAN_W)
        await sample(engine, 3000)
        assert sensor(engine)[0] == 1400


class TestSampleEvent:
    """Every evaluated sample goes out as an event, for the dev tools'
    recorder; the engine itself writes no file."""

    @pytest.mark.asyncio
    async def test_a_sample_is_emitted_when_something_listens(self, engine):
        await start(engine)
        await sample(engine, 3000)  # deviation 3 kW: charge 1.4 kW
        engine.event_bus.emit_event.assert_called_once_with(
            "reaction_sample",
            time=NOON,
            phase=1,
            deviation_w=3000,
            raw_w=1400,
            setpoint_w=1400,
            plan_w=PLAN_W,
            meter_w=3000,
            charger_w=PLAN_W,
        )

    @pytest.mark.asyncio
    async def test_only_a_fresh_sample_with_rules_emits(self, engine, clock):
        await engine.initialize()
        engine._on_charge_power_change(PLAN_W)
        await sample(engine, 3000)  # no rules yet
        await engine.install(rules())  # a forced publish, not a sample
        assert engine.event_bus.emit_event.call_count == 0
        await sample(engine, 3000)
        clock.tick(eng.STALE_AFTER_S + 1)
        await engine._tick()  # the meter went stale
        await engine._on_charger_communication_state_change(False)
        await engine.clear()
        assert engine.event_bus.emit_event.call_count == 1

    @pytest.mark.asyncio
    async def test_nothing_is_emitted_without_a_listener(self, engine):
        """The bus warns about an event nobody hears, once per sample; a
        normal installation has no recorder."""
        engine.event_bus.listeners.return_value = []
        await start(engine)
        await sample(engine, 3000)
        engine.event_bus.emit_event.assert_not_called()


class TestDrift:
    """Absorbing inside the band moves the car away from its plan: the
    integral of (setpoint minus plan) since the schedule arrived, in kWh,
    held from one sample to the next. Slots 1 and 2 of the fixture both
    plan 4.4 kW, so the first ten minutes give a steady setpoint."""

    @pytest.mark.asyncio
    async def test_absorbing_less_sun_than_planned_is_negative_drift(
        self, engine, clock, caplog
    ):
        """Deviation +3 kW holds the setpoint at 1.4 kW under a plan of
        4.4 kW: minus 3 kW for ten minutes is minus 0.5 kWh, the threshold."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            await sample(engine, 3000)  # starts the clock
            assert sensor(engine)[1]["drift_kwh"] == 0.0
            clock.tick(600)
            await sample(engine, 3000)
        assert sensor(engine)[1]["drift_kwh"] == -0.5
        assert caplog.text.count("Drift of -0.50 kWh") == 1
        assert "less than the plan" in caplog.text
        assert "Band exit" not in caplog.text  # the two stay apart

    @pytest.mark.asyncio
    async def test_absorbing_more_sun_is_positive_drift_logged_once(
        self, engine, clock, caplog
    ):
        """Deviation -3 kW raises the setpoint to 7.4 kW: plus 3 kW."""
        await start(engine)
        with caplog.at_level(logging.INFO):
            await sample(engine, -3000)
            clock.tick(600)
            await sample(engine, -3000)
            clock.tick(60)
            await sample(engine, -3000)  # slot 3 follows its plan: no change
        assert sensor(engine)[1]["drift_kwh"] == 0.5
        assert caplog.text.count("Drift of +0.50 kWh") == 1
        assert "more than the plan" in caplog.text

    @pytest.mark.asyncio
    async def test_a_new_schedule_resets_the_drift(self, engine, clock, caplog):
        await start(engine)
        with caplog.at_level(logging.INFO):
            await sample(engine, 3000)
            clock.tick(600)
            await sample(engine, 3000)
            assert sensor(engine)[1]["drift_kwh"] == -0.5
            await engine.install(rules())
            assert sensor(engine)[1]["drift_kwh"] == 0.0
            clock.now = NOON  # the same ten minutes again, under the new rules
            await sample(engine, 3000)
            clock.tick(600)
            await sample(engine, 3000)
        assert sensor(engine)[1]["drift_kwh"] == -0.5
        assert caplog.text.count("Drift of") == 2  # logged once per schedule

    @pytest.mark.asyncio
    async def test_a_stale_spell_counts_for_nothing(self, engine, clock):
        """Without samples the engine is blind and follows the plan: the gap
        adds no drift, and the first sample after it only restarts the clock."""
        await start(engine)
        await sample(engine, 3000)
        clock.tick(eng.STALE_AFTER_S + 1)
        await engine._tick()
        assert sensor(engine)[1]["meter_stale"] is True
        clock.tick(300 - eng.STALE_AFTER_S - 1)
        await sample(engine, 3000)  # 12:05, slot 2
        assert sensor(engine)[1]["drift_kwh"] == 0.0
        clock.tick(300)
        await sample(engine, 3000)
        assert sensor(engine)[1]["drift_kwh"] == -0.25

    @pytest.mark.asyncio
    async def test_a_spell_without_the_charger_counts_for_nothing(self, engine, clock):
        """The driver lost the charger for ten minutes: no verdict, so the
        held value of the last sample must not be charged to the gap."""
        await start(engine)
        await sample(engine, 3000)
        await engine._on_charger_communication_state_change(False)
        clock.tick(600)
        await engine._on_charger_communication_state_change(True)
        engine._on_charge_power_change(PLAN_W)
        await sample(engine, 3000)
        assert sensor(engine)[1]["drift_kwh"] == 0.0

    @pytest.mark.asyncio
    async def test_the_drift_is_shown_without_a_verdict_too(self, engine, clock):
        await start(engine)
        await sample(engine, 3000)
        clock.tick(600)
        await sample(engine, 3000)
        await engine._on_charger_communication_state_change(False)
        state, attributes = sensor(engine)
        assert state == "unavailable"
        assert attributes["drift_kwh"] == -0.5
        await engine.clear()
        assert sensor(engine)[1]["drift_kwh"] == 0.0
