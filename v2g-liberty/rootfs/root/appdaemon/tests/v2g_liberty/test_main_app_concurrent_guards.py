"""The guards in main_app hold when the same method is called twice at once.

At start-up one method is called from several places within milliseconds:
the kick-off, the car connecting and the first SoC reading all call
set_next_action. A flag that is checked at the top of an async method and
only set after an `await` leaves a window in which the second call still
sees the old value and does the work again. The start of the boost was
closed earlier; this covers its end and the two notification timers, and
that a notification timer which outlived its problem says nothing.
"""

import asyncio
from datetime import timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

END_OF_BOOST = "set_next_action: end_of_boost_to_min_soc"


async def _yield_once(*_args, **_kwargs):
    """Stands in for the awaited call: gives the other task its turn, as the
    real call does, and returns a timer handle."""
    await asyncio.sleep(0)
    return "handle"


@pytest.fixture
def v2g(monkeypatch):
    monkeypatch.setattr(c, "TZ", timezone(timedelta(hours=1)), raising=False)
    monkeypatch.setattr(c, "EVENT_RESOLUTION", timedelta(minutes=5), raising=False)
    monkeypatch.setattr(c, "CAR_MIN_SOC_IN_PERCENT", 17, raising=False)
    monkeypatch.setattr(c, "CAR_MAX_SOC_IN_KWH", 50, raising=False)
    monkeypatch.setattr(c, "ALLOWED_DURATION_ABOVE_MAX_SOC", 12, raising=False)
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock(return_value="Automatic")
    app = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=AsyncMock())
    app.scheduling_timers_lock = asyncio.Lock()
    app.in_boost_to_reach_min_soc = False
    app.back_to_max_soc = None
    app.unknown_car_ev_id = None
    app.calendar_targets = []
    app.timer_handle_set_next_action = None
    app.evse_client_app = AsyncMock()
    app.evse_client_app.is_car_connected = AsyncMock(return_value=True)
    app.evse_client_app.is_discharging = AsyncMock(return_value=False)
    app.evse_client_app.try_get_new_soc_in_process = False
    app.electric_vehicle = MagicMock()
    app.electric_vehicle.soc = 50
    app.electric_vehicle.soc_kwh = 30.0
    app.fm_client_app = MagicMock()
    app.fm_client_app.get_new_schedule = AsyncMock(return_value=None)
    app.fm_client_app.pop_deferred_schedule_request = MagicMock(return_value=False)
    return app


@pytest.mark.asyncio
async def test_the_end_of_the_boost_stops_the_charger_once(v2g):
    """The SoC is back above the minimum while the boost runs. Two concurrent
    set_next_action calls must end the boost once, not twice."""
    v2g.in_boost_to_reach_min_soc = True
    v2g.electric_vehicle.soc = 20
    power = AsyncMock(side_effect=_yield_once)
    with (
        patch("apps.v2g_liberty.main_app.get_local_now"),
        patch("apps.v2g_liberty.main_app.set_oneshot_timer", AsyncMock()),
        patch.object(v2g, "_V2Gliberty__set_charge_power", power),
        patch.object(v2g, "set_records_in_chart", AsyncMock()),
    ):
        await asyncio.gather(v2g.set_next_action(), v2g.set_next_action())

    stops = [
        call for call in power.await_args_list if call.args[0]["source"] == END_OF_BOOST
    ]
    assert len(stops) == 1
    assert v2g.in_boost_to_reach_min_soc is False


@pytest.mark.asyncio
async def test_the_no_new_schedule_notification_is_planned_once(v2g):
    v2g.no_schedule_errors = {"timeouts_on_schedule": True}
    v2g.notification_timer_handle = None
    v2g.no_schedule_notification_is_planned = False
    v2g.user_was_notified_of_no_schedule = False
    notify = v2g._V2Gliberty__notify_no_new_schedule
    timer = AsyncMock(side_effect=_yield_once)
    with patch("apps.v2g_liberty.main_app.set_oneshot_timer", timer):
        await asyncio.gather(notify(), notify())

    assert timer.await_count == 1
    assert v2g.no_schedule_notification_is_planned is True
    assert v2g.notification_timer_handle == "handle"


@pytest.mark.asyncio
async def test_the_discharge_refused_notification_is_planned_once(v2g):
    v2g.discharge_refusal_timer_handle = None
    v2g.discharge_refused_reason = None
    v2g.notified_discharge_refusal = None
    refused = v2g._V2Gliberty__handle_discharge_refused
    timer = AsyncMock(side_effect=_yield_once)
    with (
        patch("apps.v2g_liberty.main_app.set_oneshot_timer", timer),
        patch.object(v2g, "set_records_in_chart", AsyncMock()),
    ):
        await asyncio.gather(
            refused("car_not_ready", False), refused("car_not_ready", False)
        )

    assert timer.await_count == 1
    assert v2g.discharge_refusal_timer_handle == "handle"


def _no_schedule_titles(v2g):
    return [call.kwargs["title"] for call in v2g.notifier.notify_user.await_args_list]


@pytest.fixture
def no_schedule(v2g):
    v2g.no_schedule_errors = {
        "invalid_schedule": False,
        "timeouts_on_schedule": False,
        "no_communication_with_fm": False,
    }
    v2g.notification_timer_handle = None
    v2g.no_schedule_notification_is_planned = False
    v2g.user_was_notified_of_no_schedule = False
    return v2g


@pytest.mark.asyncio
async def test_an_error_and_its_solution_at_once_send_nothing(no_schedule):
    """The error plans a notification; its solution arrives while the timer is
    being set. "Schedules available again" must not go out, since nothing said
    they were gone, and the timer that is set anyway must stay silent."""
    v2g = no_schedule
    report = v2g.handle_no_new_schedule
    with patch(
        "apps.v2g_liberty.main_app.set_oneshot_timer",
        AsyncMock(side_effect=_yield_once),
    ):
        await asyncio.gather(
            report("timeouts_on_schedule", True),
            report("timeouts_on_schedule", False),
        )
    # The timer that was set while the problem was being solved fires.
    await v2g._V2Gliberty__no_new_schedule_notification()

    assert _no_schedule_titles(v2g) == []


@pytest.mark.asyncio
async def test_schedules_back_is_only_said_after_saying_they_were_gone(no_schedule):
    """The normal sequence, unchanged by the fix."""
    v2g = no_schedule
    report = v2g.handle_no_new_schedule
    fired = False

    async def timer_fires():
        nonlocal fired
        fired = True
        await v2g._V2Gliberty__no_new_schedule_notification()

    # AppDaemon: a timer runs until it has fired or is cancelled.
    v2g.hass.timer_running = AsyncMock(side_effect=lambda _handle: not fired)
    with patch(
        "apps.v2g_liberty.main_app.set_oneshot_timer",
        AsyncMock(return_value="no-schedule-timer"),
    ):
        # Solved within the hour: the user was never told, so nothing to undo.
        await report("timeouts_on_schedule", True)
        await report("timeouts_on_schedule", False)
        assert _no_schedule_titles(v2g) == []

        # Not solved within the hour: told, and told again when it is solved.
        await report("timeouts_on_schedule", True)
        await timer_fires()
        await report("timeouts_on_schedule", False)

    assert _no_schedule_titles(v2g) == [
        "No new schedule available",
        "Schedules available again",
    ]
