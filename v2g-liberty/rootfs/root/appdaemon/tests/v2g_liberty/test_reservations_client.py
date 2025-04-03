import pytest
from unittest.mock import MagicMock, patch
from apps.v2g_liberty.reservations_client import ReservationsClient
import constants as c


# Mock the log_wrapper to avoid actual logging
@pytest.fixture
def mock_log_wrapper():
    with patch(
        "apps.v2g_liberty.reservations_client.log_wrapper.get_class_method_logger",
        return_value=MagicMock(),
    ):
        yield


# Test cases
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
    mock_log_wrapper, monkeypatch, summary, description, expected_soc
):
    """Test the _add_target_soc method
    Assumed defaults in constants.py
    c.CAR_MAX_SOC_IN_PERCENT = 80
    c.CAR_MAX_CAPACITY_IN_PERCENT = 97
    c.CAR_MIN_SOC_IN_PERCENT = 20
    """

    # Arrange
    hass = MagicMock()
    reservations_client = ReservationsClient(hass)

    # TODO: The ReservationsClient should not have a method to set/get_constant_range_in_km
    # but it is the only way i could get this to work.
    org_value = reservations_client.get_constant_range_in_km()
    reservations_client.set_constant_range_in_km(300)

    v2g_event = {"summary": summary, "description": description}

    # Act
    result = reservations_client._ReservationsClient__add_target_soc(v2g_event)

    # Assert
    assert result["target_soc_percent"] == expected_soc, (
        f"Test failed for summary: {summary}, description: {description}. Expected {expected_soc}, but got {result['target_soc_percent']}."
    )

    # TODO: The ReservationsClient should not have a method set_constant_range_in_km
    # but it is the only way i could get this to work.
    reservations_client.set_constant_range_in_km(org_value)
