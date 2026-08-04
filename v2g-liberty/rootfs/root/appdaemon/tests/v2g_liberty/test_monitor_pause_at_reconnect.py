import logging
import pytest
from unittest.mock import AsyncMock, MagicMock, ANY
from apps.v2g_liberty.monitor_pause_at_reconnect import MonitorPauseAtReconnect
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.notifier_util import Notifier


@pytest.fixture
def mock_hass():
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock()
    hass.turn_on = AsyncMock()
    return hass


@pytest.fixture
def mock_event_bus():
    bus = AsyncMock(spec=EventBus)
    bus.add_event_listener = MagicMock()
    return bus


@pytest.fixture
def mock_notifier():
    return AsyncMock(spec=Notifier)


@pytest.fixture
def monitor(mock_hass, mock_event_bus, mock_notifier):
    return MonitorPauseAtReconnect(mock_hass, mock_event_bus, mock_notifier)


def test_init(mock_hass, mock_event_bus, mock_notifier, caplog):
    """Test that the module initializes correctly and subscribes to events."""
    with caplog.at_level(logging.INFO):
        monitor = MonitorPauseAtReconnect(mock_hass, mock_event_bus, mock_notifier)
    mock_event_bus.add_event_listener.assert_called_once_with(
        "is_car_connected", monitor._handle_connected_state_change
    )
    assert "Completed MonitorPauseAtReconnect" in caplog.text


@pytest.mark.asyncio
async def test_handle_connected_state_change_disconnected(monitor, mock_hass):
    """Test that nothing happens when the car is disconnected."""
    await monitor._handle_connected_state_change(False)
    mock_hass.get_state.assert_not_called()
    monitor.notifier.notify_user.assert_not_called()


@pytest.mark.asyncio
async def test_handle_connected_state_change_automatic(monitor, mock_hass):
    """Test that nothing happens when charge mode is Automatic."""
    mock_hass.get_state.return_value = "Automatic"
    await monitor._handle_connected_state_change(True)
    mock_hass.get_state.assert_called_once_with("input_select.charge_mode", None)
    monitor.notifier.notify_user.assert_not_called()
    # No prompt means no auto-switch fallback may be armed.
    mock_hass.run_in.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_connected_state_change_none(monitor, mock_hass):
    """Test that nothing happens (and no timer is armed) when charge mode is None."""
    mock_hass.get_state.return_value = None
    await monitor._handle_connected_state_change(True)
    mock_hass.get_state.assert_called_once_with("input_select.charge_mode", None)
    monitor.notifier.notify_user.assert_not_called()
    mock_hass.run_in.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_connected_state_change_pause(monitor, mock_hass, mock_notifier):
    """Test that user is notified when charge mode is Pause."""
    mock_hass.get_state.return_value = "Pause"
    await monitor._handle_connected_state_change(True)
    mock_hass.get_state.assert_called_once_with("input_select.charge_mode", None)
    mock_notifier.notify_user.assert_called_once_with(
        message="Would you like to set it to 'Automatic'?",
        title="Car connected, the app is set to 'Pause'",
        tag=monitor.NOTIFICATION_TAG,
        send_to_all=True,
        ttl=monitor.AUTO_SWITCH_TIMEOUT_SECONDS,
        actions=ANY,
        callback=monitor._handle_chosen_charge_mode,
    )


@pytest.mark.asyncio
async def test_handle_connected_state_change_stop(monitor, mock_hass, mock_notifier):
    """Test that user is notified when charge mode is Stop (translated to Pause)."""
    mock_hass.get_state.return_value = "Stop"
    await monitor._handle_connected_state_change(True)
    mock_hass.get_state.assert_called_once_with("input_select.charge_mode", None)
    mock_notifier.notify_user.assert_called_once_with(
        message="Would you like to set it to 'Automatic'?",
        title="Car connected, the app is set to 'Pause'",
        tag=monitor.NOTIFICATION_TAG,
        send_to_all=True,
        ttl=monitor.AUTO_SWITCH_TIMEOUT_SECONDS,
        actions=ANY,
        callback=monitor._handle_chosen_charge_mode,
    )


@pytest.mark.asyncio
async def test_handle_connected_state_change_charge(monitor, mock_hass, mock_notifier):
    """Test that user is notified when charge mode is Charge."""
    mock_hass.get_state.return_value = "Charge"
    await monitor._handle_connected_state_change(True)
    mock_hass.get_state.assert_called_once_with("input_select.charge_mode", None)
    mock_notifier.notify_user.assert_called_once_with(
        message="Would you like to set it to 'Automatic'?",
        title="Car connected, the app is set to 'Charge'",
        tag=monitor.NOTIFICATION_TAG,
        send_to_all=True,
        ttl=monitor.AUTO_SWITCH_TIMEOUT_SECONDS,
        actions=ANY,
        callback=monitor._handle_chosen_charge_mode,
    )


@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_automatic(monitor, mock_hass, mock_notifier):
    """Test that the charge mode is set to Automatic when user chooses so."""
    await monitor._handle_chosen_charge_mode(monitor.ACTION_TO_AUTOMATIC)
    mock_notifier.clear_notification.assert_called_once_with(
        tag=monitor.NOTIFICATION_TAG
    )
    mock_hass.turn_on.assert_called_once_with("input_boolean.chargemodeautomatic")


@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_keep_current(
    monitor, mock_hass, mock_notifier
):
    """Test that nothing happens when user chooses to keep current mode."""
    await monitor._handle_chosen_charge_mode(monitor.ACTION_KEEP_CURRENT)
    mock_notifier.clear_notification.assert_called_once_with(
        tag=monitor.NOTIFICATION_TAG
    )
    mock_hass.turn_on.assert_not_called()


@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_unknown(
    monitor, mock_hass, mock_notifier, caplog
):
    """Test that a warning is logged for unknown user actions."""
    await monitor._handle_chosen_charge_mode("unknown_action")
    mock_notifier.clear_notification.assert_called_once_with(
        tag=monitor.NOTIFICATION_TAG
    )
    mock_hass.turn_on.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_schedules_auto_switch_timer(monitor, mock_hass):
    """A reconnect in Pause schedules the auto-switch-to-Automatic fallback timer."""
    mock_hass.get_state.return_value = "Pause"
    await monitor._handle_connected_state_change(True)
    # A one-shot timer to _auto_switch_to_automatic is armed with the timeout delay.
    mock_hass.run_in.assert_awaited_once_with(
        monitor._auto_switch_to_automatic,
        delay=monitor.AUTO_SWITCH_TIMEOUT_SECONDS,
    )
    # The returned handle is remembered so it can be cancelled later.
    assert monitor._auto_switch_timer_handle == mock_hass.run_in.return_value


@pytest.mark.asyncio
async def test_auto_switch_to_automatic_switches_mode(
    monitor, mock_hass, mock_notifier
):
    """The fallback clears the prompt and switches the charge mode to Automatic."""
    monitor._auto_switch_timer_handle = "pending_handle"
    await monitor._auto_switch_to_automatic()
    mock_notifier.clear_notification.assert_called_once_with(
        tag=monitor.NOTIFICATION_TAG
    )
    mock_hass.turn_on.assert_called_once_with("input_boolean.chargemodeautomatic")
    # The handle is cleared so a stale value cannot be cancelled twice.
    assert monitor._auto_switch_timer_handle == ""


@pytest.mark.asyncio
async def test_user_response_cancels_pending_auto_switch(monitor, mock_hass):
    """Any user response cancels the pending auto-switch fallback timer."""
    mock_hass.timer_running = AsyncMock(return_value=True)
    monitor._auto_switch_timer_handle = "pending_handle"
    await monitor._handle_chosen_charge_mode(monitor.ACTION_KEEP_CURRENT)
    mock_hass.cancel_timer.assert_awaited_once_with("pending_handle", silent=True)
    assert monitor._auto_switch_timer_handle == ""


@pytest.mark.asyncio
async def test_disconnect_cancels_pending_auto_switch(
    monitor, mock_hass, mock_notifier
):
    """A disconnect cancels a pending auto-switch fallback and does not notify."""
    mock_hass.timer_running = AsyncMock(return_value=True)
    monitor._auto_switch_timer_handle = "pending_handle"
    await monitor._handle_connected_state_change(False)
    mock_hass.cancel_timer.assert_awaited_once_with("pending_handle", silent=True)
    assert monitor._auto_switch_timer_handle == ""
    mock_hass.get_state.assert_not_called()
    mock_notifier.notify_user.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_registers_charge_mode_listener(monitor, mock_hass):
    """initialize() subscribes to charge_mode changes so any change can cancel."""
    await monitor.initialize()
    mock_hass.listen_state.assert_awaited_once_with(
        monitor._handle_charge_mode_change,
        "input_select.charge_mode",
    )


@pytest.mark.asyncio
async def test_charge_mode_change_cancels_pending_auto_switch(monitor, mock_hass):
    """A charge_mode change by any means cancels a pending auto-switch fallback."""
    mock_hass.timer_running = AsyncMock(return_value=True)
    monitor._auto_switch_timer_handle = "pending_handle"
    # e.g. the user picks Automatic (then Pause) via the UI radio buttons.
    await monitor._handle_charge_mode_change(
        "input_select.charge_mode", "state", "Stop", "Automatic", {}
    )
    mock_hass.cancel_timer.assert_awaited_once_with("pending_handle", silent=True)
    assert monitor._auto_switch_timer_handle == ""


@pytest.mark.asyncio
async def test_charge_mode_unchanged_does_not_cancel(monitor, mock_hass):
    """A no-op state callback (new == old) leaves a pending fallback intact."""
    mock_hass.timer_running = AsyncMock(return_value=True)
    monitor._auto_switch_timer_handle = "pending_handle"
    await monitor._handle_charge_mode_change(
        "input_select.charge_mode", "state", "Stop", "Stop", {}
    )
    mock_hass.cancel_timer.assert_not_awaited()
    assert monitor._auto_switch_timer_handle == "pending_handle"
