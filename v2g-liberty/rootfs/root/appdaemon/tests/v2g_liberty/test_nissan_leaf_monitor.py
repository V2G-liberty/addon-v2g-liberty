import pytest
from unittest.mock import AsyncMock, MagicMock
import apps.v2g_liberty.constants as c
import log_wrapper
from apps.v2g_liberty.nissan_leaf_monitor import (
    NissanLeafMonitor,
)


@pytest.fixture
def hass_mock():
    return MagicMock()


@pytest.fixture
def nissan_leaf_monitor(hass_mock):
    monitor = NissanLeafMonitor(hass_mock)
    monitor.evse_client_app = MagicMock()
    monitor.__log = MagicMock()
    return monitor


@pytest.mark.asyncio
async def test_handle_soc_change_skipped_min_soc(nissan_leaf_monitor):
    # Arrange
    new_soc = 19
    old_soc = 21
    c.CAR_MIN_SOC_IN_PERCENT = 20

    # Act
    await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Assert
    nissan_leaf_monitor.__log.assert_any_call(
        "_handle_soc_change: new_soc '19', old_soc '21'."
    )
    nissan_leaf_monitor.__log.assert_any_call("TA-TU-TA-TU! Skipped Min-SoC!")


@pytest.mark.asyncio
async def test_handle_soc_change_invalid_soc(nissan_leaf_monitor):
    # Arrange
    new_soc = "unknown"
    old_soc = 21

    # Act
    await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Assert
    nissan_leaf_monitor.__log.assert_any_call(
        "_handle_soc_change: new_soc 'unknown', old_soc '21'."
    )
    nissan_leaf_monitor.__log.assert_any_call(
        "Aborting: new_soc 'unknown' and/or old_soc '21' not an int."
    )


@pytest.mark.asyncio
async def test_handle_soc_change_no_skip(nissan_leaf_monitor):
    # Arrange
    new_soc = 22
    old_soc = 21
    c.CAR_MIN_SOC_IN_PERCENT = 20

    # Act
    await nissan_leaf_monitor._handle_soc_change(new_soc, old_soc)

    # Assert
    nissan_leaf_monitor.__log.assert_any_call(
        "_handle_soc_change: new_soc '22', old_soc '21'."
    )
    nissan_leaf_monitor.__log.assert_not_called("TA-TU-TA-TU! Skipped Min-SoC!")
