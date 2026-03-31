"""Module for fetching and storing CBS reference electricity prices.

Fetches monthly reference prices from CBS (Statistics Netherlands) at startup
and refreshes on the second Thursday of each month (CBS publication day).
Prices are stored in the local SQLite database for use in savings calculations.

If a fetch fails, retries are scheduled after 2 hours and 6 hours.
"""

import asyncio
from datetime import timedelta

from appdaemon.plugins.hass.hassapi import Hass

from .data_import.fetchers.reference_price_fetcher import (
    HISTORICAL_START_MONTH,
    fetch_reference_prices,
)
from .log_wrapper import get_class_method_logger
from .v2g_globals import get_local_now

# Retry delays in seconds: 2 hours, then 6 hours.
_RETRY_DELAYS = [2 * 3600, 6 * 3600]


class ReferencePriceManager:
    """Manages CBS reference price fetching and storage.

    At startup, fetches all available monthly prices from CBS and stores
    them in the DataStore. Schedules a daily check at 02:00; on the
    second Thursday of the month (CBS publication day) the prices are
    refreshed. Failed fetches are retried after 2 and 6 hours.
    """

    data_store = None
    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = get_class_method_logger(hass.log)

    async def initialise(self):
        """Schedule daily refresh and kick off initial fetch in the background.

        The initial CBS fetch runs as a background task so it never blocks
        app startup (the CBS API may be slow or unreachable).
        """
        if self.data_store is None or not self.data_store.is_available:
            self.__log(
                "Skipped: DataStore not available.",
                level="WARNING",
            )
            return

        await self.hass.run_daily(self.__daily_check, start="02:00:00")
        asyncio.ensure_future(self.__fetch_with_retries())
        self.__log(
            "Initialised, initial fetch running in background, "
            "daily check scheduled at 02:00."
        )

    async def __daily_check(self, kwargs):
        """Daily callback: only fetch on the second Thursday of the month."""
        today = get_local_now().date()
        # Second Thursday: weekday 3 (Thursday) and day between 8 and 14.
        if today.weekday() != 3 or not (8 <= today.day <= 14):
            return
        self.__log("Second Thursday -- refreshing CBS reference prices.")
        await self.__fetch_with_retries()

    async def __fetch_with_retries(self):
        """Fetch CBS prices, retrying after 2h and 6h on failure."""
        if await self.__fetch_and_store():
            return

        for delay in _RETRY_DELAYS:
            hours = delay // 3600
            self.__log(f"CBS fetch failed, scheduling retry in {hours}h.")
            await asyncio.sleep(delay)
            if await self.__fetch_and_store():
                return

        self.__log(
            "CBS fetch failed after all retries. "
            "Will try again on next startup or second Thursday.",
            level="WARNING",
        )

    async def __fetch_and_store(self) -> bool:
        """Fetch CBS reference prices and store in DataStore.

        Returns True on success, False on failure.
        """
        now = get_local_now()
        # Fetch up to next month (CBS may publish ahead).
        end_month = (now.replace(day=1) + timedelta(days=32)).strftime("%Y-%m")

        def _log(msg, level="INFO"):
            self.hass.log(msg, level=level)

        loop = asyncio.get_event_loop()
        rows = await loop.run_in_executor(
            None, fetch_reference_prices, HISTORICAL_START_MONTH, end_month, _log
        )

        if rows:
            self.data_store.upsert_reference_prices(rows)
            return True

        self.__log("No CBS data returned.", level="WARNING")
        return False
