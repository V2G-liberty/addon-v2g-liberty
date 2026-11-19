"""The main app sends every power through the fuse guard and re-applies its
request when the guard moves a limit.

The driver skips a setpoint it already holds, so the re-apply has to send
the clipped value, not the request, and only when it differs from what went
out last. Without the bookkeeping here a lowered limit would be sent once
and a restored one never.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty.grid_connection.fuse_guard import ClipResult
from apps.v2g_liberty.main_app import V2Gliberty


@pytest.fixture
def v2g():
    hass = AsyncMock()
    hass.log = MagicMock()
    notifier = MagicMock()
    notifier.notify_user = AsyncMock()
    app = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)
    app.evse_client_app = AsyncMock()
    app.requested_power_w = None
    app.last_sent_power_w = None
    app.in_boost_to_reach_min_soc = False
    app.fuse_guard = MagicMock()
    app.fuse_guard.clip = AsyncMock(
        side_effect=lambda power, source="": ClipResult(power, power)
    )
    return app


def _clip_to(v2g, allowed_w: int):
    v2g.fuse_guard.clip = AsyncMock(
        side_effect=lambda power, source="": ClipResult(power, allowed_w, 1)
    )


def _sent(v2g):
    return [
        call.kwargs["charge_power"]
        for call in v2g.evse_client_app.start_charge_with_power.await_args_list
    ]


async def _request(v2g, power_w: int):
    await v2g._V2Gliberty__set_charge_power({"charge_power": power_w, "source": "test"})


async def _limit_change(v2g, reason="import overload on L1"):
    await v2g._V2Gliberty__handle_fuse_guard_limit_change(
        charge_limit_w=0, discharge_limit_w=0, phase=1, reason=reason
    )


@pytest.mark.asyncio
async def test_a_request_goes_to_the_driver_clipped_and_is_remembered_in_full(v2g):
    _clip_to(v2g, 2750)

    await _request(v2g, 7400)

    assert _sent(v2g) == [2750]
    assert v2g.requested_power_w == 7400
    assert v2g.last_sent_power_w == 2750
    v2g.fuse_guard.clip.assert_awaited_once_with(7400, source="test")


@pytest.mark.asyncio
async def test_the_schedule_sensor_shows_the_request_not_the_clip(v2g):
    """What the schedule asked for is what the chart and the deviation sensor
    compare against, as with the proxy before."""
    _clip_to(v2g, 2750)

    await _request(v2g, 7400)

    states = {
        call.args[0]: call.kwargs["state"]
        for call in v2g.hass.set_state.await_args_list
    }
    assert states["sensor.current_scheduled_charging_power"] == 7400


@pytest.mark.asyncio
async def test_a_wider_limit_re_applies_the_request_with_the_new_bound(v2g):
    _clip_to(v2g, 2750)
    await _request(v2g, 7400)
    _clip_to(v2g, 5000)

    await _limit_change(v2g, reason="charge headroom back")

    assert _sent(v2g) == [2750, 5000]
    assert v2g.last_sent_power_w == 5000
    assert v2g.requested_power_w == 7400  # still the full request


@pytest.mark.asyncio
async def test_a_narrower_limit_sends_the_clipped_value(v2g):
    await _request(v2g, 7400)
    _clip_to(v2g, 1500)

    await _limit_change(v2g)

    assert _sent(v2g) == [7400, 1500]


@pytest.mark.asyncio
async def test_an_unchanged_outcome_is_not_sent_again(v2g):
    _clip_to(v2g, 2750)
    await _request(v2g, 7400)

    await _limit_change(v2g)

    assert _sent(v2g) == [2750]


@pytest.mark.asyncio
async def test_without_an_outstanding_request_nothing_is_sent(v2g):
    await _limit_change(v2g)

    v2g.fuse_guard.clip.assert_not_awaited()
    assert _sent(v2g) == []


@pytest.mark.asyncio
async def test_without_a_fuse_guard_the_request_passes_straight_through(v2g):
    v2g.fuse_guard = None

    await _request(v2g, 7400)

    assert _sent(v2g) == [7400]
    assert v2g.last_sent_power_w == 7400


@pytest.mark.asyncio
async def test_a_disconnect_clears_the_outstanding_request(v2g):
    await _request(v2g, 7400)

    with patch.object(v2g, "_V2Gliberty__reset_no_new_schedule", AsyncMock()):
        await v2g._V2Gliberty__disconnect_charger()

    assert v2g.requested_power_w is None
    assert v2g.last_sent_power_w is None
    await _limit_change(v2g)
    assert _sent(v2g) == [7400]  # nothing re-applied
