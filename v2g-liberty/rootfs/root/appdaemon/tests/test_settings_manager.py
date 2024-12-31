import json
import pytest
from unittest.mock import ANY, Mock, mock_open, patch
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
        log_mock.assert_called_with("retrieve_settings, no settings file found")
        assert settings_manager.settings == {}

    @patch("os.path.exists", lambda _: True)
    @patch("builtins.open", mock_open(read_data="[]"))
    def test_non_dict_settings(self, log_mock, settings_manager):
        # Act
        settings_manager.retrieve_settings()
        # Assert
        log_mock.assert_called_with(
            "retrieve_settings, loading file content error, no dict: '[]'."
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
                "input_select.car_calendar_source": "Direct caldav source",
                "input_text.calendar_account_init_url": "url",
                "input_text.calendar_account_username": "username",
                "input_text.calendar_account_password": "password",
                "input_select.car_calendar_name": "calendar",
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
                "input_select.car_calendar_source": "Home Assistant integration",
                "input_select.integration_calendar_entity_name": "calendar",
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


@patch("builtins.open", mock_open())
def test_store_setting(settings_manager, json_dump_mock):
    # Arrange
    with patch("json.dump", json_dump_mock):
        # Act
        settings_manager.store_setting("entity_id", "value")
    # Assert
    assert settings_manager.settings == {"entity_id": "value"}
    json_dump_mock.assert_called_with({"entity_id": "value"}, ANY, indent=2)


def test_reset(settings_manager, json_dump_mock):
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
        assert value == None
