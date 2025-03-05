import pytest
from unittest.mock import MagicMock, mock_open, patch

from apps.load_balancer.config_loader import ConfigLoader


@pytest.fixture
def config_loader():
    return ConfigLoader()


@patch("os.path.exists", lambda _: False)
def test_no_config_file_present(config_loader):
    # Act
    config = config_loader.load()
    # Assert
    assert not config.get("enabled")


@patch("os.path.exists", lambda _: True)
@patch("builtins.open", mock_open(read_data=""))
def test_empty_config_file(config_loader):
    # Act
    config = config_loader.load()
    # Assert
    assert not config.get("enabled")


@patch("os.path.exists", lambda _: True)
@patch("builtins.open", mock_open(read_data='{"enabled":true,"key":"value"}'))
def test_valid_config_file(config_loader):
    # Act
    config = config_loader.load()
    # Assert
    assert config.get("enabled")
    assert config.get("key") == "value"
