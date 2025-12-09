"""Unit test (pytest) for reservations_client module."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime
from apps.v2g_liberty.reservations_client import ReservationsClient
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle


@pytest.fixture
def event_bus():
    return AsyncMock(spec=EventBus)


@pytest.fixture
def mock_ev():
    ev = MagicMock(spec=ElectricVehicle)
    ev.max_range_km = 300
    ev.max_soc_percent = 80
    ev.soc_system_limit_percent = 97
    ev.min_soc_percent = 20
    return ev


# Mock the log_wrapper to avoid actual logging
@pytest.fixture
def mock_log_wrapper():
    with patch(
        "apps.v2g_liberty.reservations_client.get_class_method_logger",
        return_value=MagicMock(),
    ):
        yield


# Mock get_local_now
@pytest.fixture
def mock_get_local_now():
    with patch("apps.v2g_liberty.v2g_globals.get_local_now") as mock:
        mock.return_value = datetime(2025, 1, 1, 12, 0, 0)  # Example fixed time
        yield mock


@pytest.mark.parametrize(
    "summary, description, expected_soc",
    [
        ("Bla bla", "", 80),  # No number nor % or km, use default
        (None, None, 80),  # No summary or description use default
        ("", "", 80),  # No summary or description use default
        ("Bla bla km %", "", 80),  # No number use default
        ("Bla bla 13:55 ", "", 80),  # No % or km, use default
        ("Blabla 50%", "", 50),
        ("BlaBla 50 %", "", 50),  # Space between number and %-sign
        ("", "BlaBla 15%", 20),  # Should be raised to min SOC
        ("BlaBla 99%", "", 97),  # Should be lowered to max SOC
        ("", "75km ver", 25),  # 75km out of 300km range
        ("BlaBla 150km", "", 50),  # 150km out of 300km range
        ("Bla 150 KM", "", 50),  # 150km out of 300km range
        ("Blæblä 10km", "", 20),  # Should be raised to min SOC, no fail on diacrites
    ],
)
def test_add_target_soc(
    mock_log_wrapper,
    monkeypatch,
    mock_get_local_now,
    event_bus,
    mock_ev,
    summary,
    description,
    expected_soc,
):
    """Test the _add_target_soc method"""
    # Arrange
    hass = MagicMock()
    reservations_client = ReservationsClient(hass, event_bus=event_bus)
    reservations_client.set_vehicle(mock_ev)
    v2g_event = {"summary": summary, "description": description}

    # Act
    result = reservations_client._ReservationsClient__add_target_soc(v2g_event)

    # Assert
    assert result["target_soc_percent"] == expected_soc, (
        f"Test failed for summary: {summary}, description: {description}. "
        f"Expected {expected_soc}, but got {result['target_soc_percent']}."
    )
