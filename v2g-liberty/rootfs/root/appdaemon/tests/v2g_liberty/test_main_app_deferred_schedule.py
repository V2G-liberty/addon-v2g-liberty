"""set_next_action asks once more when a request was refused meanwhile.

A trigger that lands while a schedule is being fetched (a reservation, a SoC
step, the car reconnecting) is refused by fm_client, so the schedule that
arrives does not know what that trigger brought. Seen on 2026-10-08: a 17:00
reservation came in 18 s into a request, and the schedule that followed
discharged the car through the reservation until the 15-minute fallback
asked again. Now the fallback is not needed: after processing, set_next_action
takes the deferral and asks again with the current data.
"""

from datetime import timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

SCHEDULE = {"values": [0.001], "duration": "PT5M", "start": "2026-02-22T12:00:00+01:00"}
RESERVATION = {
    "start": "2026-02-22T17:00:00+01:00",
    "end": "2026-02-22T22:00:00+01:00",
    "target_soc_kwh": 50.82,
}


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
    app.in_boost_to_reach_min_soc = False
    app.back_to_max_soc = None
    app.unknown_car_ev_id = None
    app.calendar_targets = []
    app.timer_handle_set_next_action = None
    app.evse_client_app = AsyncMock()
    app.evse_client_app.is_car_connected = AsyncMock(return_value=True)
    app.evse_client_app.try_get_new_soc_in_process = False
    app.electric_vehicle = MagicMock()
    app.electric_vehicle.soc = 50
    app.electric_vehicle.soc_kwh = 30.0
    app.fm_client_app = MagicMock()
    app.fm_client_app.get_new_schedule = AsyncMock(return_value=SCHEDULE)
    return app


async def _run(v2g, deferred_answers):
    answers = iter(deferred_answers)

    def pop():
        answer = next(answers)
        return answer() if callable(answer) else answer

    v2g.fm_client_app.pop_deferred_schedule_request = MagicMock(side_effect=pop)
    process = AsyncMock()
    with (
        patch("apps.v2g_liberty.main_app.set_oneshot_timer", AsyncMock()),
        patch("apps.v2g_liberty.main_app.get_local_now"),
        patch.object(v2g, "_V2Gliberty__process_schedule", process),
    ):
        await v2g.set_next_action()
    return process


@pytest.mark.asyncio
async def test_a_deferred_request_is_sent_right_after_processing(v2g):
    """A reservation landed while the first request was in flight. The second
    request must carry it; the first could not."""

    def reservation_arrived_meanwhile():
        # handle_calendar_change rebuilds the list, so reassign, not append.
        v2g.calendar_targets = [RESERVATION]
        return True

    process = await _run(
        v2g, deferred_answers=[reservation_arrived_meanwhile, lambda: False]
    )

    requests = v2g.fm_client_app.get_new_schedule.await_args_list
    assert len(requests) == 2
    assert requests[0].kwargs["targets"] == []
    assert requests[1].kwargs["targets"] == [RESERVATION]
    assert process.await_count == 2
    # The second round is the deferred one, not a third.
    assert v2g.fm_client_app.pop_deferred_schedule_request.call_count == 2


@pytest.mark.asyncio
async def test_no_deferral_means_one_round(v2g):
    process = await _run(v2g, deferred_answers=[False])

    assert v2g.fm_client_app.get_new_schedule.await_count == 1
    assert process.await_count == 1


@pytest.mark.asyncio
async def test_a_failed_request_does_not_ask_again(v2g):
    """On failure the existing no_new_schedule handling applies; the deferral
    is picked up by whichever request starts next, not by hammering now."""
    v2g.fm_client_app.get_new_schedule = AsyncMock(return_value=None)
    process = await _run(v2g, deferred_answers=[True])

    assert v2g.fm_client_app.get_new_schedule.await_count == 1
    process.assert_not_awaited()
    v2g.fm_client_app.pop_deferred_schedule_request.assert_not_called()


@pytest.mark.asyncio
async def test_a_calendar_load_before_kick_off_does_not_ask_for_a_schedule(v2g):
    """The calendar is now read during initialisation, before kick-off. That
    load must only set the targets; the kick-off asks for the schedule with
    them, once. After kick-off a calendar change asks as before."""
    v2g.has_kicked_off = False
    ask = AsyncMock()
    with (
        patch("apps.v2g_liberty.main_app.get_local_now"),
        patch.object(v2g, "set_next_action", ask),
    ):
        await v2g.handle_calendar_change(v2g_events=[], v2g_args="initial load")
        ask.assert_not_awaited()

        v2g.has_kicked_off = True
        await v2g.handle_calendar_change(v2g_events=[], v2g_args="changed v2g_events")
        ask.assert_awaited_once()
