"""Unit tests for charger settings functionality in v2g_globals module."""

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from apps.v2g_liberty.v2g_globals import V2GLibertyGlobals


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = MagicMock()
    hass.fire_event = Mock()
    return hass


@pytest.fixture
def mock_event_bus():
    """Create a mock EventBus instance."""
    return MagicMock()


@pytest.fixture
def mock_settings_manager():
    """Create a mock SettingsManager instance."""
    settings_manager = MagicMock()
    settings_manager.store_setting = Mock()
    settings_manager.get = Mock(return_value=None)
    return settings_manager


@pytest.fixture
def mock_notifier():
    """Create a mock Notifier instance."""
    return MagicMock()


@pytest.fixture
def mock_v2g_main_app():
    """Create a mock V2Gliberty main app."""
    main_app = MagicMock()
    main_app.kick_off_v2g_liberty = AsyncMock()
    return main_app


@pytest.fixture
def v2g_globals(
    mock_hass, mock_event_bus, mock_notifier, mock_settings_manager, mock_v2g_main_app
):
    """Create a V2GLibertyGlobals instance with mocked dependencies."""
    with patch(
        "apps.v2g_liberty.v2g_globals.SettingsManager",
        return_value=mock_settings_manager,
    ):
        v2g_globals = V2GLibertyGlobals(mock_hass, mock_event_bus, mock_notifier)
        v2g_globals.v2g_main_app = mock_v2g_main_app
        # Mock __initialise_charger_settings to avoid complex setup
        v2g_globals._V2GLibertyGlobals__initialise_charger_settings = AsyncMock()
        return v2g_globals


class TestSaveChargerSettings:
    """Test suite for __save_charger_settings method."""

    @pytest.mark.asyncio
    async def test_save_basic_charger_settings(
        self, v2g_globals, mock_settings_manager, mock_hass
    ):
        """Test saving basic charger settings without reduced power."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 502,
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        # Verify all basic settings were stored
        assert mock_settings_manager.store_setting.call_count == 5
        mock_settings_manager.store_setting.assert_any_call(
            "input_text.charger_type", "wallbox-quasar-1"
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_text.charger_host_url", "192.168.1.100"
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_number.charger_port", 502
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_boolean.use_reduced_max_charge_power", False
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_boolean.charger_settings_initialised", True
        )

        # Verify event was fired
        mock_hass.fire_event.assert_called_once_with("save_charger_settings.result")

    @pytest.mark.asyncio
    async def test_save_charger_settings_with_evtec_type(
        self, v2g_globals, mock_settings_manager
    ):
        """Test saving charger settings with EVtec BiDiPro charger type."""
        # Arrange
        data = {
            "charger_type": "evtec-bidi-pro-10",
            "host": "192.168.1.200",
            "port": 502,
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        mock_settings_manager.store_setting.assert_any_call(
            "input_text.charger_type", "evtec-bidi-pro-10"
        )

    @pytest.mark.asyncio
    async def test_save_charger_settings_with_reduced_power(
        self, v2g_globals, mock_settings_manager, mock_hass
    ):
        """Test saving charger settings with reduced max charge power enabled."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 502,
            "useReducedMaxChargePower": True,
            "maxChargingPower": 5000,
            "maxDischargingPower": 4500,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        # Verify all settings including power limits were stored
        assert mock_settings_manager.store_setting.call_count == 7
        mock_settings_manager.store_setting.assert_any_call(
            "input_text.charger_type", "wallbox-quasar-1"
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_text.charger_host_url", "192.168.1.100"
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_number.charger_port", 502
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_boolean.use_reduced_max_charge_power", True
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_number.charger_max_charging_power", 5000
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_number.charger_max_discharging_power", 4500
        )
        mock_settings_manager.store_setting.assert_any_call(
            "input_boolean.charger_settings_initialised", True
        )

        # Verify event was fired
        mock_hass.fire_event.assert_called_once_with("save_charger_settings.result")

    @pytest.mark.asyncio
    async def test_save_charger_settings_without_reduced_power_doesnt_save_limits(
        self, v2g_globals, mock_settings_manager
    ):
        """Test that power limits are not saved when useReducedMaxChargePower is False."""
        # Arrange
        data = {
            "charger_type": "evtec-bidi-pro-10",
            "host": "192.168.1.200",
            "port": 502,
            "useReducedMaxChargePower": False,
            "maxChargingPower": 5000,  # These should be ignored
            "maxDischargingPower": 4500,  # These should be ignored
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        # Verify power limits were NOT stored
        assert mock_settings_manager.store_setting.call_count == 5
        # Check that maxChargingPower was never called
        for call in mock_settings_manager.store_setting.call_args_list:
            assert "charger_max_charging_power" not in str(call)
            assert "charger_max_discharging_power" not in str(call)

    @pytest.mark.asyncio
    async def test_save_charger_settings_fires_event(self, v2g_globals, mock_hass):
        """Test that save_charger_settings.result event is fired."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 502,
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        mock_hass.fire_event.assert_called_once_with("save_charger_settings.result")

    @pytest.mark.asyncio
    async def test_save_charger_settings_calls_initialise_charger(self, v2g_globals):
        """Test that __initialise_charger_settings is called after saving."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 502,
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        v2g_globals._V2GLibertyGlobals__initialise_charger_settings.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_charger_settings_calls_kick_off_v2g_liberty(
        self, v2g_globals, mock_v2g_main_app
    ):
        """Test that v2g_main_app.kick_off_v2g_liberty is called after saving."""
        # Arrange
        data = {
            "charger_type": "evtec-bidi-pro-10",
            "host": "192.168.1.200",
            "port": 502,
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        mock_v2g_main_app.kick_off_v2g_liberty.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_charger_settings_with_custom_port(
        self, v2g_globals, mock_settings_manager
    ):
        """Test saving charger settings with a custom Modbus port."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 5020,  # Non-default port
            "useReducedMaxChargePower": False,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        mock_settings_manager.store_setting.assert_any_call(
            "input_number.charger_port", 5020
        )

    @pytest.mark.asyncio
    async def test_save_charger_settings_execution_order(
        self, v2g_globals, mock_settings_manager, mock_hass, mock_v2g_main_app
    ):
        """Test that operations happen in the correct order: save -> event -> init -> kickoff."""
        # Arrange
        data = {
            "charger_type": "wallbox-quasar-1",
            "host": "192.168.1.100",
            "port": 502,
            "useReducedMaxChargePower": False,
        }
        call_order = []

        # Track call order
        mock_settings_manager.store_setting.side_effect = (
            lambda *args: call_order.append("store")
        )
        mock_hass.fire_event.side_effect = lambda *args: call_order.append("event")
        v2g_globals._V2GLibertyGlobals__initialise_charger_settings.side_effect = (
            lambda: call_order.append("init")
        )
        mock_v2g_main_app.kick_off_v2g_liberty.side_effect = lambda: call_order.append(
            "kickoff"
        )

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        # Check that "event" comes after all "store" calls and before "init" and "kickoff"
        event_index = call_order.index("event")
        init_index = call_order.index("init")
        kickoff_index = call_order.index("kickoff")

        # Count how many store calls happened before event
        store_count_before_event = sum(
            1 for call in call_order[:event_index] if call == "store"
        )

        assert store_count_before_event == 5  # All 5 basic settings stored before event
        assert event_index < init_index < kickoff_index  # Correct execution order

    @pytest.mark.asyncio
    async def test_save_charger_settings_sets_initialised_flag(
        self, v2g_globals, mock_settings_manager
    ):
        """Test that charger_settings_initialised flag is always set to True."""
        # Arrange
        data = {
            "charger_type": "evtec-bidi-pro-10",
            "host": "192.168.1.200",
            "port": 502,
            "useReducedMaxChargePower": True,
            "maxChargingPower": 10000,
            "maxDischargingPower": 9000,
        }

        # Act
        await v2g_globals._V2GLibertyGlobals__save_charger_settings(None, data, None)

        # Assert
        mock_settings_manager.store_setting.assert_any_call(
            "input_boolean.charger_settings_initialised", True
        )
