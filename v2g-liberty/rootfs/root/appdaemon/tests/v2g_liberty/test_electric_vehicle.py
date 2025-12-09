"""Unit tests for ElectricVehicle class."""

import sys
import os

sys.path.insert(0, os.path.abspath("rootfs/root/appdaemon"))

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle
from apps.v2g_liberty.event_bus import EventBus


@pytest.fixture(autouse=True)
def restore_electric_vehicle(monkeypatch):
    """Ensure the real ElectricVehicle class is used in this test module."""
    modules_to_remove = [k for k in sys.modules.keys() if k.startswith("v2g_liberty")]
    for k in modules_to_remove:
        del sys.modules[k]
    monkeypatch.syspath_prepend(os.path.abspath("rootfs/root/appdaemon"))
    if "ElectricVehicle" in globals():
        del globals()["ElectricVehicle"]
    from apps.v2g_liberty.evs.electric_vehicle import ElectricVehicle

    globals()["ElectricVehicle"] = ElectricVehicle


@pytest.fixture
def mock_hass():
    """Mock Hass object."""
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock()
    hass.turn_on = AsyncMock()
    return hass


@pytest.fixture
def mock_event_bus():
    """Mock EventBus object."""
    return AsyncMock(spec=EventBus)


@pytest.mark.parametrize(
    "name, battery_capacity_kwh, charging_efficiency_percent, consumption_wh_per_km, min_soc_percent, max_soc_percent, "
    "expected_battery_capacity, expected_efficiency, expected_consumption, expected_min_soc, expected_max_soc",
    [
        # Valid values
        ("Test EV", 50, 80, 150, 20, 80, 50, 0.8, 150, 20, 80),
        # Battery capacity too low
        ("Test EV", 5, 80, 150, 20, 80, 10, 0.8, 150, 20, 80),
        # Battery capacity too high
        ("Test EV", 250, 80, 150, 20, 80, 200, 0.8, 150, 20, 80),
        # Charging efficiency too low
        ("Test EV", 50, 40, 150, 20, 80, 50, 0.5, 150, 20, 80),
        # Charging efficiency too high
        ("Test EV", 50, 110, 150, 20, 80, 50, 1.0, 150, 20, 80),
        # Consumption too low
        ("Test EV", 50, 80, 50, 20, 80, 50, 0.8, 100, 20, 80),
        # Consumption too high
        ("Test EV", 50, 80, 500, 20, 80, 50, 0.8, 400, 20, 80),
        # Min SoC too low
        ("Test EV", 50, 80, 150, 5, 80, 50, 0.8, 150, 10, 80),
        # Min SoC too high
        ("Test EV", 50, 80, 150, 60, 80, 50, 0.8, 150, 55, 80),
        # Max SoC too low
        ("Test EV", 50, 80, 150, 20, 50, 50, 0.8, 150, 20, 60),
        # Max SoC too high
        ("Test EV", 50, 80, 150, 20, 100, 50, 0.8, 150, 20, 95),
    ],
)
def test_initialise_ev(
    mock_hass,
    mock_event_bus,
    name,
    battery_capacity_kwh,
    charging_efficiency_percent,
    consumption_wh_per_km,
    min_soc_percent,
    max_soc_percent,
    expected_battery_capacity,
    expected_efficiency,
    expected_consumption,
    expected_min_soc,
    expected_max_soc,
):
    """Test ElectricVehicle.initialise_ev with various inputs."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    # Act
    ev.initialise_ev(
        name,
        battery_capacity_kwh,
        charging_efficiency_percent,
        consumption_wh_per_km,
        min_soc_percent,
        max_soc_percent,
    )
    # Assert
    assert ev.name == name
    assert ev._battery_capacity_kwh == expected_battery_capacity
    assert ev._charging_efficiency == expected_efficiency
    assert ev._consumption_wh_per_km == expected_consumption
    assert ev.min_soc_percent == expected_min_soc
    assert ev.max_soc_percent == expected_max_soc


@pytest.mark.asyncio
async def test_set_soc_emits_soc_changed_event(mock_hass, mock_event_bus):
    """Test that setting a new SoC emits the SOC_CHANGED_EVENT_NAME event."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    ev.initialise_ev("Test EV", 50, 80, 150, 20, 80)
    ev.set_soc(50.0)  # Set initial SoC
    mock_event_bus.emit_event.reset_mock()

    # Act
    ev.set_soc(60.0)  # Change SoC

    # Assert
    mock_event_bus.emit_event.assert_any_call(
        ev.SOC_CHANGED_EVENT_NAME, new_soc=60.0, old_soc=50.0
    )


@pytest.mark.asyncio
async def test_set_soc_emits_remaining_range_event(mock_hass, mock_event_bus):
    """Test that setting a new SoC emits the REMAINING_RANGE_EVENT_NAME event."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    ev.initialise_ev("Test EV", 50, 80, 150, 20, 80)
    ev.set_soc(50.0)  # Set initial SoC
    mock_event_bus.emit_event.reset_mock()

    # Act
    ev.set_soc(60.0)  # Change SoC

    # Assert
    mock_event_bus.emit_event.assert_any_call(
        ev.REMAINING_RANGE_EVENT_NAME, remaining_range=ev.remaining_range_km
    )


@pytest.mark.asyncio
async def test_set_soc_does_not_emit_event_if_same(mock_hass, mock_event_bus):
    """Test that setting the same SoC does not emit any events."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    ev.initialise_ev("Test EV", 50, 80, 150, 20, 80)
    ev.set_soc(50.0)  # Set initial SoC
    mock_event_bus.emit_event.reset_mock()

    # Act
    ev.set_soc(50.0)  # Set same SoC

    # Assert
    mock_event_bus.emit_event.assert_not_called()


@pytest.mark.asyncio
async def test_set_soc_does_not_emit_event_if_invalid(mock_hass, mock_event_bus):
    """Test that setting an invalid SoC does not emit any events."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    ev.initialise_ev("Test EV", 50, 80, 150, 20, 80)
    ev.set_soc(50.0)  # Set initial SoC
    mock_event_bus.emit_event.reset_mock()

    # Act
    ev.set_soc(-1.0)  # Set invalid SoC

    # Assert
    mock_event_bus.emit_event.assert_not_called()


@pytest.mark.asyncio
async def test_remaining_range_calculation_and_event(mock_hass, mock_event_bus):
    """Test that remaining range is calculated and event is emitted with correct value."""
    # Arrange
    ev = ElectricVehicle(mock_hass, mock_event_bus)
    ev.initialise_ev("Test EV", 50, 80, 150, 20, 80)
    ev.set_soc(50.0)  # Set SoC to 50%
    mock_event_bus.emit_event.reset_mock()

    # Act
    ev.set_soc(60.0)  # Change SoC to 60%

    # Assert
    expected_range = int(round((ev.soc_kwh * 1000 / ev._consumption_wh_per_km), 0))
    mock_event_bus.emit_event.assert_any_call(
        ev.REMAINING_RANGE_EVENT_NAME, remaining_range=expected_range
    )
