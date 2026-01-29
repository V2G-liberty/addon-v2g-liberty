"""Base fetcher class with common retry logic for FlexMeasures data operations."""

from typing import Callable, Optional
from appdaemon.plugins.hass.hassapi import Hass
from ...v2g_globals import is_price_epex_based
from ...log_wrapper import get_class_method_logger
from ..utils.retry_handler import RetryHandler


class BaseFetcher:
    """
    Base class for all FlexMeasures data fetchers.

    Provides common retry logic that can be used by specific fetcher implementations.
    Handles EPEX-based contract checking, retry scheduling, and logging.
    """

    def __init__(self, hass: Hass, fm_client_app: object, retry_handler: RetryHandler):
        """
        Initialise the base fetcher.

        Args:
            hass: AppDaemon Hass instance for logging and scheduling
            fm_client_app: FlexMeasures client for API calls
            retry_handler: Handler for managing retry logic
        """
        self.hass = hass
        self.fm_client_app = fm_client_app
        self.retry_handler = retry_handler
        self.__log = get_class_method_logger(hass.log)

    async def fetch_with_retry(
        self,
        fetch_func: Callable,
        failure_message: str,
        check_epex_based: bool = True,
    ) -> Optional[any]:
        """
        Execute a fetch operation with automatic retry on failure.

        Args:
            fetch_func: Async function that performs the actual fetch operation.
                       Should return data on success, None or raise exception on failure.
            failure_message: Message to log if fetch fails
            check_epex_based: Whether to check if contract is EPEX-based before retrying.
                            Set to False for operations that should retry regardless of
                            contract type (e.g., charging cost/energy fetching).

        Returns:
            The result from fetch_func if successful, None otherwise.
            If retry is scheduled, returns None but will call fetch_func again later.
        """
        try:
            result = await fetch_func()
            if result is not None:
                return result

            # Result is None, treat as failure
            await self._handle_failure(failure_message, fetch_func, check_epex_based)
            return None

        except Exception as e:
            self.__log(
                f"Exception during fetch: {failure_message}: {str(e)}", level="WARNING"
            )
            await self._handle_failure(failure_message, fetch_func, check_epex_based)
            return None

    async def _handle_failure(
        self, failure_message: str, retry_callback: Callable, check_epex_based: bool
    ):
        """
        Handle fetch failure and schedule retry if appropriate.

        Args:
            failure_message: Message to log
            retry_callback: Function to call for retry
            check_epex_based: Whether to check EPEX-based status before retrying
        """
        if check_epex_based and not is_price_epex_based():
            self.__log(f"{failure_message}, not EPEX based: not retrying.")
            return

        if self.retry_handler.should_retry():
            was_scheduled = await self.retry_handler.schedule_retry(retry_callback)
            if was_scheduled:
                self.__log(
                    f"{failure_message}, try again in "
                    f"'{self.retry_handler.config.interval_seconds}' sec."
                )
        else:
            self.__log(
                f"{failure_message}, 'now' is out of time bounds "
                f"start: '{self.retry_handler.config.start_time}' - "
                f"end: '{self.retry_handler.config.end_time}', not retrying."
            )

    def is_client_available(self) -> bool:
        """
        Check if FlexMeasures client is available.

        Returns:
            bool: True if client is available, False otherwise
        """
        if self.fm_client_app is None:
            self.__log("Could not call get_sensor_data on fm_client_app as it is None.")
            return False
        return True
