"""The boost to the minimum SoC must survive a refused discharge.

Found while testing the orphaned-timers fix (2026-10-07): with the SoC
below the minimum the boost starts; the user then presses Max discharge,
which the app refuses by switching back to Automatic. That return sends 0 W
to stop the manual mode — and the boost flag, never cleared on leaving
Automatic, told the next set_next_action that the boost was still running.
The charger stood at 0 W with the battery below its minimum and the app did
nothing until the car was unplugged or Stop was pressed.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)


@pytest.fixture
def v2g(monkeypatch):
    monkeypatch.setattr(c, "TZ", TEST_TZ, raising=False)
    monkeypatch.setattr(c, "EVENT_RESOLUTION", timedelta(minutes=5), raising=False)
    monkeypatch.setattr(c, "CAR_MIN_SOC_IN_PERCENT", 17, raising=False)
    monkeypatch.setattr(c, "CAR_MAX_SOC_IN_KWH", 50, raising=False)
    monkeypatch.setattr(c, "CAR_MAX_CAPACITY_IN_KWH", 58, raising=False)
    monkeypatch.setattr(c, "ROUNDTRIP_EFFICIENCY_FACTOR", 0.85, raising=False)
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 3900, raising=False)
    monkeypatch.setattr(c, "DATE_TIME_FORMAT", "%H:%M", raising=False)

    hass = AsyncMock()
    hass.log = MagicMock()
    app = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=AsyncMock())

    app.mode = "Automatic"
    hass.get_state = AsyncMock(side_effect=lambda *a, **k: app.mode)

    app.scheduling_timer_handles = []
    app.scheduling_timers_lock = asyncio.Lock()
    app.in_boost_to_reach_min_soc = False
    app.back_to_max_soc = None
    app.unknown_car_ev_id = None
    app.timer_handle_set_next_action = None
    app.evse_client_app = AsyncMock()
    app.evse_client_app.is_car_connected = AsyncMock(return_value=True)
    app.evse_client_app.try_get_new_soc_in_process = False
    app.electric_vehicle = MagicMock()
    app.electric_vehicle.soc = 10
    app.electric_vehicle.soc_kwh = 5.8
    return app


@pytest.mark.asyncio
async def test_refused_discharge_does_not_end_the_boost_to_min_soc(v2g):
    boost = AsyncMock()
    power = AsyncMock()
    handle_mode_change = v2g._V2Gliberty__handle_charge_mode_change

    with (
        patch("apps.v2g_liberty.main_app.get_local_now", return_value=TEST_NOW),
        patch("apps.v2g_liberty.main_app.set_oneshot_timer", AsyncMock()),
        patch.object(v2g, "_V2Gliberty__start_max_charge_now", boost),
        patch.object(v2g, "_V2Gliberty__set_charge_power", power),
        patch.object(v2g, "_V2Gliberty__clear_all_soc_chart_lines", AsyncMock()),
        patch.object(v2g, "_V2Gliberty__reset_no_new_schedule", AsyncMock()),
        patch.object(v2g, "set_records_in_chart", AsyncMock()),
    ):
        # SoC 10% < 17% in Automatic: the boost starts.
        await v2g.set_next_action()
        assert boost.await_count == 1

        # The user presses Max discharge. The refusal itself (set_next_action
        # in that mode switching back to Automatic) is not what is tested
        # here, so it is stubbed; its effect is the next handler call.
        v2g.mode = "Max discharge now"
        with patch.object(v2g, "set_next_action", AsyncMock()):
            await handle_mode_change(
                None, None, {"state": "Automatic"}, {"state": v2g.mode}, {}
            )

        # Back to Automatic: the manual mode is reset to 0 W ...
        v2g.mode = "Automatic"
        await handle_mode_change(
            None, None, {"state": "Max discharge now"}, {"state": v2g.mode}, {}
        )
        assert power.await_args.args[0]["charge_power"] == 0

    # ... and the boost must start again, because the battery is still
    # below its minimum. Before the fix it did not: the charger stayed at 0 W.
    assert boost.await_count == 2
    assert v2g.in_boost_to_reach_min_soc is True
