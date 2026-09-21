"""Unit tests for charger-type settings in V2GLibertyGlobals.

Covers the charger-type selection contract:
- __save_charger_settings: stores the charger type (both types), the
  reduced-power fields only when enabled, the charger phase when the settings
  flow supplies one, fires the result event and then runs init -> historical
  import -> kick-off; a changed type swaps the driver in place (no restart),
  an unchanged type does not.
- __switch_evse_client: shuts the old driver down and rewires main_app
  and data_monitor to the new one.
- __test_charger_connection: maps the driver's status to the four UI
  messages, uses the running driver when the type matches and a temporary
  one otherwise; an unknown type reports "Failed to connect".
- get_configured_charger_type: default for existing users vs configured.
"""

from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from apps.v2g_liberty.chargers.factory import DEFAULT_CHARGER_TYPE
from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

_QUASAR = "wallbox-quasar-1"
_EVTEC = "evtec-bidi-pro-10"
_CREATE_EVSE_CLIENT = "apps.v2g_liberty.v2g_globals.create_evse_client"


def _evse_mock(charger_type: str) -> MagicMock:
    """Fake charger driver exposing the parts V2GLibertyGlobals touches."""
    evse = MagicMock()
    evse.CHARGER_TYPE = charger_type
    evse.test_charger_connection = AsyncMock(return_value=("success", 5750))
    evse.shutdown = AsyncMock()
    return evse


def _save_payload(**overrides) -> dict:
    payload = {
        "charger_type": _QUASAR,
        "host": "192.168.1.100",
        "port": 502,
        "useReducedMaxChargePower": False,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def hass_mock():
    mock = MagicMock()
    mock.fire_event = Mock()
    return mock


@pytest.fixture
def settings_manager_mock():
    """Fake settings manager: real dicts behind get()/store_object()."""
    mock = MagicMock()
    mock.settings = {}
    mock.objects = {}
    mock.store_setting = Mock()
    mock.get = Mock(side_effect=lambda key: mock.settings.get(key, None))
    mock.retrieve_settings = Mock()
    mock.store_object = Mock(
        side_effect=lambda key, value: mock.objects.__setitem__(key, value)
    )
    mock.get_object = Mock(side_effect=lambda key: mock.objects.get(key))
    return mock


@pytest.fixture
def main_app_mock():
    mock = MagicMock()
    mock.kick_off_v2g_liberty = AsyncMock()
    return mock


@pytest.fixture
def data_monitor_mock():
    return MagicMock()


@pytest.fixture
def evse_mock():
    """The running driver: a Wallbox Quasar 1 by default."""
    return _evse_mock(_QUASAR)


@pytest.fixture
def globals_instance(
    log_mock,
    hass_mock,
    settings_manager_mock,
    main_app_mock,
    data_monitor_mock,
    evse_mock,
):
    """V2GLibertyGlobals with mocked dependencies and a running Quasar driver.

    The three follow-up steps of a save (charger init, historical import,
    kick-off) and the driver switch are replaced by AsyncMocks so the save
    tests only observe orchestration; __switch_evse_client is exercised for
    real in :class:`TestSwitchEvseClient` (which restores it).
    """
    instance = object.__new__(V2GLibertyGlobals)
    instance._V2GLibertyGlobals__log = log_mock
    instance.hass = hass_mock
    instance.notifier = MagicMock()
    instance.event_bus = MagicMock()
    instance.v2g_settings = settings_manager_mock
    instance.v2g_main_app = main_app_mock
    instance.data_monitor = data_monitor_mock
    instance.evse_client_app = evse_mock
    instance._V2GLibertyGlobals__initialise_charger_settings = AsyncMock()
    instance._V2GLibertyGlobals__try_historical_import = AsyncMock()
    instance._V2GLibertyGlobals__switch_evse_client = AsyncMock()
    instance._V2GLibertyGlobals__refresh_car_settings_initialised = AsyncMock()
    return instance


# ── Setting schema ────────────────────────────────────────────────────


def test_charger_type_setting_schema():
    """The persisted key is input_text.charger_type (entity_type.entity_name)."""
    assert V2GLibertyGlobals.SETTING_CHARGER_TYPE == {
        "entity_name": "charger_type",
        "entity_type": "input_text",
        "value_type": "str",
        "factory_default": None,
    }


# ── __save_charger_settings ───────────────────────────────────────────


class TestSaveChargerSettings:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("charger_type", "port"),
        [(_QUASAR, 502), (_EVTEC, 5020)],
    )
    async def test_stores_settings_in_order_and_fires_result(
        self, globals_instance, settings_manager_mock, hass_mock, charger_type, port
    ):
        """Both charger types are stored, in the documented order, then the
        result event fires exactly once."""
        data = _save_payload(charger_type=charger_type, port=port)

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        assert settings_manager_mock.store_setting.call_args_list == [
            call("input_text.charger_type", charger_type),
            call("input_text.charger_host_url", "192.168.1.100"),
            call("input_number.charger_port", port),
            call("input_boolean.use_reduced_max_charge_power", False),
            call("input_boolean.charger_settings_initialised", True),
        ]
        hass_mock.fire_event.assert_called_once_with("save_charger_settings.result")

    @pytest.mark.asyncio
    async def test_reduced_power_fields_stored_when_enabled(
        self, globals_instance, settings_manager_mock
    ):
        """With useReducedMaxChargePower the two power limits are stored
        between the flag and the initialised marker."""
        data = _save_payload(
            useReducedMaxChargePower=True,
            maxChargingPower=5000,
            maxDischargingPower=4500,
        )

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        assert settings_manager_mock.store_setting.call_args_list == [
            call("input_text.charger_type", _QUASAR),
            call("input_text.charger_host_url", "192.168.1.100"),
            call("input_number.charger_port", 502),
            call("input_boolean.use_reduced_max_charge_power", True),
            call("input_number.charger_max_charging_power", 5000),
            call("input_number.charger_max_discharging_power", 4500),
            call("input_boolean.charger_settings_initialised", True),
        ]

    @pytest.mark.asyncio
    async def test_reduced_power_fields_not_stored_when_disabled(
        self, globals_instance, settings_manager_mock
    ):
        """Power limits present in the payload are ignored when the flag is off."""
        data = _save_payload(
            charger_type=_EVTEC,
            useReducedMaxChargePower=False,
            maxChargingPower=5000,
            maxDischargingPower=4500,
        )

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        stored_keys = [
            c_.args[0] for c_ in settings_manager_mock.store_setting.call_args_list
        ]
        assert len(stored_keys) == 5
        assert "input_number.charger_max_charging_power" not in stored_keys
        assert "input_number.charger_max_discharging_power" not in stored_keys

    @pytest.mark.asyncio
    async def test_execution_order_save_event_init_import_kickoff(
        self, globals_instance, settings_manager_mock, hass_mock, main_app_mock
    ):
        """All settings are stored before the result event; the event precedes
        charger init, which precedes the historical import and the kick-off."""
        call_order = []
        settings_manager_mock.store_setting.side_effect = lambda *a: call_order.append(
            "store"
        )
        hass_mock.fire_event.side_effect = lambda *a, **kw: call_order.append("event")
        globals_instance._V2GLibertyGlobals__initialise_charger_settings.side_effect = (
            lambda: call_order.append("init")
        )
        globals_instance._V2GLibertyGlobals__try_historical_import.side_effect = (
            lambda: call_order.append("import")
        )
        main_app_mock.kick_off_v2g_liberty.side_effect = lambda: call_order.append(
            "kickoff"
        )

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", _save_payload(), {}
        )

        assert call_order == ["store"] * 5 + ["event", "init", "import", "kickoff"]

    @pytest.mark.asyncio
    async def test_changed_type_switches_driver(self, globals_instance):
        """Running Quasar, saving EVtec → the driver is swapped in place."""
        data = _save_payload(charger_type=_EVTEC, port=5020)

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        globals_instance._V2GLibertyGlobals__switch_evse_client.assert_awaited_once_with(
            _EVTEC
        )

    @pytest.mark.asyncio
    async def test_same_type_does_not_switch_driver(self, globals_instance):
        """Re-saving the running driver's type (e.g. a new host) keeps it."""
        data = _save_payload(charger_type=_QUASAR, host="192.168.1.101")

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        globals_instance._V2GLibertyGlobals__switch_evse_client.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_switch_happens_after_result_event_and_before_init(
        self, globals_instance, hass_mock
    ):
        """The UI gets its result first; the new driver is in place before
        __initialise_charger_settings talks to it."""
        call_order = []
        hass_mock.fire_event.side_effect = lambda *a, **kw: call_order.append("event")
        globals_instance._V2GLibertyGlobals__switch_evse_client.side_effect = (
            lambda charger_type: call_order.append("switch")
        )
        globals_instance._V2GLibertyGlobals__initialise_charger_settings.side_effect = (
            lambda: call_order.append("init")
        )

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", _save_payload(charger_type=_EVTEC, port=5020), {}
        )

        assert call_order == ["event", "switch", "init"]

    @pytest.mark.asyncio
    async def test_car_flag_is_refreshed_after_the_switch(
        self, globals_instance, hass_mock
    ):
        """Whether the car is finished depends on the charger (a car without an
        id is fine on a Quasar, not on an EVtec), so the flag is derived again
        once the new driver is in place."""
        call_order = []
        globals_instance._V2GLibertyGlobals__switch_evse_client.side_effect = (
            lambda charger_type: call_order.append("switch")
        )
        globals_instance._V2GLibertyGlobals__refresh_car_settings_initialised.side_effect = (
            lambda: call_order.append("refresh")
        )

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", _save_payload(charger_type=_EVTEC, port=5020), {}
        )

        assert call_order == ["switch", "refresh"]


# ── __save_charger_settings: the phase ────────────────────────────────


class TestSaveChargerPhase:
    """The phase is saved with the rest of the charger settings, in one call.

    Saving it separately (and later) is what let someone abandon the settings
    flow at the phase step and keep a new charger type with the phase of the
    charger it replaced.
    """

    @pytest.mark.asyncio
    async def test_phase_is_stored_with_the_settings(
        self, globals_instance, settings_manager_mock
    ):
        data = _save_payload(charger_type=_EVTEC, connected_to_phase=[1, 2, 3])

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        assert settings_manager_mock.objects["charger_phase"] == {
            "connected_to_phase": [1, 2, 3]
        }

    @pytest.mark.asyncio
    async def test_bare_phase_number_is_normalised(
        self, globals_instance, settings_manager_mock
    ):
        """The manual 1-phase selection sends a plain int."""
        data = _save_payload(connected_to_phase=2)

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        assert settings_manager_mock.objects["charger_phase"] == {
            "connected_to_phase": [2]
        }

    @pytest.mark.asyncio
    async def test_without_a_phase_nothing_phase_related_is_touched(
        self, globals_instance, settings_manager_mock
    ):
        """No grid connection configured means no phase step, so the stored
        phase (if any) stays as it is."""
        settings_manager_mock.objects["charger_phase"] = {"connected_to_phase": [2]}

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", _save_payload(), {}
        )

        assert settings_manager_mock.objects["charger_phase"] == {
            "connected_to_phase": [2]
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", [0, 4, [], [1, 1], "1"])
    async def test_unusable_phase_refuses_the_whole_save(
        self, globals_instance, settings_manager_mock, hass_mock, log_mock, value
    ):
        """Not a single setting is stored: a refused phase must not leave the
        charger half-configured, which is the failure this call prevents."""
        data = _save_payload(charger_type=_EVTEC, connected_to_phase=value)

        await globals_instance._V2GLibertyGlobals__save_charger_settings(
            "event", data, {}
        )

        settings_manager_mock.store_setting.assert_not_called()
        assert "charger_phase" not in settings_manager_mock.objects
        assert "error" in hass_mock.fire_event.call_args.kwargs
        assert log_mock.call_args.kwargs.get("level") == "WARNING"


# ── __switch_evse_client ──────────────────────────────────────────────


class TestSwitchEvseClient:
    @pytest.fixture
    def switching_instance(self, globals_instance):
        """Restore the real __switch_evse_client (the shared fixture mocks it)."""
        del globals_instance._V2GLibertyGlobals__switch_evse_client
        return globals_instance

    @pytest.mark.asyncio
    async def test_shuts_down_old_driver_and_rewires(
        self, switching_instance, evse_mock, main_app_mock, data_monitor_mock
    ):
        new_evse = _evse_mock(_EVTEC)
        call_order = []
        evse_mock.shutdown.side_effect = lambda: call_order.append("shutdown")

        def fake_create(*args, **kwargs):
            call_order.append("create")
            return new_evse

        with patch(_CREATE_EVSE_CLIENT, side_effect=fake_create) as create_mock:
            await switching_instance._V2GLibertyGlobals__switch_evse_client(_EVTEC)

        create_mock.assert_called_once_with(
            _EVTEC,
            switching_instance.hass,
            switching_instance.event_bus,
            switching_instance.notifier,
        )
        # The new driver is built before the old one is shut down.
        assert call_order == ["create", "shutdown"]
        evse_mock.shutdown.assert_awaited_once()

        assert switching_instance.evse_client_app is new_evse
        assert new_evse.v2g_main_app is main_app_mock
        assert main_app_mock.evse_client_app is new_evse
        assert data_monitor_mock.evse_client_app is new_evse

    @pytest.mark.asyncio
    async def test_without_data_monitor(self, switching_instance, main_app_mock):
        """No data_monitor wired (yet) → main_app is still rewired, no error."""
        switching_instance.data_monitor = None
        new_evse = _evse_mock(_QUASAR)

        with patch(_CREATE_EVSE_CLIENT, return_value=new_evse):
            await switching_instance._V2GLibertyGlobals__switch_evse_client(_QUASAR)

        assert switching_instance.evse_client_app is new_evse
        assert main_app_mock.evse_client_app is new_evse


# ── __test_charger_connection ─────────────────────────────────────────


class TestChargerConnection:
    @staticmethod
    def _payload(**overrides) -> dict:
        payload = {"charger_type": _QUASAR, "host": "192.168.1.100", "port": 502}
        payload.update(overrides)
        return payload

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("status", "msg"),
        [
            ("success", "Successfully connected"),
            ("connection_failed", "Failed to connect"),
            ("not_recognised", "Charger not recognised"),
            ("no_active_plug", "No active plug found"),
        ],
    )
    async def test_status_maps_to_message(
        self, globals_instance, evse_mock, hass_mock, status, msg
    ):
        evse_mock.test_charger_connection = AsyncMock(return_value=(status, 5750))

        await globals_instance._V2GLibertyGlobals__test_charger_connection(
            "event", self._payload(), {}
        )

        evse_mock.test_charger_connection.assert_awaited_once_with("192.168.1.100", 502)
        hass_mock.fire_event.assert_called_once_with(
            "test_charger_connection.result", msg=msg, max_available_power=5750
        )

    @pytest.mark.asyncio
    async def test_unknown_status_maps_to_failed(
        self, globals_instance, evse_mock, hass_mock
    ):
        evse_mock.test_charger_connection = AsyncMock(return_value=("weird", None))

        await globals_instance._V2GLibertyGlobals__test_charger_connection(
            "event", self._payload(), {}
        )

        hass_mock.fire_event.assert_called_once_with(
            "test_charger_connection.result",
            msg="Failed to connect",
            max_available_power=None,
        )

    @pytest.mark.asyncio
    async def test_uses_running_driver_when_type_matches(
        self, globals_instance, evse_mock
    ):
        with patch(_CREATE_EVSE_CLIENT) as create_mock:
            await globals_instance._V2GLibertyGlobals__test_charger_connection(
                "event", self._payload(charger_type=_QUASAR), {}
            )

        create_mock.assert_not_called()
        evse_mock.test_charger_connection.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_defaults_to_running_driver_when_type_missing(
        self, globals_instance, evse_mock, hass_mock
    ):
        """Old callers that send no charger_type test the running driver."""
        payload = self._payload()
        del payload["charger_type"]

        with patch(_CREATE_EVSE_CLIENT) as create_mock:
            await globals_instance._V2GLibertyGlobals__test_charger_connection(
                "event", payload, {}
            )

        create_mock.assert_not_called()
        evse_mock.test_charger_connection.assert_awaited_once_with("192.168.1.100", 502)
        hass_mock.fire_event.assert_called_once_with(
            "test_charger_connection.result",
            msg="Successfully connected",
            max_available_power=5750,
        )

    @pytest.mark.asyncio
    async def test_uses_temporary_driver_when_type_differs(
        self, globals_instance, evse_mock, hass_mock
    ):
        """Testing another type than the running one must not touch the running
        driver; a temporary driver of the requested type is used instead."""
        temp_evse = _evse_mock(_EVTEC)
        temp_evse.test_charger_connection = AsyncMock(return_value=("success", 10000))

        with patch(_CREATE_EVSE_CLIENT, return_value=temp_evse) as create_mock:
            await globals_instance._V2GLibertyGlobals__test_charger_connection(
                "event", self._payload(charger_type=_EVTEC, port=5020), {}
            )

        create_mock.assert_called_once_with(
            _EVTEC,
            globals_instance.hass,
            globals_instance.event_bus,
            globals_instance.notifier,
        )
        temp_evse.test_charger_connection.assert_awaited_once_with(
            "192.168.1.100", 5020
        )
        evse_mock.test_charger_connection.assert_not_awaited()
        # The running driver is left untouched.
        assert globals_instance.evse_client_app is evse_mock
        hass_mock.fire_event.assert_called_once_with(
            "test_charger_connection.result",
            msg="Successfully connected",
            max_available_power=10000,
        )

    @pytest.mark.asyncio
    async def test_unknown_charger_type_fires_failed(
        self, globals_instance, evse_mock, hass_mock
    ):
        """An unknown type (factory raises ValueError) reports a failure to
        the UI instead of raising."""
        with patch(_CREATE_EVSE_CLIENT, side_effect=ValueError("Unknown charger type")):
            await globals_instance._V2GLibertyGlobals__test_charger_connection(
                "event", self._payload(charger_type="unknown-charger"), {}
            )

        evse_mock.test_charger_connection.assert_not_awaited()
        hass_mock.fire_event.assert_called_once_with(
            "test_charger_connection.result",
            msg="Failed to connect",
            max_available_power=None,
        )


# ── get_configured_charger_type ───────────────────────────────────────


class TestGetConfiguredChargerType:
    def test_default_when_not_configured(self, globals_instance, settings_manager_mock):
        """Existing users have no charger_type stored → Wallbox Quasar 1."""
        assert globals_instance.get_configured_charger_type() == DEFAULT_CHARGER_TYPE
        assert DEFAULT_CHARGER_TYPE == _QUASAR

    def test_loads_settings_when_empty(self, globals_instance, settings_manager_mock):
        """Called before settings are loaded (startup) → retrieve first."""

        def fake_retrieve():
            settings_manager_mock.settings["input_text.charger_type"] = _EVTEC

        settings_manager_mock.retrieve_settings.side_effect = fake_retrieve

        assert globals_instance.get_configured_charger_type() == _EVTEC
        settings_manager_mock.retrieve_settings.assert_called_once()

    def test_returns_configured_type(self, globals_instance, settings_manager_mock):
        settings_manager_mock.settings["input_text.charger_type"] = _EVTEC

        assert globals_instance.get_configured_charger_type() == _EVTEC
        settings_manager_mock.retrieve_settings.assert_not_called()
