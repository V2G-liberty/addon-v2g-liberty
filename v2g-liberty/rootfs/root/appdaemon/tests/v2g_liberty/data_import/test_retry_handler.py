"""Tests for RetryHandler class."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from appdaemon.plugins.hass.hassapi import Hass
from apps.v2g_liberty.data_import.utils.retry_handler import (
    RetryHandler,
    RetryConfig,
)
from apps.v2g_liberty.data_import import fetch_timing as fm_c


@pytest.fixture
def hass():
    """Create a mock Hass instance."""
    hass = AsyncMock(spec=Hass)
    hass.run_in = AsyncMock()
    return hass


@pytest.fixture
def retry_config():
    """Create a retry configuration."""
    return RetryConfig(
        start_time="13:00:00", end_time="18:00:00", interval_seconds=1800
    )


@pytest.fixture
def retry_handler(hass, retry_config):
    """Create a RetryHandler instance."""
    return RetryHandler(hass, retry_config)


class TestRetryConfig:
    """Test RetryConfig dataclass."""

    def test_retry_config_creation(self):
        """Test creating a RetryConfig with all fields."""
        config = RetryConfig(
            start_time=fm_c.GET_PRICES_TIME,
            end_time=fm_c.TRY_UNTIL,
            interval_seconds=fm_c.CHECK_RESOLUTION_SECONDS,
        )
        assert config.start_time == fm_c.GET_PRICES_TIME
        assert config.end_time == fm_c.TRY_UNTIL
        assert config.interval_seconds == fm_c.CHECK_RESOLUTION_SECONDS


class TestRetryHandler:
    """Test RetryHandler class."""

    def test_initialisation(self, hass, retry_config):
        """Test RetryHandler initialisation."""
        handler = RetryHandler(hass, retry_config)
        assert handler.hass == hass
        assert handler.config == retry_config

    @patch(
        "apps.v2g_liberty.data_import.utils.retry_handler.is_local_now_between",
        return_value=True,
    )
    def test_should_retry_within_window(self, mock_is_between, retry_handler):
        """Test should_retry returns True when within time window."""
        result = retry_handler.should_retry()

        assert result is True
        mock_is_between.assert_called_once_with(
            start_time="13:00:00", end_time="18:00:00"
        )

    @patch(
        "apps.v2g_liberty.data_import.utils.retry_handler.is_local_now_between",
        return_value=False,
    )
    def test_should_retry_outside_window(self, mock_is_between, retry_handler):
        """Test should_retry returns False when outside time window."""
        result = retry_handler.should_retry()

        assert result is False
        mock_is_between.assert_called_once_with(
            start_time="13:00:00", end_time="18:00:00"
        )

    @pytest.mark.asyncio
    async def test_schedule_retry_success(self, retry_handler, hass):
        """Test schedule_retry schedules callback when within window."""
        mock_callback = AsyncMock()

        with patch.object(retry_handler, "should_retry", return_value=True):
            result = await retry_handler.schedule_retry(
                mock_callback, price_type="consumption"
            )

        assert result is True
        hass.run_in.assert_called_once_with(
            mock_callback, delay=1800, price_type="consumption"
        )

    @pytest.mark.asyncio
    async def test_schedule_retry_outside_window(self, retry_handler, hass):
        """Test schedule_retry returns False when outside window."""
        mock_callback = AsyncMock()

        with patch.object(retry_handler, "should_retry", return_value=False):
            result = await retry_handler.schedule_retry(
                mock_callback, price_type="consumption"
            )

        assert result is False
        hass.run_in.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedule_retry_without_kwargs(self, retry_handler, hass):
        """Test schedule_retry works without additional kwargs."""
        mock_callback = AsyncMock()

        with patch.object(retry_handler, "should_retry", return_value=True):
            result = await retry_handler.schedule_retry(mock_callback)

        assert result is True
        hass.run_in.assert_called_once_with(mock_callback, delay=1800)

    @pytest.mark.asyncio
    async def test_schedule_retry_with_multiple_kwargs(self, retry_handler, hass):
        """Test schedule_retry passes multiple kwargs correctly."""
        mock_callback = AsyncMock()

        with patch.object(retry_handler, "should_retry", return_value=True):
            result = await retry_handler.schedule_retry(
                mock_callback, price_type="production", sensor_id=42, duration="P3D"
            )

        assert result is True
        hass.run_in.assert_called_once_with(
            mock_callback,
            delay=1800,
            price_type="production",
            sensor_id=42,
            duration="P3D",
        )


class TestRetryHandlerIntegration:
    """Integration tests for RetryHandler."""

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.utils.retry_handler.is_local_now_between",
        return_value=True,
    )
    async def test_full_retry_flow_within_window(self, mock_is_between):
        """Test complete retry flow when within time window."""
        hass = AsyncMock(spec=Hass)
        hass.run_in = AsyncMock()

        config = RetryConfig(
            start_time=fm_c.GET_PRICES_TIME,
            end_time=fm_c.TRY_UNTIL,
            interval_seconds=fm_c.CHECK_RESOLUTION_SECONDS,
        )
        handler = RetryHandler(hass, config)

        mock_callback = AsyncMock()

        # Check if should retry
        should_retry = handler.should_retry()
        assert should_retry is True

        # Schedule retry
        result = await handler.schedule_retry(mock_callback, test_param="value")
        assert result is True

        # Verify scheduling was called correctly
        hass.run_in.assert_called_once_with(
            mock_callback, delay=fm_c.CHECK_RESOLUTION_SECONDS, test_param="value"
        )
        mock_is_between.assert_called()

    @pytest.mark.asyncio
    @patch(
        "apps.v2g_liberty.data_import.utils.retry_handler.is_local_now_between",
        return_value=False,
    )
    async def test_full_retry_flow_outside_window(self, mock_is_between):
        """Test complete retry flow when outside time window."""
        hass = AsyncMock(spec=Hass)
        hass.run_in = AsyncMock()

        config = RetryConfig(
            start_time=fm_c.GET_PRICES_TIME,
            end_time=fm_c.TRY_UNTIL,
            interval_seconds=fm_c.CHECK_RESOLUTION_SECONDS,
        )
        handler = RetryHandler(hass, config)

        mock_callback = AsyncMock()

        # Check if should retry
        should_retry = handler.should_retry()
        assert should_retry is False

        # Attempt to schedule retry
        result = await handler.schedule_retry(mock_callback, test_param="value")
        assert result is False

        # Verify scheduling was NOT called
        hass.run_in.assert_not_called()
        mock_is_between.assert_called()
