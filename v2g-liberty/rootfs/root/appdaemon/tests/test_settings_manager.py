from unittest.mock import ANY, Mock, mock_open, patch
from apps.v2g_liberty.settings_manager import SettingsManager

class TestRetrieveSettings:

    @patch('os.path.exists', lambda _: False)
    def test_retrieve_initial_settings(self):
        # Arrange
        log = Mock()
        settings = SettingsManager(log=log)
        # Act
        settings.retrieve_settings()
        # Assert
        log.assert_called_with("retrieve_settings, no settings file found")
        assert settings.settings == {}

    @patch('os.path.exists', lambda _: True)
    @patch('builtins.open', mock_open(read_data='[]'))
    def test_non_dict_settings(self):
        # Arrange
        log = Mock()
        settings = SettingsManager(log=log)
        # Act
        settings.retrieve_settings()
        # Assert
        log.assert_called_with("retrieve_settings, loading file content error, no dict: '[]'.")
        assert settings.settings == {}

    @patch('os.path.exists', lambda _: True)
    @patch('builtins.open', mock_open(read_data='{"key":"value"}'))
    def test_existing_settings(self):
        # Arrange
        log = Mock()
        settings = SettingsManager(log=log)
        # Act
        settings.retrieve_settings()
        # Assert
        assert settings.settings == {"key": "value"}


@patch('builtins.open', mock_open())
def test_store_setting():
    # Arrange
    settings = SettingsManager(log=Mock())
    dump_mock = Mock()
    with patch('json.dump', dump_mock):
        # Act
        settings.store_setting('entity_id', 'value')
    # Assert
    assert settings.settings == {"entity_id": "value"}
    dump_mock.assert_called_with({"entity_id": "value"}, ANY)


def test_reset():
    # Arrange
    settings = SettingsManager(log=Mock())
    settings.settings = {"entity_id": "value"}
    dump_mock = Mock()
    with patch('json.dump', dump_mock):
        # Act
        settings.reset()
    # Assert
    assert settings.settings == {}
    dump_mock.assert_called_with({}, ANY)


class TestGet:

    def test_get_existing(self):
        # Arrange
        settings = SettingsManager(log=Mock())
        settings.settings = {"entity_id": "value"}
        # Act
        value = settings.get("entity_id")
        # Assert
        assert value == "value"

    def test_get_missing(self):
        # Arrange
        settings = SettingsManager(log=Mock())
        settings.settings = {"entity_id": "value"}
        # Act
        value = settings.get("missing_entity_id")
        # Assert
        assert value == None
