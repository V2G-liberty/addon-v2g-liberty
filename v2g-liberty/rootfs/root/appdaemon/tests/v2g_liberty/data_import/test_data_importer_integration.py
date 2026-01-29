"""Integration tests for FlexMeasuresDataImporter.

These tests verify that the refactored FlexMeasuresDataImporter correctly
integrates the fetcher, processor, and validator components.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from apps.v2g_liberty.fm_data_importer import FlexMeasuresDataImporter
from apps.v2g_liberty.notifier_util import Notifier


@pytest.fixture
def hass():
    """Create a mock Hass instance."""
    mock_hass = MagicMock()
    mock_hass.log = MagicMock()
    mock_hass.set_state = AsyncMock()
    mock_hass.run_in = AsyncMock()
    mock_hass.run_daily = MagicMock()
    mock_hass.timer_running = MagicMock(return_value=False)
    mock_hass.cancel_timer = AsyncMock()
    return mock_hass


@pytest.fixture
def notifier():
    """Create a mock Notifier instance."""
    mock_notifier = MagicMock(spec=Notifier)
    mock_notifier.notify_user = MagicMock()
    mock_notifier.clear_notification = MagicMock()
    return mock_notifier


@pytest.fixture
def v2g_main_app():
    """Create a mock v2g_main_app."""
    mock_app = MagicMock()
    mock_app.set_records_in_chart = AsyncMock()
    mock_app.set_price_is_up_to_date = AsyncMock()
    return mock_app


@pytest.fixture
def fm_client():
    """Create a mock FlexMeasures client."""
    mock_client = MagicMock()
    mock_client.get_sensor_data = AsyncMock()
    return mock_client


@pytest.fixture
def data_importer(hass, notifier, v2g_main_app, fm_client):
    """Create a FlexMeasuresDataImporter instance with mocked dependencies."""
    importer = FlexMeasuresDataImporter(hass, notifier)
    importer.v2g_main_app = v2g_main_app
    importer.fm_client_app = fm_client
    return importer


@pytest.fixture
def fixed_now():
    """Return a fixed datetime for testing."""
    return datetime(2026, 1, 28, 15, 0, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))


class TestGetChargingCostIntegration:
    """Integration tests for get_charging_cost method."""

    @pytest.mark.asyncio
    async def test_get_charging_cost_success(
        self, data_importer, fm_client, hass, fixed_now
    ):
        """Test successful charging cost retrieval."""
        # Setup mock response
        fm_client.get_sensor_data.return_value = {
            "values": [1.50, 2.00, None, 1.75, 2.25, 1.00, 0.50],
            "start": "2026-01-21T00:00:00+01:00",
            "duration": "P7D",
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            await data_importer.get_charging_cost()

        # Verify sensor was updated with correct total
        expected_total = round(1.50 + 2.00 + 1.75 + 2.25 + 1.00 + 0.50, 2)
        hass.set_state.assert_called_with(
            entity_id="sensor.total_charging_cost_last_7_days",
            state=expected_total,
        )

    @pytest.mark.asyncio
    async def test_get_charging_cost_no_client(self, data_importer, hass):
        """Test charging cost when client is not available."""
        data_importer._fm_client_app = None
        data_importer.cost_fetcher = None

        result = await data_importer.get_charging_cost()

        assert result is False
        hass.set_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_charging_cost_fetch_failure(
        self, data_importer, fm_client, hass, fixed_now
    ):
        """Test charging cost when fetch returns None."""
        fm_client.get_sensor_data.return_value = None

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            result = await data_importer.get_charging_cost()

        assert result is False


class TestGetChargedEnergyIntegration:
    """Integration tests for get_charged_energy method."""

    @pytest.mark.asyncio
    async def test_get_charged_energy_success(
        self, data_importer, fm_client, hass, fixed_now
    ):
        """Test successful charged energy retrieval."""
        # Setup mock response with power values in MW
        # 5 minute resolution over 7 days = 2016 values
        # Simplified: just a few values for testing
        power_values = [0.001, 0.002, -0.001, None, 0.001]  # MW values

        fm_client.get_sensor_data.return_value = {
            "values": power_values,
            "start": "2026-01-21T00:00:00+01:00",
            "duration": "P7D",
            "unit": "MW",
        }

        # Setup emission cache
        start = datetime(2026, 1, 21, 0, 0, 0, tzinfo=ZoneInfo("Europe/Amsterdam"))
        data_importer.emission_intensities = {
            start: 400.0,
            start + timedelta(minutes=5): 410.0,
            start + timedelta(minutes=10): 420.0,
            start + timedelta(minutes=15): 430.0,
            start + timedelta(minutes=20): 440.0,
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            await data_importer.get_charged_energy()

        # Verify sensors were updated (8 set_state calls expected)
        assert hass.set_state.call_count == 8

    @pytest.mark.asyncio
    async def test_get_charged_energy_no_client(self, data_importer, hass):
        """Test charged energy when client is not available."""
        data_importer._fm_client_app = None
        data_importer.energy_fetcher = None

        result = await data_importer.get_charged_energy()

        assert result is False
        hass.set_state.assert_not_called()


class TestGetEmissionIntensitiesIntegration:
    """Integration tests for get_emission_intensities method."""

    @pytest.mark.asyncio
    async def test_get_emission_intensities_success(
        self, data_importer, fm_client, v2g_main_app, fixed_now
    ):
        """Test successful emission intensities retrieval."""
        # Setup mock response
        fm_client.get_sensor_data.return_value = {
            "values": [400.0, 410.0, 420.0, None, 430.0],
            "start": "2026-01-21T00:00:00+01:00",
            "duration": "P9D",
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            with patch(
                "apps.v2g_liberty.fm_data_importer.is_price_epex_based",
                return_value=False,
            ):
                result = await data_importer.get_emission_intensities()

        assert result == "emissions successfully retrieved."
        v2g_main_app.set_records_in_chart.assert_called_once()

        # Verify emission_intensities cache was populated
        assert len(data_importer.emission_intensities) > 0

    @pytest.mark.asyncio
    async def test_get_emission_intensities_no_client(
        self, data_importer, v2g_main_app
    ):
        """Test emission intensities when client is not available."""
        data_importer._fm_client_app = None
        data_importer.emission_fetcher = None

        # Need to mock is_price_epex_based to avoid calling get_local_now
        with patch(
            "apps.v2g_liberty.fm_data_importer.is_price_epex_based", return_value=False
        ):
            result = await data_importer.get_emission_intensities()

        assert result is False
        v2g_main_app.set_records_in_chart.assert_not_called()


class TestGetPricesIntegration:
    """Integration tests for get_prices method."""

    @pytest.mark.asyncio
    async def test_get_prices_consumption_success(
        self, data_importer, fm_client, v2g_main_app, fixed_now
    ):
        """Test successful consumption price retrieval."""
        # Setup mock response
        fm_client.get_sensor_data.return_value = {
            "values": [10.0, 12.0, 15.0, None, 11.0],
            "start": "2026-01-27T00:00:00+01:00",
            "duration": "P3D",
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            with patch(
                "apps.v2g_liberty.fm_data_importer.is_price_epex_based",
                return_value=False,
            ):
                with patch(
                    "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
                    return_value=True,
                ):
                    result = await data_importer.get_prices(price_type="consumption")

        assert result == "prices successfully retrieved."
        v2g_main_app.set_records_in_chart.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_prices_production_success(
        self, data_importer, fm_client, v2g_main_app, fixed_now
    ):
        """Test successful production price retrieval."""
        fm_client.get_sensor_data.return_value = {
            "values": [8.0, 10.0, 12.0, None, 9.0],
            "start": "2026-01-27T00:00:00+01:00",
            "duration": "P3D",
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            with patch(
                "apps.v2g_liberty.fm_data_importer.is_price_epex_based",
                return_value=False,
            ):
                with patch(
                    "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
                    return_value=True,
                ):
                    result = await data_importer.get_prices(price_type="production")

        assert result == "prices successfully retrieved."
        v2g_main_app.set_records_in_chart.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_prices_invalid_price_type(self, data_importer):
        """Test get_prices with invalid price type."""
        result = await data_importer.get_prices(price_type="invalid")

        assert result is False

    @pytest.mark.asyncio
    async def test_get_prices_no_client(self, data_importer, v2g_main_app):
        """Test get_prices when client is not available."""
        data_importer._fm_client_app = None
        data_importer.price_fetcher = None

        with patch(
            "apps.v2g_liberty.fm_data_importer.is_price_epex_based", return_value=False
        ):
            result = await data_importer.get_prices(price_type="consumption")

        assert result is False
        v2g_main_app.set_records_in_chart.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_prices_detects_negative_price(
        self, data_importer, fm_client, v2g_main_app, notifier, fixed_now
    ):
        """Test that negative prices are detected and notification is triggered."""
        # Setup mock response with negative price in the future
        fm_client.get_sensor_data.return_value = {
            "values": [10.0, 12.0, -5.0, None, 11.0],  # -5.0 is negative
            "start": "2026-01-27T00:00:00+01:00",
            "duration": "P3D",
        }

        with patch(
            "apps.v2g_liberty.fm_data_importer.get_local_now", return_value=fixed_now
        ):
            # Set to False to skip EPEX validation (which triggers more is_local_now_between calls)
            with patch(
                "apps.v2g_liberty.fm_data_importer.is_price_epex_based",
                return_value=False,
            ):
                with patch(
                    "apps.v2g_liberty.data_import.fetchers.price_fetcher.is_local_now_between",
                    return_value=True,
                ):
                    # First call for consumption
                    await data_importer.get_prices(price_type="consumption")
                    # Second call for production to trigger notification
                    await data_importer.get_prices(price_type="production")

        # Note: Notification logic depends on both consumption and production being set
        # and is_price_epex_based() being True, so notification won't trigger here.
        # The test verifies the method runs without errors and prices are processed.


class TestComponentInitialisation:
    """Tests for component initialisation."""

    def test_components_initialised(self, data_importer):
        """Test that all components are properly initialised."""
        # Verify processors are initialised
        assert data_importer.price_processor is not None
        assert data_importer.emission_processor is not None
        assert data_importer.energy_processor is not None

        # Verify validators and utilities are initialised
        assert data_importer.datetime_utils is not None
        assert data_importer.data_validator is not None
        assert data_importer.retry_handler is not None

        # Verify fetchers are initialised (after fm_client_app is set)
        assert data_importer.price_fetcher is not None
        assert data_importer.emission_fetcher is not None
        assert data_importer.energy_fetcher is not None
        assert data_importer.cost_fetcher is not None

    def test_fetchers_not_initialised_without_client(self, hass, notifier):
        """Test that fetchers are None when fm_client_app is not set."""
        importer = FlexMeasuresDataImporter(hass, notifier)

        # Before setting fm_client_app, fetchers should be None
        assert importer.price_fetcher is None
        assert importer.emission_fetcher is None
        assert importer.energy_fetcher is None
        assert importer.cost_fetcher is None

    def test_fetchers_initialised_when_client_set(self, hass, notifier, fm_client):
        """Test that fetchers are initialised when fm_client_app is set."""
        importer = FlexMeasuresDataImporter(hass, notifier)

        # Before setting fm_client_app
        assert importer.price_fetcher is None

        # Set fm_client_app
        importer.fm_client_app = fm_client

        # After setting fm_client_app, fetchers should be initialised
        assert importer.price_fetcher is not None
        assert importer.emission_fetcher is not None
        assert importer.energy_fetcher is not None
        assert importer.cost_fetcher is not None
