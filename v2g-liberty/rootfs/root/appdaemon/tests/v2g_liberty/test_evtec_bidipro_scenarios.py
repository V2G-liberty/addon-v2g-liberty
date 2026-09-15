"""Regression net for the EVtec BiDiPro driver's decode -> entity/event pipeline.

The EVtec counterpart of ``test_modbus_evse_client_scenarios.py``: drives the
real ``evtec_bidipro`` driver with register states (via a fake pymodbus client
behind the real ``V2GmodbusClient``) and asserts the observable contract — the
event_bus events it emits, the entity values it caches, the registers it writes
and the direct v2g_main_app / notifier calls it makes. Register encoding comes
from the shared, pure-data ``dev_tools.charger_scenarios_evtec`` module, so the
tests and the dev emulator speak the same register dialect.

Heavy side-effects (poll-timer scheduling, the range calculation) are patched so
the tests focus on the contract; individual tests un-patch what they exercise.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import apps.v2g_liberty.constants as c
import pytest
from apps.dev_tools.charger_scenarios_evtec import (
    CP_MODEL,
    OFF_CONNECTOR_STATE,
    OFF_ERROR,
    OFF_INPUT_POWER,
    OFF_LOWER_LIMIT,
    OFF_LOWER_LIMIT_POTENTIAL,
    OFF_POWER,
    OFF_SESSION_STATE,
    OFF_SESSION_TYPE,
    OFF_SOC,
    OFF_SUSPEND_MODE,
    OFF_UPPER_LIMIT,
    dec_int32,
    enc_float32,
    enc_int32,
    enc_int64,
    enc_string,
    words_at,
)
from apps.v2g_liberty.chargers import evtec_bidipro
from apps.v2g_liberty.chargers.evtec_bidipro import (
    CP_COMMUNICATION_TIMEOUT,
    EVtecBiDiProClient,
)
from apps.v2g_liberty.event_bus import EventBus
from pymodbus.exceptions import ModbusException

CONNECTOR = 9
BASE = CONNECTOR * 100

_EVENTS = [
    "soc_change",
    "remaining_range_change",
    "charge_power_change",
    "charger_state_change",
    "is_car_connected",
    "evse_polled",
    "update_charger_info",
    "charger_communication_state_change",
    "discharge_refused",
]


class FakeModbusClient:
    """In-memory pymodbus stand-in (uint16 words). ``fault = "raise"`` makes
    every call raise, ``"error"`` returns error responses."""

    def __init__(self, store=None):
        self.store = dict(store or {})
        self.connected = True
        self.fault = None
        self.writes = []

    async def connect(self):
        self.connected = True
        return True

    async def read_holding_registers(self, address, count=1, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        regs = [self.store.get(address + i, 0) for i in range(count)]
        return SimpleNamespace(registers=regs, isError=lambda: self.fault == "error")

    async def write_register(self, address, value, device_id=1):
        return await self.write_registers(address, [value], device_id)

    async def write_registers(self, address, values, device_id=1):
        if self.fault == "raise":
            raise ModbusException("simulated comms loss")
        for i, value in enumerate(values):
            self.store[address + i] = value
        self.writes.append((address, list(values)))
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


def connector_words(
    state=10,
    session_type=5,
    power=0.0,
    soc_permille=550,
    floor=-10000.0,
    upper=10000.0,
    lower=0.0,
    error=0,
    connector=CONNECTOR,
):
    """A connected, idle, bidirectional car unless overridden."""
    base = connector * 100
    return {
        **words_at(base + OFF_CONNECTOR_STATE, enc_int32(state)),
        **words_at(base + OFF_SESSION_STATE, enc_int32(2)),
        **words_at(base + OFF_SESSION_TYPE, enc_int32(session_type)),
        **words_at(base + OFF_POWER, enc_float32(power)),
        **words_at(base + OFF_SOC, enc_int32(soc_permille)),
        **words_at(base + OFF_LOWER_LIMIT_POTENTIAL, enc_float32(floor)),
        **words_at(base + OFF_UPPER_LIMIT, enc_float32(upper)),
        **words_at(base + OFF_LOWER_LIMIT, enc_float32(lower)),
        **words_at(base + OFF_ERROR, enc_int64(error)),
    }


def setpoint(store):
    return dec_int32(
        [store.get(BASE + OFF_INPUT_POWER, 0), store.get(BASE + OFF_INPUT_POWER + 1, 0)]
    )


def suspend_mode(store):
    return dec_int32(
        [
            store.get(BASE + OFF_SUSPEND_MODE, 0),
            store.get(BASE + OFF_SUSPEND_MODE + 1, 0),
        ]
    )


@pytest.fixture
def driver(monkeypatch):
    hass = MagicMock()
    for m in ("set_state", "run_every", "run_in", "get_state", "cancel_timer"):
        setattr(hass, m, AsyncMock())
    bus = EventBus(hass)
    rec = Recorder()
    rec.subscribe(bus)

    e = EVtecBiDiProClient(hass, bus, MagicMock())
    e.notifier = MagicMock()
    e.v2g_main_app = MagicMock()
    e.v2g_main_app.handle_none_responsive_charger = AsyncMock()
    e.v2g_main_app.reset_charger_communication_fault = AsyncMock()
    # The fake sits behind the real transport, so decoding, spans and FC16
    # writes are the production code paths.
    e.client = FakeModbusClient()
    e._mb_client._mbc = e.client
    e._bind_connector(CONNECTOR)
    e._am_i_active = True
    e.modbus_exception_counter = 0
    e.requested_charge_power = 0

    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 10000)
    monkeypatch.setattr(c, "CHARGER_MAX_DISCHARGE_POWER", 10000)
    monkeypatch.setattr(c, "CAR_MIN_SOC_IN_PERCENT", 20)

    # Heavy side-effects patched; tests un-patch what they exercise.
    e._set_poll_strategy = AsyncMock()
    e.get_car_remaining_range = AsyncMock(return_value=100)
    return e, rec


async def poll(e):
    await e._get_and_process_registers(e.CHARGER_POLLING_ENTITIES)


# --- decode -> entities -> events ------------------------------------------
@pytest.mark.asyncio
async def test_full_poll_decodes_and_emits(driver):
    e, rec = driver
    e.client.store.update(connector_words(state=7, power=3680.4, soc_permille=550))
    await poll(e)

    assert e._MCE_CHARGER_STATE.current_value == 7
    assert e._MCE_ACTUAL_POWER.current_value == 3680  # float32 -> int W
    assert e._MCE_CAR_SOC.current_value == 55  # per-mille -> %
    assert e._MCE_SESSION_TYPE.current_value == 5
    assert e._MCE_LOWER_LIMIT_POTENTIAL.current_value == -10000.0
    assert e._MCE_ERROR.current_value == 0
    assert rec.find("charge_power_change")[-1]["new_power"] == 3680
    state_event = rec.find("charger_state_change")[-1]
    assert state_event["new_charger_state"] == 7
    assert state_event["new_charger_state_str"] == "Charging"
    assert rec.find("soc_change")[-1] == {"new_soc": 55, "old_soc": None}
    assert rec.find("remaining_range_change")
    # None -> a connected state means the car just connected
    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True


@pytest.mark.asyncio
async def test_soc_zero_while_connected_is_ignored(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = 7
    e._MCE_CAR_SOC.current_value = 50
    e.client.store.update(connector_words(state=7, soc_permille=0))
    await poll(e)
    assert e._MCE_CAR_SOC.current_value == 50
    assert not rec.find("soc_change")


@pytest.mark.asyncio
async def test_error_bitmask_decodes_unsigned_and_zero_means_no_error(driver):
    e, _ = driver
    e._MCE_CHARGER_STATE.current_value = 10
    e.client.store.update(connector_words(error=1 << 39))
    await poll(e)
    assert e._MCE_ERROR.current_value == 1 << 39
    # a non-zero bitmask arms the error timer ...
    e.hass.run_in.assert_awaited()
    e.hass.run_in.reset_mock()
    # ... and 0 clears it without arming a new one
    e.client.store.update(connector_words(error=0))
    await poll(e)
    assert e._MCE_ERROR.current_value == 0
    e.hass.run_in.assert_not_awaited()
    assert e.timer_id_check_error_state is None


# --- connection transitions ------------------------------------------------
@pytest.mark.asyncio
async def test_disconnect_marks_soc_unavailable_and_zeroes_the_setpoint(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = 7
    e._MCE_CAR_SOC.current_value = 55
    e.requested_charge_power = 4000
    e.client.store.update(words_at(BASE + OFF_INPUT_POWER, enc_int32(4000)))
    e.client.store.update(connector_words(state=1, soc_permille=0))
    await poll(e)

    assert rec.find("is_car_connected")[-1]["is_car_connected"] is False
    assert e._MCE_CAR_SOC.current_value == "unavailable"
    # the real stop: X+86 = 0, so a stale setpoint cannot resume on reconnect
    assert setpoint(e.client.store) == 0
    assert e.requested_charge_power == 0


@pytest.mark.asyncio
async def test_plugged_but_not_controllable_counts_as_no_car(driver):
    e, rec = driver
    e._MCE_CHARGER_STATE.current_value = 1
    for state in (4, 5, 6):
        e.client.store.update(connector_words(state=state))
        await poll(e)
        # like the Quasar driver, a move between two "no car" states re-emits
        # False; what must not happen is a True before the session is up
        assert all(
            kw["is_car_connected"] is False for kw in rec.find("is_car_connected")
        )
        assert await e.is_car_connected() is False
    e.client.store.update(connector_words(state=7))
    await poll(e)
    assert rec.find("is_car_connected")[-1]["is_car_connected"] is True
    assert await e.is_car_connected() is True


def test_state_sets_are_consistent(driver):
    e, _ = driver
    assert set(e.DISCONNECTED_STATES).isdisjoint(e.AVAILABILITY_STATES)
    assert set(e.ERROR_STATES).isdisjoint(e.AVAILABILITY_STATES)
    assert e.CHARGING_STATE in e.AVAILABILITY_STATES
    assert e.DISCHARGING_STATE in e.AVAILABILITY_STATES
    assert set(e.CHARGER_STATES) == set(range(13))
    e._MCE_CHARGER_STATE.current_value = 10
    assert e.is_available_for_automated_charging() is True
    e._MCE_CHARGER_STATE.current_value = 12
    assert e.is_available_for_automated_charging() is False


# --- setpoint: interlocks, windows, stop -----------------------------------
@pytest.mark.asyncio
async def test_session_type_interlock_refuses_discharge(driver):
    e, _ = driver
    e.client.store.update(connector_words(session_type=1))
    await poll(e)
    await e._set_charge_power(-3000, source="test")
    assert setpoint(e.client.store) == 0
    assert e.client.writes == []
    # charging is still allowed
    await e._set_charge_power(3000, source="test")
    assert setpoint(e.client.store) == 3000


@pytest.mark.asyncio
async def test_v2g_not_offered_refuses_discharge_without_error(driver):
    e, _ = driver
    e.client.store.update(connector_words(floor=0.0))
    await poll(e)
    await e._set_charge_power(-3000, source="test")
    assert e.client.writes == []
    e.v2g_main_app.handle_none_responsive_charger.assert_not_awaited()
    assert e.timer_id_check_error_state is None


@pytest.mark.asyncio
async def test_discharge_is_clamped_into_the_offered_window(driver):
    e, _ = driver
    e.client.store.update(connector_words(floor=-3000.0))
    await poll(e)
    await e._set_charge_power(-8000, source="test")
    assert setpoint(e.client.store) == -3000


@pytest.mark.asyncio
async def test_charge_is_clamped_into_the_accept_window(driver):
    e, _ = driver
    e.client.store.update(connector_words(upper=6000.0, lower=1000.0))
    await poll(e)
    await e._set_charge_power(8000, source="test")
    assert setpoint(e.client.store) == 6000
    await e._set_charge_power(500, source="test")
    assert setpoint(e.client.store) == 1000  # below the floor: the station rejects it


@pytest.mark.asyncio
async def test_unsane_charge_window_falls_back_to_configured_maximum(driver):
    e, _ = driver
    e.client.store.update(connector_words(upper=0.0))  # car not charge-ready
    await poll(e)
    await e._set_charge_power(12000, source="test")
    assert setpoint(e.client.store) == 10000  # c.CHARGER_MAX_CHARGE_POWER


@pytest.mark.asyncio
async def test_discharge_below_minimum_soc_is_refused(driver):
    e, _ = driver
    e.client.store.update(connector_words(soc_permille=150))  # 15 % < 20 %
    await poll(e)
    await e._set_charge_power(-3000, source="test")
    assert e.client.writes == []


@pytest.mark.asyncio
async def test_start_and_stop_write_the_expected_registers(driver):
    e, _ = driver
    e.client.store.update(connector_words(state=10))
    await poll(e)
    await e.start_charge_with_power(4000, source="test")
    assert suspend_mode(e.client.store) == 1
    assert setpoint(e.client.store) == 4000
    # an identical setpoint is not written again (the suspend-mode gate is
    # re-asserted, as the Quasar re-asserts its start action)
    setpoint_writes = [w for w in e.client.writes if w[0] == BASE + OFF_INPUT_POWER]
    await e.start_charge_with_power(4000, source="test")
    assert [
        w for w in e.client.writes if w[0] == BASE + OFF_INPUT_POWER
    ] == setpoint_writes

    await e.stop_charging()
    assert setpoint(e.client.store) == 0
    assert suspend_mode(e.client.store) == 0
    assert e.requested_charge_power == 0


@pytest.mark.asyncio
async def test_start_is_ignored_without_a_car(driver):
    e, _ = driver
    e.client.store.update(connector_words(state=1))
    await poll(e)
    e.client.writes.clear()  # the disconnect itself wrote the stop
    await e.start_charge_with_power(4000, source="test")
    assert e.client.writes == []


# --- discovery and connection test -----------------------------------------
@pytest.mark.asyncio
async def test_discovery_scans_offsets_0_2_4_and_binds_the_lowest(driver):
    e, _ = driver
    # connector 3 is configured but still booting: state 0, session type set
    e.client.store.update(words_at(300 + OFF_SESSION_TYPE, enc_int32(5)))
    e.client.store.update(connector_words(connector=9))
    assert await e._discover_connector(e._mb_client) == 3
    e.client.store.clear()
    assert await e._discover_connector(e._mb_client) is None


@pytest.mark.asyncio
async def test_initialise_charger_binds_the_discovered_connector(driver, monkeypatch):
    e, _ = driver
    monkeypatch.setattr(c, "CHARGER_HOST_URL", "evtec-mock")
    monkeypatch.setattr(c, "CHARGER_PORT", 5020)
    fake = FakeModbusClient(connector_words(connector=4))
    e._bind_connector(1)
    e._mb_client.terminate = MagicMock()

    async def fake_initialise(host, port):
        e._mb_client._mbc = fake
        return True

    e._mb_client.initialise = fake_initialise
    assert await e.initialise_charger() == (True, e.DEFAULT_HARDWARE_POWER_LIMIT_W)
    assert e._connector == 4
    assert e._MCE_CHARGER_STATE.modbus_register.address == 400
    # communication timeout written to the ChargePoint object
    assert (
        dec_int32(
            [
                fake.store[CP_COMMUNICATION_TIMEOUT],
                fake.store[CP_COMMUNICATION_TIMEOUT + 1],
            ]
        )
        == 600
    )


def _transport_with(store, connected=True):
    """A V2GmodbusClient whose pymodbus client is the fake and whose
    initialise() does not open a socket."""
    from apps.v2g_liberty.chargers.v2g_modbus_client import V2GmodbusClient

    transport = V2GmodbusClient(MagicMock())
    transport._mbc = FakeModbusClient(store)
    transport.initialise = AsyncMock(return_value=connected)
    transport.terminate = MagicMock()
    return transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "store, connected, expected",
    [
        ({}, False, ("connection_failed", None)),
        (
            words_at(CP_MODEL, enc_string("acme-evse", 10)),
            True,
            ("not_recognised", None),
        ),
        (
            words_at(CP_MODEL, enc_string("cremacharge", 10)),
            True,
            ("no_active_plug", None),
        ),
        (
            {**words_at(CP_MODEL, enc_string("cremacharge", 10)), **connector_words()},
            True,
            ("success", 10000),
        ),
    ],
)
async def test_test_charger_connection_statuses(driver, store, connected, expected):
    e, _ = driver
    transport = _transport_with(store, connected)
    with patch.object(evtec_bidipro, "V2GmodbusClient", return_value=transport):
        assert await e.test_charger_connection("host", 5020) == expected
    transport.terminate.assert_called()


# --- comms loss, recovery, un-recoverable ----------------------------------
@pytest.mark.asyncio
async def test_first_modbus_exception_arms_grace_timer_and_recovery_resets(driver):
    e, rec = driver
    e.client.fault = "raise"
    await poll(e)
    assert e.modbus_exception_counter == 1
    e.hass.run_in.assert_awaited()
    # a later successful read clears the fault and reports the recovery
    e.client.fault = None
    e.client.store.update(connector_words())
    await poll(e)
    assert e.modbus_exception_counter == 0
    e.v2g_main_app.reset_charger_communication_fault.assert_awaited()
    assert rec.find("charger_communication_state_change")[-1]["can_communicate"] is True


@pytest.mark.asyncio
async def test_exception_before_first_connection_posts_sticky_memo(driver):
    e, _ = driver
    e.modbus_exception_counter = None
    e.client.fault = "raise"
    await poll(e)
    e.notifier.post_sticky_memo.assert_called_once()
    assert (
        e.notifier.post_sticky_memo.call_args.kwargs["memo_id"] == "no_comm_with_evse"
    )


@pytest.mark.asyncio
async def test_unrecoverable_error_unpacks_an_appdaemon_timer_kwargs_dict(
    driver, caplog
):
    """The handler is both called directly and scheduled as a one-shot timer.
    AppDaemon hands a timer its kwargs as a single positional dict, which lands
    in `reason`; logging that dict as the reason would make the escalation
    untraceable -- and taak 26h builds the user-facing text on it."""
    e, _ = driver
    e.client.store.update(connector_words(state=7))
    await poll(e)

    e._log = MagicMock()
    await e._handle_un_recoverable_error(
        {"reason": "no Modbus response", "source": "read", "__thread_id": "MainThread"}
    )

    e.v2g_main_app.handle_none_responsive_charger.assert_awaited_once()
    logged = " ".join(str(call) for call in e._log.call_args_list)
    assert "reason='no Modbus response'" in logged
    assert "source='read'" in logged
    assert "__thread_id" not in logged


@pytest.mark.asyncio
async def test_unrecoverable_error_deactivates_and_notifies(driver):
    e, rec = driver
    e.client.store.update(connector_words(state=7))
    await poll(e)
    await e._handle_un_recoverable_error(reason="test", source="test")
    # The reason travels on: main_app needs it to tell the user whether the
    # charger is unreachable or reporting a fault.
    e.v2g_main_app.handle_none_responsive_charger.assert_awaited_once_with(
        was_car_connected=True, reason="test"
    )
    assert (
        rec.find("charger_communication_state_change")[-1]["can_communicate"] is False
    )
    assert rec.find("evse_polled")[-1]["stop"] is True
    assert e._MCE_CAR_SOC.current_value == "unavailable"
    assert e._MCE_ACTUAL_POWER.current_value == "unavailable"
    assert e._am_i_active is False


@pytest.mark.asyncio
async def test_error_state_final_check_escalates(driver):
    e, _ = driver
    e.client.store.update(connector_words(state=12))
    await poll(e)
    assert e.timer_id_check_error_state is not None
    await e._handle_charger_error_state_change(
        {"new_charger_state": None, "is_final_check": True}
    )
    e.v2g_main_app.handle_none_responsive_charger.assert_awaited()


# --- polling ---------------------------------------------------------------
@pytest.mark.asyncio
async def test_poll_strategy_minimal_when_disconnected_base_when_connected(driver):
    e, _ = driver
    for state, interval in (
        (1, e.MINIMAL_POLLING_INTERVAL_SECONDS),
        (10, e.BASE_POLLING_INTERVAL_SECONDS),
    ):
        e._MCE_CHARGER_STATE.current_value = state
        e.hass.run_every.reset_mock()
        await EVtecBiDiProClient._set_poll_strategy(e)
        assert e.hass.run_every.await_args.args[2] == interval


@pytest.mark.asyncio
async def test_polling_emits_evse_polled_heartbeat_and_stop(driver):
    e, rec = driver
    e.client.store.update(connector_words())
    await e._base_polling({})
    assert rec.find("evse_polled")[-1]["stop"] is False
    await e._minimal_polling({})
    assert rec.find("evse_polled")[-1]["stop"] is False
    await e._cancel_polling(reason="test")
    assert rec.find("evse_polled")[-1]["stop"] is True


# --- misc public API -------------------------------------------------------
@pytest.mark.asyncio
async def test_get_car_soc_reads_when_unknown_and_unavailable_when_no_car(driver):
    e, _ = driver
    e.client.store.update(connector_words(state=10, soc_permille=420))
    assert await e.get_car_soc() == 42
    e.client.store.update(connector_words(state=1, soc_permille=0))
    await poll(e)
    assert await e.get_car_soc() == "unavailable"


@pytest.mark.asyncio
async def test_charger_info_and_shutdown(driver, monkeypatch):
    e, rec = driver
    e.client.store.update(words_at(0, enc_string("ECP4-2.0", 16)))
    e.client.store.update(words_at(16, enc_string("SER-1", 10)))
    e.client.store.update(words_at(CP_MODEL, enc_string("cremacharge", 10)))
    info = await e._get_charger_info()
    assert info.startswith("EVtec BiDiPro 10") and "cremacharge" in info

    e._mb_client.terminate = MagicMock()
    await e.shutdown()
    e._mb_client.terminate.assert_called_once()
    assert rec.find("evse_polled")[-1]["stop"] is True
    assert e._am_i_active is False


# --- reconnect above the normal SoC window (regression) --------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("permille, expected", [(990, 99), (10, 1)])
async def test_reconnect_outside_the_normal_soc_window_is_accepted(
    driver, permille, expected
):
    """After a disconnect the SoC cache holds the "unavailable" sentinel. A car
    that returns above 97 % (or at 1 %) must still be read, or it would stay
    "unavailable" for good: nothing can discharge it back into range."""
    e, rec = driver
    e.client.store.update(connector_words(state=7, soc_permille=550))
    await poll(e)
    assert e._MCE_CAR_SOC.current_value == 55

    e.client.store.update(connector_words(state=1, soc_permille=0))
    await poll(e)
    assert e._MCE_CAR_SOC.current_value == "unavailable"

    e.client.store.update(connector_words(state=7, soc_permille=permille))
    await poll(e)
    assert e._MCE_CAR_SOC.current_value == expected
    assert rec.find("soc_change")[-1]["new_soc"] == expected
    assert await e.get_car_soc() == expected


@pytest.mark.asyncio
async def test_zero_soc_is_still_ignored_while_connected(driver):
    """The rescue above must not weaken the glitch filter: a 0 while connected
    keeps the last good value."""
    e, rec = driver
    e.client.store.update(connector_words(state=7, soc_permille=550))
    await poll(e)
    before = len(rec.find("soc_change"))
    e.client.store.update(connector_words(state=7, soc_permille=0))
    await poll(e)
    assert e._MCE_CAR_SOC.current_value == 55
    assert len(rec.find("soc_change")) == before


# --- the configured maximum wins over the station's charge floor -----------
@pytest.mark.asyncio
async def test_charge_floor_above_the_configured_maximum_refuses_to_charge(
    driver, monkeypatch
):
    """Raising the request to X+38 must never exceed the user's reduced-power
    setting: that setting protects their grid connection."""
    e, _ = driver
    monkeypatch.setattr(c, "CHARGER_MAX_CHARGE_POWER", 3000)
    e.client.store.update(connector_words(state=7, lower=4000.0, upper=10000.0))
    await poll(e)
    await e._set_charge_power(3000, source="schedule")
    assert e.client.writes == []
    assert e.requested_charge_power == 0


# --- shutdown must not leave a zombie behind -------------------------------
@pytest.mark.asyncio
async def test_shutdown_stops_an_in_flight_poll_from_re_arming_timers(driver):
    """shutdown() closes the socket, so a poll that is already running raises.
    That exception must not arm the un-recoverable-error timer on a driver that
    is no longer the active one."""
    e, _ = driver
    e._mb_client.terminate = MagicMock()
    await e.shutdown()
    e.hass.run_in.reset_mock()

    e.client.fault = "raise"
    await poll(e)  # the in-flight poll, finishing after shutdown

    e.hass.run_in.assert_not_awaited()
    assert e.timer_id_check_modus_exception_state is None
    await e._handle_un_recoverable_error(reason="late timer", source="test")
    e.v2g_main_app.handle_none_responsive_charger.assert_not_awaited()


class TestDischargeRefusalClearsItself:
    """Refusing is tied to a request; clearing must not be, or the warning
    outlives the problem. A charger that starts offering V2G again would keep
    being reported as refusing until someone happens to ask for a discharge,
    which can be hours.

    These drive _base_polling rather than the poll() helper: the check belongs
    to the full polling cycle, where the window registers have just been read.
    """

    @pytest.mark.asyncio
    async def test_a_resolved_refusal_is_cleared_on_the_next_poll(self, driver):
        e, rec = driver
        e.client.store.update(connector_words(state=8, session_type=1))
        await e._base_polling({})
        await e._set_charge_power(-4000, source="schedule")
        assert (
            rec.find("discharge_refused")[-1]["reason"] == "session_not_bidirectional"
        )

        # The charger starts a bidirectional session; nobody asks to discharge.
        e.client.store.update(connector_words(state=8, session_type=5))
        await e._base_polling({})

        assert rec.find("discharge_refused")[-1]["reason"] is None

    @pytest.mark.asyncio
    async def test_polling_does_not_raise_a_refusal_by_itself(self, driver):
        """Warning about a discharge nobody asked for would be its own noise."""
        e, rec = driver
        e.client.store.update(connector_words(state=8, session_type=1))
        await e._base_polling({})

        assert rec.find("discharge_refused") == []

    @pytest.mark.asyncio
    async def test_a_standing_refusal_is_not_cleared_while_it_still_applies(
        self, driver
    ):
        e, rec = driver
        e.client.store.update(connector_words(state=8, session_type=1))
        await e._base_polling({})
        await e._set_charge_power(-4000, source="schedule")
        await e._base_polling({})

        assert (
            rec.find("discharge_refused")[-1]["reason"] == "session_not_bidirectional"
        )
