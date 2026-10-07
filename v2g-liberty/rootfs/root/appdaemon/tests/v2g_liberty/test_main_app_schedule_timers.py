"""Regression tests for the orphaned charging timers.

__process_schedule cancels the previous timers, then awaits once per timer
while building the new set, and only assigns the handles at the end. That
window is a few hundred milliseconds, and three things can land in it:
another schedule, a user leaving Automatic (or a disconnect, or a boost
to the minimum SoC), or both. Seen in production on 2026-09-12 and
reproduced in dev several times since: a set of ~324 timers kept firing
0 W for the rest of the horizon, overruling later schedules and the Charge
and Discharge buttons alike.

Each test here fails when the guard it covers is removed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)
VALUES = [0.001] * 12


def _schedule():
    """A schedule in the shape FlexMeasures returns it, starting in an hour."""
    return {
        "values": VALUES,
        "duration": "PT1H",
        "start": (TEST_NOW + timedelta(hours=1)).isoformat(),
        "scheduler_info": {"scheduler": "StorageScheduler"},
    }


@pytest.fixture
def v2g(monkeypatch):
    monkeypatch.setattr(c, "TZ", TEST_TZ, raising=False)
    monkeypatch.setattr(c, "EVENT_RESOLUTION", timedelta(minutes=5), raising=False)
    monkeypatch.setattr(c, "CAR_MAX_CAPACITY_IN_KWH", 60, raising=False)
    monkeypatch.setattr(c, "ROUNDTRIP_EFFICIENCY_FACTOR", 0.85, raising=False)

    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock(return_value="Automatic")

    app = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=MagicMock())
    app.scheduling_timer_handles = []
    app.scheduling_timers_lock = asyncio.Lock()
    app.in_boost_to_reach_min_soc = False
    app.discharge_refused_reason = None
    app.evse_client_app = AsyncMock()

    async def is_car_connected():
        # The real check talks to the charger, so it yields; an AsyncMock does
        # not, and without a yield here the two runs never interleave before
        # the lock, which is the situation the third test is about.
        await asyncio.sleep(0)
        return True

    app.evse_client_app.is_car_connected = is_car_connected
    app.electric_vehicle = MagicMock()
    app.electric_vehicle.soc = 50
    app.handle_no_new_schedule = AsyncMock()
    return app


class _Timers:
    """Stand-in for hass.run_at / cancel_timer_silent that records handles.

    run_at yields to the event loop, as the real one does; that is what opens
    the race.
    """

    def __init__(self):
        self.created = []
        self.cancelled = []

    async def run_at(self, callback, when, **kwargs):
        await asyncio.sleep(0)
        handle = f"handle-{len(self.created)}"
        self.created.append(handle)
        return handle

    async def cancel(self, _hass, handle):
        self.cancelled.append(handle)

    def orphans(self, reachable):
        return set(self.created) - set(self.cancelled) - set(reachable)


@pytest.fixture
def timers(v2g):
    t = _Timers()
    v2g.hass.run_at = t.run_at
    with (
        patch("apps.v2g_liberty.main_app.cancel_timer_silent", t.cancel),
        patch("apps.v2g_liberty.main_app.get_local_now", return_value=TEST_NOW),
        patch.object(v2g, "_V2Gliberty__set_charge_power", AsyncMock()),
    ):
        yield t


@pytest.mark.asyncio
async def test_two_schedules_at_once_leave_no_timer_behind(v2g, timers):
    """Guard: the lock in __process_schedule.

    Two schedules arriving together must end with exactly one reachable set;
    everything the other run created must have been cancelled.
    """
    process = v2g._V2Gliberty__process_schedule
    await asyncio.gather(process(_schedule()), process(_schedule()))

    assert len(v2g.scheduling_timer_handles) == len(VALUES)
    assert timers.orphans(v2g.scheduling_timer_handles) == set()


@pytest.mark.asyncio
async def test_cancel_during_a_rebuild_waits_and_then_cancels_everything(v2g, timers):
    """Guard: __cancel_charging_timers takes the lock.

    The user leaves Automatic while a schedule is being armed. The cancel
    must wait for the rebuild and then clear the whole new set, instead of
    cancelling the empty list it finds mid-rebuild and leaving ~324 timers
    live against the user's action.
    """
    build = asyncio.create_task(v2g._V2Gliberty__process_schedule(_schedule()))
    while not timers.created:  # let the build get past its first timer
        await asyncio.sleep(0)

    await v2g._V2Gliberty__cancel_charging_timers()
    await build

    assert v2g.scheduling_timer_handles == []
    assert set(timers.created) <= set(timers.cancelled)


@pytest.mark.asyncio
async def test_a_run_that_waited_rechecks_the_mode_and_stands_down(v2g, timers):
    """Guard: the re-check inside the lock.

    Run B passed the charge-mode check, then waited for run A. The user left
    Automatic while A was building. B must notice and not arm a set — nor
    cancel A's, which is for the mode change's own cancel to clear.
    """
    mode = ["Automatic"]
    v2g.hass.get_state = AsyncMock(side_effect=lambda *a, **k: mode[0])

    original_run_at = timers.run_at

    async def run_at_then_leave_automatic(*args, **kwargs):
        mode[0] = "Stop"  # only the run that holds the lock reaches run_at
        return await original_run_at(*args, **kwargs)

    v2g.hass.run_at = run_at_then_leave_automatic

    process = v2g._V2Gliberty__process_schedule
    await asyncio.gather(process(_schedule()), process(_schedule()))

    assert len(timers.created) == len(VALUES), "only the first run may build"
    assert len(v2g.scheduling_timer_handles) == len(VALUES)
    assert timers.orphans(v2g.scheduling_timer_handles) == set()
