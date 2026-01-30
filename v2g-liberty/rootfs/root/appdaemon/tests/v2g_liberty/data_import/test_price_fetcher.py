"""Tests for PriceFetcher class."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.price_fetcher import PriceFetcher
from apps.v2g_liberty.data_import.utils.retry_handler import (
    RetryHandler,
    RetryConfig,
)
from apps.v2g_liberty.data_import import data_import_constants as fm_c


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
        start_time=fm_c.GET_PRICES_TIME,
        end_time=fm_c.TRY_UNTIL,
        interval_seconds=fm_c.CHECK_RESOLUTION_SECONDS,
    )


@pytest.fixture
def retry_handler(hass, retry_config):
    """Create a RetryHandler instance."""
    return RetryHandler(hass, retry_config)


@pytest.fixture
def price_fetcher(hass, fm_client, retry_handler):
    """Create a PriceFetcher instance."""
    return PriceFetcher(hass, fm_client, retry_handler)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing."""
    return datetime(2026, 1, 28, 15, 0, 0)


class TestPriceFetcher:
    """Test PriceFetcher class."""

    def test_initialisation(self, hass, fm_client, retry_handler):
        """Test PriceFetcher initialisation."""
        fetcher = PriceFetcher(hass, fm_client, retry_handler)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.retry_handler == retry_handler

    @pytest.mark.asyncio
    async def test_fetch_prices_consumption_success(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test successful fetch of consumption prices."""
        # Mock the FM client responses
        fm_client.get_sensor_data.side_effect = [
            # ENTSOE prices
            {"values": [0.15, 0.16, 0.17, None, 0.18]},
            # Consumption prices
            {"values": [0.20, 0.21, 0.22, None, 0.23]},
        ]

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is not None
        assert "prices" in result
        assert "entsoe_prices" in result
        assert "start" in result
        assert "latest_price_dt" in result
        assert "entsoe_latest_price_dt" in result
        assert result["prices"] == [0.20, 0.21, 0.22, None, 0.23]
        assert result["entsoe_prices"] == [0.15, 0.16, 0.17, None, 0.18]

    @pytest.mark.asyncio
    async def test_fetch_prices_production_success(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test successful fetch of production prices."""
        # Mock the FM client responses
        fm_client.get_sensor_data.side_effect = [
            # ENTSOE prices
            {"values": [0.15, 0.16, 0.17]},
            # Production prices
            {"values": [0.10, 0.11, 0.12]},
        ]

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("production", mock_now)

        assert result is not None
        assert result["prices"] == [0.10, 0.11, 0.12]

    @pytest.mark.asyncio
    async def test_fetch_prices_invalid_type(self, price_fetcher, mock_now):
        """Test fetch_prices with invalid price_type."""
        result = await price_fetcher.fetch_prices("invalid", mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_prices_fm_client_none(self, hass, retry_handler, mock_now):
        """Test fetch_prices when FM client is None."""
        fetcher = PriceFetcher(hass, None, retry_handler)
        result = await fetcher.fetch_prices("consumption", mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_prices_api_returns_none(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test fetch_prices when API returns None."""
        fm_client.get_sensor_data.return_value = None

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_prices_exception(self, price_fetcher, fm_client, mock_now):
        """Test fetch_prices handles exceptions."""
        fm_client.get_sensor_data.side_effect = Exception("API error")

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is None

    def test_find_latest_value_datetime_with_values(self, price_fetcher):
        """Test finding latest datetime with non-None values."""
        values = [0.1, 0.2, None, 0.3, 0.4]
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 60

        result = price_fetcher._find_latest_value_datetime(values, start, resolution)

        expected = start + timedelta(minutes=4 * 60)
        assert result == expected

    def test_find_latest_value_datetime_all_none(self, price_fetcher):
        """Test finding latest datetime when all values are None."""
        values = [None, None, None]
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 60

        result = price_fetcher._find_latest_value_datetime(values, start, resolution)

        assert result is None

    def test_find_latest_value_datetime_empty_list(self, price_fetcher):
        """Test finding latest datetime with empty list."""
        values = []
        start = datetime(2026, 1, 28, 0, 0, 0)
        resolution = 60

        result = price_fetcher._find_latest_value_datetime(values, start, resolution)

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_prices_before_publish_time(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test fetch_prices before GET_PRICES_TIME uses 2 days back."""
        early_morning = datetime(2026, 1, 28, 8, 0, 0)
        fm_client.get_sensor_data.side_effect = [
            {"values": [0.15]},
            {"values": [0.20]},
        ]

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=False,
        ):
            result = await price_fetcher.fetch_prices("consumption", early_morning)

        assert result is not None
        # Verify it used 2 days back (start date should be 2 days before)
        expected_start = datetime(2026, 1, 26, 0, 0, 0)
        # The actual start will be timezone-aware, but we can check it's roughly correct
        assert result["start"].day == expected_start.day

    @pytest.mark.asyncio
    async def test_fetch_prices_after_publish_time(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test fetch_prices after GET_PRICES_TIME uses 1 day back."""
        fm_client.get_sensor_data.side_effect = [
            {"values": [0.15]},
            {"values": [0.20]},
        ]

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is not None
        # Verify it used 1 day back
        expected_start = datetime(2026, 1, 27, 0, 0, 0)
        assert result["start"].day == expected_start.day
