"""Tests for PriceFetcher class."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.price_fetcher import PriceFetcher


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
def price_fetcher(hass, fm_client):
    """Create a PriceFetcher instance."""
    return PriceFetcher(hass, fm_client)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing."""
    return datetime(2026, 1, 28, 15, 0, 0)


class TestPriceFetcher:
    """Test PriceFetcher class."""

    def test_initialisation(self, hass, fm_client):
        """Test PriceFetcher initialisation."""
        fetcher = PriceFetcher(hass, fm_client)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client

    @pytest.mark.asyncio
    async def test_fetch_prices_consumption_success(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test successful fetch of consumption prices."""
        # Mock the FM client response (only prices, no ENTSOE)
        fm_client.get_sensor_data.return_value = {
            "values": [0.20, 0.21, 0.22, None, 0.23]
        }

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is not None
        assert "prices" in result
        assert "start" in result
        # ENTSOE data is no longer fetched by PriceFetcher
        assert "entsoe_prices" not in result
        assert "entsoe_latest_price_dt" not in result
        assert result["prices"] == [0.20, 0.21, 0.22, None, 0.23]

    @pytest.mark.asyncio
    async def test_fetch_prices_production_success(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test successful fetch of production prices."""
        # Mock the FM client response
        fm_client.get_sensor_data.return_value = {"values": [0.10, 0.11, 0.12]}

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
    async def test_fetch_prices_fm_client_none(self, hass, mock_now):
        """Test fetch_prices when FM client is None."""
        fetcher = PriceFetcher(hass, None)
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

    @pytest.mark.asyncio
    async def test_fetch_prices_before_publish_time(
        self, price_fetcher, fm_client, mock_now
    ):
        """Test fetch_prices before GET_PRICES_TIME uses 2 days back."""
        early_morning = datetime(2026, 1, 28, 8, 0, 0)
        fm_client.get_sensor_data.return_value = {"values": [0.20]}

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
        fm_client.get_sensor_data.return_value = {"values": [0.20]}

        with patch(
            "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
            return_value=True,
        ):
            result = await price_fetcher.fetch_prices("consumption", mock_now)

        assert result is not None
        # Verify it used 1 day back
        expected_start = datetime(2026, 1, 27, 0, 0, 0)
        assert result["start"].day == expected_start.day
