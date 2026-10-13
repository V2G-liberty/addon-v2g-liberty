"""The CBS fetcher's log lines are logged on the event loop, not in its thread.

The fetch runs in an executor thread and logged from there through a logger
in AppDaemon's logger tree. AppDaemon's LogSubscriptionHandler, which hangs
on that tree, touches the event loop, and from another thread that raises:
a traceback in the log on every start. The lines are now handed to the loop.
"""

import logging
import threading
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from apps.v2g_liberty.reference_price_manager import ReferencePriceManager


def _fetch_that_logs(start_month, end_month, log_fn):
    log_fn("CBS fetcher: GET https://example", "DEBUG")
    log_fn("CBS fetcher: fetched 24 reference price row(s).", "INFO")
    return [{"month": "2026-09", "price": 0.25}]


@pytest.mark.asyncio
async def test_lines_are_logged_on_the_loop_thread():
    manager = object.__new__(ReferencePriceManager)
    manager._ReferencePriceManager__log = MagicMock()
    manager.data_store = MagicMock()
    loop_thread = threading.get_ident()
    logged = []

    def record(level, msg):
        logged.append((threading.get_ident(), msg))

    with (
        patch(
            "apps.v2g_liberty.reference_price_manager.get_local_now",
            return_value=datetime(2026, 10, 13, tzinfo=UTC),
        ),
        patch(
            "apps.v2g_liberty.reference_price_manager.fetch_reference_prices",
            _fetch_that_logs,
        ),
        patch.object(
            logging.getLogger("AppDaemon.v2g-app.cbs_fetcher"),
            "log",
            side_effect=record,
        ),
    ):
        stored = await manager._ReferencePriceManager__fetch_and_store()

    assert stored is True
    assert [msg for _thread, msg in logged] == [
        "CBS fetcher: GET https://example",
        "CBS fetcher: fetched 24 reference price row(s).",
    ]
    assert all(thread == loop_thread for thread, _msg in logged)
