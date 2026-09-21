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
