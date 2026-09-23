"""An unknown car at a charger that identifies cars (EVtec).

The driver reports a car whose id differs from the registered one with
``unknown_car_connected`` instead of ``is_car_connected=True``. The main app
must not charge or discharge it on the owner's schedule: it forces the
charge mode to Stop, tells the user, and lifts that Stop again when the car
leaves or gets registered. The bookkeeping is persisted, because a guest
easily outlives a restart while input_select.charge_mode gets its Stop back.
"""

from unittest.mock import AsyncMock, MagicMock, call, patch
from zoneinfo import ZoneInfo

import pytest
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.main_app import V2Gliberty

GUEST = "VISITOR-01"
OWNER = "DEVCAR-EVCCID-01"
SENSOR = "sensor.unknown_car_connected"
FORCED = {"reason": "unknown_car", "ev_id": GUEST, "previous_mode": "Automatic"}


class FakeSettings:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})

    def get_object(self, key, default=None):
        value = self.objects.get(key)
        return value if isinstance(value, (dict, list)) else default

    def store_object(self, key, data):
        self.objects[key] = data


@pytest.fixture
def v2g(monkeypatch):
    monkeypatch.setattr(c, "CAR_EV_ID", OWNER)
    monkeypatch.setattr(c, "CAR_NAME", "Ioniq 5")
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock(return_value="Automatic")

    notifier = MagicMock()
    notifier.notify_user = AsyncMock()

    instance = V2Gliberty(hass=hass, event_bus=MagicMock(), notifier=notifier)
    instance.v2g_settings = FakeSettings()
    instance.unknown_car_ev_id = None
    instance._V2Gliberty__set_charge_mode_in_ui = AsyncMock()
    return instance


def _handle_unknown(v2g, ev_id=GUEST):
    return v2g._V2Gliberty__handle_unknown_car(ev_id)


def _sensor_writes(v2g) -> list[str]:
    return [
        call.kwargs["state"]
        for call in v2g.hass.set_state.await_args_list
        if (call.args and call.args[0] == SENSOR)
        or call.kwargs.get("entity_id") == SENSOR
    ]


def _state_events(v2g) -> list[bool]:
    return [
        call.kwargs["is_unknown_car"]
        for call in v2g.event_bus.emit_event.call_args_list
        if call.args[0] == "unknown_car_connected_state"
    ]


# ── Detection ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_car_forces_stop_and_tells_the_user(v2g):
    await _handle_unknown(v2g)

    assert v2g.unknown_car_ev_id == GUEST
    assert v2g.v2g_settings.objects["forced_stop"] == FORCED
    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_once_with("Stop")
    assert _sensor_writes(v2g) == [GUEST]
    sensor_call = v2g.hass.set_state.await_args_list[0]
    assert sensor_call.kwargs["attributes"] == {
        "registered_ev_id": OWNER,
        "registered_car_name": "Ioniq 5",
    }
    assert _state_events(v2g) == [True]
    kwargs = v2g.notifier.notify_user.await_args.kwargs
    assert GUEST in kwargs["message"]
    assert "Ioniq 5" in kwargs["message"]
    assert kwargs["critical"] is False
    assert kwargs["send_to_all"] is True
    assert kwargs["tag"] == "unknown_car_connected"


@pytest.mark.asyncio
async def test_second_report_of_the_same_car_is_idempotent(v2g):
    """A driver swap or a restart runs the connect transition again. The
    second report must not record the Stop we forced as the user's mode, and
    must not notify twice."""
    await _handle_unknown(v2g)
    v2g.hass.get_state = AsyncMock(return_value="Stop")  # our own Stop

    await _handle_unknown(v2g)

    assert v2g.v2g_settings.objects["forced_stop"]["previous_mode"] == "Automatic"
    v2g.notifier.notify_user.assert_awaited_once()
    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_once()
    # The sensor and the state event are refreshed all the same.
    assert _sensor_writes(v2g) == [GUEST, GUEST]
    assert _state_events(v2g) == [True, True]


@pytest.mark.asyncio
async def test_the_id_is_set_before_the_first_await(v2g):
    """set_next_action may run between the event and the Stop landing."""
    seen = []
    v2g.hass.set_state = AsyncMock(
        side_effect=lambda *a, **kw: seen.append(v2g.unknown_car_ev_id)
    )

    await _handle_unknown(v2g)

    assert seen[0] == GUEST


# ── Restart ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_up_restores_a_standing_unknown_car(v2g):
    v2g.v2g_settings = FakeSettings({"forced_stop": FORCED})

    await v2g._V2Gliberty__restore_unknown_car_bookkeeping()

    assert v2g.unknown_car_ev_id == GUEST
    assert _sensor_writes(v2g) == [GUEST]


@pytest.mark.asyncio
async def test_start_up_without_a_record_reports_none(v2g):
    await v2g._V2Gliberty__restore_unknown_car_bookkeeping()

    assert v2g.unknown_car_ev_id is None
    assert _sensor_writes(v2g) == ["none"]


@pytest.mark.asyncio
async def test_start_up_without_a_settings_store_does_not_crash(v2g):
    v2g.v2g_settings = None

    await v2g._V2Gliberty__restore_unknown_car_bookkeeping()

    assert v2g.unknown_car_ev_id is None


# ── The guard in set_next_action ──────────────────────────────────────


@pytest.fixture
def v2g_next(v2g, monkeypatch):
    monkeypatch.setattr(c, "TZ", ZoneInfo("Europe/Amsterdam"))
    monkeypatch.setattr(c, "CAR_MAX_CAPACITY_IN_PERCENT", 97)
    monkeypatch.setattr(c, "CAR_MIN_SOC_IN_PERCENT", 20)
    v2g.timer_handle_set_next_action = ""
    v2g.call_next_action_at_least_every = 900
    v2g.evse_client_app = MagicMock()
    v2g.evse_client_app.is_car_connected = AsyncMock(return_value=True)
    v2g.evse_client_app.try_get_new_soc_in_process = False
    v2g.electric_vehicle = MagicMock()
    v2g.electric_vehicle.soc = 50
    v2g.electric_vehicle.soc_kwh = 30
    v2g.fm_client_app = MagicMock()
    v2g.fm_client_app.get_new_schedule = AsyncMock(return_value=None)
    v2g._V2Gliberty__start_max_charge_now = AsyncMock()
    v2g._V2Gliberty__start_max_discharge_now = AsyncMock()
    v2g._V2Gliberty__set_charge_power = AsyncMock()
    v2g._V2Gliberty__cancel_charging_timers = AsyncMock()
    v2g.set_records_in_chart = AsyncMock()
    v2g.back_to_max_soc = None
    v2g.in_boost_to_reach_min_soc = False
    v2g.calendar_targets = []
    v2g.discharge_refused_reason = None
    return v2g


async def _next(v2g, charge_mode):
    v2g.hass.get_state = AsyncMock(return_value=charge_mode)
    with patch("apps.v2g_liberty.main_app.set_oneshot_timer", AsyncMock()):
        await v2g.set_next_action(v2g_args="test")


@pytest.mark.asyncio
@pytest.mark.parametrize("charge_mode", ["Automatic", "Stop"])
async def test_unknown_car_aborts_before_any_branch(v2g_next, charge_mode):
    v2g_next.unknown_car_ev_id = GUEST

    await _next(v2g_next, charge_mode)

    v2g_next.fm_client_app.get_new_schedule.assert_not_awaited()
    v2g_next._V2Gliberty__start_max_charge_now.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_boost_is_let_through_with_an_unknown_car(v2g_next):
    """The user asked for it, standing there (decision 3)."""
    v2g_next.unknown_car_ev_id = GUEST

    await _next(v2g_next, "Max boost now")

    v2g_next._V2Gliberty__start_max_charge_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_without_an_unknown_car_nothing_changes(v2g_next):
    await _next(v2g_next, "Max boost now")

    v2g_next._V2Gliberty__start_max_charge_now.assert_awaited_once()


@pytest.mark.asyncio
async def test_boost_limit_with_an_unknown_car_stops_but_keeps_the_mode(v2g_next):
    """Falling back to Automatic would show a mode the guard blocks and leave
    nothing for the restore to put back."""
    v2g_next.unknown_car_ev_id = GUEST
    v2g_next.electric_vehicle.soc = 97

    await _next(v2g_next, "Max boost now")

    v2g_next._V2Gliberty__set_charge_power.assert_awaited_once()
    assert (
        v2g_next._V2Gliberty__set_charge_power.await_args.args[0]["charge_power"] == 0
    )
    v2g_next._V2Gliberty__set_charge_mode_in_ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_discharge_limit_with_an_unknown_car_stops_but_keeps_the_mode(
    v2g_next,
):
    v2g_next.unknown_car_ev_id = GUEST
    v2g_next.electric_vehicle.soc = 20

    await _next(v2g_next, "Max discharge now")

    v2g_next._V2Gliberty__set_charge_power.assert_awaited_once()
    v2g_next._V2Gliberty__set_charge_mode_in_ui.assert_not_awaited()


@pytest.mark.asyncio
async def test_boost_limit_without_an_unknown_car_falls_back_as_before(v2g_next):
    v2g_next.electric_vehicle.soc = 97

    await _next(v2g_next, "Max boost now")

    v2g_next._V2Gliberty__set_charge_mode_in_ui.assert_awaited_once_with("Automatic")


# ── Registering the car ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_registering_the_standing_car_lifts_the_stop(v2g, monkeypatch):
    await _handle_unknown(v2g)
    v2g.hass.get_state = AsyncMock(return_value="Stop")
    monkeypatch.setattr(c, "CAR_EV_ID", GUEST.lower())  # the save refreshed it

    assert await v2g.handle_car_settings_saved() is True

    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_with("Automatic")
    v2g.notifier.clear_notification.assert_called_once_with(tag="unknown_car_connected")
    assert _sensor_writes(v2g)[-1] == "none"
    assert v2g.v2g_settings.objects["forced_stop"] == {}
    assert v2g.unknown_car_ev_id is None
    assert _state_events(v2g)[-1] is False


@pytest.mark.asyncio
async def test_registering_another_car_changes_nothing(v2g, monkeypatch):
    await _handle_unknown(v2g)
    monkeypatch.setattr(c, "CAR_EV_ID", "SOMETHING-ELSE")

    assert await v2g.handle_car_settings_saved() is False

    assert v2g.unknown_car_ev_id == GUEST
    assert v2g.v2g_settings.objects["forced_stop"] == FORCED


@pytest.mark.asyncio
async def test_saving_without_an_unknown_car_returns_false(v2g):
    assert await v2g.handle_car_settings_saved() is False
    v2g._V2Gliberty__set_charge_mode_in_ui.assert_not_awaited()


# ── The car leaves ────────────────────────────────────────────────────


@pytest.fixture
def v2g_disconnect(v2g):
    v2g._V2Gliberty__cancel_charging_timers = AsyncMock()
    v2g._V2Gliberty__clear_all_soc_chart_lines = AsyncMock()
    v2g.back_to_max_soc = None
    v2g.in_boost_to_reach_min_soc = False
    return v2g


def _disconnect(v2g):
    return v2g._V2Gliberty__handle_car_disconnect()


@pytest.mark.asyncio
async def test_unplugging_the_unknown_car_resumes_automatic(v2g_disconnect):
    v2g = v2g_disconnect
    await _handle_unknown(v2g)
    v2g.hass.get_state = AsyncMock(return_value="Stop")

    await _disconnect(v2g)

    v2g._V2Gliberty__set_charge_mode_in_ui.assert_awaited_with("Automatic")
    assert v2g.unknown_car_ev_id is None
    assert v2g.v2g_settings.objects["forced_stop"] == {}
    messages = [
        call.kwargs["message"] for call in v2g.notifier.notify_user.await_args_list
    ]
    assert any("resumed" in m for m in messages)
    v2g.notifier.clear_notification.assert_any_call(tag="unknown_car_connected")


@pytest.mark.asyncio
async def test_a_stop_the_user_had_before_is_left_alone(v2g_disconnect):
    v2g = v2g_disconnect
    v2g.hass.get_state = AsyncMock(return_value="Stop")  # before the guest
    await _handle_unknown(v2g)
    v2g._V2Gliberty__set_charge_mode_in_ui.reset_mock()

    await _disconnect(v2g)

    v2g._V2Gliberty__set_charge_mode_in_ui.assert_not_awaited()
    assert v2g.unknown_car_ev_id is None


@pytest.mark.asyncio
async def test_a_mode_the_user_changed_since_is_left_alone(v2g_disconnect):
    """The user switched to a boost while the guest stood there: not our Stop
    any more, so nothing to restore -- but the bookkeeping is cleared."""
    v2g = v2g_disconnect
    await _handle_unknown(v2g)
    v2g._V2Gliberty__set_charge_mode_in_ui.reset_mock()
    v2g.hass.get_state = AsyncMock(return_value="Max boost now")

    await _disconnect(v2g)

    # Only the existing boost -> Automatic rule of the disconnect handler
    # fires, not the unknown-car restore.
    assert v2g._V2Gliberty__set_charge_mode_in_ui.await_args_list == [call("Automatic")]
    assert v2g.unknown_car_ev_id is None
    assert v2g.v2g_settings.objects["forced_stop"] == {}
    assert _state_events(v2g)[-1] is False


@pytest.mark.asyncio
async def test_a_known_car_leaving_does_not_touch_the_bookkeeping(v2g_disconnect):
    v2g = v2g_disconnect

    await _disconnect(v2g)

    v2g.notifier.clear_notification.assert_any_call(tag="dismiss_event_or_not")
    assert "forced_stop" not in v2g.v2g_settings.objects
    assert not _state_events(v2g)


# ── Separate from the charger-problem bookkeeping ─────────────────────


@pytest.mark.asyncio
async def test_charger_recovery_leaves_the_unknown_car_alone(v2g):
    await _handle_unknown(v2g)
    v2g.charge_mode_before_charger_problem = "Automatic"
    v2g.reset_charger_communication_fault = AsyncMock()

    await v2g.handle_charger_recovered()

    assert v2g.unknown_car_ev_id == GUEST
    assert v2g.v2g_settings.objects["forced_stop"] == FORCED
