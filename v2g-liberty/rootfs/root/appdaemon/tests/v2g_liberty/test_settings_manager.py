"""Unit test (pytest) for settings_manager module."""

from unittest.mock import ANY, Mock, mock_open, patch
import json
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
