"""Unit test (pytest) for settings_manager module."""

import json
from unittest.mock import ANY, Mock, mock_open, patch

import pytest
from apps.v2g_liberty.settings_manager import SettingsManager


@pytest.fixture
def log_mock():
    return Mock()


@pytest.fixture
def settings_manager(log_mock):
    return SettingsManager(log=log_mock)


@pytest.fixture
def json_dump_mock():
    return Mock()


class TestRetrieveSettings:
    @patch("os.path.exists", lambda _: False)
    def test_retrieve_initial_settings(self, log_mock, settings_manager):
        # Act
        settings_manager.retrieve_settings()
        # Assert
        log_mock.assert_called_with("no settings file found", level="WARNING")
        assert settings_manager.settings == {}

    @patch("os.path.exists", lambda _: True)
    @patch("builtins.open", mock_open(read_data="[]"))
    def test_non_dict_settings(self, log_mock, settings_manager):
        # Act
        settings_manager.retrieve_settings()
        # Assert
        log_mock.assert_called_with(
            "loading file content error, no dict: '[]'.", level="WARNING"
        )
        assert settings_manager.settings == {}

    @patch("os.path.exists", lambda _: True)
    @patch("builtins.open", mock_open(read_data='{"key":"value"}'))
    def test_existing_settings(self, settings_manager):
        # Act
        settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.settings == {"key": "value"}

    @patch("os.path.exists", lambda _: True)
    def test_upgrade(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.admin_mobile_name": "mobile_name",
                "input_select.fm_asset": "asset",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.settings == {
            "input_text.admin_mobile_name": "mobile_name",
            "input_text.fm_asset": "asset",
        }

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_admin_settings_initialised(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.admin_mobile_name": "mobile_name",
                "input_select.admin_mobile_platform": "mobile_platform",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.get("input_boolean.admin_settings_initialised") is True

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_calendar_settings_initialised_caldav(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.car_calendar_source": "remoteCaldav",
                "input_text.calendar_account_init_url": "url",
                "input_text.calendar_account_username": "username",
                "input_text.calendar_account_password": "password",
                "input_text.car_calendar_name": "calendar",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_boolean.calendar_settings_initialised") is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_calendar_settings_initialised_homeassistant(
        self, settings_manager
    ):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.car_calendar_source": "localIntegration",
                "input_text.integration_calendar_entity_name": "calendar",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_boolean.calendar_settings_initialised") is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_calendar_source_caldav(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.car_calendar_source": "Direct caldav source",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.get("input_text.car_calendar_source") == "remoteCaldav"

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_calendar_source_homeassistant(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.car_calendar_source": "Home Assistant integration",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_text.car_calendar_source") == "localIntegration"
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_charger_settings_initialised(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.charger_host_url": "host",
                "input_number.charger_port": "port",
                "input_boolean.use_reduced_max_charge_power": False,
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_boolean.charger_settings_initialised") is True
        )

    # Following four tests
    # Check the charger_type migration: users who configured a charger before the
    # charger_type setting existed get "wallbox-quasar-1", the only charger type
    # available back then. Anything else is left alone.

    @patch("os.path.exists", lambda _: True)
    @patch("os.replace")
    def test_upgrade_charger_type_existing_user(
        self, os_replace_mock, settings_manager, json_dump_mock
    ):
        # Arrange: charger configured, but from before charger_type existed
        saved_settings = json.dumps(
            {
                "input_text.charger_host_url": "192.168.1.1",
                "input_number.charger_port": 502,
                "input_boolean.use_reduced_max_charge_power": False,
            }
        )
        with (
            patch("builtins.open", mock_open(read_data=saved_settings)),
            patch("json.dump", json_dump_mock),
        ):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.get("input_text.charger_type") == "wallbox-quasar-1"
        # The migrated settings must be written back to the file
        json_dump_mock.assert_called_once()
        written_settings = json_dump_mock.call_args.args[0]
        assert written_settings["input_text.charger_type"] == "wallbox-quasar-1"

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_charger_type_already_set(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.charger_host_url": "192.168.1.1",
                "input_number.charger_port": 5020,
                "input_boolean.use_reduced_max_charge_power": False,
                "input_text.charger_type": "evtec-bidi-pro-10",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.get("input_text.charger_type") == "evtec-bidi-pro-10"

    @patch("os.path.exists", lambda _: True)
    @patch("builtins.open", mock_open(read_data="{}"))
    def test_upgrade_charger_type_fresh_install(self, settings_manager):
        # Act
        settings_manager.retrieve_settings()
        # Assert
        assert "input_text.charger_type" not in settings_manager.settings

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_charger_type_partial_charger_settings(self, settings_manager):
        # Arrange: only the host is present, charger never fully configured
        saved_settings = json.dumps(
            {
                "input_text.charger_host_url": "192.168.1.1",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert "input_text.charger_type" not in settings_manager.settings

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_electricity_contract_settings_initialised(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.electricity_provider": "nl_tibber",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get(
                "input_boolean.electricity_contract_settings_initialised"
            )
            is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_electricity_contract_settings_initialised_nl_generic(
        self, settings_manager
    ):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.electricity_provider": "nl_generic",
                "input_number.energy_price_vat": "vat",
                "input_number.energy_price_markup_per_kwh": "markup",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get(
                "input_boolean.electricity_contract_settings_initialised"
            )
            is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_electricity_contract_settings_initialised_amber(
        self, settings_manager
    ):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.electricity_provider": "au_amber_electric",
                "input_text.own_consumption_price_entity_id": "consumption",
                "input_text.own_production_price_entity_id": "production",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get(
                "input_boolean.electricity_contract_settings_initialised"
            )
            is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_electricity_contract_settings_initialised_octopus(
        self, settings_manager
    ):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_select.electricity_provider": "gb_octopus_energy",
                "input_text.octopus_import_code": "import_code",
                "input_text.octopus_export_code": "export_code",
                "input_select.gb_dno_region": "region",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get(
                "input_boolean.electricity_contract_settings_initialised"
            )
            is True
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_schedule_settings_initialised(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.fm_account_username": "username",
                "input_text.fm_account_password": "password",
                "input_boolean.fm_show_option_to_change_url": True,
                "input_text.fm_host_url": "host",
                "input_text.fm_asset": "asset",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_boolean.schedule_settings_initialised") is True
        )

    # Following two tests
    # Check that the url to Seita's FlexMeasures server is updated automatically
    # (update version 0.5.3 from March 2025).
    # TODO: Review if this code can be removed again if all users have upgraded to version 0.5.3
    # or above."""

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_fm_url(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.fm_host_url": "https://seita.energy",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert (
            settings_manager.get("input_text.fm_host_url") == "https://ems.seita.energy"
        )

    @patch("os.path.exists", lambda _: True)
    def test_upgrade_fm_url_non_default(self, settings_manager):
        # Arrange
        saved_settings = json.dumps(
            {
                "input_text.fm_host_url": "https://localhost:81",
            }
        )
        with patch("builtins.open", mock_open(read_data=saved_settings)):
            # Act
            settings_manager.retrieve_settings()
        # Assert
        assert settings_manager.get("input_text.fm_host_url") == "https://localhost:81"


@patch("builtins.open", mock_open())
@patch("os.replace")
def test_store_setting(os_replace_mock, settings_manager, json_dump_mock):
    # Arrange
    with patch("json.dump", json_dump_mock):
        # Act
        settings_manager.store_setting("entity_id", "value")
    # Assert
    assert settings_manager.settings == {"entity_id": "value"}
    json_dump_mock.assert_called_with({"entity_id": "value"}, ANY, indent=2)


@patch("builtins.open", mock_open())
@patch("os.replace")
def test_reset(os_replace_mock, settings_manager, json_dump_mock):
    # Arrange
    settings_manager.settings = {"entity_id": "value"}
    with patch("json.dump", json_dump_mock):
        # Act
        settings_manager.reset()
    # Assert
    assert settings_manager.settings == {}
    json_dump_mock.assert_called_with({}, ANY, indent=2)


class TestGet:
    def test_get_existing(self, settings_manager):
        # Arrange
        settings_manager.settings = {"entity_id": "value"}
        # Act
        value = settings_manager.get("entity_id")
        # Assert
        assert value == "value"

    def test_get_missing(self, settings_manager):
        # Arrange
        settings_manager.settings = {"entity_id": "value"}
        # Act
        value = settings_manager.get("missing_entity_id")
        # Assert
        assert value is None


class TestGetObject:
    def test_get_object_existing(self, settings_manager):
        # Arrange
        obj = {"phases": 3, "capacity_per_phase": 25}
        settings_manager.settings = {"grid_connection": obj}
        # Act
        result = settings_manager.get_object("grid_connection")
        # Assert
        assert result == obj

    def test_get_object_missing_key(self, settings_manager):
        # Arrange
        settings_manager.settings = {}
        # Act
        result = settings_manager.get_object("grid_connection")
        # Assert
        assert result is None

    def test_get_object_non_dict_value(self, settings_manager):
        # Arrange: key exists but value is not a dict
        settings_manager.settings = {"grid_connection": "not_a_dict"}
        # Act
        result = settings_manager.get_object("grid_connection")
        # Assert
        assert result is None

    def test_get_object_nested(self, settings_manager):
        # Arrange: object with nested structure
        obj = {
            "phases": 3,
            "consumption_entities": ["sensor.l1", "sensor.l2", "sensor.l3"],
        }
        settings_manager.settings = {"grid_connection": obj}
        # Act
        result = settings_manager.get_object("grid_connection")
        # Assert
        assert result == obj
        assert result["consumption_entities"] == ["sensor.l1", "sensor.l2", "sensor.l3"]


class TestStoreObject:
    @patch("builtins.open", mock_open())
    @patch("os.replace")
    def test_store_object_new(self, os_replace_mock, settings_manager, json_dump_mock):
        # Arrange
        obj = {"phases": 3, "capacity_per_phase": 25}
        with patch("json.dump", json_dump_mock):
            # Act
            settings_manager.store_object("grid_connection", obj)
        # Assert
        assert settings_manager.settings["grid_connection"] == obj
        json_dump_mock.assert_called_once()

    @patch("builtins.open", mock_open())
    @patch("os.replace")
    def test_store_object_overwrite(
        self, os_replace_mock, settings_manager, json_dump_mock
    ):
        # Arrange: existing object
        settings_manager.settings = {
            "grid_connection": {"phases": 1, "capacity_per_phase": 16}
        }
        new_obj = {"phases": 3, "capacity_per_phase": 25}
        with patch("json.dump", json_dump_mock):
            # Act
            settings_manager.store_object("grid_connection", new_obj)
        # Assert
        assert settings_manager.settings["grid_connection"] == new_obj

    @patch("builtins.open", mock_open())
    @patch("os.replace")
    def test_store_object_preserves_other_settings(
        self, os_replace_mock, settings_manager, json_dump_mock
    ):
        # Arrange: existing settings should not be affected
        settings_manager.settings = {"input_text.charger_host_url": "192.168.1.1"}
        obj = {"phases": 3}
        with patch("json.dump", json_dump_mock):
            # Act
            settings_manager.store_object("grid_connection", obj)
        # Assert
        assert settings_manager.settings["input_text.charger_host_url"] == "192.168.1.1"
        assert settings_manager.settings["grid_connection"] == obj


# ── Car settings migration ────────────────────────────────────────────
# The six car values used to be six entity-keyed settings; they now live in
# one object in the "cars" list. The flat keys stay one release (roll-back).

_FLAT_CAR_DEFAULTS = {
    "input_number.car_max_capacity_in_kwh": 24,
    "input_number.charger_plus_car_roundtrip_efficiency": 85,
    "input_number.car_consumption_wh_per_km": 175,
    "input_number.car_min_soc_in_percent": 20,
    "input_number.car_max_soc_in_percent": 80,
    "input_number.allowed_duration_above_max_soc_in_hrs": 4,
}


def _retrieve(settings_manager, saved: dict, json_dump_mock=None):
    with (
        patch("os.path.exists", lambda _: True),
        patch("os.replace"),
        patch("builtins.open", mock_open(read_data=json.dumps(saved))),
        patch("json.dump", json_dump_mock or Mock()),
    ):
        settings_manager.retrieve_settings()


class TestUpgradeCarSettings:
    def test_changed_values_migrate_as_configured(
        self, settings_manager, json_dump_mock
    ):
        saved = {
            "input_number.car_max_capacity_in_kwh": 62,
            "input_number.charger_plus_car_roundtrip_efficiency": 90,
            "input_number.car_consumption_wh_per_km": 160,
            "input_number.car_min_soc_in_percent": 25,
            "input_number.car_max_soc_in_percent": 85,
            "input_number.allowed_duration_above_max_soc_in_hrs": 6,
        }
        _retrieve(settings_manager, saved, json_dump_mock)

        assert settings_manager.get_object("cars") == [
            {
                "capacity_kwh": 62,
                "roundtrip_efficiency": 90,
                "consumption_wh_per_km": 160,
                "min_soc_percent": 25,
                "max_soc_percent": 85,
                "allowed_duration_above_max_soc_hrs": 6,
                "name": "",
                "ev_id": "",
                "configured": True,
            }
        ]
        # The flat keys stay, so a roll-back finds its values.
        for key, value in saved.items():
            assert settings_manager.get(key) == value
        # And the migrated settings are written back to the file.
        json_dump_mock.assert_called_once()
        assert json_dump_mock.call_args.args[0]["cars"][0]["configured"] is True

    def test_untouched_defaults_without_charger_are_not_configured(
        self, settings_manager
    ):
        """The app writes the six defaults on the very first boot, so their
        presence alone does not mean the user ever configured a car."""
        _retrieve(settings_manager, dict(_FLAT_CAR_DEFAULTS))

        car = settings_manager.get_object("cars")[0]
        assert car["configured"] is False
        assert car["capacity_kwh"] == 24

    def test_untouched_defaults_with_charger_are_configured(self, settings_manager):
        """An installation that was in use (charger configured) kept the
        defaults on purpose."""
        saved = dict(_FLAT_CAR_DEFAULTS)
        saved["input_boolean.charger_settings_initialised"] = True
        _retrieve(settings_manager, saved)

        assert settings_manager.get_object("cars")[0]["configured"] is True

    def test_partial_flat_keys_migrate_what_is_there(self, settings_manager):
        _retrieve(
            settings_manager,
            {"input_number.car_max_capacity_in_kwh": 40},
        )

        assert settings_manager.get_object("cars") == [
            {"capacity_kwh": 40, "name": "", "ev_id": "", "configured": True}
        ]

    def test_existing_cars_list_is_left_alone(self, settings_manager):
        cars = [{"name": "Ioniq 5", "ev_id": "X", "configured": True}]
        saved = dict(_FLAT_CAR_DEFAULTS)
        saved["cars"] = cars
        _retrieve(settings_manager, saved)

        assert settings_manager.get_object("cars") == cars

    def test_no_car_keys_no_object(self, settings_manager):
        _retrieve(settings_manager, {"input_text.charger_host_url": "192.168.1.1"})

        assert "cars" not in settings_manager.settings

    def test_factory_defaults_match_v2g_globals(self):
        """The migration's notion of "unchanged" must be the app's defaults."""
        from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals

        expected = {
            key: setting["factory_default"]
            for key, setting in V2GLibertyGlobals.CAR_VALUE_SETTINGS.items()
        }
        assert SettingsManager._CAR_FACTORY_DEFAULTS == expected
        # And the legacy keys are exactly the entities those settings project to.
        assert SettingsManager._LEGACY_CAR_KEYS == {
            f"{setting['entity_type']}.{setting['entity_name']}": key
            for key, setting in V2GLibertyGlobals.CAR_VALUE_SETTINGS.items()
        }
