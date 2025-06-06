"""Unit test (pytest) for event_bus module."""

from unittest.mock import AsyncMock, patch
import pytest
from apps.v2g_liberty.event_bus import EventBus
from appdaemon.plugins.hass.hassapi import Hass

# pylint: disable=C0116,W0621,W0719
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)
# W0719 - Catching Exception is safe in event wrapper testing


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


def test_initialization(event_bus, hass):
    assert event_bus.hass == hass
    log_call = hass.log.call_args
    assert "EventBus initialized successfully." in get_log_message(log_call)
    assert log_call.kwargs.get("level") == "INFO"


def test_add_event_listener_and_emit_event(event_bus):
    called = []

    def mock_listener(*args, **kwargs):
        called.append((args, kwargs))

    event_bus.add_event_listener("test_event", mock_listener)
    event_bus.emit_event("test_event", "arg1", key="value")

    assert len(called) == 1
    assert called[0] == (("arg1",), {"key": "value"})


def test_add_event_listener_duplicate_warning(event_bus, hass):
    def mock_listener(*_args, **_kwargs):
        pass

    event_bus.add_event_listener("test_event", mock_listener)
    event_bus.add_event_listener("test_event", mock_listener)

    log_call = hass.log.call_args
    log_message = get_log_message(log_call)
    assert "already registered" in log_message
    assert log_call.kwargs.get("level") == "WARNING"


def test_remove_event_listener(event_bus):
    called = []

    def mock_listener(*_args, **_kwargs):
        called.append("called")

    event_bus.add_event_listener("test_event", mock_listener)
    event_bus.remove_event_listener("test_event", mock_listener)
    event_bus.emit_event("test_event")

    assert "called" not in called


def test_remove_event_listener_not_found(event_bus, hass):
    def mock_listener(*_args, **_kwargs):
        pass

    event_bus.remove_event_listener("test_event", mock_listener)
    log_call = hass.log.call_args
    assert "not found" in get_log_message(log_call)
    assert log_call.kwargs.get("level") == "WARNING"


def test_emit_event_no_listeners_logs_warning(event_bus, hass):
    event_bus.emit_event("no_listener_event", 1, test="x")
    log_call = hass.log.call_args
    assert "has no listeners" in get_log_message(log_call)
    assert log_call.kwargs.get("level") == "WARNING"


def test_emit_event_with_exception(event_bus, hass):
    def mock_listener(*args, **kwargs):
        raise Exception("Oops!")

    event_bus.add_event_listener("err_event", mock_listener)
    event_bus.emit_event("err_event", 1)

    log_call = hass.log.call_args
    assert "Error while emitting event 'err_event': Oops!" in get_log_message(log_call)
    assert log_call.kwargs.get("level") == "WARNING"


def test_add_event_listener_with_exception(event_bus, hass):
    def mock_listener(*_args, **_kwargs):
        pass

    with patch.object(event_bus, "listeners", side_effect=Exception("Boom!")):
        event_bus.add_event_listener("faulty_add", mock_listener)

    log_call = hass.log.call_args
    assert (
        "Error while adding listener to event 'faulty_add': Boom!"
        in get_log_message(log_call)
    )
    assert log_call.kwargs.get("level") == "WARNING"


def test_remove_event_listener_with_exception(event_bus, hass):
    def mock_listener(*_args, **_kwargs):
        pass

    with patch.object(event_bus, "listeners", side_effect=Exception("Boom!")):
        event_bus.remove_event_listener("faulty_remove", mock_listener)

    log_call = hass.log.call_args
    assert (
        "Error while removing listener from event 'faulty_remove': Boom!"
        in get_log_message(log_call)
    )
    assert log_call.kwargs.get("level") == "WARNING"
