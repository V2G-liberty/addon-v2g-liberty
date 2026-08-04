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
from pymodbus.exceptions import ModbusException

from apps.dev_tools.charger_scenarios import (
    REG_ACTUAL_POWER,
    REG_ERROR_1,
    REG_LOCKED,
    REG_SOC,
    REG_STATE,
    STATE_CHARGING,
    STATE_DISCHARGING,
    STATE_DISCONNECTED,
    STATE_ERROR,
    STATE_LOCKED,
    STATE_PAUSED,
    int16_to_uint16,
)
import apps.v2g_liberty.constants as c
from apps.v2g_liberty.event_bus import EventBus
from apps.v2g_liberty.chargers.wallbox_quasar_1 import WallboxQuasar1Client

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
        # Fault injection for the comms-loss tests. The real driver only reacts
        # to a raised ModbusException (it never inspects isError() and treats a
        # None/short return as success), so "raise" is the only way to drive the
        # __handle_modbus_exception path. Default None keeps the base tests green.
        self.fault = None

    async def connect(self):
        self.connected = True
        return True

    async def read_holding_registers(self, address, count=1, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        regs = [self.store.get(address + i, 0) for i in range(count)]
        if self.fault == "short" and regs:
            # Return fewer registers than requested to drive the partial-result path.
            regs = regs[:-1]
        return SimpleNamespace(registers=regs, isError=lambda: False)

    async def write_register(self, address, value, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
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

    e = WallboxQuasar1Client(hass, bus, MagicMock())
    e.notifier = MagicMock()
    e.v2g_main_app = MagicMock()
    # Inject the fake as the transport's low-level pymodbus client. The charger
    # routes its raw reads/writes through self._mb_client, which delegates to
    # _mbc; the tests keep addressing the same fake via e.client (store/fault).
    e.client = FakeModbusClient()
    e._mb_client._mbc = e.client
    e._am_i_active = True
    e.modbus_exception_counter = 0
    e.requested_charge_power = 0
    e._is_power_deviating = False
    e.try_get_new_soc_in_process = False

    # Reset the shared (class-level) entity caches to a clean baseline.
    for ent in (
        e._MCE_ACTUAL_POWER,
        e._MCE_CHARGER_STATE,
        e._MCE_CAR_SOC,
        e._MCE_ERROR_1,
        e._MCE_ERROR_2,
        e._MCE_ERROR_3,
        e._MCE_ERROR_4,
        e._MCE_CHARGER_LOCKED,
    ):
        ent.current_value = None

    # Patch the heavy side-effects so the tests exercise the decode/event path
    # without real charger I/O, timers or notifications. What each one does:
    #  - __set_charger_action: writes a start/stop command to the charger.
    #  - __set_poll_strategy: cancels/(re)schedules the poll timer (AppDaemon).
    #  - __get_car_soc: the 1 W "SoC-refresh dance" (start -> read 538 -> stop).
    #  - __handle_charger_error_state_change: user notifications + grace timers.
    #  - get_car_remaining_range: range calc that needs runtime constants.
    e._set_charger_action = AsyncMock()
    e._set_poll_strategy = AsyncMock()
    e._get_car_soc = AsyncMock(return_value=50)
    e._handle_charger_error_state_change = AsyncMock()
    e.get_car_remaining_range = AsyncMock(return_value=100)

    return e, rec


def _process(e, entities):
    return e._get_and_process_registers(entities)


def _update(e, entity, value):
    return e._update_evse_entity(entity, value)


def _modbus_read(e, address, length):
    return e._modbus_read(address, length)


def _modbus_write(e, address, value):
    return e._modbus_write(address, value, "test")


def _handle_error_state(e, **kw):
    return e._handle_charger_error_state_change(kw)


def _base_poll(e):
    return e._base_polling({})


def _minimal_poll(e):
    return e._minimal_polling({})


def _get_soc(e, **kw):
    return e._get_car_soc(**kw)


def _force_get(e, address, min_at, max_at, min_after=None, max_after=None):
    return e._force_get_register(address, min_at, max_at, min_after, max_after)


def _handle_power(e, new_power):
    return e._handle_charge_power_change(new_power)


def _set_poll_strategy(e):
    # Bypass the fixture's instance-level AsyncMock and run the REAL class method.
    return WallboxQuasar1Client._set_poll_strategy(e)


def _comm_states(rec):
    return [
        kw["can_communicate"] for kw in rec.find("charger_communication_state_change")
    ]


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
    await _update(e, e._MCE_ACTUAL_POWER, 4000)
    assert e._MCE_ACTUAL_POWER.current_value == 4000
    assert rec.find("charge_power_change")[-1]["new_power"] == 4000
    await _update(e, e._MCE_ACTUAL_POWER, -2000)
    assert rec.find("charge_power_change")[-1]["new_power"] == -2000


@pytest.mark.asyncio
async def test_soc_change_emits_events(driver):
    e, rec = driver
    e._MCE_CAR_SOC.current_value = 50
    await _update(e, e._MCE_CAR_SOC, 55)
    assert e._MCE_CAR_SOC.current_value == 55
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

    assert e._MCE_ACTUAL_POWER.current_value == 4000
    assert e._MCE_CHARGER_STATE.current_value == STATE_CHARGING
    assert e._MCE_CAR_SOC.current_value == 55
    assert rec.find("charge_power_change")[-1]["new_power"] == 4000
    assert rec.find("charger_state_change")[-1]["new_charger_state"] == STATE_CHARGING
    assert rec.find("soc_change")[-1]["new_soc"] == 55
    # None -> a connected state means the car just connected
    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True


@pytest.mark.asyncio
async def test_disconnect_marks_soc_unavailable_and_emits_event(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_CAR_SOC.current_value = 55
    e.client.store.update(
        {REG_STATE: STATE_DISCONNECTED, REG_ACTUAL_POWER: 0, REG_SOC: 0}
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert rec.find("is_car_connected")[-1]["is_car_connected"] is False
    assert e._MCE_CAR_SOC.current_value == "unavailable"
    e._set_charger_action.assert_awaited()  # explicit stop


@pytest.mark.asyncio
async def test_connect_emits_is_car_connected_true(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_DISCONNECTED
    e.client.store.update({REG_STATE: STATE_PAUSED, REG_ACTUAL_POWER: 0, REG_SOC: 55})
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True
    assert rec.find("charger_state_change")[-1]["new_charger_state"] == STATE_PAUSED


@pytest.mark.asyncio
async def test_soc_zero_while_connected_is_ignored(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING  # connected
    e._MCE_CAR_SOC.current_value = 50
    e.client.store.update(
        {REG_STATE: STATE_CHARGING, REG_SOC: 0, REG_ACTUAL_POWER: 3000}
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)

    # A polled 0 while connected is a glitch: keep the last value, no event.
    assert e._MCE_CAR_SOC.current_value == 50
    assert not rec.find("soc_change")


@pytest.mark.asyncio
async def test_error_register_invokes_error_handler(driver):
    e, _ = driver
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_ACTUAL_POWER.current_value = 3000
    e._MCE_CAR_SOC.current_value = 55
    e._MCE_ERROR_1.current_value = 0
    e.client.store.update(
        {
            REG_ACTUAL_POWER: 3000,
            REG_STATE: STATE_CHARGING,
            REG_SOC: 55,
            REG_ERROR_1: 1234,
        }
    )
    await _process(e, e.CHARGER_POLLING_ENTITIES)
    e._handle_charger_error_state_change.assert_awaited()


@pytest.mark.asyncio
async def test_base_polling_emits_evse_polled(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e.client.store.update(
        {REG_ACTUAL_POWER: 3000, REG_STATE: STATE_CHARGING, REG_SOC: 55}
    )
    await _base_poll(e)
    assert rec.find("evse_polled")[-1]["stop"] is False


# ===========================================================================
# EXPANDED COVERAGE (Fase 0): the lifecycle paths the base net patches away.
# These drive the REAL __get_car_soc / __force_get_register, the comms-loss /
# recovery / un-recoverable escalation, the poll-strategy selection and the
# power-deviation logic, asserting on the same observable boundary (events +
# cached entity values + the direct v2g_main_app / notifier calls the driver
# makes instead of an event). Un-patching is done per-test in the body so the
# 9 base tests above stay untouched.
# ===========================================================================


# --- SoC-refresh dance (real __get_car_soc) --------------------------------
@pytest.mark.asyncio
async def test_get_car_soc_dance_connected_not_charging(driver):
    """Connected but idle -> the 1 W start/read/stop dance runs and force-emits."""
    e, rec = driver
    # Run the real SoC method; keep the heavy action/poll timers stubbed.
    del e._get_car_soc
    e._MCE_CHARGER_STATE.current_value = STATE_PAUSED  # connected, not charging
    e._MCE_CAR_SOC.current_value = None
    e.client.store[REG_SOC] = 55  # in the strict [2, 97] window -> accepted at once

    result = await _get_soc(e, do_not_use_cache=True)

    assert result == 55
    assert e._MCE_CAR_SOC.current_value == 55
    assert rec.find("soc_change")[-1] == {"new_soc": 55, "old_soc": None}
    assert rec.find("remaining_range_change")
    assert e.try_get_new_soc_in_process is False
    # The dance starts a 1 W charge then stops it again.
    calls = e._set_charger_action.await_args_list
    assert calls[0].args[0] == "start"
    assert calls[-1].args[0] == "stop"
    assert _comm_states(rec)[-1] is True
    # Polling is paused for the duration of the dance and restored afterwards.
    assert {"stop": True} in rec.find("evse_polled")
    e._set_poll_strategy.assert_awaited()


@pytest.mark.asyncio
async def test_get_car_soc_direct_read_when_already_charging(driver):
    """Already (dis)charging -> a plain force-read, NO action/poll churn."""
    e, rec = driver
    del e._get_car_soc
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_CAR_SOC.current_value = None
    e.client.store[REG_SOC] = 55

    result = await _get_soc(e, do_not_use_cache=True)

    assert result == 55
    assert e._MCE_CAR_SOC.current_value == 55
    assert rec.find("soc_change")[-1]["new_soc"] == 55
    e._set_charger_action.assert_not_awaited()
    e._set_poll_strategy.assert_not_awaited()
    assert e.try_get_new_soc_in_process is False


@pytest.mark.asyncio
async def test_get_car_soc_force_emit_vs_cache_return(driver):
    """force_emit == do_not_use_cache: cached read is silent, forced re-emits."""
    e, rec = driver
    del e._get_car_soc
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_CAR_SOC.current_value = 55
    e.client.store[REG_SOC] = 55

    # Cache hit: no modbus read, no event.
    r1 = await _get_soc(e, do_not_use_cache=False)
    assert r1 == 55
    assert rec.find("soc_change") == []

    # Forced refresh re-emits even though the value is unchanged.
    r2 = await _get_soc(e, do_not_use_cache=True)
    assert r2 == 55
    assert rec.find("soc_change")[-1] == {"new_soc": 55, "old_soc": 55}


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnected_state", [STATE_DISCONNECTED, STATE_LOCKED])
async def test_get_car_soc_not_connected_returns_unavailable(
    driver, disconnected_state
):
    """No car -> SoC/kwh/range all short-circuit to 'unavailable', no event."""
    e, rec = driver
    del e._get_car_soc
    del e.get_car_remaining_range  # run the real range wrapper too
    e._MCE_CHARGER_STATE.current_value = disconnected_state

    assert await _get_soc(e, do_not_use_cache=True) == "unavailable"
    assert await _get_soc(e, do_not_use_cache=False) == "unavailable"
    assert rec.find("soc_change") == []
    assert await e.get_car_soc_kwh() == "unavailable"
    assert await e.get_car_remaining_range() == "unavailable"


@pytest.mark.asyncio
async def test_get_car_soc_none_coerced_to_unavailable(driver):
    """None from the (here-stubbed) forced read is coerced/cached as 'unavailable'.

    The forced-read loop is stubbed to isolate the None -> 'unavailable' coercion
    (driver 826-827) and the resulting cache + soc_change emit; the loop's own
    timeout->None outcome is pinned by test_force_get_register_timeout_*.
    """
    e, rec = driver
    del e._get_car_soc
    e._force_get_register = AsyncMock(return_value=None)
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_CAR_SOC.current_value = 55

    assert await _get_soc(e, do_not_use_cache=True) == "unavailable"
    assert e._MCE_CAR_SOC.current_value == "unavailable"
    assert rec.find("soc_change")[-1] == {"new_soc": "unavailable", "old_soc": 55}
    assert rec.find("remaining_range_change")


# --- __force_get_register (real forced-read loop) --------------------------
@pytest.mark.asyncio
async def test_force_get_register_accepts_first_valid_read(driver):
    """A value in the strict window is accepted on the first iteration."""
    e, rec = driver
    e.client.store[REG_SOC] = 55
    result = await _force_get(e, REG_SOC, 2, 97, 1, 100)
    assert result == 55
    assert _comm_states(rec)[-1] is True


@pytest.mark.asyncio
async def test_force_get_register_relaxed_accept_at_timeout(driver):
    """At timeout a value inside the relaxed window is still accepted."""
    e, rec = driver
    e.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS = 0  # force the timeout branch
    e.client.store[REG_SOC] = 100  # outside [2,97], inside relaxed [1,100]

    result = await _force_get(e, REG_SOC, 2, 97, 1, 100)

    assert result == 100
    assert _comm_states(rec)[-1] is True
    assert e._am_i_active is True  # no escalation


@pytest.mark.asyncio
async def test_force_get_register_timeout_returns_none_and_escalates(driver):
    """No acceptable value at timeout -> None + full un-recoverable escalation."""
    e, rec = driver
    e.v2g_main_app = AsyncMock()
    e.MAX_CHARGER_ERROR_STATE_DURATION_IN_SECONDS = 0
    e.client.store[REG_SOC] = 0  # outside strict AND relaxed windows
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING  # was connected

    result = await _force_get(e, REG_SOC, 2, 97, 1, 100)

    assert result is None
    assert e._am_i_active is False
    e.v2g_main_app.handle_none_responsive_charger.assert_awaited_once_with(
        was_car_connected=True
    )
    assert _comm_states(rec)[-1] is False
    assert rec.find("evse_polled")[-1]["stop"] is True
    assert e._MCE_ACTUAL_POWER.current_value == "unavailable"
    assert e._MCE_CAR_SOC.current_value == "unavailable"
    assert rec.find("charge_power_change")[-1] == {"new_power": 0}
    assert rec.find("soc_change")[-1]["new_soc"] == "unavailable"


# --- comms-loss / recovery / bad-config ------------------------------------
@pytest.mark.asyncio
async def test_first_modbus_exception_arms_grace_timer(driver):
    """First failure after init is recoverable: it only arms the grace timer."""
    e, rec = driver
    e.v2g_main_app = AsyncMock()
    e.client.fault = "raise"

    assert await _modbus_read(e, REG_STATE, 1) is None
    assert e.modbus_exception_counter == 1
    # A one-shot timer to __handle_un_recoverable_error was armed with delay 60.
    e.hass.run_in.assert_awaited_once()
    assert e.hass.run_in.await_args.args[0] == (e._handle_un_recoverable_error)
    assert e.hass.run_in.await_args.kwargs["delay"] == 60
    # Still recoverable: no comms-fault, no user notification yet.
    assert _comm_states(rec) == []
    e.notifier.post_sticky_memo.assert_not_called()


@pytest.mark.asyncio
async def test_modbus_exception_recovers_before_timer(driver):
    """A successful read after one failure resets the fault (Boundary R)."""
    e, rec = driver
    e.v2g_main_app = AsyncMock()

    e.client.fault = "raise"
    assert await _modbus_read(e, REG_STATE, 1) is None
    assert e.modbus_exception_counter == 1

    e.client.fault = None
    e.client.store[REG_STATE] = STATE_CHARGING
    res = await _modbus_read(e, REG_STATE, 1)

    assert res == [STATE_CHARGING]
    e.v2g_main_app.reset_charger_communication_fault.assert_awaited_once()
    assert e.modbus_exception_counter == 0
    assert e.timer_id_check_modus_exception_state is None
    assert _comm_states(rec)[-1] is True


@pytest.mark.asyncio
async def test_grace_timer_firing_escalates_to_unrecoverable(driver):
    """When the armed grace timer fires, it escalates to un-recoverable."""
    e, rec = driver
    e.v2g_main_app = AsyncMock()
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING

    e.client.fault = "raise"
    await _modbus_read(e, REG_STATE, 1)  # arm the timer
    escalation_cb = e.hass.run_in.await_args.args[0]
    assert escalation_cb == e._handle_un_recoverable_error

    await escalation_cb()  # simulate the timer firing

    assert rec.find("evse_polled")[-1]["stop"] is True
    assert e._am_i_active is False
    e.v2g_main_app.handle_none_responsive_charger.assert_awaited_once_with(
        was_car_connected=True
    )
    assert _comm_states(rec)[-1] is False
    assert e._MCE_ACTUAL_POWER.current_value == "unavailable"
    assert e._MCE_CAR_SOC.current_value == "unavailable"


@pytest.mark.asyncio
async def test_bad_modbus_config_posts_sticky_memo(driver):
    """A failure before the first success (counter None) is a config problem."""
    e, rec = driver
    e.modbus_exception_counter = None  # never successfully connected yet
    e.client.fault = "raise"

    assert await _modbus_read(e, REG_STATE, 1) is None

    e.notifier.post_sticky_memo.assert_called_once()
    assert (
        e.notifier.post_sticky_memo.call_args.kwargs["memo_id"] == "no_comm_with_evse"
    )
    assert rec.find("evse_polled")[-1]["stop"] is True
    assert _comm_states(rec) == []  # no comms-state emit on the config path


# --- poll strategy (real __set_poll_strategy) ------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("disconnected_state", [STATE_DISCONNECTED, STATE_LOCKED])
async def test_set_poll_strategy_minimal_when_disconnected(driver, disconnected_state):
    """Disconnected -> minimal polling (15 s), and a cancel-first teardown."""
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = disconnected_state

    await _set_poll_strategy(e)

    e.hass.run_every.assert_awaited_once()
    assert e.hass.run_every.await_args.args[0] == e._minimal_polling
    assert e.hass.run_every.await_args.args[2] == 15
    assert e.poll_timer_handle is e.hass.run_every.return_value
    polled = rec.find("evse_polled")
    assert polled == [{"stop": True}]  # cancel-first only; run_every does not fire


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "connected_state", [STATE_CHARGING, STATE_PAUSED, STATE_DISCHARGING]
)
async def test_set_poll_strategy_base_when_connected(driver, connected_state):
    """Any connected state -> base polling (5 s)."""
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = connected_state

    await _set_poll_strategy(e)

    assert e.hass.run_every.await_args.args[0] == e._base_polling
    assert e.hass.run_every.await_args.args[2] == 5
    assert e.poll_timer_handle is e.hass.run_every.return_value
    assert rec.find("evse_polled")[-1]["stop"] is True


@pytest.mark.asyncio
async def test_set_poll_strategy_unavailable_state_coerced_to_minimal(driver):
    """An 'unavailable' charger state is treated as disconnected -> minimal."""
    e, _ = driver
    e._MCE_CHARGER_STATE.current_value = "unavailable"

    await _set_poll_strategy(e)

    assert e.hass.run_every.await_args.args[0] == e._minimal_polling
    assert e.hass.run_every.await_args.args[2] == 15


@pytest.mark.asyncio
async def test_set_poll_strategy_suppressed_during_soc_dance(driver):
    """While a forced-SoC read is running, poll strategy is a no-op."""
    e, rec = driver
    e.try_get_new_soc_in_process = True
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING

    await _set_poll_strategy(e)

    e.hass.run_every.assert_not_awaited()
    assert rec.find("evse_polled") == []


@pytest.mark.asyncio
async def test_minimal_polling_emits_stop_false_and_skips_soc_and_power(driver):
    """Minimal poll reads only state+lock; a disconnected car still ticks stop=False."""
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_DISCONNECTED  # no state change
    e.client.store.update({REG_STATE: STATE_DISCONNECTED, REG_LOCKED: 0})

    await _minimal_poll(e)

    assert (
        rec.find("evse_polled")[-1]["stop"] is False
    )  # teardown flag, not "disconnected"
    assert e._MCE_CAR_SOC.current_value is None  # 538 never read
    assert e._MCE_ACTUAL_POWER.current_value is None  # 526 never read
    assert e._MCE_CHARGER_LOCKED.current_value == 0  # 256 read, cached, no event


# --- power deviation (>500 W flag, no event/action) ------------------------
@pytest.mark.asyncio
async def test_power_deviation_flag_thresholds(driver):
    """Deviation is a strict >500 W flag; it fires no event and no charger action."""
    e, rec = driver

    e.requested_charge_power = 3000
    e._is_power_deviating = False
    await _handle_power(e, 3600)  # delta 600
    assert e._is_power_deviating is True
    assert rec.find("charge_power_change")[-1] == {"new_power": 3600}

    e._is_power_deviating = False
    e.requested_charge_power = 3000
    await _handle_power(e, 3500)  # delta exactly 500 -> not deviating (strict >)
    assert e._is_power_deviating is False

    e._is_power_deviating = False
    e.requested_charge_power = 3000
    await _handle_power(e, 2450)  # delta 550
    assert e._is_power_deviating is True

    # The deviation handler emits ONLY charge_power_change: no charger action and
    # no side-effect event (no charger_state_change / is_car_connected / evse_polled).
    assert {n for n, _ in rec.events} == {"charge_power_change"}


@pytest.mark.asyncio
async def test_power_deviation_edge_transitions_and_non_numeric(driver):
    """Rising/falling edges flip the flag; a non-numeric power is coerced to 0."""
    e, rec = driver

    e._is_power_deviating = True
    e.requested_charge_power = 3000
    await _handle_power(e, 3100)  # delta 100 -> resolves
    assert e._is_power_deviating is False
    assert rec.find("charge_power_change")[-1]["new_power"] == 3100

    await _handle_power(e, 4000)  # delta 1000 -> deviates again
    assert e._is_power_deviating is True
    assert rec.find("charge_power_change")[-1]["new_power"] == 4000

    e._is_power_deviating = False
    e.requested_charge_power = 0
    await _handle_power(e, "unavailable")  # coerced to 0 BEFORE emit
    assert rec.find("charge_power_change")[-1] == {"new_power": 0}
    assert e._is_power_deviating is False


@pytest.mark.asyncio
async def test_base_poll_power_deviation_integration(driver):
    """Deviation surfaces through a full base poll and clears when power realigns."""
    e, rec = driver
    e.requested_charge_power = 0
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e.client.store.update(
        {
            REG_ACTUAL_POWER: int16_to_uint16(4000),
            REG_STATE: STATE_CHARGING,
            REG_SOC: 55,
        }
    )

    await _base_poll(e)

    assert e._MCE_ACTUAL_POWER.current_value == 4000
    assert rec.find("charge_power_change")[-1] == {"new_power": 4000}
    assert e._is_power_deviating is True  # 4000 vs requested 0
    assert rec.find("evse_polled")[-1]["stop"] is False

    # Realign the request; a new actual within 500 W clears the flag.
    e.requested_charge_power = 4000
    e.client.store[REG_ACTUAL_POWER] = int16_to_uint16(4200)
    await _base_poll(e)

    assert rec.find("charge_power_change")[-1] == {"new_power": 4200}
    assert e._is_power_deviating is False


# --- charger-error-state escalation (real __handle_charger_error_state_change)
# One of the four documented "none-responsive" triggers; fully patched in the
# base fixture, so unpin it here (register-decode-adjacent -> matters for the
# MBR/MCE rewrite).
@pytest.mark.asyncio
async def test_charger_error_state_arms_final_check_timer(driver):
    """A non-zero error register arms a one-shot final-check, still recoverable."""
    e, _ = driver
    del e._handle_charger_error_state_change
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_ERROR_1.current_value = 1234  # charger reports an error

    await _handle_error_state(e, new_charger_state=None, is_final_check=False)

    e.hass.run_in.assert_awaited_once()
    assert e.hass.run_in.await_args.args[0] == (e._handle_charger_error_state_change)
    assert e.hass.run_in.await_args.kwargs["delay"] == 60
    assert e.hass.run_in.await_args.kwargs["is_final_check"] is True
    assert e.timer_id_check_error_state is e.hass.run_in.return_value
    assert e._am_i_active is True  # not yet escalated


@pytest.mark.asyncio
async def test_charger_error_final_check_escalates_to_unrecoverable(driver):
    """The final check on a persistent error escalates to un-recoverable."""
    e, rec = driver
    del e._handle_charger_error_state_change
    e.v2g_main_app = AsyncMock()
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e._MCE_ERROR_1.current_value = 1234

    await _handle_error_state(e, new_charger_state=None, is_final_check=True)

    assert rec.find("evse_polled")[-1]["stop"] is True
    assert e._am_i_active is False
    e.v2g_main_app.handle_none_responsive_charger.assert_awaited_once_with(
        was_car_connected=True
    )
    assert _comm_states(rec)[-1] is False
    assert e._MCE_ACTUAL_POWER.current_value == "unavailable"
    assert e._MCE_CAR_SOC.current_value == "unavailable"


@pytest.mark.asyncio
async def test_charger_error_cleared_cancels_timer(driver):
    """When no error is present any more, the pending final-check timer is cleared."""
    e, _ = driver
    del e._handle_charger_error_state_change
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING  # not an error state
    for err in (e._MCE_ERROR_1, e._MCE_ERROR_2, e._MCE_ERROR_3, e._MCE_ERROR_4):
        err.current_value = 0
    e.timer_id_check_error_state = MagicMock()  # a timer is pending

    await _handle_error_state(e, new_charger_state=None, is_final_check=False)

    assert e.timer_id_check_error_state is None


# --- partial / short modbus result -----------------------------------------
@pytest.mark.asyncio
async def test_partial_modbus_result_arms_grace_timer_and_aborts(driver):
    """A short read aborts processing and arms the grace timer (cannot escalate
    on its own, because __modbus_read resets the counter before this check)."""
    e, rec = driver
    e.v2g_main_app = AsyncMock()
    e.client.fault = "short"
    e.client.store.update(
        {REG_ACTUAL_POWER: 3000, REG_STATE: STATE_CHARGING, REG_SOC: 55}
    )

    await _process(e, e.CHARGER_POLLING_ENTITIES)

    assert e.modbus_exception_counter == 1  # grace timer armed
    e.hass.run_in.assert_awaited_once()
    assert e.hass.run_in.await_args.args[0] == (e._handle_un_recoverable_error)
    # Processing aborted: no entity was updated from the partial batch.
    assert e._MCE_ACTUAL_POWER.current_value is None
    assert rec.find("charge_power_change") == []


# --- write-side + repeated-exception branches ------------------------------
@pytest.mark.asyncio
async def test_write_exception_arms_grace_timer(driver):
    """A failed write follows the same recoverable-first path as a failed read."""
    e, _ = driver
    e.v2g_main_app = AsyncMock()
    e.client.fault = "raise"

    assert await _modbus_write(e, e.SET_ACTION_REGISTER, 2) is None
    assert e.modbus_exception_counter == 1
    e.hass.run_in.assert_awaited_once()
    assert e.hass.run_in.await_args.args[0] == (e._handle_un_recoverable_error)
    assert e.hass.run_in.await_args.kwargs["delay"] == 60


@pytest.mark.asyncio
async def test_repeated_exception_without_timer_returns_none(driver):
    """A repeat failure after the grace timer expired is treated as unrecoverable."""
    e, _ = driver
    e.v2g_main_app = AsyncMock()
    e.modbus_exception_counter = 1  # already had a failure
    e.timer_id_check_modus_exception_state = None  # grace period elapsed
    e.client.fault = "raise"

    assert await _modbus_read(e, REG_STATE, 1) is None
    # __handle_modbus_exception returned unrecoverable -> read bailed with None.
    assert e.modbus_exception_counter == 1


@pytest.mark.asyncio
async def test_force_get_register_returns_none_on_unrecoverable_exception(driver):
    """An in-loop ModbusException that is already unrecoverable bails with None."""
    e, _ = driver
    e.v2g_main_app = AsyncMock()
    e.modbus_exception_counter = 1
    e.timer_id_check_modus_exception_state = None  # grace period elapsed
    e.client.fault = "raise"

    result = await _force_get(e, REG_SOC, 2, 97, 1, 100)

    assert result is None
    # The bare early-return does not itself escalate; the expired timer already did.
    assert e._am_i_active is True


# --- charger_state_change secondary branches -------------------------------
@pytest.mark.asyncio
async def test_state_change_into_error_invokes_error_handler(driver):
    """A transition INTO a charger error state routes to the error handler."""
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING
    e.client.store.update({REG_STATE: STATE_ERROR, REG_ACTUAL_POWER: 0, REG_SOC: 55})

    await _process(e, e.CHARGER_POLLING_ENTITIES)

    e._handle_charger_error_state_change.assert_awaited()
    assert rec.find("charger_state_change")[-1]["new_charger_state"] == STATE_ERROR


@pytest.mark.asyncio
async def test_state_change_suppressed_during_soc_dance(driver):
    """A (non-error) state change emits nothing while a forced-SoC read runs."""
    e, rec = driver
    e.try_get_new_soc_in_process = True
    e._MCE_CHARGER_STATE.current_value = STATE_PAUSED

    await _update(e, e._MCE_CHARGER_STATE, STATE_CHARGING)

    assert rec.find("charger_state_change") == []
    assert rec.find("is_car_connected") == []


# --- SoC -> kWh -> range derivation (Fase-1 EV-parity anchor) ---------------
@pytest.mark.asyncio
async def test_soc_getter_derivations(driver):
    """Pin the charger's SoC->kWh->range derivation that the EV mirrors bit-for-bit.

    Paired with test_electric_vehicle.py: both sides use round(soc*cap/100, 2)
    and int(round(kwh*1000/consumption, 0)), so a drift on either side fails.
    """
    e, _ = driver
    del e._get_car_soc  # real SoC getter
    del e.get_car_remaining_range  # real range wrapper
    e._MCE_CHARGER_STATE.current_value = STATE_CHARGING  # connected
    e._MCE_CAR_SOC.current_value = 55

    assert await e.get_car_soc() == 55
    soc_kwh = await e.get_car_soc_kwh()
    assert soc_kwh == round(55 * float(c.CAR_MAX_CAPACITY_IN_KWH / 100), 2)
    assert await e.get_car_remaining_range() == int(
        round(soc_kwh * 1000 / c.CAR_CONSUMPTION_WH_PER_KM, 0)
    )
