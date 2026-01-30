"""Retry handler for FlexMeasures data fetching operations."""

from dataclasses import dataclass
from appdaemon.plugins.hass.hassapi import Hass
from ...v2g_globals import is_local_now_between


@dataclass
class RetryConfig:
    """Configuration for retry behaviour."""

    start_time: str  # When retry window starts (e.g., "13:35:51")
    end_time: str  # When retry window ends (e.g., "11:22:33")
    interval_seconds: int  # Seconds between retries (e.g., 1800 for 30 minutes)


class RetryHandler:
    """Manages retry logic for data fetching operations."""

    def __init__(self, hass: Hass, config: RetryConfig):
        """
        Initialise the retry handler.

        Args:
            hass: AppDaemon Hass instance for scheduling retries
            config: Retry configuration (time window and interval)
        """
        self.hass = hass
        self.config = config

    def should_retry(self) -> bool:
        """
        Check if current time is within the retry window.

        Returns:
            bool: True if within retry window, False otherwise
        """
        return is_local_now_between(
            start_time=self.config.start_time, end_time=self.config.end_time
        )

    async def schedule_retry(self, callback, **kwargs) -> bool:
        """
        Schedule a retry if within the time window.

        Args:
            callback: The async function to call after delay
            **kwargs: Arguments to pass to the callback

        Returns:
            bool: True if retry was scheduled, False if outside time window
        """
        if not self.should_retry():
            return False

        await self.hass.run_in(callback, delay=self.config.interval_seconds, **kwargs)
        return True
