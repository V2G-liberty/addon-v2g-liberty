"""REST API and HA event interface for aggregated interval data."""

from datetime import datetime, timezone

from appdaemon.plugins.hass.hassapi import Hass

from .log_wrapper import get_class_method_logger

VALID_GRANULARITIES = ("quarter_hours", "hours", "days", "weeks", "months", "years")


class ApiServer:
    """Provides REST API and HA event interface for querying aggregated data.

    Two interfaces for querying data from the local SQLite database:
    1. REST: GET /api/appdaemon/v2g_data?start=...&end=...&granularity=...
    2. HA event: fire 'v2g_data_query' → receive 'v2g_data_query.result'
    """

    def __init__(self, hass: Hass):
        self.__hass = hass
        self.__log = get_class_method_logger(hass.log)
        self.data_store = None
        self.__log("ApiServer created.")

    async def initialise(self):
        """Register REST endpoint and HA event listener."""
        self.__hass.register_endpoint(self.__handle_aggregated_data, "v2g_data")
        await self.__hass.listen_event(self.__handle_data_query_event, "v2g_data_query")
        self.__log("API endpoint and event listener registered.")

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
            return {
                "data": result,
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
            self.__hass.fire_event(
                "v2g_data_query.result",
                data=result,
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
