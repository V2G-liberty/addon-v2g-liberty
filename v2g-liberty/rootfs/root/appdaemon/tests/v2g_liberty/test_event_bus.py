"""Unit test (pytest) for event_bus module."""

import logging
from unittest.mock import AsyncMock, patch
import pytest
from apps.v2g_liberty.event_bus import EventBus
from appdaemon.plugins.hass.hassapi import Hass

# pylint: disable=C0116,W0621,W0719
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)
# W0719 - Catching Exception is safe in event wrapper testing


@pytest.fixture
def hass():
    return AsyncMock(spec=Hass)


@pytest.fixture
def event_bus(hass):
    return EventBus(hass)


def test_initialization(hass, caplog):
    with caplog.at_level(logging.INFO):
        bus = EventBus(hass)
    assert bus.hass == hass
    assert "EventBus initialized successfully." in caplog.text


def test_add_event_listener_and_emit_event(event_bus):
    called = []

    def mock_listener(*args, **kwargs):
        called.append((args, kwargs))

    event_bus.add_event_listener("test_event", mock_listener)
    event_bus.emit_event("test_event", "arg1", key="value")

    assert len(called) == 1
    assert called[0] == (("arg1",), {"key": "value"})


def test_add_event_listener_duplicate_warning(event_bus, hass, caplog):
    def mock_listener(*_args, **_kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        event_bus.add_event_listener("test_event", mock_listener)
        event_bus.add_event_listener("test_event", mock_listener)

    assert "already registered" in caplog.text


def test_remove_event_listener(event_bus):
    called = []

    def mock_listener(*_args, **_kwargs):
        called.append("called")

    event_bus.add_event_listener("test_event", mock_listener)
    event_bus.remove_event_listener("test_event", mock_listener)
    event_bus.emit_event("test_event")

    assert "called" not in called


def test_remove_event_listener_not_found(event_bus, hass, caplog):
    def mock_listener(*_args, **_kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        event_bus.remove_event_listener("test_event", mock_listener)

    assert "not found" in caplog.text


def test_emit_event_no_listeners_logs_warning(event_bus, hass, caplog):
    with caplog.at_level(logging.WARNING):
        event_bus.emit_event("no_listener_event", 1, test="x")

    assert "has no listeners" in caplog.text


def test_emit_event_with_exception(event_bus, hass, caplog):
    def mock_listener(*args, **kwargs):
        raise Exception("Oops!")

    event_bus.add_event_listener("err_event", mock_listener)

    with caplog.at_level(logging.WARNING):
        event_bus.emit_event("err_event", 1)

    assert "err_event" in caplog.text
    assert "Oops!" in caplog.text


def test_add_event_listener_with_exception(event_bus, hass, caplog):
    def mock_listener(*_args, **_kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        with patch.object(event_bus, "listeners", side_effect=Exception("Boom!")):
            event_bus.add_event_listener("faulty_add", mock_listener)

    assert "Error while adding listener to event 'faulty_add': Boom!" in caplog.text


def test_remove_event_listener_with_exception(event_bus, hass, caplog):
    def mock_listener(*_args, **_kwargs):
        pass

    with caplog.at_level(logging.WARNING):
        with patch.object(event_bus, "listeners", side_effect=Exception("Boom!")):
            event_bus.remove_event_listener("faulty_remove", mock_listener)

    assert (
        "Error while removing listener from event 'faulty_remove': Boom!" in caplog.text
    )
