"""Tests for CostFetcher class."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.cost_fetcher import CostFetcher
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
        start_time="01:15:00", end_time="03:00:00", interval_seconds=1800
    )


@pytest.fixture
def retry_handler(hass, retry_config):
    """Create a RetryHandler instance."""
    return RetryHandler(hass, retry_config)


@pytest.fixture
def cost_fetcher(hass, fm_client, retry_handler):
    """Create a CostFetcher instance."""
    return CostFetcher(hass, fm_client, retry_handler)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing."""
    return datetime(2026, 1, 28, 2, 0, 0)


class TestCostFetcher:
    """Test CostFetcher class."""

    def test_initialisation(self, hass, fm_client, retry_handler):
        """Test CostFetcher initialisation."""
        fetcher = CostFetcher(hass, fm_client, retry_handler)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.retry_handler == retry_handler
        assert fetcher.DAYS_HISTORY == 7

    @pytest.mark.asyncio
    async def test_fetch_costs_success(self, cost_fetcher, fm_client, mock_now):
        """Test successful fetch of cost data."""
        # Mock the FM client response
        fm_client.get_sensor_data.return_value = {
            "values": [1.50, 2.30, 1.80, None, 2.10, 1.95, 2.40]
        }

        result = await cost_fetcher.fetch_costs(mock_now)

        assert result is not None
        assert "costs" in result
        assert "start" in result
        assert result["costs"] == [1.50, 2.30, 1.80, None, 2.10, 1.95, 2.40]

    @pytest.mark.asyncio
    async def test_fetch_costs_fm_client_none(self, hass, retry_handler, mock_now):
        """Test fetch_costs when FM client is None."""
        fetcher = CostFetcher(hass, None, retry_handler)
        result = await fetcher.fetch_costs(mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_costs_api_returns_none(
        self, cost_fetcher, fm_client, mock_now
    ):
        """Test fetch_costs when API returns None."""
        fm_client.get_sensor_data.return_value = None

        result = await cost_fetcher.fetch_costs(mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_costs_exception(self, cost_fetcher, fm_client, mock_now):
        """Test fetch_costs handles exceptions."""
        fm_client.get_sensor_data.side_effect = Exception("API error")

        result = await cost_fetcher.fetch_costs(mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_costs_correct_parameters(
        self, cost_fetcher, fm_client, mock_now
    ):
        """Test that fetch_costs calls API with correct parameters."""
        fm_client.get_sensor_data.return_value = {"values": [1.50, 2.30]}

        await cost_fetcher.fetch_costs(mock_now)

        # Verify the API was called with correct parameters
        fm_client.get_sensor_data.assert_called_once()
        call_kwargs = fm_client.get_sensor_data.call_args.kwargs
        assert "sensor_id" in call_kwargs
        assert "start" in call_kwargs
        assert "duration" in call_kwargs
        assert call_kwargs["resolution"] == "P1D"  # Daily resolution
        assert "uom" in call_kwargs

    @pytest.mark.asyncio
    async def test_fetch_costs_all_none_values(self, cost_fetcher, fm_client, mock_now):
        """Test fetch_costs when all values are None."""
        fm_client.get_sensor_data.return_value = {"values": [None, None, None]}

        result = await cost_fetcher.fetch_costs(mock_now)

        assert result is not None
        assert result["costs"] == [None, None, None]

    @pytest.mark.asyncio
    async def test_fetch_costs_empty_values(self, cost_fetcher, fm_client, mock_now):
        """Test fetch_costs when values list is empty."""
        fm_client.get_sensor_data.return_value = {"values": []}

        result = await cost_fetcher.fetch_costs(mock_now)

        assert result is not None
        assert result["costs"] == []
