"""Unit test (pytest) for event_bus module."""

from unittest.mock import AsyncMock, Mock, patch
import pytest
from apps.v2g_liberty.event_bus import EventBus
from appdaemon.plugins.hass.hassapi import Hass


def get_log_message(log_call):
    """Extract the log message from the call arguments."""
    if log_call and log_call.args:
        return log_call.args[0]
    elif log_call and log_call.kwargs:
        return log_call.kwargs.get("msg", "")
    return ""


@pytest.fixture
def hass():
    return AsyncMock(spec=Hass)


@pytest.fixture
def event_bus(hass):
    return EventBus(hass)


@pytest.mark.asyncio
async def test_initialization(event_bus, hass):
    assert event_bus.hass == hass
    log_call = hass.log.call_args
    assert "EventBus initialized successfully." in get_log_message(log_call)
    assert log_call.kwargs.get("level") == "INFO"


@pytest.mark.asyncio
async def test_add_event_listener(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        pass

    with (
        patch.object(event_bus, "listeners", return_value=[]),
        patch.object(event_bus, "on", new_callable=Mock) as mock_on,
    ):
        event_bus.add_event_listener("test_event", mock_listener)
        mock_on.assert_called_with("test_event", mock_listener)

    with patch.object(event_bus, "listeners", return_value=[mock_listener]):
        with patch.object(event_bus, "on", new_callable=Mock) as mock_on:
            event_bus.add_event_listener("test_event", mock_listener)
            log_call = hass.log.call_args
            log_message = get_log_message(log_call)
            expected_substring = (
                "Listener '<function test_add_event_listener.<locals>.mock_listener at"
            )
            assert expected_substring in log_message
            assert log_call.kwargs.get("level") == "WARNING"


@pytest.mark.asyncio
async def test_remove_event_listener(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        pass

    with (
        patch.object(event_bus, "listeners", return_value=[mock_listener]),
        patch.object(event_bus, "remove_listener", new_callable=Mock) as mock_remove,
    ):
        event_bus.remove_event_listener("test_event", mock_listener)
        mock_remove.assert_called_with("test_event", mock_listener)

    with patch.object(event_bus, "listeners", return_value=[]):
        with patch.object(
            event_bus, "remove_listener", new_callable=Mock
        ) as mock_remove:
            event_bus.remove_event_listener("test_event", mock_listener)
            log_call = hass.log.call_args
            log_message = get_log_message(log_call)
            expected_substring = "Listener '<function test_remove_event_listener.<locals>.mock_listener at"
            assert expected_substring in log_message
            assert log_call.kwargs.get("level") == "WARNING"


@pytest.mark.asyncio
async def test_emit_event(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        pass

    with (
        patch.object(event_bus, "listeners", return_value=[mock_listener]),
        patch.object(event_bus, "emit", new_callable=Mock) as mock_emit,
    ):
        event_bus.emit_event("test_event", "arg1", "arg2", kwarg1="value1")
        mock_emit.assert_called_with("test_event", "arg1", "arg2", kwarg1="value1")

    with patch.object(event_bus, "listeners", return_value=[]):
        with patch.object(event_bus, "emit", new_callable=Mock) as mock_emit:
            event_bus.emit_event("test_event", "arg1", "arg2", kwarg1="value1")
            log_call = hass.log.call_args
            assert "Event 'test_event' has no listeners." in get_log_message(log_call)
            assert log_call.kwargs.get("level") == "WARNING"


@pytest.mark.asyncio
async def test_emit_event_with_exception(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        raise Exception("Test exception")

    with (
        patch.object(event_bus, "listeners", return_value=[mock_listener]),
        patch.object(
            event_bus, "emit", side_effect=Exception("Test exception")
        ) as mock_emit,
    ):
        event_bus.emit_event("test_event", "arg1", "arg2", kwarg1="value1")
        log_call = hass.log.call_args
        assert (
            "Error while emitting event 'test_event': Test exception"
            in get_log_message(log_call)
        )
        assert log_call.kwargs.get("level") == "WARNING"


@pytest.mark.asyncio
async def test_add_event_listener_with_exception(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        pass

    with patch.object(event_bus, "listeners", side_effect=Exception("Test exception")):
        event_bus.add_event_listener("test_event", mock_listener)
        log_call = hass.log.call_args
        assert (
            "Error while adding listener to event 'test_event': Test exception"
            in get_log_message(log_call)
        )
        assert log_call.kwargs.get("level") == "WARNING"


@pytest.mark.asyncio
async def test_remove_event_listener_with_exception(event_bus, hass):
    async def mock_listener(*args, **kwargs):
        pass

    with patch.object(event_bus, "listeners", side_effect=Exception("Test exception")):
        event_bus.remove_event_listener("test_event", mock_listener)
        log_call = hass.log.call_args
        assert (
            "Error while removing listener from event 'test_event': Test exception"
            in get_log_message(log_call)
        )
        assert log_call.kwargs.get("level") == "WARNING"
