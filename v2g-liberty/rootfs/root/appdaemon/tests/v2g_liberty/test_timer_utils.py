"""Unit tests for timer_utils module."""

from unittest.mock import AsyncMock

import pytest

from apps.v2g_liberty.timer_utils import (
    cancel_timer_silent,
    set_daily_timer,
    set_oneshot_timer,
)

# pylint: disable=C0116,W0621


@pytest.fixture
def hass():
    """Return a Hass mock with the async timer API."""
    h = AsyncMock()
    h.timer_running = AsyncMock(return_value=True)
    h.cancel_timer = AsyncMock()
    h.run_in = AsyncMock(return_value="new-handle-uuid")
    h.run_daily = AsyncMock(return_value="daily-handle-uuid")
    return h


# ── cancel_timer_silent ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_timer_silent_no_op_on_none(hass):
    await cancel_timer_silent(hass, None)
    hass.timer_running.assert_not_called()
    hass.cancel_timer.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_timer_silent_no_op_on_empty_string(hass):
    await cancel_timer_silent(hass, "")
    hass.timer_running.assert_not_called()
    hass.cancel_timer.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_timer_silent_cancels_running_timer(hass):
    hass.timer_running.return_value = True
    await cancel_timer_silent(hass, "abc-123")
    hass.timer_running.assert_awaited_once_with("abc-123")
    hass.cancel_timer.assert_awaited_once_with("abc-123", silent=True)


@pytest.mark.asyncio
async def test_cancel_timer_silent_skips_when_not_running(hass):
    hass.timer_running.return_value = False
    await cancel_timer_silent(hass, "abc-123")
    hass.timer_running.assert_awaited_once_with("abc-123")
    hass.cancel_timer.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_timer_silent_swallows_timer_running_exception(hass):
    hass.timer_running.side_effect = RuntimeError("boom")
    # Must not raise
    await cancel_timer_silent(hass, "abc-123")
    hass.cancel_timer.assert_not_called()


@pytest.mark.asyncio
async def test_cancel_timer_silent_swallows_cancel_exception(hass):
    hass.timer_running.return_value = True
    hass.cancel_timer.side_effect = RuntimeError("boom")
    # Must not raise
    await cancel_timer_silent(hass, "abc-123")
    hass.cancel_timer.assert_awaited_once()


# ── set_oneshot_timer ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_oneshot_timer_first_call_no_cancel(hass):
    callback = AsyncMock()
    handle = await set_oneshot_timer(hass, None, callback, delay=60)
    assert handle == "new-handle-uuid"
    hass.timer_running.assert_not_called()
    hass.cancel_timer.assert_not_called()
    hass.run_in.assert_awaited_once_with(callback, delay=60)


@pytest.mark.asyncio
async def test_set_oneshot_timer_cancels_previous_handle(hass):
    callback = AsyncMock()
    hass.timer_running.return_value = True
    handle = await set_oneshot_timer(hass, "old-handle", callback, delay=120)
    assert handle == "new-handle-uuid"
    hass.timer_running.assert_awaited_once_with("old-handle")
    hass.cancel_timer.assert_awaited_once_with("old-handle", silent=True)
    hass.run_in.assert_awaited_once_with(callback, delay=120)


@pytest.mark.asyncio
async def test_set_oneshot_timer_forwards_kwargs(hass):
    callback = AsyncMock()
    await set_oneshot_timer(hass, None, callback, delay=30, foo="bar", number=42)
    hass.run_in.assert_awaited_once_with(callback, delay=30, foo="bar", number=42)


# ── set_daily_timer ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_daily_timer_first_call_no_cancel(hass):
    callback = AsyncMock()
    handle = await set_daily_timer(hass, None, callback, start="03:00:00")
    assert handle == "daily-handle-uuid"
    hass.cancel_timer.assert_not_called()
    hass.run_daily.assert_awaited_once_with(callback, start="03:00:00")


@pytest.mark.asyncio
async def test_set_daily_timer_cancels_previous_handle(hass):
    callback = AsyncMock()
    hass.timer_running.return_value = True
    handle = await set_daily_timer(hass, "old-daily-handle", callback, start="04:00:00")
    assert handle == "daily-handle-uuid"
    hass.cancel_timer.assert_awaited_once_with("old-daily-handle", silent=True)
    hass.run_daily.assert_awaited_once_with(callback, start="04:00:00")
