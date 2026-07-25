"""Regression net for the charger driver's decode -> entity/event pipeline.

Drives the real ``modbus_evse_client`` with scenario register states (via a fake
Modbus client) and asserts the observable contract: the event_bus events it
emits and the entity values it caches. This pins the current behaviour so the
359 refactor (MBR/MCE dataclasses, EV split) can be verified to preserve it.

Heavy side-effect methods (the SoC-refresh dance, poll-strategy timers, charger
actions, error notifications) are patched so the tests focus on the decode/event
surface — the part that must survive the rewrite unchanged. Assert on that
boundary (events + cached entity values), not on the patched internals: that is
what keeps this net green across the refactor.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from apps.dev_tools.charger_scenarios import (
    REG_ACTUAL_POWER,
    REG_ERROR_1,
    REG_SOC,
    REG_STATE,
    STATE_CHARGING,
    STATE_DISCONNECTED,
    STATE_PAUSED,
    int16_to_uint16,
)
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.modbus_evse_client import ModbusEVSEclient

_EVENTS = [
    "soc_change",
    "remaining_range_change",
    "charge_power_change",
    "charger_state_change",
    "is_car_connected",
    "evse_polled",
    "update_charger_info",
    "charger_communication_state_change",
]


class FakeModbusClient:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.connected = True

    async def connect(self):
        self.connected = True
        return True

    async def read_holding_registers(self, address, count=1, device_id=1):
        regs = [self.store.get(address + i, 0) for i in range(count)]
        return SimpleNamespace(registers=regs, isError=lambda: False)

    async def write_register(self, address, value, device_id=1):
        self.store[address] = value
        return SimpleNamespace(isError=lambda: False)

    def close(self):
        pass


class Recorder:
    def __init__(self):
        self.events = []

    def subscribe(self, bus):
        for name in _EVENTS:
            bus.add_event_listener(name, self._make(name))

    def _make(self, name):
        def rec(*args, **kwargs):
            self.events.append((name, kwargs))

        return rec

    def find(self, name):
        return [kw for n, kw in self.events if n == name]


@pytest.fixture
def driver():
    hass = MagicMock()
    for m in ("set_state", "run_every", "run_in", "get_state", "cancel_timer"):
        setattr(hass, m, AsyncMock())
    bus = EventBus(hass)
    rec = Recorder()
    rec.subscribe(bus)

    e = ModbusEVSEclient(hass, bus, MagicMock())
    e.notifier = MagicMock()
    e.v2g_main_app = MagicMock()
    e.client = FakeModbusClient()
    e._am_i_active = True
    e.modbus_exception_counter = 0
    e.requested_charge_power = 0
    e._is_power_deviating = False
    e.try_get_new_soc_in_process = False

    # Reset the shared (class-level) entity caches to a clean baseline.
    for ent in (
        e.ENTITY_CHARGER_CURRENT_POWER,
        e.ENTITY_CHARGER_STATE,
        e.ENTITY_CAR_SOC,
        e.ENTITY_ERROR_1,
        e.ENTITY_ERROR_2,
        e.ENTITY_ERROR_3,
        e.ENTITY_ERROR_4,
        e.ENTITY_CHARGER_LOCKED,
    ):
        ent["current_value"] = None

    # Patch the heavy side-effects so the tests exercise the decode/event path
    # without real charger I/O, timers or notifications. What each one does:
    #  - __set_charger_action: writes a start/stop command to the charger.
    #  - __set_poll_strategy: cancels/(re)schedules the poll timer (AppDaemon).
    #  - __get_car_soc: the 1 W "SoC-refresh dance" (start -> read 538 -> stop).
    #  - __handle_charger_error_state_change: user notifications + grace timers.
    #  - get_car_remaining_range: range calc that needs runtime constants.
    e._ModbusEVSEclient__set_charger_action = AsyncMock()
    e._ModbusEVSEclient__set_poll_strategy = AsyncMock()
    e._ModbusEVSEclient__get_car_soc = AsyncMock(return_value=50)
    e._ModbusEVSEclient__handle_charger_error_state_change = AsyncMock()
    e.get_car_remaining_range = AsyncMock(return_value=100)

    return e, rec


def _process(e, entities):
    return e._ModbusEVSEclient__get_and_process_registers(entities)


def _update(e, entity, value):
    return e._ModbusEVSEclient__update_evse_entity(entity, value)


def _modbus_read(e, address, length):
    return e._ModbusEVSEclient__modbus_read(address, length)


def _base_poll(e):
    return e._ModbusEVSEclient__base_polling({})


# --- decode ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_modbus_read_decodes_twos_complement(driver):
    e, _ = driver
    e.client.store[REG_ACTUAL_POWER] = int16_to_uint16(-1000)
    assert await _modbus_read(e, REG_ACTUAL_POWER, 1) == [-1000]


# --- single-entity event contract ------------------------------------------
@pytest.mark.asyncio
async def test_power_change_emits_event_and_caches_value(driver):
    e, rec = driver
    await _update(e, e.ENTITY_CHARGER_CURRENT_POWER, 4000)
    assert e.ENTITY_CHARGER_CURRENT_POWER["current_value"] == 4000
    assert rec.find("charge_power_change")[-1]["new_power"] == 4000
    await _update(e, e.ENTITY_CHARGER_CURRENT_POWER, -2000)
    assert rec.find("charge_power_change")[-1]["new_power"] == -2000


@pytest.mark.asyncio
async def test_soc_change_emits_events(driver):
    e, rec = driver
    e.ENTITY_CAR_SOC["current_value"] = 50
    await _update(e, e.ENTITY_CAR_SOC, 55)
    assert e.ENTITY_CAR_SOC["current_value"] == 55
    assert rec.find("soc_change")[-1] == {"new_soc": 55, "old_soc": 50}
    assert rec.find("remaining_range_change")


# --- full poll -------------------------------------------------------------
@pytest.mark.asyncio
async def test_full_poll_charging(driver):
    e, rec = driver
    e.client.store.update(
        {REG_ACTUAL_POWER: 4000, REG_STATE: STATE_CHARGING, REG_SOC: 55}
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert e.ENTITY_CHARGER_CURRENT_POWER["current_value"] == 4000
    assert e.ENTITY_CHARGER_STATE["current_value"] == STATE_CHARGING
    assert e.ENTITY_CAR_SOC["current_value"] == 55
    assert rec.find("charge_power_change")[-1]["new_power"] == 4000
    assert rec.find("charger_state_change")[-1]["new_charger_state"] == STATE_CHARGING
    assert rec.find("soc_change")[-1]["new_soc"] == 55
    # None -> a connected state means the car just connected
    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True


@pytest.mark.asyncio
async def test_disconnect_marks_soc_unavailable_and_emits_event(driver):
    e, rec = driver
    e.ENTITY_CHARGER_STATE["current_value"] = STATE_CHARGING
    e.ENTITY_CAR_SOC["current_value"] = 55
    e.client.store.update(
        {REG_STATE: STATE_DISCONNECTED, REG_ACTUAL_POWER: 0, REG_SOC: 0}
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert rec.find("is_car_connected")[-1]["is_car_connected"] is False
    assert e.ENTITY_CAR_SOC["current_value"] == "unavailable"
    e._ModbusEVSEclient__set_charger_action.assert_awaited()  # explicit stop


@pytest.mark.asyncio
async def test_connect_emits_is_car_connected_true(driver):
    e, rec = driver
    e.ENTITY_CHARGER_STATE["current_value"] = STATE_DISCONNECTED
    e.client.store.update({REG_STATE: STATE_PAUSED, REG_ACTUAL_POWER: 0, REG_SOC: 55})
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True
    assert rec.find("charger_state_change")[-1]["new_charger_state"] == STATE_PAUSED


@pytest.mark.asyncio
async def test_soc_zero_while_connected_is_ignored(driver):
    e, rec = driver
    e.ENTITY_CHARGER_STATE["current_value"] = STATE_CHARGING  # connected
    e.ENTITY_CAR_SOC["current_value"] = 50
    e.client.store.update(
        {REG_STATE: STATE_CHARGING, REG_SOC: 0, REG_ACTUAL_POWER: 3000}
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    # A polled 0 while connected is a glitch: keep the last value, no event.
    assert e.ENTITY_CAR_SOC["current_value"] == 50
    assert not rec.find("soc_change")


@pytest.mark.asyncio
async def test_error_register_invokes_error_handler(driver):
    e, _ = driver
    e.ENTITY_CHARGER_STATE["current_value"] = STATE_CHARGING
    e.ENTITY_CHARGER_CURRENT_POWER["current_value"] = 3000
    e.ENTITY_CAR_SOC["current_value"] = 55
    e.ENTITY_ERROR_1["current_value"] = 0
    e.client.store.update(
        {
            REG_ACTUAL_POWER: 3000,
            REG_STATE: STATE_CHARGING,
            REG_SOC: 55,
            REG_ERROR_1: 1234,
        }
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)
    e._ModbusEVSEclient__handle_charger_error_state_change.assert_awaited()


@pytest.mark.asyncio
async def test_base_polling_emits_evse_polled(driver):
    e, rec = driver
    e.ENTITY_CHARGER_STATE["current_value"] = STATE_CHARGING
    e.client.store.update(
        {REG_ACTUAL_POWER: 3000, REG_STATE: STATE_CHARGING, REG_SOC: 55}
    )
    await _base_poll(e)
    assert rec.find("evse_polled")[-1]["stop"] is False
