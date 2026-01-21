"""Unit tests for SunSpec EVSE base class and Fermate FE20 implementation."""

import sys
import os

sys.path.insert(0, os.path.abspath("rootfs/root/appdaemon"))

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apps.v2g_liberty.chargers.modbus_types import MBR, ModbusConfigEntity
from apps.v2g_liberty.chargers.base_sunspec_evse import BaseSunSpecEVSE
from apps.v2g_liberty.chargers.fermate_fe20 import FermateFE20Client
from apps.v2g_liberty.event_bus import EventBus


@pytest.fixture
def mock_hass():
    """Mock Hass object."""
    hass = AsyncMock()
    hass.log = MagicMock()
    hass.get_state = AsyncMock()
    hass.run_every = AsyncMock(return_value="timer_handle")
    hass.cancel_timer = AsyncMock()
    return hass


@pytest.fixture
def mock_event_bus():
    """Mock EventBus object."""
    eb = MagicMock(spec=EventBus)
    eb.emit_event = MagicMock()
    return eb


@pytest.fixture
def mock_get_vehicle_func():
    """Mock function to get vehicle by ID."""
    return MagicMock(return_value=None)


class TestMBRScaleFactors:
    """Test MBR encode/decode for scale factor handling."""

    def test_decode_int16_positive(self):
        """Test decoding positive int16 value."""
        mbr = MBR(address=40163, data_type="int16", length=1)
        # -2 as int16 = 0xFFFE
        result = mbr.decode([0xFFFE])
        assert result == -2

    def test_decode_int16_negative(self):
        """Test decoding negative int16 value."""
        mbr = MBR(address=40163, data_type="int16", length=1)
        result = mbr.decode([65534])  # -2 as unsigned
        assert result == -2

    def test_decode_int16_zero(self):
        """Test decoding zero int16 value."""
        mbr = MBR(address=40163, data_type="int16", length=1)
        result = mbr.decode([0])
        assert result == 0

    def test_encode_int16_negative(self):
        """Test encoding negative int16 value."""
        mbr = MBR(address=40163, data_type="int16", length=1)
        result = mbr.encode(-2)
        # -2 & 0xFFFF = 65534
        assert result == [65534]

    def test_encode_int16_positive(self):
        """Test encoding positive int16 value."""
        mbr = MBR(address=40301, data_type="int16", length=1)
        result = mbr.encode(1000)
        assert result == [1000]


class TestSunSpecScaleFactorApplication:
    """Test scale factor application logic."""

    def test_apply_power_scale_factor_negative(self):
        """Test applying negative scale factor (e.g., -2)."""
        # Raw value 12340, scale factor -2 -> 123.4 -> 123
        raw_value = 12340
        w_sf = -2
        scaled = int(raw_value * (10**w_sf))
        assert scaled == 123

    def test_apply_power_scale_factor_zero(self):
        """Test applying zero scale factor."""
        raw_value = 1000
        w_sf = 0
        scaled = int(raw_value * (10**w_sf))
        assert scaled == 1000

    def test_apply_power_scale_factor_positive(self):
        """Test applying positive scale factor (e.g., 1)."""
        raw_value = 100
        w_sf = 1
        scaled = int(raw_value * (10**w_sf))
        assert scaled == 1000

    def test_remove_power_scale_factor(self):
        """Test removing scale factor for writing."""
        watts = 1234
        w_sf = -2
        register_value = int(watts / (10**w_sf))
        assert register_value == 123400


class TestSunSpecStateMapping:
    """Test SunSpec state mapping."""

    def test_state_mapping_off(self):
        """Test mapping Off state."""
        mapping = BaseSunSpecEVSE._SUNSPEC_STATE_MAPPING
        assert mapping[1] == 1  # Off -> No car connected

    def test_state_mapping_charging(self):
        """Test mapping Charging state."""
        mapping = BaseSunSpecEVSE._SUNSPEC_STATE_MAPPING
        assert mapping[9] == 3  # Charging -> Charging

    def test_state_mapping_discharging(self):
        """Test mapping Discharging state."""
        mapping = BaseSunSpecEVSE._SUNSPEC_STATE_MAPPING
        assert mapping[10] == 4  # Discharging -> Discharging

    def test_state_mapping_fault(self):
        """Test mapping Fault state."""
        mapping = BaseSunSpecEVSE._SUNSPEC_STATE_MAPPING
        assert mapping[7] == 9  # Fault -> Error

    def test_state_mapping_standby(self):
        """Test mapping Standby state."""
        mapping = BaseSunSpecEVSE._SUNSPEC_STATE_MAPPING
        assert mapping[8] == 2  # Standby -> Idle


class TestFermateFE20Client:
    """Test Fermate FE20 specific functionality."""

    def test_default_port(self, mock_hass, mock_event_bus, mock_get_vehicle_func):
        """Test that Fermate FE20 uses port 8502 by default."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        assert client._DEFAULT_PORT == 8502

    def test_charger_name(self, mock_hass, mock_event_bus, mock_get_vehicle_func):
        """Test charger name."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        assert client._CHARGER_NAME == "Fermate FE20"

    def test_soc_mce_address(self, mock_hass, mock_event_bus, mock_get_vehicle_func):
        """Test that SoC MCE uses custom address 41104."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        soc_mce = client._get_soc_mce()
        assert soc_mce.modbus_register.address == 41104

    def test_soc_mce_range(self, mock_hass, mock_event_bus, mock_get_vehicle_func):
        """Test SoC MCE value range."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        soc_mce = client._get_soc_mce()
        assert soc_mce.minimum_value == 0
        assert soc_mce.maximum_value == 100


class TestSunSpecEVSERegisters:
    """Test SunSpec register definitions."""

    def test_scale_factor_registers(self):
        """Test scale factor register addresses."""
        assert BaseSunSpecEVSE._MBR_W_SF.address == 40163
        assert BaseSunSpecEVSE._MBR_VA_SF.address == 40164
        assert BaseSunSpecEVSE._MBR_VAR_SF.address == 40165

    def test_der_measurement_registers(self):
        """Test DER AC Measurement register addresses."""
        assert BaseSunSpecEVSE._MBR_OPERATING_STATE.address == 40073
        assert BaseSunSpecEVSE._MBR_INVERTER_STATE.address == 40074
        assert BaseSunSpecEVSE._MBR_CONN_STATE.address == 40075
        assert BaseSunSpecEVSE._MBR_ACTUAL_POWER.address == 40080

    def test_der_capacity_registers(self):
        """Test DER Capacity register addresses."""
        assert BaseSunSpecEVSE._MBR_MAX_CHARGE_RATE.address == 40235
        assert BaseSunSpecEVSE._MBR_MAX_DISCHARGE_RATE.address == 40236

    def test_power_setpoint_register(self):
        """Test power setpoint register."""
        assert BaseSunSpecEVSE._MBR_POWER_SETPOINT.address == 40301


class TestModbusConfigEntityValidation:
    """Test MCE validation logic."""

    def test_soc_in_range(self):
        """Test SoC value in valid range."""
        mce = ModbusConfigEntity(
            modbus_register=MBR(address=41104, data_type="int16"),
            minimum_value=0,
            maximum_value=100,
            current_value=None,
        )
        changed = mce.set_value(50)
        assert changed is True
        assert mce.current_value == 50

    def test_soc_out_of_range_with_relaxed(self):
        """Test SoC value out of strict range but in relaxed range."""
        mce = ModbusConfigEntity(
            modbus_register=MBR(address=41104, data_type="int16"),
            minimum_value=2,
            maximum_value=97,
            relaxed_min_value=0,
            relaxed_max_value=100,
            current_value=None,
        )
        # Value 100 is outside 2-97 but inside relaxed 0-100
        # When current_value is None, relaxed range is used
        changed = mce.set_value(100)
        assert changed is True
        assert mce.current_value == 100

    def test_soc_out_of_all_ranges(self):
        """Test SoC value completely out of range."""
        mce = ModbusConfigEntity(
            modbus_register=MBR(address=41104, data_type="int16"),
            minimum_value=0,
            maximum_value=100,
            relaxed_min_value=0,
            relaxed_max_value=100,
            current_value=None,
        )
        changed = mce.set_value(150)
        assert changed is False  # None to None is not a change
        assert mce.current_value is None

    def test_power_negative_value(self):
        """Test negative power value (discharge)."""
        mce = ModbusConfigEntity(
            modbus_register=MBR(address=40080, data_type="int16"),
            minimum_value=-50000,
            maximum_value=50000,
            current_value=None,
        )
        changed = mce.set_value(-5000)
        assert changed is True
        assert mce.current_value == -5000


class TestSunSpecEVSEInitialization:
    """Test SunSpecEVSE initialization."""

    def test_initial_state(self, mock_hass, mock_event_bus, mock_get_vehicle_func):
        """Test initial state after construction."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)

        assert client._mb_client is None
        assert client._mb_host is None
        assert client._mb_port == 8502
        assert client._w_sf == 0
        assert client._va_sf == 0
        assert client._var_sf == 0
        assert client._max_charge_power_w is None
        assert client._max_discharge_power_w is None
        assert client._connected_car is None
        assert client._am_i_active is False

    def test_polling_entities_include_soc(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test that polling entities include SoC MCE."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        entities = client._get_polling_entities()

        # Should include base entities plus SoC
        assert len(entities) == 4
        soc_mce = client._get_soc_mce()
        assert soc_mce in entities


@pytest.mark.asyncio
class TestSunSpecEVSEScaleFactorMethods:
    """Test scale factor methods."""

    async def test_apply_power_scale_factor_method(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test _apply_power_scale_factor method."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        client._w_sf = -2  # Scale factor of -2

        result = client._apply_power_scale_factor(12340)
        assert result == 123  # 12340 * 10^-2 = 123.4 -> 123

    async def test_apply_power_scale_factor_none(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test _apply_power_scale_factor with None input."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        client._w_sf = -2

        result = client._apply_power_scale_factor(None)
        assert result is None

    async def test_remove_power_scale_factor_method(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test _remove_power_scale_factor method."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)
        client._w_sf = -2

        result = client._remove_power_scale_factor(1234)
        assert result == 123400  # 1234 / 10^-2 = 123400

    async def test_get_base_state_charging(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test _get_base_state for charging state."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)

        result = client._get_base_state(9)  # SunSpec Charging
        assert result == 3  # Base Charging

    async def test_get_base_state_unknown(
        self, mock_hass, mock_event_bus, mock_get_vehicle_func
    ):
        """Test _get_base_state for unknown state."""
        client = FermateFE20Client(mock_hass, mock_event_bus, mock_get_vehicle_func)

        result = client._get_base_state(99)  # Unknown state
        assert result == 0  # Default to Starting up
