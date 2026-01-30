"""Tests for EmissionFetcher class."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.emission_fetcher import EmissionFetcher
from apps.v2g_liberty.data_import.utils.retry_handler import (
    RetryHandler,
    RetryConfig,
)


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
def retry_config():
    """Create a retry configuration."""
    return RetryConfig(
        start_time="13:45:26", end_time="11:22:33", interval_seconds=1800
    )


@pytest.fixture
def retry_handler(hass, retry_config):
    """Create a RetryHandler instance."""
    return RetryHandler(hass, retry_config)


@pytest.fixture
def emission_fetcher(hass, fm_client, retry_handler):
    """Create an EmissionFetcher instance."""
    return EmissionFetcher(hass, fm_client, retry_handler)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing."""
    return datetime(2026, 1, 28, 15, 0, 0)


class TestEmissionFetcher:
    """Test EmissionFetcher class."""

    def test_initialisation(self, hass, fm_client, retry_handler):
        """Test EmissionFetcher initialisation."""
        fetcher = EmissionFetcher(hass, fm_client, retry_handler)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.retry_handler == retry_handler
        assert fetcher.DAYS_HISTORY == 7

    @pytest.mark.asyncio
    async def test_fetch_emissions_success(self, emission_fetcher, fm_client, mock_now):
        """Test successful fetch of emission data."""
        # Mock the FM client response
        fm_client.get_sensor_data.return_value = {
            "values": [100.0, 120.0, 110.0, None, 130.0]
        }

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is not None
        assert "emissions" in result
        assert "start" in result
        assert "latest_emission_dt" in result
        assert result["emissions"] == [100.0, 120.0, 110.0, None, 130.0]
        assert result["latest_emission_dt"] is not None

    @pytest.mark.asyncio
    async def test_fetch_emissions_fm_client_none(self, hass, retry_handler, mock_now):
        """Test fetch_emissions when FM client is None."""
        fetcher = EmissionFetcher(hass, None, retry_handler)
        result = await fetcher.fetch_emissions(mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_emissions_api_returns_none(
        self, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions when API returns None."""
        fm_client.get_sensor_data.return_value = None

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_emissions_exception(
        self, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions handles exceptions."""
        fm_client.get_sensor_data.side_effect = Exception("API error")

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is None

    def test_find_latest_value_datetime_with_values(self, emission_fetcher):
        """Test finding latest datetime with non-None values."""
        values = [100.0, 120.0, None, 110.0, 130.0]
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 15

        result = emission_fetcher._find_latest_value_datetime(values, start, resolution)

        expected = start + timedelta(minutes=4 * 15)
        assert result == expected

    def test_find_latest_value_datetime_all_none(self, emission_fetcher):
        """Test finding latest datetime when all values are None."""
        values = [None, None, None]
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 15

        result = emission_fetcher._find_latest_value_datetime(values, start, resolution)

        assert result is None

    def test_find_latest_value_datetime_empty_list(self, emission_fetcher):
        """Test finding latest datetime with empty list."""
        values = []
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 15

        result = emission_fetcher._find_latest_value_datetime(values, start, resolution)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_emissions_correct_parameters(
        self, emission_fetcher, fm_client, mock_now
    ):
        """Test that fetch_emissions calls API with correct parameters."""
        fm_client.get_sensor_data.return_value = {"values": [100.0, 120.0]}

        await emission_fetcher.fetch_emissions(mock_now)

        # Verify the API was called with correct parameters
        fm_client.get_sensor_data.assert_called_once()
        call_kwargs = fm_client.get_sensor_data.call_args.kwargs
        assert "sensor_id" in call_kwargs
        assert "start" in call_kwargs
        assert "duration" in call_kwargs
        assert call_kwargs["duration"] == "P9D"  # DAYS_HISTORY + 2
        assert "resolution" in call_kwargs
        assert "uom" in call_kwargs
