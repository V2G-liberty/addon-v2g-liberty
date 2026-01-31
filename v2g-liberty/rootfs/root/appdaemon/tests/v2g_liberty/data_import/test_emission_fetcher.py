"""Tests for EmissionFetcher class."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
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
    """Create a fixed datetime for testing (afternoon, after EPEX publication)."""
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
        assert fetcher.ENTSOE_SENSOR_ID == 14
        assert fetcher.ENTSOE_SOURCE_ID == 37

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_success(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test successful fetch of emission data with ENTSOE timing."""
        mock_is_local_now_between.return_value = True  # Afternoon scenario

        # Mock the FM client response for both ENTSOE and emissions calls
        entsoe_response = {"values": [50.0, 60.0, 70.0, None, None]}
        emission_response = {"values": [100.0, 120.0, 110.0, None, 130.0]}
        fm_client.get_sensor_data.side_effect = [entsoe_response, emission_response]

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is not None
        assert "emissions" in result
        assert "start" in result
        assert "latest_emission_dt" in result
        assert "entsoe_latest_dt" in result
        assert result["emissions"] == [100.0, 120.0, 110.0, None, 130.0]
        assert result["latest_emission_dt"] is not None
        assert result["entsoe_latest_dt"] is not None
        # ENTSOE data has 3 non-None values (indices 0, 1, 2)
        # So entsoe_latest_dt should be at index 2

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_entsoe_returns_none(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions when ENTSOE returns None (emissions still works)."""
        mock_is_local_now_between.return_value = True

        # ENTSOE returns None, emissions returns data
        emission_response = {"values": [100.0, 120.0, 110.0]}
        fm_client.get_sensor_data.side_effect = [None, emission_response]

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is not None
        assert result["entsoe_latest_dt"] is None
        assert result["emissions"] == [100.0, 120.0, 110.0]

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_fm_client_none(
        self, mock_is_local_now_between, hass, retry_handler, mock_now
    ):
        """Test fetch_emissions when FM client is None."""
        fetcher = EmissionFetcher(hass, None, retry_handler)
        result = await fetcher.fetch_emissions(mock_now)
        assert result is None

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_api_returns_none(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions when emission API returns None."""
        mock_is_local_now_between.return_value = True

        # ENTSOE returns data, emissions returns None
        entsoe_response = {"values": [50.0, 60.0]}
        fm_client.get_sensor_data.side_effect = [entsoe_response, None]

        result = await emission_fetcher.fetch_emissions(mock_now)

        assert result is None

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_exception(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test fetch_emissions handles exceptions."""
        mock_is_local_now_between.return_value = True
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
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_correct_parameters_afternoon(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test that fetch_emissions calls API with correct parameters (afternoon)."""
        mock_is_local_now_between.return_value = True  # Afternoon = days_back = 1

        entsoe_response = {"values": [50.0, 60.0]}
        emission_response = {"values": [100.0, 120.0]}
        fm_client.get_sensor_data.side_effect = [entsoe_response, emission_response]

        await emission_fetcher.fetch_emissions(mock_now)

        # Verify the API was called twice (ENTSOE + emissions)
        assert fm_client.get_sensor_data.call_count == 2

        # Check ENTSOE call (first call)
        entsoe_call_kwargs = fm_client.get_sensor_data.call_args_list[0].kwargs
        assert entsoe_call_kwargs["sensor_id"] == 14  # ENTSOE_SENSOR_ID
        assert entsoe_call_kwargs["source"] == 37  # ENTSOE_SOURCE_ID
        assert entsoe_call_kwargs["duration"] == "P3D"  # days_back(1) + 2

        # Check emissions call (second call)
        emission_call_kwargs = fm_client.get_sensor_data.call_args_list[1].kwargs
        assert "sensor_id" in emission_call_kwargs
        assert "source" not in emission_call_kwargs  # Emissions don't use source
        assert emission_call_kwargs["duration"] == "P3D"

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_correct_parameters_morning(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test that fetch_emissions calls API with correct parameters (morning)."""
        mock_is_local_now_between.return_value = False  # Morning = days_back = 2

        entsoe_response = {"values": [50.0, 60.0]}
        emission_response = {"values": [100.0, 120.0]}
        fm_client.get_sensor_data.side_effect = [entsoe_response, emission_response]

        await emission_fetcher.fetch_emissions(mock_now)

        # Check that duration is P4D for morning (days_back(2) + 2)
        entsoe_call_kwargs = fm_client.get_sensor_data.call_args_list[0].kwargs
        assert entsoe_call_kwargs["duration"] == "P4D"

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.fetchers.emission_fetcher.is_local_now_between"
    )
    async def test_fetch_emissions_entsoe_latest_dt_calculation(
        self, mock_is_local_now_between, emission_fetcher, fm_client, mock_now
    ):
        """Test that entsoe_latest_dt is correctly calculated from ENTSOE data."""
        mock_is_local_now_between.return_value = True

        # ENTSOE has values at indices 0, 1, 2 (last non-None at index 2)
        entsoe_response = {"values": [50.0, 60.0, 70.0, None, None]}
        emission_response = {"values": [100.0, 120.0, 110.0, 80.0, 130.0]}
        fm_client.get_sensor_data.side_effect = [entsoe_response, emission_response]

        result = await emission_fetcher.fetch_emissions(mock_now)

        # entsoe_latest_dt should be at index 2 (third value)
        # With 5-minute resolution, that's start + 10 minutes
        assert result["entsoe_latest_dt"] is not None
        # latest_emission_dt should be at index 4 (fifth value)
        assert result["latest_emission_dt"] is not None
        # entsoe_latest_dt should be earlier than latest_emission_dt
        assert result["entsoe_latest_dt"] < result["latest_emission_dt"]
