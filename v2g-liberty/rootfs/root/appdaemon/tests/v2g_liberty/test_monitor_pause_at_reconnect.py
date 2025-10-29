import pytest
from unittest.mock import AsyncMock, MagicMock, call, ANY
from apps.v2g_liberty.monitor_pause_at_reconnect import MonitorPauseAtReconnect
from apps.v2g_liberty import constants as c
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

def test_init(monitor, mock_hass, mock_event_bus):
    """Test that the module initializes correctly and subscribes to events."""
    mock_event_bus.add_event_listener.assert_called_once_with(
        "is_car_connected", monitor._handle_connected_state_change
    )
    args, kwargs = mock_hass.log.call_args
    assert "Completed MonitorPauseAtReconnect" in kwargs["msg"]

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
        ttl=30*60,
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
        ttl=30*60,
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
        ttl=30*60,
        actions=ANY,
        callback=monitor._handle_chosen_charge_mode,
    )

@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_automatic(monitor, mock_hass, mock_notifier):
    """Test that the charge mode is set to Automatic when user chooses so."""
    await monitor._handle_chosen_charge_mode(monitor.ACTION_TO_AUTOMATIC)
    mock_notifier.clear_notification.assert_called_once_with(tag=monitor.NOTIFICATION_TAG)
    mock_hass.turn_on.assert_called_once_with("input_boolean.chargemodeautomatic")

@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_keep_current(monitor, mock_hass, mock_notifier):
    """Test that nothing happens when user chooses to keep current mode."""
    await monitor._handle_chosen_charge_mode(monitor.ACTION_KEEP_CURRENT)
    mock_notifier.clear_notification.assert_called_once_with(tag=monitor.NOTIFICATION_TAG)
    mock_hass.turn_on.assert_not_called()

@pytest.mark.asyncio
async def test_handle_chosen_charge_mode_unknown(monitor, mock_hass, mock_notifier, caplog):
    """Test that a warning is logged for unknown user actions."""
    await monitor._handle_chosen_charge_mode("unknown_action")
    mock_notifier.clear_notification.assert_called_once_with(tag=monitor.NOTIFICATION_TAG)
    mock_hass.turn_on.assert_not_called()
