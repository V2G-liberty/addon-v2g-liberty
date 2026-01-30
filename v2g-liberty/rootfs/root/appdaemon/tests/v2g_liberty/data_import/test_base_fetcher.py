"""Tests for BaseFetcher class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.fetchers.base_fetcher import BaseFetcher
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
    hass.run_in = AsyncMock()
    return hass


@pytest.fixture
def fm_client():
    """Create a mock FlexMeasures client."""
    return AsyncMock()


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
def base_fetcher(hass, fm_client, retry_handler):
    """Create a BaseFetcher instance."""
    return BaseFetcher(hass, fm_client, retry_handler)


class TestBaseFetcher:
    """Test BaseFetcher class."""

    def test_initialisation(self, hass, fm_client, retry_handler):
        """Test BaseFetcher initialisation."""
        fetcher = BaseFetcher(hass, fm_client, retry_handler)
        assert fetcher.hass == hass
        assert fetcher.fm_client_app == fm_client
        assert fetcher.retry_handler == retry_handler

    def test_is_client_available_when_client_exists(self, base_fetcher):
        """Test is_client_available returns True when client is set."""
        result = base_fetcher.is_client_available()
        assert result is True

    def test_is_client_available_when_client_is_none(self, hass, retry_handler):
        """Test is_client_available returns False when client is None."""
        fetcher = BaseFetcher(hass, None, retry_handler)
        result = fetcher.is_client_available()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_with_retry_success(self, base_fetcher):
        """Test fetch_with_retry returns result on success."""
        expected_result = {"data": "test"}
        fetch_func = AsyncMock(return_value=expected_result)

        result = await base_fetcher.fetch_with_retry(fetch_func, "test failure message")

        assert result == expected_result
        fetch_func.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_with_retry_returns_none_on_failure(self, base_fetcher):
        """Test fetch_with_retry returns None when fetch_func returns None."""
        fetch_func = AsyncMock(return_value=None)

        with patch.object(
            base_fetcher, "_handle_failure", new_callable=AsyncMock
        ) as mock_handle:
            result = await base_fetcher.fetch_with_retry(
                fetch_func, "test failure message"
            )

        assert result is None
        fetch_func.assert_called_once()
        mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_with_retry_handles_exception(self, base_fetcher):
        """Test fetch_with_retry handles exceptions from fetch_func."""
        fetch_func = AsyncMock(side_effect=Exception("Test error"))

        with patch.object(
            base_fetcher, "_handle_failure", new_callable=AsyncMock
        ) as mock_handle:
            result = await base_fetcher.fetch_with_retry(
                fetch_func, "test failure message"
            )

        assert result is None
        fetch_func.assert_called_once()
        mock_handle.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_with_retry_skips_non_epex(self, base_fetcher):
        """Test fetch_with_retry skips retry for non-EPEX contracts."""
        fetch_func = AsyncMock(return_value=None)

        with patch(
            "apps.v2g_liberty.data_import.fetchers.base_fetcher.is_price_epex_based",
            return_value=False,
        ):
            result = await base_fetcher.fetch_with_retry(
                fetch_func, "test failure", check_epex_based=True
            )

        assert result is None
        # Should not schedule retry for non-EPEX
        base_fetcher.retry_handler.hass.run_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_with_retry_schedules_retry_for_epex(self, base_fetcher):
        """Test fetch_with_retry schedules retry for EPEX contracts."""
        fetch_func = AsyncMock(return_value=None)

        with patch(
            "apps.v2g_liberty.data_import.fetchers.base_fetcher.is_price_epex_based",
            return_value=True,
        ):
            with patch.object(
                base_fetcher.retry_handler, "should_retry", return_value=True
            ):
                result = await base_fetcher.fetch_with_retry(
                    fetch_func, "test failure", check_epex_based=True
                )

        assert result is None
        # Should schedule retry for EPEX
        base_fetcher.retry_handler.hass.run_in.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_not_epex_based(self, base_fetcher):
        """Test _handle_failure when contract is not EPEX-based."""
        callback = AsyncMock()

        with patch(
            "apps.v2g_liberty.data_import.fetchers.base_fetcher.is_price_epex_based",
            return_value=False,
        ):
            await base_fetcher._handle_failure("test message", callback, True)

        # Should not schedule retry
        base_fetcher.retry_handler.hass.run_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_within_retry_window(self, base_fetcher):
        """Test _handle_failure schedules retry when within window."""
        callback = AsyncMock()

        with patch(
            "apps.v2g_liberty.data_import.fetchers.base_fetcher.is_price_epex_based",
            return_value=True,
        ):
            with patch.object(
                base_fetcher.retry_handler, "should_retry", return_value=True
            ):
                await base_fetcher._handle_failure("test message", callback, True)

        # Should schedule retry
        base_fetcher.retry_handler.hass.run_in.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_failure_outside_retry_window(self, base_fetcher):
        """Test _handle_failure does not schedule retry when outside window."""
        callback = AsyncMock()

        with patch(
            "apps.v2g_liberty.data_import.fetchers.base_fetcher.is_price_epex_based",
            return_value=True,
        ):
            with patch.object(
                base_fetcher.retry_handler, "should_retry", return_value=False
            ):
                await base_fetcher._handle_failure("test message", callback, True)

        # Should not schedule retry
        base_fetcher.retry_handler.hass.run_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_check_epex_false(self, base_fetcher):
        """Test _handle_failure always retries when check_epex_based is False."""
        callback = AsyncMock()

        # Don't patch is_price_epex_based - it should not be called
        with patch.object(
            base_fetcher.retry_handler, "should_retry", return_value=True
        ):
            await base_fetcher._handle_failure("test message", callback, False)

        # Should schedule retry regardless of EPEX status
        base_fetcher.retry_handler.hass.run_in.assert_called_once()
