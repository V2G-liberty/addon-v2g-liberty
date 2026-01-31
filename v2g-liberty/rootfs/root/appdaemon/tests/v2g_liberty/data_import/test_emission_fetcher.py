"""Tests for EmissionFetcher class."""

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.emission_fetcher import EmissionFetcher


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
def emission_fetcher(hass, fm_client):
    """Create an EmissionFetcher instance."""
    return EmissionFetcher(hass, fm_client)


@pytest.fixture
def mock_now():
    """Create a fixed datetime for testing (afternoon, after EPEX publication)."""
    return datetime(2026, 1, 28, 15, 0, 0)


class TestEmissionFetcher:
    """Test EmissionFetcher class."""

    def test_initialisation(self, hass, fm_client):
        """Test EmissionFetcher initialisation."""
        fetcher = EmissionFetcher(hass, fm_client)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.DAYS_HISTORY == 7

    @pytest.mark.asyncio
    async def test_fetch_emissions_success(self, emission_fetcher, fm_client, mock_now):
        """Test successful fetch of emission data."""
        # Mock the FM client response (only emissions, no ENTSOE)
        emission_response = {"values": [100.0, 120.0, 110.0, None, 130.0]}
        fm_client.get_sensor_data.return_value = emission_response

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is not None
        assert "emissions" in result
        assert "start" in result
        # ENTSOE data is no longer fetched by EmissionFetcher
        assert "entsoe_latest_dt" not in result
        assert result["emissions"] == [100.0, 120.0, 110.0, None, 130.0]

    @pytest.mark.asyncio
    async def test_fetch_emissions_fm_client_none(self, hass, mock_now):
        """Test fetch_emissions when FM client is None."""
        fetcher = EmissionFetcher(hass, None)
        result = await fetcher.fetch_emissions(mock_now)
        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_emissions_api_returns_none(
        self, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions when emission API returns None."""
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

    @pytest.mark.asyncio
    async def test_fetch_emissions_correct_parameters(
        self, emission_fetcher, fm_client, mock_now
    ):
        """Test that fetch_emissions calls API with correct parameters."""
        emission_response = {"values": [100.0, 120.0]}
        fm_client.get_sensor_data.return_value = emission_response

        await emission_fetcher.fetch_emissions(mock_now)

        # Verify the API was called once (only emissions, no ENTSOE)
        assert fm_client.get_sensor_data.call_count == 1

        # Check emissions call parameters
        emission_call_kwargs = fm_client.get_sensor_data.call_args.kwargs
        assert "sensor_id" in emission_call_kwargs
        assert "start" in emission_call_kwargs
        assert "duration" in emission_call_kwargs
        assert "resolution" in emission_call_kwargs
        # Duration should be P9D (DAYS_HISTORY=7 + 2)
        assert emission_call_kwargs["duration"] == "P9D"
