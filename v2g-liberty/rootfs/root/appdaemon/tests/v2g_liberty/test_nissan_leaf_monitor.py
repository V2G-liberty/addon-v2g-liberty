"""Unit test (pytest) for nissan_leaf_monitor module."""

from unittest.mock import MagicMock, patch, ANY
import pytest
from apps.v2g_liberty.nissan_leaf_monitor import NissanLeafMonitor


@pytest.fixture
def mock_hass():
    """Mock Home Assistant API with a log method we can inspect."""
    hass = MagicMock()
    hass.log = MagicMock()
    return hass


@pytest.fixture
def mock_event_bus():
    return MagicMock()


@pytest.fixture
def mock_notifier():
    return MagicMock()


@pytest.fixture
def nissan_leaf_monitor(mock_hass, mock_event_bus, mock_notifier):
    """Create NissanLeafMonitor with mocked hass.log already in place."""
    return NissanLeafMonitor(mock_hass, mock_event_bus, mock_notifier)


def _msg_contains(call, *parts):
    """Check if all parts appear in the msg kwarg."""
    msg = call.kwargs.get("msg", "")
    return all(str(p) in msg for p in parts)


def _dump_logs(mock_hass):
    """Return all logged messages for debugging."""
    return "\n".join(c.kwargs.get("msg", "") for c in mock_hass.log.call_args_list)


@pytest.mark.asyncio
async def test_check_notification(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    """Test the _check_notification method."""
    min_soc = 20
    ev_name = "My Nissan Leaf"

    # Act
    await nissan_leaf_monitor._check_notification(min_soc, ev_name)

    # Assert
    # Check that the notifier was called with the correct message and tag
    mock_notifier.notify_user.assert_called_once_with(
        message=ANY,
        tag="soc_skipped",
        ttl=24 * 60 * 60,
    )
    notify_kwargs = mock_notifier.notify_user.call_args.kwargs
    assert "The 'My Nissan Leaf' faulted" in notify_kwargs["message"]
    assert "20%" in notify_kwargs["message"]

    # Check that the event listener was removed
    mock_event_bus.remove_event_listener.assert_called_once_with(
        "nissan_leaf_soc_skipped", nissan_leaf_monitor._check_notification
    )

    # Check that the listener is re-added after the specified duration
    mock_hass.run_in.assert_called_once_with(
        nissan_leaf_monitor._initialize, 24 * 60 * 60
    )

    # Check that the log message was written
    if not any(
        _msg_contains(call, "Notified user with message:")
        for call in mock_hass.log.call_args_list
    ):
        pytest.fail(f"Expected log not found. Actual logs:\n{_dump_logs(mock_hass)}")


@pytest.mark.asyncio
async def test_check_notification_exception(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    """Test the _check_notification method when an exception occurs."""
    min_soc = 20
    ev_name = "My Nissan Leaf"

    # Simulate an exception in notify_user
    mock_notifier.notify_user.side_effect = Exception("Notification failed")

    # Act
    await nissan_leaf_monitor._check_notification(min_soc, ev_name)

    # Assert
    # Check that the exception was logged
    if not any(
        _msg_contains(call, "Problem soc_skipped_warning event. Exception:")
        and call.kwargs.get("level") == "WARNING"
        for call in mock_hass.log.call_args_list
    ):
        pytest.fail(
            f"Expected warning log not found. Actual logs:\n{_dump_logs(mock_hass)}"
        )

    # Check that the event listener was NOT removed
    mock_event_bus.remove_event_listener.assert_not_called()

    # Check that the listener is NOT re-added
    mock_hass.run_in.assert_not_called()
