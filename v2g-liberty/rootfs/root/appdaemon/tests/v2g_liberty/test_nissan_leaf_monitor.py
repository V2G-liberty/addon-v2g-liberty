"""Unit test (pytest) for nissan_leaf_monitor module."""

from unittest.mock import MagicMock, call
import pytest
from apps.v2g_liberty.nissan_leaf_monitor import NissanLeafMonitor
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.notifier_util import Notifier
import apps.v2g_liberty.constants as c
from appdaemon.plugins.hass.hassapi import Hass


@pytest.fixture
def mock_hass():
    """Mock the Hass object."""
    hass = MagicMock(spec=Hass)
    hass.log = MagicMock()
    return hass


@pytest.fixture
def mock_event_bus():
    """Mock the EventBus object."""
    return MagicMock(spec=EventBus)


@pytest.fixture
def mock_notifier():
    """Mock the Notifier object."""
    notifier = MagicMock(spec=Notifier)
    # notifier.notify_user = MagicMock()
    return notifier


@pytest.fixture
def nissan_leaf_monitor(mock_hass, mock_event_bus, mock_notifier):
    """Create an instance of NissanLeafMonitor with mocked dependencies."""
    c.CAR_MIN_SOC_IN_PERCENT = 20  # Set the minimum SoC threshold for the test
    monitor = NissanLeafMonitor(
        hass=mock_hass, event_bus=mock_event_bus, notifier=mock_notifier
    )
    monitor.notifier = mock_notifier
    return monitor


def test_handle_soc_change_skip(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    """
    Test _handle_soc_change behavior when the SoC skips the threshold.
    """
    new_soc = 19
    old_soc = 21

    # Call the method
    nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Verify logging
    log_call = mock_hass.log.call_args_list
    assert any(
        "SoC change jump: old_soc '21', new_soc '19'," in call.kwargs.get("msg", "")
        for call in log_call
    ), "Expected log message not found!"

    # Verify notification
    mock_notifier.notify_user.assert_called_once_with(
        message=(
            f"The Nissan Leaf faulted, skipping the state-of-charge "
            f"{c.CAR_MIN_SOC_IN_PERCENT}%. This often leads to toggled charging. "
            f"A possible solution is to change the setting for 'schedule lower limit'"
            f"to 1%-point higher or lower."
        ),
        tag="soc_skipped",
        ttl=24 * 60 * 60,
    )

    # Verify event listener removal and re-registration
    mock_event_bus.remove_event_listener.assert_called_once_with(
        "soc_change", nissan_leaf_monitor._handle_soc_change
    )
    mock_hass.run_in.assert_called_once_with(
        nissan_leaf_monitor._initialize, 24 * 60 * 60
    )


def test_handle_soc_change_no_skip(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    """
    Test _handle_soc_change behavior when the SoC does not skip the threshold.
    """
    new_soc = 21
    old_soc = 22

    # Reset the log mock to clear initialization log calls
    mock_hass.log.reset_mock()

    # Call the method
    nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Verify no logging for skip warning
    mock_hass.log.assert_not_called()

    # Verify no notification sent
    mock_notifier.notify_user.assert_not_called()

    # Verify event listener not removed
    mock_event_bus.remove_event_listener.assert_not_called()


def test_handle_soc_change_invalid_soc(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier
):
    new_soc = None
    old_soc = "unknown"

    # Call the method
    nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Verify logging for invalid input
    log_call = mock_hass.log.call_args_list
    assert any(
        "Aborting: new_soc 'None' and/or old_soc 'unknown'"
        in call.kwargs.get("msg", "")
        for call in log_call
    ), "Expected log message not found!"

    # Verify no notification sent
    mock_notifier.notify_user.assert_not_called()

    # Verify event listener not removed
    mock_event_bus.remove_event_listener.assert_not_called()
