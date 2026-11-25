"""main_app hands the reaction rules of a schedule to the engine and takes
them away when the schedule's timers go.

The engine runs in shadow, so these tests only pin the hand-over: install
on a schedule that carries rules, clear on one that does not and on every
cancel of the charging timers. Without an engine (older tests, no wiring)
nothing changes.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)


def _schedule(rules=None):
    schedule = {
        "values": [0.001] * 12,
        "duration": "PT1H",
        "start": (TEST_NOW + timedelta(hours=1)).isoformat(),
        "scheduler_info": {"scheduler": "StorageScheduler"},
        "reaction_rules": rules,
    }
    return schedule


@pytest.fixture
def v2g(monkeypatch):
    monkeypatch.setattr(c, "TZ", TEST_TZ, raising=False)
    monkeypatch.setattr(c, "EVENT_RESOLUTION", timedelta(minutes=5), raising=False)
    monkeypatch.setattr(c, "CAR_MAX_CAPACITY_IN_KWH", 60, raising=False)
    monkeypatch.setattr(c, "ROUNDTRIP_EFFICIENCY_FACTOR", 0.85, raising=False)

    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock(return_value="Automatic")
    hass.run_at = AsyncMock(return_value="handle")

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
    app.reaction_engine = AsyncMock()
    with (
        patch("apps.v2g_liberty.main_app.cancel_timer_silent", AsyncMock()),
        patch("apps.v2g_liberty.main_app.get_local_now", return_value=TEST_NOW),
        patch.object(app, "_V2Gliberty__set_charge_power", AsyncMock()),
    ):
        yield app


@pytest.mark.asyncio
async def test_a_schedule_with_rules_installs_them(v2g):
    rules = MagicMock(name="ReactionSchedule")
    await v2g._V2Gliberty__process_schedule(_schedule(rules))
    v2g.reaction_engine.install.assert_awaited_once_with(rules)


@pytest.mark.asyncio
async def test_a_schedule_without_rules_clears_the_old_ones(v2g):
    await v2g._V2Gliberty__process_schedule(_schedule(None))
    v2g.reaction_engine.install.assert_not_awaited()
    v2g.reaction_engine.clear.assert_awaited()


@pytest.mark.asyncio
async def test_cancelling_the_timers_clears_the_rules(v2g):
    """Leaving Automatic, a disconnect and a boost all cancel the timers;
    the rules go with them."""
    await v2g._V2Gliberty__cancel_charging_timers()
    v2g.reaction_engine.clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_rejected_schedule_installs_nothing(v2g):
    v2g.hass.get_state = AsyncMock(return_value="Max boost now")
    await v2g._V2Gliberty__process_schedule(_schedule(MagicMock()))
    v2g.reaction_engine.install.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_an_engine_a_schedule_still_goes_through(v2g):
    v2g.reaction_engine = None
    await v2g._V2Gliberty__process_schedule(_schedule(MagicMock()))
    assert len(v2g.scheduling_timer_handles) == 13
