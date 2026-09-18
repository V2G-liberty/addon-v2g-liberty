"""Unit tests for RecoveryProbe: the timer administration that re-checks a
charger after the driver has given up on it.

The probe owns exactly one timer, claims it before the first await so two
concurrent escalations cannot both arm, releases the claim when arming fails,
and treats anything the check raises as "not back yet".
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from apps.v2g_liberty.chargers.recovery_probe import RecoveryProbe


def _hass():
    hass = MagicMock()
    hass.run_every = AsyncMock(return_value="handle-1")
    hass.timer_running = AsyncMock(return_value=True)
    hass.cancel_timer = AsyncMock()
    return hass


@pytest.fixture
def probe():
    hass = _hass()
    check = AsyncMock(return_value=True)
    on_recovered = AsyncMock()
    p = RecoveryProbe(hass, MagicMock(), 60, check=check, on_recovered=on_recovered)
    return p, hass, check, on_recovered


@pytest.mark.asyncio
async def test_arm_schedules_the_tick_at_the_interval(probe):
    p, hass, _, _ = probe
    await p.arm()

    # "now": AppDaemon fires the first run at now + interval (+N for "now+N").
    hass.run_every.assert_awaited_once_with(p._tick, "now", 60)
    assert p.is_armed


@pytest.mark.asyncio
async def test_arming_twice_keeps_the_first_timer(probe):
    p, hass, _, _ = probe
    await p.arm()
    await p.arm()

    hass.run_every.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_arming_releases_the_claim(probe):
    """A later escalation must be able to try again."""
    p, hass, _, _ = probe
    hass.run_every = AsyncMock(side_effect=RuntimeError("boom"))
    await p.arm()

    assert not p.is_armed
    hass.run_every = AsyncMock(return_value="handle-2")
    await p.arm()
    assert p.is_armed


@pytest.mark.asyncio
async def test_cancel_during_arming_does_not_keep_the_timer(probe):
    """A manual recovery that lands while the timer is being created."""
    p, hass, _, _ = probe

    async def create_then_cancel(*args, **kwargs):
        await p.cancel()
        return "handle-1"

    hass.run_every = AsyncMock(side_effect=create_then_cancel)
    await p.arm()

    assert not p.is_armed
    hass.cancel_timer.assert_awaited_once_with("handle-1", silent=True)


@pytest.mark.asyncio
async def test_cancel_stops_the_timer(probe):
    p, hass, _, _ = probe
    await p.arm()
    await p.cancel()

    hass.cancel_timer.assert_awaited_once_with("handle-1", silent=True)
    assert not p.is_armed


@pytest.mark.asyncio
async def test_a_raising_check_means_not_back_yet(probe):
    """The transport raises on a dead connection: the expected state."""
    p, _, check, on_recovered = probe
    check.side_effect = ConnectionError("still dead")
    await p.arm()
    await p._tick()

    on_recovered.assert_not_awaited()
    assert p.is_armed


@pytest.mark.asyncio
async def test_an_unhealthy_check_keeps_waiting(probe):
    p, _, check, on_recovered = probe
    check.return_value = False
    await p.arm()
    await p._tick()

    on_recovered.assert_not_awaited()
    assert p.is_armed


@pytest.mark.asyncio
async def test_a_healthy_check_cancels_the_probe_then_recovers(probe):
    p, hass, _, on_recovered = probe
    await p.arm()
    await p._tick()

    hass.cancel_timer.assert_awaited_once_with("handle-1", silent=True)
    assert not p.is_armed
    on_recovered.assert_awaited_once()
