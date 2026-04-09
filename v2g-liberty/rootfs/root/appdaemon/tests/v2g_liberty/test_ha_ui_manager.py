"""Unit test (pytest) for ha_ui_manager module."""

from unittest.mock import AsyncMock
import pytest
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.ha_ui_manager import HAUIManager
from appdaemon.plugins.hass.hassapi import Hass


@pytest.fixture
def hass():
    hass = AsyncMock(spec=Hass)
    hass.entity_exists.return_value = True

    # Create a coroutine for get_state that returns a dictionary
    async def mock_get_state(*args, **kwargs):
        return {"attributes": {}}

    hass.get_state.side_effect = mock_get_state
    hass.set_state = AsyncMock()
    return hass


@pytest.fixture
def event_bus():
    return AsyncMock(spec=EventBus)


@pytest.fixture
def ha_ui_manager(hass, event_bus):
    return HAUIManager(hass, event_bus)


@pytest.mark.asyncio
async def test_initialization(ha_ui_manager, event_bus):
    assert ha_ui_manager.hass is not None
    assert ha_ui_manager.event_bus is not None
    assert event_bus.add_event_listener.call_count == 9


@pytest.mark.asyncio
async def test_update_charger_communication_state(ha_ui_manager, hass):
    await ha_ui_manager._update_charger_communication_state(can_communicate=True)
    # Custom assertion to check the state and the presence of 'set_at' attribute
    awaited_args, awaited_kwargs = hass.set_state.await_args
    assert awaited_args[0] == "sensor.charger_connection_status"
    assert awaited_kwargs["state"] == "Successfully connected"
    assert "set_at" in awaited_kwargs["attributes"]


@pytest.mark.asyncio
async def test_update_charger_info(ha_ui_manager, hass):
    await ha_ui_manager._update_charger_info(charger_info="Charger Info")
    hass.set_state.assert_awaited_with(
        "sensor.charger_info", state="Charger Info", attributes={}
    )


@pytest.mark.asyncio
async def test_handle_charge_power_change(ha_ui_manager, hass):
    await ha_ui_manager._handle_charge_power_change(new_power=100)
    hass.set_state.assert_awaited_with(
        "sensor.charger_real_charging_power", state=100, attributes={}
    )


@pytest.mark.asyncio
async def test_handle_soc_change(ha_ui_manager, hass):
    await ha_ui_manager._handle_soc_change(new_soc=80, old_soc=70)
    hass.set_state.assert_awaited_with(
        "sensor.car_state_of_charge", state=80, attributes={}
    )


@pytest.mark.asyncio
async def test_handle_remaining_range_change(ha_ui_manager, hass):
    await ha_ui_manager._handle_remaining_range_change(remaining_range=200)
    hass.set_state.assert_awaited_with(
        "sensor.car_remaining_range", state=200, attributes={}
    )


@pytest.mark.asyncio
async def test_update_poll_indicator_in_ui(ha_ui_manager, hass):
    await ha_ui_manager._update_poll_indicator_in_ui(stop=False)
    hass.set_state.assert_awaited_with(
        "sensor.poll_refresh_indicator", state="↺", attributes={}
    )


@pytest.mark.asyncio
async def test_update_fm_connection_status(ha_ui_manager, hass):
    await ha_ui_manager._update_fm_connection_status(state="connected")
    # Custom assertion to check the state and the presence of 'set_at' attribute
    awaited_args, awaited_kwargs = hass.set_state.await_args
    assert awaited_args[0] == "sensor.fm_connection_status"
    assert awaited_kwargs["state"] == "connected"
    assert "set_at" in awaited_kwargs["attributes"]


@pytest.mark.asyncio
async def test_handle_charger_state_change(ha_ui_manager, hass):
    await ha_ui_manager._handle_charger_state_change(
        new_charger_state=2, old_charger_state=1, new_charger_state_str="Charging"
    )
    calls = hass.set_state.await_args_list
    assert len(calls) == 2
    assert calls[0][0][0] == "sensor.charger_state_int"
    assert calls[0][1]["state"] == 2
    assert calls[1][0][0] == "sensor.charger_state_text"
    assert calls[1][1]["state"] == "Charging"


@pytest.mark.asyncio
async def test_update_today_energy_sensors(ha_ui_manager, hass):
    await ha_ui_manager._update_today_energy_sensors(
        charge_kwh=5.5,
        charge_cost=1.23,
        discharge_kwh=3.2,
        discharge_revenue=0.89,
    )
    calls = hass.set_state.await_args_list
    assert len(calls) == 4
    assert calls[0][0][0] == "sensor.v2g_liberty_charged_today_kwh"
    assert calls[0][1]["state"] == 5.5
    assert calls[1][0][0] == "sensor.v2g_liberty_charge_cost_today"
    assert calls[1][1]["state"] == 1.23
    assert calls[2][0][0] == "sensor.v2g_liberty_discharged_today_kwh"
    assert calls[2][1]["state"] == 3.2
    assert calls[3][0][0] == "sensor.v2g_liberty_discharge_revenue_today"
    assert calls[3][1]["state"] == 0.89


@pytest.mark.asyncio
async def test_update_today_energy_sensors_with_zeros(ha_ui_manager, hass):
    await ha_ui_manager._update_today_energy_sensors(
        charge_kwh=0.0,
        charge_cost=0.0,
        discharge_kwh=0.0,
        discharge_revenue=0.0,
    )
    calls = hass.set_state.await_args_list
    assert len(calls) == 4
    assert calls[0][1]["state"] == 0.0
    assert calls[1][1]["state"] == 0.0
    assert calls[2][1]["state"] == 0.0
    assert calls[3][1]["state"] == 0.0
