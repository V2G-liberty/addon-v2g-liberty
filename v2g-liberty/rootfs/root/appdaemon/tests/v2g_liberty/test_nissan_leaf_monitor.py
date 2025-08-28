"""Unit test (pytest) for nissan_leaf_monitor module."""

from unittest.mock import MagicMock
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
async def test_handle_soc_change_skip(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    new_soc = 19
    old_soc = 21

    await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Look at hass.log calls directly
    if not any(
        _msg_contains(
            call, "SoC change jump", f"old_soc '{old_soc}'", f"new_soc '{new_soc}'"
        )
        and call.kwargs.get("level") == "WARNING"
        for call in mock_hass.log.call_args_list
    ):
        pytest.fail(
            f"Expected skip log not found. Actual logs:\n{_dump_logs(mock_hass)}"
        )

    mock_notifier.notify_user.assert_called_once()
    notify_kwargs = mock_notifier.notify_user.call_args.kwargs
    assert notify_kwargs["tag"] == "soc_skipped"

    mock_event_bus.remove_event_listener.assert_called_once_with(
        "soc_change", nissan_leaf_monitor._handle_soc_change
    )
    nissan_leaf_monitor.hass.run_in.assert_called_once()


@pytest.mark.asyncio
async def test_handle_soc_change_invalid_soc(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    new_soc = None
    old_soc = "unknown"

    await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    if not any(
        _msg_contains(call, "Aborting: new_soc", f"'{new_soc}'", f"'{old_soc}'")
        for call in mock_hass.log.call_args_list
    ):
        pytest.fail(
            f"Expected abort log not found. Actual logs:\n{_dump_logs(mock_hass)}"
        )

    mock_notifier.notify_user.assert_not_called()
    mock_event_bus.remove_event_listener.assert_not_called()
