"""Tests for EnergyFetcher class."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.energy_fetcher import EnergyFetcher


@pytest.fixture
def hass():
    """Create a mock Hass instance."""
    hass = AsyncMock(spec=Hass)
    hass.log = MagicMock()
    return hass


@pytest.fixture
def fm_client():
    """Create a mock FlexMeasures client."""
    client = AsyncMock()
    client.get_sensor_data = AsyncMock()
    return client


@pytest.fixture
def energy_fetcher(hass, fm_client):
    """Create an EnergyFetcher instance."""
    return EnergyFetcher(hass, fm_client)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing."""
    return datetime(2026, 1, 28, 2, 0, 0)


class TestEnergyFetcher:
    """Test EnergyFetcher class."""

    def test_initialisation(self, hass, fm_client):
        """Test EnergyFetcher initialisation."""
        fetcher = EnergyFetcher(hass, fm_client)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.DAYS_HISTORY == 7

    @pytest.mark.asyncio
    async def test_fetch_power_data_success(self, energy_fetcher, fm_client, mock_now):
        """Test successful fetch of power data."""
        # Mock the FM client response
        fm_client.get_sensor_data.return_value = {
            "values": [0.005, 0.003, -0.002, None, 0.004, 0.006, -0.001]
        }

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert "power_values" in result
        assert "start" in result
        assert result["power_values"] == [
            0.005,
            0.003,
            -0.002,
            None,
            0.004,
            0.006,
            -0.001,
        ]

    @pytest.mark.asyncio
    async def test_fetch_power_data_fm_client_none(self, hass, mock_now):
        """Test fetch_power_data when FM client is None."""
        fetcher = EnergyFetcher(hass, None)
        result = await fetcher.fetch_power_data(mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_power_data_api_returns_none(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data when API returns None."""
        fm_client.get_sensor_data.return_value = None

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_power_data_exception(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data handles exceptions."""
        fm_client.get_sensor_data.side_effect = Exception("API error")

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_power_data_correct_parameters(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test that fetch_power_data calls API with correct parameters."""
        fm_client.get_sensor_data.return_value = {"values": [0.005, 0.003]}

        await energy_fetcher.fetch_power_data(mock_now)

        # Verify the API was called with correct parameters
        fm_client.get_sensor_data.assert_called_once()
        call_kwargs = fm_client.get_sensor_data.call_args.kwargs
        assert "sensor_id" in call_kwargs
        assert "start" in call_kwargs
        assert "duration" in call_kwargs
        assert call_kwargs["duration"] == "P7D"  # 7 days
        assert "resolution" in call_kwargs
        assert call_kwargs["uom"] == "MW"

    @pytest.mark.asyncio
    async def test_fetch_power_data_with_positive_values(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data with only positive (charging) values."""
        fm_client.get_sensor_data.return_value = {
            "values": [0.005, 0.003, 0.004, 0.006]
        }

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert all(v >= 0 for v in result["power_values"] if v is not None)

    @pytest.mark.asyncio
    async def test_fetch_power_data_with_negative_values(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data with negative (discharging) values."""
        fm_client.get_sensor_data.return_value = {
            "values": [-0.005, -0.003, -0.004, -0.006]
        }

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert all(v <= 0 for v in result["power_values"] if v is not None)

    @pytest.mark.asyncio
    async def test_fetch_power_data_with_mixed_values(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data with mixed charging and discharging values."""
        fm_client.get_sensor_data.return_value = {
            "values": [0.005, -0.003, 0.004, -0.006]
        }

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert len(result["power_values"]) == 4
        assert result["power_values"][0] > 0  # Charging
        assert result["power_values"][1] < 0  # Discharging
        assert result["power_values"][2] > 0  # Charging
        assert result["power_values"][3] < 0  # Discharging

    @pytest.mark.asyncio
    async def test_fetch_power_data_all_none_values(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data when all values are None."""
        fm_client.get_sensor_data.return_value = {"values": [None, None, None]}

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert result["power_values"] == [None, None, None]

    @pytest.mark.asyncio
    async def test_fetch_power_data_empty_values(
        self, energy_fetcher, fm_client, mock_now
    ):
        """Test fetch_power_data when values list is empty."""
        fm_client.get_sensor_data.return_value = {"values": []}

        result = await energy_fetcher.fetch_power_data(mock_now)

        assert result is not None
        assert result["power_values"] == []
