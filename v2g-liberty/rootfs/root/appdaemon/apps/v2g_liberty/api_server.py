"""REST API and HA event interface for aggregated interval data."""

import logging
from datetime import datetime, timezone

from appdaemon.plugins.hass.hassapi import Hass

from .log_wrapper import get_class_method_logger
from .timer_utils import set_oneshot_timer

VALID_GRANULARITIES = ("quarter_hours", "hours", "days", "weeks", "months", "years")

# Logger that controls the log level for all v2g-app modules.
_V2G_LOGGER = logging.getLogger("AppDaemon.v2g-app")

MAX_DEBUG_DURATION_HOURS = 24
DEFAULT_DEBUG_DURATION_HOURS = 1


class ApiServer:
    """Provides REST API and HA event interface for querying aggregated data.

    Two interfaces for querying data from the local SQLite database:
    1. REST: GET /api/appdaemon/v2g_data?start=...&end=...&granularity=...
    2. HA event: fire 'v2g_data_query' → receive 'v2g_data_query.result'
    """

    def __init__(self, hass: Hass):
        self.__hass = hass
        self.__log = get_class_method_logger(module_name="api_server")
        self.data_store = None
        self.data_repairer = None
        self._debug_timer_handle = ""
        self.__log("ApiServer created.")

    async def initialise(self):
        """Register REST endpoint and HA event listener."""
        self.__hass.register_endpoint(self.__handle_aggregated_data, "v2g_data")
        await self.__hass.listen_event(self.__handle_data_query_event, "v2g_data_query")
        await self.__hass.listen_event(
            self.__handle_run_full_repair_event, "v2g_run_full_repair"
        )
        await self.__hass.listen_event(
            self.__handle_debug_logging_event, "v2g_enable_debug_logging"
        )
        self.__log(
            "API endpoint and event listeners registered "
            "(v2g_data_query, v2g_run_full_repair, v2g_enable_debug_logging)."
        )

    async def __handle_run_full_repair_event(self, event_name, data, kwargs):
        """Trigger a full data repair on demand.

        Fire from HA Developer Tools → Events with event type
        'v2g_run_full_repair' (no data needed). Useful for testing without
        having to do a full Re-import. Logs a warning if the repairer is
        not wired up yet.
        """
        if self.data_repairer is None:
            self.__log(
                "v2g_run_full_repair received but data_repairer is not wired.",
                level="WARNING",
            )
            return
        self.__log("v2g_run_full_repair event received — starting full repair.")
        await self.data_repairer.run_full_repair_async()

    async def __handle_debug_logging_event(self, event_name, data, kwargs):
        """Enable debug logging for a limited duration.

        Fire from HA Developer Tools → Events with event type
        ``v2g_enable_debug_logging``.

        Optional event data:
            duration_hours (int): How long to keep debug logging enabled.
                Default: 1, maximum: 24.

        Debug logging is automatically disabled after the specified duration.
        If the app restarts, the log level resets to INFO (in-memory only).
        """
        try:
            duration = int(data.get("duration_hours", DEFAULT_DEBUG_DURATION_HOURS))
        except (TypeError, ValueError):
            duration = DEFAULT_DEBUG_DURATION_HOURS
        duration = max(1, min(duration, MAX_DEBUG_DURATION_HOURS))

        _V2G_LOGGER.setLevel(logging.DEBUG)
        self.__log(f"Debug logging enabled for {duration} hour(s).")
        self.__log(
            "This DEBUG message confirms debug logging is active.", level="DEBUG"
        )

        self._debug_timer_handle = await set_oneshot_timer(
            self.__hass,
            self._debug_timer_handle,
            self.__disable_debug_logging,
            delay=duration * 3600,
        )

    async def __disable_debug_logging(self, *args):
        """Timer callback: restore log level to INFO."""
        _V2G_LOGGER.setLevel(logging.INFO)
        self.__log("Debug logging disabled (timer expired).")

    async def __handle_aggregated_data(self, data, kwargs):
        """Handle requests for aggregated data.

        Query parameters:
            start: ISO 8601 timestamp (inclusive lower bound)
            end: ISO 8601 timestamp (exclusive upper bound)
            granularity: One of quarter_hours, hours, days, weeks, months, years

        Returns:
            Tuple of (response_dict, status_code).
        """
        try:
            request = kwargs.get("request")
            if request is None:
                return {"error": "No request object."}, 400

            params = request.query
            start = params.get("start")
            end = params.get("end")
            granularity = params.get("granularity")

            # Validate required parameters
            missing = []
            if not start:
                missing.append("start")
            if not end:
                missing.append("end")
            if not granularity:
                missing.append("granularity")
            if missing:
                return {
                    "error": f"Missing required parameters: {', '.join(missing)}"
                }, 400

            # Validate granularity
            if granularity not in VALID_GRANULARITIES:
                return {
                    "error": (
                        f"Invalid granularity '{granularity}'. "
                        f"Must be one of: {', '.join(VALID_GRANULARITIES)}"
                    )
                }, 400

            # Validate and convert to UTC for database comparison
            try:
                start = (
                    datetime.fromisoformat(start).astimezone(timezone.utc).isoformat()
                )
                end = datetime.fromisoformat(end).astimezone(timezone.utc).isoformat()
            except ValueError:
                return {"error": "Invalid timestamp format. Use ISO 8601."}, 400

            result = self.data_store.get_aggregated_data(start, end, granularity)
            first_available = self.data_store.get_first_available()
            return {
                "data": result,
                "first_available": first_available,
                "granularity": granularity,
                "start": start,
                "end": end,
            }, 200

        except Exception as e:
            self.__log(f"Error handling API request: {e}", level="ERROR")
            return {"error": "Internal server error."}, 500

    async def __handle_data_query_event(self, event, data, kwargs):
        """Handle HA event-based data queries from custom cards.

        Expects event data with keys: start, end, granularity.
        Fires 'v2g_data_query.result' with the aggregated data or an error.
        """
        try:
            start = data.get("start")
            end = data.get("end")
            granularity = data.get("granularity")

            missing = []
            if not start:
                missing.append("start")
            if not end:
                missing.append("end")
            if not granularity:
                missing.append("granularity")
            if missing:
                self.__hass.fire_event(
                    "v2g_data_query.result",
                    error=f"Missing required parameters: {', '.join(missing)}",
                )
                return

            if granularity not in VALID_GRANULARITIES:
                self.__hass.fire_event(
                    "v2g_data_query.result",
                    error=(
                        f"Invalid granularity '{granularity}'. "
                        f"Must be one of: {', '.join(VALID_GRANULARITIES)}"
                    ),
                )
                return

            try:
                start = (
                    datetime.fromisoformat(start).astimezone(timezone.utc).isoformat()
                )
                end = datetime.fromisoformat(end).astimezone(timezone.utc).isoformat()
            except ValueError:
                self.__hass.fire_event(
                    "v2g_data_query.result",
                    error="Invalid timestamp format. Use ISO 8601.",
                )
                return

            result = self.data_store.get_aggregated_data(start, end, granularity)
            first_available = self.data_store.get_first_available()
            self.__hass.fire_event(
                "v2g_data_query.result",
                data=result,
                first_available=first_available,
                granularity=granularity,
                start=start,
                end=end,
            )

        except Exception as e:
            self.__log(f"Error handling data query event: {e}", level="ERROR")
            self.__hass.fire_event(
                "v2g_data_query.result",
                error="Internal server error.",
            )
