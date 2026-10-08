"""Unit test (pytest) for reservations_client module."""

from datetime import timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.reservations_client import ReservationsClient


@pytest.fixture
def event_bus():
    return AsyncMock(spec=EventBus)


# Mock the log_wrapper to avoid actual logging
@pytest.fixture
def mock_log_wrapper():
    with patch(
        "apps.v2g_liberty.reservations_client.get_class_method_logger",
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
    reservations_client = ReservationsClient(hass, event_bus=event_bus)

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


@pytest.mark.asyncio
async def test_the_calendar_is_read_during_initialisation_not_on_a_timer(
    mock_log_wrapper, monkeypatch
):
    """AppDaemon discards timer callbacks that fire while initialize() still
    runs, and ours runs for close to a minute, so a "shortly after start-up"
    timer never ran and the first schedule did not know the reservations.
    The first read is therefore awaited directly in initialise_calendar."""
    from apps.v2g_liberty import constants as c

    monkeypatch.setattr(c, "TZ", timezone(timedelta(hours=1)), raising=False)
    monkeypatch.setattr(c, "CAR_CALENDAR_SOURCE", "localIntegration", raising=False)
    monkeypatch.setattr(
        c, "INTEGRATION_CALENDAR_ENTITY_NAME", "calendar.car", raising=False
    )
    hass = AsyncMock()
    hass.call_service = AsyncMock(
        return_value={"result": {"response": {"calendar.car": {"events": []}}}}
    )
    client = ReservationsClient(hass, event_bus=AsyncMock(spec=EventBus))
    recurring = AsyncMock(return_value="timer-1")
    one_shot = AsyncMock()

    with (
        patch("apps.v2g_liberty.reservations_client.set_recurring_timer", recurring),
        patch("apps.v2g_liberty.reservations_client.set_oneshot_timer", one_shot),
        patch.object(
            client, "_ReservationsClient__set_caldav_connection_status", AsyncMock()
        ),
    ):
        result = await client.initialise_calendar()

    assert result == "Successfully connected"
    hass.call_service.assert_awaited_once()  # the calendar was read, now
    one_shot.assert_not_awaited()  # and not left to a timer
    assert recurring.await_args.kwargs["start"] == "now"
    assert recurring.await_args.kwargs["interval"] == client.POLLING_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_the_first_read_retries_inline_when_the_answer_has_no_events_data(
    mock_log_wrapper, monkeypatch
):
    """A timer-based retry would be discarded during initialisation too."""
    client = _client_for_polling(monkeypatch)
    client.hass.call_service = AsyncMock(
        side_effect=[
            {"result": {}},
            {"result": {}},
            {"result": {"response": {"calendar.car": {"events": []}}}},
        ]
    )
    processed = AsyncMock()

    with (
        patch("apps.v2g_liberty.reservations_client.set_oneshot_timer", AsyncMock()),
        patch("apps.v2g_liberty.reservations_client.asyncio.sleep", AsyncMock()),
        patch.object(client, "_ReservationsClient__process_v2g_events", processed),
    ):
        await client._ReservationsClient__first_poll(
            client._ReservationsClient__poll_calendar_integration
        )

    assert client.hass.call_service.await_count == 3
    processed.assert_awaited_once_with([])


def _client_for_polling(monkeypatch):
    from datetime import timedelta, timezone

    from apps.v2g_liberty import constants as c

    monkeypatch.setattr(c, "TZ", timezone(timedelta(hours=1)), raising=False)

    monkeypatch.setattr(
        c, "INTEGRATION_CALENDAR_ENTITY_NAME", "calendar.car", raising=False
    )
    hass = AsyncMock()
    hass.log = MagicMock()
    client = ReservationsClient(hass, event_bus=AsyncMock(spec=EventBus))
    client.poll_retries_left = client.MAX_POLL_RETRIES
    return client


@pytest.mark.asyncio
async def test_a_poll_answered_without_events_data_is_retried(
    mock_log_wrapper, monkeypatch
):
    """Right after start-up AppDaemon may call the calendar service without
    asking for its response; the answer then has no 'response' key. That is
    not "no reservations": retry shortly instead of waiting for the next poll."""
    client = _client_for_polling(monkeypatch)
    client.hass.call_service = AsyncMock(return_value={"result": {}})
    retry = AsyncMock(return_value="timer-retry")
    processed = AsyncMock()

    with (
        patch("apps.v2g_liberty.reservations_client.set_oneshot_timer", retry),
        patch.object(client, "_ReservationsClient__process_v2g_events", processed),
    ):
        await client._ReservationsClient__poll_calendar_integration()

    processed.assert_not_awaited()
    retry.assert_awaited_once()
    assert retry.await_args.kwargs["delay"] == client.POLL_RETRY_DELAY_SECONDS
    assert client.poll_retries_left == client.MAX_POLL_RETRIES - 1


@pytest.mark.asyncio
async def test_retries_stop_after_the_budget(mock_log_wrapper, monkeypatch):
    client = _client_for_polling(monkeypatch)
    client.poll_retries_left = 0
    client.hass.call_service = AsyncMock(return_value={"result": {}})
    retry = AsyncMock()

    with patch("apps.v2g_liberty.reservations_client.set_oneshot_timer", retry):
        await client._ReservationsClient__poll_calendar_integration()

    retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_proper_answer_is_processed_and_resets_the_budget(
    mock_log_wrapper, monkeypatch
):
    client = _client_for_polling(monkeypatch)
    client.poll_retries_left = 1
    client.hass.call_service = AsyncMock(
        return_value={"result": {"response": {"calendar.car": {"events": []}}}}
    )
    retry = AsyncMock()
    processed = AsyncMock()

    with (
        patch("apps.v2g_liberty.reservations_client.set_oneshot_timer", retry),
        patch.object(client, "_ReservationsClient__process_v2g_events", processed),
    ):
        await client._ReservationsClient__poll_calendar_integration()

    processed.assert_awaited_once_with([])
    retry.assert_not_awaited()
    assert client.poll_retries_left == client.MAX_POLL_RETRIES
