"""The drivers time how long the charger takes to follow a setpoint.

The reaction engine will wait this long before it judges a band exit, so the
figure must come from the installation itself: per kind of transition, from
the setpoint write to the first power report within tolerance, judged from
the power the charger reported rather than the previous setpoint.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.chargers import settle_time as st
from apps.v2g_liberty.chargers.evtec_bidipro import EVtecBiDiProClient
from apps.v2g_liberty.chargers.settle_time import SettleTimeTracker, classify
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client

SENSOR = "sensor.charger_settle_time"


class TestClassify:
    @pytest.mark.parametrize(
        "actual, target, kind",
        [
            (0, 3000, "start_charging"),
            (0, -3000, "start_discharging"),
            (200, 3000, "start_charging"),  # noise around zero is zero
            (3000, 0, "stop_charging"),
            (-3000, 0, "stop_discharging"),
            (3000, 5000, "adjust_charging"),
            (5000, 3000, "adjust_charging"),
            (-3000, -5000, "adjust_discharging"),
            (3000, -3000, "reverse_to_discharging"),
            (-3000, 3000, "reverse_to_charging"),
            (0, 0, None),
            (100, 0, None),
        ],
    )
    def test_names_the_transition_from_the_reported_power(self, actual, target, kind):
        assert classify(actual, target) == kind


def _settle(tracker, at, from_w, to_w, seconds):
    """Run one transition that settles after ``seconds``; return the result."""
    tracker.note_actual(from_w, at)
    tracker.note_setpoint(to_w, at + 1)
    return tracker.note_actual(to_w, at + 1 + seconds)


class TestTracker:
    def test_nothing_is_timed_before_the_charger_reported_its_power(self):
        t = SettleTimeTracker()
        assert t.note_setpoint(3000, 0.0) is None
        assert t.note_actual(3000, 10.0) is None
        assert t.medians() == {}

    def test_a_start_is_timed_to_the_first_report_within_tolerance(self):
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        assert t.note_setpoint(3000, 10.0) == "start_charging"
        assert t.note_actual(0, 15.0) is None
        assert t.note_actual(1500, 20.0) is None
        got = t.note_actual(2800, 25.0)
        assert got == st.SettleTime("start_charging", 0, 3000, 2800, 15.0, True)
        assert t.medians() == {"start_charging": 15.0}
        assert t.counts() == {"start_charging": 1}
        assert t.slowest() == ("start_charging", 15.0)

    def test_tolerance_is_one_ampere_or_ten_percent_whichever_is_more(self):
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(6000, 1.0)
        assert t.note_actual(5350, 10.0) is None  # 650 off, more than 10 %
        assert t.note_actual(5450, 12.0).settled  # 550 off, within 10 %
        t.note_setpoint(1380, 20.0)
        assert t.note_actual(1100, 25.0) is None  # 280 off, more than 230 W
        assert t.note_actual(1160, 27.0).settled  # 220 off

    def test_a_setpoint_within_tolerance_of_the_reported_power_is_not_timed(self):
        t = SettleTimeTracker()
        t.note_actual(2950, 0.0)
        assert t.note_setpoint(3100, 1.0) is None
        assert t.note_actual(3100, 6.0) is None
        assert t.medians() == {}

    def test_a_newer_setpoint_replaces_the_pending_one(self):
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(3000, 10.0)
        assert t.note_setpoint(5000, 12.0) == "start_charging"
        assert t.note_actual(3000, 20.0) is None  # the old target does not count
        got = t.note_actual(4600, 30.0)
        assert (got.to_w, got.seconds) == (5000, 18.0)

    def test_gives_up_after_five_minutes_and_counts_nothing(self):
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(-5937, 0.0)
        assert t.note_actual(-300, 100.0) is None
        got = t.note_actual(-5000, 301.0)
        assert got == st.SettleTime("start_discharging", 0, -5937, -5000, 301.0, False)
        assert t.medians() == {}
        assert t.counts() == {}
        assert t.slowest() is None
        assert t.note_actual(-5900, 310.0) is None  # nothing pending anymore

    def test_median_per_kind_and_the_slowest_of_them(self):
        t = SettleTimeTracker()
        at = 0.0
        for seconds in (10, 30, 20):
            _settle(t, at, 0, 3000, seconds)
            at += 100
        _settle(t, at, 3000, 0, 5)
        assert t.medians() == {"start_charging": 20, "stop_charging": 5}
        assert t.counts() == {"start_charging": 3, "stop_charging": 1}
        assert t.slowest() == ("start_charging", 20)

    def test_only_the_recent_durations_count(self):
        t = SettleTimeTracker()
        at = 0.0
        for seconds in [100] * st.KEEP + [10] * st.KEEP:
            _settle(t, at, 0, 3000, seconds)
            at += 1000
        assert t.medians() == {"start_charging": 10}
        assert t.counts() == {"start_charging": st.KEEP}

    def test_settling_after_the_give_up_time_is_not_counted(self):
        # The drivers report a power only when it changes, so the first report
        # after a long refusal may already be the setpoint itself.
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(-5000, 0.0)
        got = t.note_actual(-5000, 1200.0)
        assert got == st.SettleTime("start_discharging", 0, -5000, -5000, 1200.0, False)
        assert t.medians() == {}

    def test_a_car_that_takes_less_than_asked_settles_where_it_stands_still(self):
        # A 6.6 kW on-board charger at a 7.4 kW setpoint: 11 % short, never
        # within tolerance, settled all the same.
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(7400, 1.0)
        assert t.note_actual(2000, 6.0) is None
        assert t.note_actual(4500, 11.0) is None
        assert t.note_actual(6600, 16.0) is None
        assert t.note_poll(21.0) is None  # still for 5 s
        got = t.note_poll(26.0)  # still for 10 s
        assert got == st.SettleTime("start_charging", 0, 7400, 6600, 15.0, True)
        assert t.medians() == {"start_charging": 15.0}

    def test_jitter_in_the_readings_is_standing_still(self):
        # Seen in the dev environment: the emulator rests at 8000 W +/- 50 W,
        # a different reading every poll, and was never found still.
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(10000, 1.0)
        for at, power in ((6.0, 2000), (11.0, 4500), (16.0, 8028), (21.0, 7962)):
            assert t.note_actual(power, at) is None
        got = t.note_actual(8046, 26.0)
        assert got == st.SettleTime("start_charging", 0, 10000, 8046, 15.0, True)

    def test_a_slow_ramp_is_not_standing_still(self):
        # The Quasar discharges at 35-40 W/s, 200 W per poll: a ramp, timed
        # until it is within tolerance of the setpoint.
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(-5937, 0.0)
        for i in range(1, 27):
            assert t.note_actual(-200 * i, 5.0 * i) is None
        got = t.note_actual(-5400, 135.0)  # 537 W short, within 10 %
        assert (got.settled, got.seconds) == (True, 135.0)

    def test_standing_still_where_it_started_is_not_settling(self):
        t = SettleTimeTracker()
        t.note_actual(0, 0.0)
        t.note_setpoint(-5000, 0.0)
        for at in range(5, 300, 5):
            assert t.note_poll(float(at)) is None  # the car refuses
        got = t.note_poll(305.0)
        assert got.settled is False
        assert t.medians() == {}

    def test_a_poll_without_a_pending_transition_does_nothing(self):
        t = SettleTimeTracker()
        assert t.note_poll(0.0) is None
        t.note_actual(0, 0.0)
        assert t.note_poll(5.0) is None


def _hass():
    hass = MagicMock()
    for m in ("set_state", "run_every", "run_in", "get_state", "cancel_timer"):
        setattr(hass, m, AsyncMock())
    return hass


@pytest.fixture
def quasar(monkeypatch):
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 7400)
    monkeypatch.setattr(c, "CHARGER_MAX_DISCHARGE_POWER", 7400)
    e = WallboxQuasar1Client(_hass(), MagicMock(), MagicMock())
    e._am_i_active = True
    e._modbus_write = AsyncMock(return_value=True)
    e._get_car_soc = AsyncMock(return_value=50)
    clock = [1000.0]
    e._clock = lambda: clock[0]
    return e, clock


@pytest.fixture
def evtec():
    e = EVtecBiDiProClient(_hass(), MagicMock(), MagicMock())
    e._am_i_active = True
    e._modbus_write_mbr = AsyncMock(return_value=True)
    clock = [2000.0]
    e._clock = lambda: clock[0]
    return e, clock


def _written(e):
    e.hass.set_state.assert_awaited_once()
    args, kwargs = e.hass.set_state.await_args
    assert args[0] == SENSOR
    return kwargs["state"], kwargs["attributes"]


@pytest.mark.asyncio
class TestQuasar:
    async def test_times_a_start_from_the_setpoint_write_to_the_report_near_it(
        self, quasar
    ):
        e, clock = quasar
        await e._handle_charge_power_change(0)  # the charger reports idle
        clock[0] = 1010.0
        await e._set_charge_power(3000, source="test")
        assert e.requested_charge_power == 3000
        clock[0] = 1015.0
        await e._handle_charge_power_change(1200)  # ramping
        e.hass.set_state.assert_not_awaited()
        clock[0] = 1038.0
        await e._handle_charge_power_change(2900)

        state, attributes = _written(e)
        assert state == 28
        assert attributes == {
            "medians_s": {"start_charging": 28},
            "counts": {"start_charging": 1},
            "slowest": "start_charging",
            "last_kind": "start_charging",
            "last_s": 28,
            "last_from_w": 0,
            "last_to_w": 3000,
        }

    async def test_the_clock_starts_before_the_modbus_write_not_after_it(self, quasar):
        e, clock = quasar

        async def slow_write(**_):
            clock[0] += 2.5
            return True

        e._modbus_write = slow_write
        await e._handle_charge_power_change(0)
        clock[0] = 1010.0
        await e._set_charge_power(3000, source="test")
        clock[0] = 1020.0
        await e._handle_charge_power_change(3000)
        state, _ = _written(e)
        assert state == 10

    async def test_a_repeated_setpoint_does_not_restart_the_clock(self, quasar):
        e, clock = quasar
        await e._handle_charge_power_change(0)
        clock[0] = 1010.0
        await e._set_charge_power(3000, source="test")
        clock[0] = 1020.0
        await e._set_charge_power(3000, source="again")  # same value: not written
        clock[0] = 1030.0
        await e._handle_charge_power_change(3000)
        state, _ = _written(e)
        assert state == 20

    async def test_a_report_that_is_not_a_number_settles_nothing(self, quasar):
        e, clock = quasar
        await e._handle_charge_power_change(3000)
        e.requested_charge_power = 3000
        clock[0] = 1010.0
        await e._set_charge_power(0, source="test")
        clock[0] = 1015.0
        await e._handle_charge_power_change("unavailable")  # 0 for the event only
        e.hass.set_state.assert_not_awaited()
        clock[0] = 1019.0
        await e._handle_charge_power_change(0)
        state, attributes = _written(e)
        assert (state, attributes["last_kind"]) == (9, "stop_charging")

    async def test_a_setpoint_the_car_never_reaches_is_not_counted(self, quasar):
        e, clock = quasar
        await e._handle_charge_power_change(0)
        clock[0] = 1000.0
        await e._set_charge_power(-5937, source="test")
        clock[0] = 1100.0
        await e._handle_charge_power_change(-300)
        clock[0] = 1301.0
        await e._handle_charge_power_change(-5000)
        e.hass.set_state.assert_not_awaited()
        assert e._settle.last.settled is False
        assert e._settle.medians() == {}


@pytest.mark.asyncio
class TestEVtec:
    async def test_a_stop_through_the_action_register_is_timed_too(self, evtec):
        e, clock = evtec
        await e._handle_charge_power_change(4000)
        e.requested_charge_power = 4000
        await e._set_charger_action("stop", reason="test")
        assert e.requested_charge_power == 0
        clock[0] = 2011.0
        await e._handle_charge_power_change(0)
        state, attributes = _written(e)
        assert state == 11
        assert attributes["medians_s"] == {"stop_charging": 11}


@pytest.mark.asyncio
class TestPollHook:
    """A power that stands still is not reported, so the drivers ask the
    tracker from their base poll."""

    async def test_the_quasar_poll_settles_a_car_that_takes_less(self, quasar):
        e, clock = quasar
        e._recovery_probe = MagicMock(is_armed=False)
        e._get_and_process_registers = AsyncMock()
        await e._handle_charge_power_change(0)
        clock[0] = 1001.0
        await e._set_charge_power(7400, source="test")
        for at, power in ((1006.0, 2000), (1011.0, 4500), (1016.0, 6600)):
            clock[0] = at
            await e._handle_charge_power_change(power)
        clock[0] = 1021.0
        await e._handle_charge_power_change(6640)  # jitter, still at rest
        await WallboxQuasar1Client._base_polling(e, {})
        e.hass.set_state.assert_not_awaited()
        clock[0] = 1026.0
        await WallboxQuasar1Client._base_polling(e, {})
        state, attributes = _written(e)
        assert (state, attributes["last_to_w"]) == (15, 7400)

    async def test_the_evtec_poll_gives_up_on_a_car_that_refuses(self, evtec):
        e, clock = evtec
        e._recovery_probe = MagicMock(is_armed=False)
        e._get_and_process_registers = AsyncMock()
        e._retry_car_id = AsyncMock()
        e._clear_refusal_if_resolved = MagicMock()
        await e._handle_charge_power_change(4000)
        e.requested_charge_power = 4000
        await e._set_charger_action("stop", reason="test")
        clock[0] = 2305.0
        await EVtecBiDiProClient._base_polling(e, {})
        e.hass.set_state.assert_not_awaited()
        assert e._settle.last.settled is False
        assert e._settle.medians() == {}
