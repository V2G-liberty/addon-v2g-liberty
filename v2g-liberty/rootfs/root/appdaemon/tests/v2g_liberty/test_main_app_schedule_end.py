"""A schedule that runs out without a successor stops the charger.

The charger holds its last setting until it is told otherwise. Seen in
production in September 2026: FlexMeasures refused every new schedule, the
last interval of the old one asked for -5937 W, and after the horizon ended
nothing else was sent. The car discharged for another hour, until the
minimum-SoC guard stopped it, and missed its reservation the next morning.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)
DISCHARGE = -0.005937  # MW, as FlexMeasures returns it


def _schedule(start, values=None):
    values = values or [DISCHARGE] * 12
    return {
        "values": values,
        "duration": f"PT{5 * len(values)}M",
        "start": start.isoformat(),
        "scheduler_info": {"scheduler": "StorageScheduler"},
    }


class _Timers:
    """Stand-in for hass.run_at and cancel_timer_silent that records them."""

    def __init__(self):
        self.created = []  # (handle, when, kwargs)
        self.cancelled = []

    async def run_at(self, _callback, when, **kwargs):
        handle = f"handle-{len(self.created)}"
        self.created.append((handle, when, kwargs))
        return handle

    async def cancel(self, _hass, handle):
        self.cancelled.append(handle)


def _closing_timer(timers):
    """The handle of the one timer that closes a schedule with 0 W."""
    closing = [
        handle
        for handle, _when, kwargs in timers.created
        if "end of schedule" in kwargs["source"]
    ]
    assert len(closing) == 1
    return closing[0]


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
    app.evse_client_app.is_car_connected = AsyncMock(return_value=True)
    app.electric_vehicle = MagicMock()
    app.electric_vehicle.soc = 50
    app.handle_no_new_schedule = AsyncMock()
    return app


@pytest.fixture
def timers(v2g):
    t = _Timers()
    v2g.hass.run_at = t.run_at
    power = AsyncMock()
    with (
        patch("apps.v2g_liberty.main_app.cancel_timer_silent", t.cancel),
        patch("apps.v2g_liberty.main_app.get_local_now", return_value=TEST_NOW),
        patch.object(v2g, "_V2Gliberty__set_charge_power", power),
    ):
        t.power = power
        yield t


@pytest.mark.asyncio
async def test_the_end_of_the_schedule_sets_the_charger_to_zero(v2g, timers):
    start = TEST_NOW + timedelta(minutes=5)
    await v2g._V2Gliberty__process_schedule(_schedule(start))

    handle, when, kwargs = timers.created[-1]
    assert when == start + timedelta(hours=1)
    assert kwargs["charge_power"] == 0
    assert "end of schedule" in kwargs["source"]
    assert handle in v2g.scheduling_timer_handles
    # One timer per interval, plus the closing one.
    assert len(timers.created) == 12 + 1


@pytest.mark.asyncio
async def test_a_new_schedule_cancels_the_closing_timer(v2g, timers):
    """In normal operation a new schedule arrives every 15 minutes at most,
    so the closing timer never fires."""
    process = v2g._V2Gliberty__process_schedule
    await process(_schedule(TEST_NOW + timedelta(minutes=5)))
    closing_of_the_first = _closing_timer(timers)

    await process(_schedule(TEST_NOW + timedelta(minutes=10)))

    assert closing_of_the_first in timers.cancelled
    assert closing_of_the_first not in v2g.scheduling_timer_handles


@pytest.mark.asyncio
async def test_leaving_automatic_cancels_the_closing_timer(v2g, timers):
    """Leaving Automatic, a disconnect and a boost all cancel through here; a
    0 W from a schedule that is no longer in charge must not overrule them."""
    await v2g._V2Gliberty__process_schedule(_schedule(TEST_NOW + timedelta(minutes=5)))
    closing = _closing_timer(timers)

    await v2g._V2Gliberty__cancel_charging_timers()

    assert closing in timers.cancelled


@pytest.mark.asyncio
async def test_a_schedule_that_is_already_over_sets_zero_at_once(v2g, timers):
    """All intervals in the past: they are applied in order, as before, and
    the charger then ends at 0 W instead of on the last interval's power."""
    start = TEST_NOW - timedelta(hours=2)
    await v2g._V2Gliberty__process_schedule(_schedule(start, values=[DISCHARGE] * 3))

    assert timers.created == []
    powers = [call.args[0]["charge_power"] for call in timers.power.await_args_list]
    assert powers == [-5937, -5937, -5937, 0]
