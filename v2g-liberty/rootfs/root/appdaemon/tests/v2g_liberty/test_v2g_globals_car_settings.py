"""Unit tests for the car settings foundation in V2GLibertyGlobals.

The car lives in the ``cars`` list of the settings file (one element in this
release). ``__initialise_car_settings`` is its only reader: it sets the car
constants and their derived values, projects the six numbers to the HA
entities the dashboard and FlexMeasures read, and derives the
``car_settings_initialised`` flag into a runtime sensor. The flag is derived,
not latched: a configured car without an id on a charger that identifies
cars is not finished.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
import yaml
from apps.v2g_liberty import constants as c
from apps.v2g_liberty.settings_manager import SettingsManager
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

_PACKAGE = (
    Path(__file__).parents[3]
    / "homeassistant"
    / "packages"
    / "v2g_liberty"
    / "v2g_liberty_package.yaml"
)
_FLAG = "sensor.car_settings_initialised"

_CAR = {
    "name": "Ioniq 5",
    "ev_id": "DEVCAR-EVCCID-01",
    "configured": True,
    "capacity_kwh": 74,
    "roundtrip_efficiency": 90,
    "consumption_wh_per_km": 160,
    "min_soc_percent": 25,
    "max_soc_percent": 85,
    "allowed_duration_above_max_soc_hrs": 6,
}


def _evse(identifies_car: bool | None) -> MagicMock:
    """A charger driver; None = a driver without the attribute at all."""
    evse = MagicMock(spec=[])
    if identifies_car is not None:
        evse.IDENTIFIES_CAR = identifies_car
    return evse


@pytest.fixture
def hass_mock():
    mock = MagicMock()
    mock.set_state = AsyncMock()
    return mock


@pytest.fixture
def settings_manager_mock():
    mock = MagicMock()
    mock.objects = {}
    mock.get_object = Mock(
        side_effect=lambda key, default=None: mock.objects.get(key, default)
    )
    return mock


@pytest.fixture
def globals_instance(hass_mock, settings_manager_mock):
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = Mock()
    instance.hass = hass_mock
    instance.notifier = MagicMock()
    instance.notifier.post_sticky_memo = AsyncMock()
    instance.v2g_settings = settings_manager_mock
    instance.evse_client_app = _evse(False)
    return instance


def _initialise(instance):
    return instance._V2GLibertyGlobals__initialise_car_settings()


def _refresh(instance, car=None):
    return instance._V2GLibertyGlobals__refresh_car_settings_initialised(car)


def _flag_writes(hass_mock) -> list[str]:
    return [
        kw["state"]
        for args, kw in hass_mock.set_state.call_args_list
        if args[0] == _FLAG
    ]


def _entity_writes(hass_mock) -> dict[str, object]:
    return {
        args[0]: kw["state"]
        for args, kw in hass_mock.set_state.call_args_list
        if args[0] != _FLAG
    }


# ── Schema ────────────────────────────────────────────────────────────


def test_limits_match_the_home_assistant_package():
    """The backend clamps (and later refuses) on the same limits the HA helpers
    have, so a value the entity accepts is never rejected and vice versa."""
    with open(_PACKAGE, encoding="utf-8") as f:
        package = yaml.safe_load(f)
    helpers = package["input_number"]

    for setting in V2GLibertyGlobals.CAR_VALUE_SETTINGS.values():
        helper = helpers[setting["entity_name"]]
        assert setting["entity_type"] == "input_number"
        assert (setting["min"], setting["max"]) == (helper["min"], helper["max"]), (
            setting["entity_name"]
        )
        assert helper["min"] <= setting["factory_default"] <= helper["max"]


def test_car_value_settings_cover_the_six_values():
    assert set(V2GLibertyGlobals.CAR_VALUE_SETTINGS) == set(
        SettingsManager._CAR_FACTORY_DEFAULTS
    )


# ── __initialise_car_settings ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_constants_and_derived_values_come_from_the_car(
    globals_instance, settings_manager_mock, hass_mock
):
    settings_manager_mock.objects["cars"] = [dict(_CAR)]

    await _initialise(globals_instance)

    assert c.CAR_NAME == "Ioniq 5"
    assert c.CAR_EV_ID == "DEVCAR-EVCCID-01"
    assert c.CAR_MAX_CAPACITY_IN_KWH == 74
    assert c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY == 90
    assert c.CAR_CONSUMPTION_WH_PER_KM == 160
    assert c.CAR_MIN_SOC_IN_PERCENT == 25
    assert c.CAR_MAX_SOC_IN_PERCENT == 85
    assert c.ALLOWED_DURATION_ABOVE_MAX_SOC == 6
    # Derived values, unchanged formulas.
    assert c.CAR_MIN_SOC_IN_KWH == 74 * 25 / 100
    assert c.CAR_MAX_SOC_IN_KWH == 74 * 85 / 100
    assert c.ROUNDTRIP_EFFICIENCY_FACTOR == 0.9
    assert c.CAR_MAX_RANGE_IN_KM == round(74 * 0.97 / 160 * 1000)
    assert c.USAGE_PER_EVENT_TIME_INTERVAL == (20 * 160 / 1000) / (
        60 / c.FM_EVENT_RESOLUTION_IN_MINUTES
    )
    # The six numbers are projected to their HA entities, as initialised.
    assert _entity_writes(hass_mock) == {
        "input_number.car_max_capacity_in_kwh": 74,
        "input_number.charger_plus_car_roundtrip_efficiency": 90,
        "input_number.car_consumption_wh_per_km": 160,
        "input_number.car_min_soc_in_percent": 25,
        "input_number.car_max_soc_in_percent": 85,
        "input_number.allowed_duration_above_max_soc_in_hrs": 6,
    }
    for args, kw in hass_mock.set_state.call_args_list:
        if args[0] != _FLAG:
            assert kw["attributes"]["initialised"] is True, args[0]


@pytest.mark.asyncio
async def test_no_car_falls_back_to_the_factory_defaults(globals_instance, hass_mock):
    """Nine modules read the constants, so they are set for an unconfigured
    car too -- but the entities are marked as not initialised."""
    await _initialise(globals_instance)

    assert c.CAR_NAME == ""
    assert c.CAR_EV_ID == ""
    assert c.CAR_MAX_CAPACITY_IN_KWH == 24
    assert c.CHARGER_PLUS_CAR_ROUNDTRIP_EFFICIENCY == 85
    assert c.CAR_CONSUMPTION_WH_PER_KM == 175
    assert c.CAR_MIN_SOC_IN_PERCENT == 20
    assert c.CAR_MAX_SOC_IN_PERCENT == 80
    assert c.ALLOWED_DURATION_ABOVE_MAX_SOC == 4
    assert c.CAR_MIN_SOC_IN_KWH == 24 * 20 / 100
    for args, kw in hass_mock.set_state.call_args_list:
        if args[0] != _FLAG:
            assert kw["attributes"]["initialised"] is False, args[0]
    assert _flag_writes(hass_mock) == ["off"]


@pytest.mark.asyncio
async def test_missing_field_falls_back_to_its_default(
    globals_instance, settings_manager_mock
):
    car = dict(_CAR)
    del car["allowed_duration_above_max_soc_hrs"]
    settings_manager_mock.objects["cars"] = [car]

    await _initialise(globals_instance)

    assert c.ALLOWED_DURATION_ABOVE_MAX_SOC == 4
    assert c.CAR_MAX_CAPACITY_IN_KWH == 74


@pytest.mark.asyncio
async def test_stored_value_outside_the_limits_is_clamped(
    globals_instance, settings_manager_mock
):
    """The same safety net __process_setting has: a value that got out of
    range is pulled back and the user is told, instead of breaking schedules."""
    car = dict(_CAR)
    car["capacity_kwh"] = 999
    settings_manager_mock.objects["cars"] = [car]

    await _initialise(globals_instance)

    assert c.CAR_MAX_CAPACITY_IN_KWH == 200
    globals_instance.notifier.post_sticky_memo.assert_awaited_once()


@pytest.mark.asyncio
async def test_values_stored_as_strings_are_converted(
    globals_instance, settings_manager_mock
):
    car = dict(_CAR)
    car["capacity_kwh"] = "74"
    car["ev_id"] = None
    settings_manager_mock.objects["cars"] = [car]

    await _initialise(globals_instance)

    assert c.CAR_MAX_CAPACITY_IN_KWH == 74
    assert c.CAR_EV_ID == ""


# ── The derived flag ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "configured, ev_id, identifies_car, expected",
    [
        (True, "", False, "on"),  # configured on a Quasar: done
        (True, "", True, "off"),  # configured on an EVtec without an id: not done
        (True, "X", True, "on"),  # configured on an EVtec with an id: done
        (False, "X", False, "off"),  # never saved: not done, whatever else
        (False, "", True, "off"),
        (True, "", None, "on"),  # a driver without the attribute does not identify
    ],
)
@pytest.mark.asyncio
async def test_flag_is_derived_from_configured_and_the_id_requirement(
    globals_instance,
    settings_manager_mock,
    hass_mock,
    configured,
    ev_id,
    identifies_car,
    expected,
):
    settings_manager_mock.objects["cars"] = [
        dict(_CAR, configured=configured, ev_id=ev_id)
    ]
    globals_instance.evse_client_app = _evse(identifies_car)

    await _initialise(globals_instance)

    assert _flag_writes(hass_mock) == [expected]


@pytest.mark.asyncio
async def test_refresh_without_a_car_reads_the_stored_one(
    globals_instance, settings_manager_mock, hass_mock
):
    """Called after a charger save: the driver may have changed, the car not."""
    settings_manager_mock.objects["cars"] = [dict(_CAR, ev_id="")]
    globals_instance.evse_client_app = _evse(True)

    await _refresh(globals_instance)

    hass_mock.set_state.assert_awaited_once_with(_FLAG, state="off")


@pytest.mark.asyncio
async def test_empty_cars_list_is_not_configured(
    globals_instance, settings_manager_mock, hass_mock
):
    settings_manager_mock.objects["cars"] = []

    await _refresh(globals_instance)

    hass_mock.set_state.assert_awaited_once_with(_FLAG, state="off")


# ── Order in kick_off_settings ────────────────────────────────────────


@pytest.mark.asyncio
async def test_car_is_initialised_before_the_charger(globals_instance):
    """The charger's first poll compares the connected car against
    c.CAR_EV_ID, so the car constants must be in place before it."""
    order = []

    def record(name):
        async def _run(*_args, **_kwargs):
            order.append(name)

        return _run

    globals_instance.v2g_settings.retrieve_settings = Mock()
    for name in (
        "notification",
        "car",
        "charger",
        "electricity_contract",
        "general",
        "fm_client",
        "calendar",
    ):
        setattr(
            globals_instance,
            f"_V2GLibertyGlobals__initialise_{name}_settings",
            record(name),
        )
    for name in ("grid_connection", "charger_phase", "solar_panel"):
        setattr(
            globals_instance,
            f"_V2GLibertyGlobals__initialise_{name}_settings",
            Mock(),
        )

    await globals_instance.kick_off_settings()

    assert order.index("car") < order.index("charger")
    assert order[:3] == ["notification", "car", "charger"]


@pytest.mark.asyncio
async def test_general_settings_no_longer_touch_the_car(globals_instance):
    """The six car values moved out of __initialise_general_settings; only the
    optimisation mode is left there."""
    processed = []

    async def fake_process(setting_object):
        processed.append(setting_object["entity_name"])
        return "price"

    globals_instance._V2GLibertyGlobals__process_setting = fake_process

    await globals_instance._V2GLibertyGlobals__initialise_general_settings()

    assert processed == ["optimisation_mode"]


# ── get_car_settings ──────────────────────────────────────────────────


def _get(instance):
    return instance._V2GLibertyGlobals__get_car_settings("event", {}, {})


def _result(hass_mock, event: str) -> dict:
    calls = [kw for args, kw in hass_mock.fire_event.call_args_list if args[0] == event]
    assert len(calls) == 1, hass_mock.fire_event.call_args_list
    return calls[0]


@pytest.fixture
def get_save_instance(globals_instance, hass_mock, settings_manager_mock):
    """The read/save path: the follow-up work of a save is mocked so the tests
    observe orchestration only."""
    hass_mock.fire_event = Mock()
    settings_manager_mock.store_object = Mock(
        side_effect=lambda key, value: settings_manager_mock.objects.__setitem__(
            key, value
        )
    )
    settings_manager_mock.store_setting = Mock()
    globals_instance.v2g_main_app = MagicMock()
    globals_instance.v2g_main_app.handle_car_settings_saved = AsyncMock(
        return_value=False
    )
    globals_instance.v2g_main_app.kick_off_v2g_liberty = AsyncMock()
    globals_instance._V2GLibertyGlobals__initialise_car_settings = AsyncMock()
    return globals_instance


@pytest.mark.asyncio
async def test_get_answers_with_the_stored_car(
    get_save_instance, settings_manager_mock, hass_mock
):
    settings_manager_mock.objects["cars"] = [dict(_CAR)]
    get_save_instance.evse_client_app = _evse(True)

    await _get(get_save_instance)

    assert _result(hass_mock, "get_car_settings.result") == {
        **_CAR,
        "identifies_car": True,
    }


@pytest.mark.asyncio
async def test_get_always_answers_with_defaults_when_there_is_no_car(
    get_save_instance, hass_mock
):
    """The dialog would otherwise hang in its timeout; and it needs no
    defaults of its own."""
    await _get(get_save_instance)

    assert _result(hass_mock, "get_car_settings.result") == {
        "name": "",
        "ev_id": "",
        "configured": False,
        "identifies_car": False,
        "capacity_kwh": 24,
        "roundtrip_efficiency": 85,
        "consumption_wh_per_km": 175,
        "min_soc_percent": 20,
        "max_soc_percent": 80,
        "allowed_duration_above_max_soc_hrs": 4,
    }


@pytest.mark.asyncio
async def test_get_fills_in_a_missing_field(
    get_save_instance, settings_manager_mock, hass_mock
):
    car = dict(_CAR)
    del car["max_soc_percent"]
    settings_manager_mock.objects["cars"] = [car]

    await _get(get_save_instance)

    assert _result(hass_mock, "get_car_settings.result")["max_soc_percent"] == 80


# ── save_car_settings ─────────────────────────────────────────────────

_PAYLOAD = {
    "name": "Ioniq 5",
    "capacity_kwh": 74,
    "efficiency": 90,
    "consumption_wh_km": 160,
    "min_soc": 25,
    "max_soc": 85,
    "allowed_duration_above_max": 6,
}


def _save(instance, **overrides):
    data = {**_PAYLOAD, **overrides}
    for key, value in list(data.items()):
        if value is ...:
            del data[key]
    return instance._V2GLibertyGlobals__save_car_settings("event", data, {})


@pytest.mark.asyncio
async def test_save_stores_the_whole_car_and_runs_the_follow_up_in_order(
    get_save_instance, settings_manager_mock, hass_mock
):
    order = []
    settings_manager_mock.store_object.side_effect = lambda *a: order.append("store")
    hass_mock.fire_event.side_effect = lambda *a, **kw: order.append("result")
    get_save_instance._V2GLibertyGlobals__initialise_car_settings.side_effect = lambda: (
        order.append("init")
    )
    main_app = get_save_instance.v2g_main_app
    main_app.handle_car_settings_saved.side_effect = lambda: order.append("handle")
    main_app.kick_off_v2g_liberty.side_effect = lambda: order.append("kickoff")

    await _save(get_save_instance, ev_id="DEVCAR-EVCCID-01")

    settings_manager_mock.store_object.assert_called_once_with("cars", [_CAR])
    hass_mock.fire_event.assert_called_once_with("save_car_settings.result")
    assert order == ["store", "result", "init", "handle", "kickoff"]


@pytest.mark.asyncio
async def test_save_skips_the_kick_off_when_the_charge_mode_was_restored(
    get_save_instance,
):
    """Restoring the mode already re-activates the driver; a kick-off on top
    would race it."""
    main_app = get_save_instance.v2g_main_app
    main_app.handle_car_settings_saved.return_value = True

    await _save(get_save_instance)

    main_app.handle_car_settings_saved.assert_awaited_once()
    main_app.kick_off_v2g_liberty.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_without_an_id_keeps_the_stored_one(
    get_save_instance, settings_manager_mock
):
    settings_manager_mock.objects["cars"] = [dict(_CAR)]

    await _save(get_save_instance, name="Renamed")

    car = settings_manager_mock.objects["cars"][0]
    assert car["ev_id"] == "DEVCAR-EVCCID-01"
    assert car["name"] == "Renamed"


@pytest.mark.asyncio
async def test_save_with_an_id_replaces_the_stored_one(
    get_save_instance, settings_manager_mock
):
    settings_manager_mock.objects["cars"] = [dict(_CAR)]

    await _save(get_save_instance, ev_id=" NEW-ID ")

    assert settings_manager_mock.objects["cars"][0]["ev_id"] == "NEW-ID"


@pytest.mark.asyncio
async def test_save_accepts_numbers_as_strings_and_trims_the_name(
    get_save_instance, settings_manager_mock
):
    await _save(
        get_save_instance, name="  " + "x" * 50, capacity_kwh="74", min_soc="25.0"
    )

    car = settings_manager_mock.objects["cars"][0]
    assert car["name"] == "x" * 40
    assert car["capacity_kwh"] == 74
    assert car["min_soc_percent"] == 25


@pytest.mark.asyncio
async def test_save_without_an_id_on_an_identifying_charger_is_refused(
    get_save_instance, settings_manager_mock, hass_mock
):
    get_save_instance.evse_client_app = _evse(True)

    await _save(get_save_instance)

    assert "ID" in _result(hass_mock, "save_car_settings.result")["error"]
    settings_manager_mock.store_object.assert_not_called()


@pytest.mark.asyncio
async def test_save_without_an_id_on_a_quasar_is_fine(
    get_save_instance, settings_manager_mock
):
    await _save(get_save_instance)

    assert settings_manager_mock.objects["cars"][0]["ev_id"] == ""


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"name": ""}, "name"),
        ({"name": "   "}, "name"),
        ({"name": ...}, "name"),
        ({"capacity_kwh": 9}, "Usable capacity must be between 10 and 200"),
        ({"capacity_kwh": 201}, "Usable capacity must be between 10 and 200"),
        ({"capacity_kwh": "abc"}, "whole number between 10 and 200"),
        ({"capacity_kwh": ...}, "whole number between 10 and 200"),
        ({"efficiency": 49}, "Roundtrip efficiency must be between 50 and 100"),
        ({"efficiency": 101}, "Roundtrip efficiency must be between 50 and 100"),
        ({"consumption_wh_km": 99}, "Energy consumption must be between 100 and 400"),
        ({"consumption_wh_km": 401}, "Energy consumption must be between 100 and 400"),
        ({"min_soc": 9}, "Schedule lower limit must be between 10 and 55"),
        ({"min_soc": 56}, "Schedule lower limit must be between 10 and 55"),
        ({"max_soc": 59}, "Schedule upper limit must be between 60 and 95"),
        ({"max_soc": 96}, "Schedule upper limit must be between 60 and 95"),
        ({"allowed_duration_above_max": 0}, "must be between 1 and 12"),
        ({"allowed_duration_above_max": 13}, "must be between 1 and 12"),
    ],
)
@pytest.mark.asyncio
async def test_save_refuses_and_stores_nothing(
    get_save_instance, settings_manager_mock, hass_mock, overrides, fragment
):
    await _save(get_save_instance, **overrides)

    error = _result(hass_mock, "save_car_settings.result")["error"]
    assert fragment in error
    settings_manager_mock.store_object.assert_not_called()
    settings_manager_mock.store_setting.assert_not_called()
    get_save_instance._V2GLibertyGlobals__initialise_car_settings.assert_not_awaited()
    get_save_instance.v2g_main_app.kick_off_v2g_liberty.assert_not_awaited()


@pytest.mark.asyncio
async def test_boundary_values_are_accepted(get_save_instance, settings_manager_mock):
    await _save(
        get_save_instance,
        capacity_kwh=10,
        efficiency=100,
        consumption_wh_km=400,
        min_soc=55,
        max_soc=60,
        allowed_duration_above_max=12,
    )

    assert settings_manager_mock.objects["cars"][0]["capacity_kwh"] == 10


# ── The flag after a charger save ─────────────────────────────────────


@pytest.mark.asyncio
async def test_switching_to_an_identifying_charger_unfinishes_a_car_without_id(
    globals_instance, settings_manager_mock, hass_mock
):
    """A migrated Quasar user (configured, no id) who moves to an EVtec gets
    the car back in the blocking dialog, asking for the id."""
    settings_manager_mock.objects["cars"] = [dict(_CAR, ev_id="")]
    settings_manager_mock.store_setting = Mock()
    settings_manager_mock.store_object = Mock()
    hass_mock.fire_event = Mock()
    globals_instance.v2g_main_app = MagicMock()
    globals_instance.v2g_main_app.kick_off_v2g_liberty = AsyncMock()
    globals_instance._V2GLibertyGlobals__initialise_charger_settings = AsyncMock()
    globals_instance._V2GLibertyGlobals__try_historical_import = AsyncMock()
    globals_instance.evse_client_app = _evse(False)
    globals_instance.evse_client_app.CHARGER_TYPE = "wallbox-quasar-1"

    async def switch(charger_type):
        globals_instance.evse_client_app = _evse(True)

    globals_instance._V2GLibertyGlobals__switch_evse_client = switch

    await globals_instance._V2GLibertyGlobals__save_charger_settings(
        "event",
        {
            "charger_type": "evtec-bidi-pro-10",
            "host": "192.168.1.100",
            "port": 5020,
            "useReducedMaxChargePower": False,
        },
        {},
    )

    assert _flag_writes(hass_mock) == ["off"]
