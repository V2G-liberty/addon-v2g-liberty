"""Unit test (pytest) for nissan_leaf_monitor module."""

import logging
from unittest.mock import AsyncMock, MagicMock
import pytest
from apps.v2g_liberty.nissan_leaf_monitor import NissanLeafMonitor


@pytest.fixture
def mock_hass():
    """Mock Home Assistant API."""
    hass = MagicMock()
    hass.log = MagicMock()
    hass.run_in = AsyncMock()
    return hass


@pytest.fixture
def mock_event_bus():
    return MagicMock()


@pytest.fixture
def mock_notifier():
    notifier = MagicMock()
    notifier.notify_user = AsyncMock()
    return notifier


@pytest.fixture
def nissan_leaf_monitor(mock_hass, mock_event_bus, mock_notifier):
    """Create NissanLeafMonitor with mocked hass.log already in place."""
    return NissanLeafMonitor(mock_hass, mock_event_bus, mock_notifier)


@pytest.mark.asyncio
async def test_handle_soc_change_skip(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier, caplog
):
    new_soc = 19
    old_soc = 21

    with caplog.at_level(logging.WARNING):
        await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    assert "SoC change jump" in caplog.text
    assert f"old_soc '{old_soc}'" in caplog.text
    assert f"new_soc '{new_soc}'" in caplog.text

    mock_notifier.notify_user.assert_called_once()
    notify_kwargs = mock_notifier.notify_user.call_args.kwargs
    assert notify_kwargs["tag"] == "soc_skipped"

    mock_event_bus.remove_event_listener.assert_called_once_with(
        "soc_change", nissan_leaf_monitor._handle_soc_change
    )
    nissan_leaf_monitor.hass.run_in.assert_called_once()


@pytest.mark.asyncio
async def test_handle_soc_change_invalid_soc(
    nissan_leaf_monitor, mock_hass, mock_event_bus, mock_notifier, caplog
):
    new_soc = None
    old_soc = "unknown"

    with caplog.at_level(logging.INFO):
        await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    assert "Aborting: new_soc" in caplog.text
    assert f"'{new_soc}'" in caplog.text
    assert f"'{old_soc}'" in caplog.text

    mock_notifier.notify_user.assert_not_called()
    mock_event_bus.remove_event_listener.assert_not_called()
