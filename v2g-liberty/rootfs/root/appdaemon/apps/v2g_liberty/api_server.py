"""REST API endpoint for aggregated interval data."""

from datetime import datetime

from appdaemon.plugins.hass.hassapi import Hass

from .log_wrapper import get_class_method_logger

VALID_GRANULARITIES = ("quarter_hours", "hours", "days", "weeks", "months", "years")


class ApiServer:
    """Provides a REST API for querying aggregated interval data.

    Registers an endpoint on AppDaemon's HTTP server (port 5050) that
    serves aggregated data from the local SQLite database as JSON.

    Endpoint: GET /api/appdaemon/v2g_data?start=...&end=...&granularity=...
    """

    def __init__(self, hass: Hass):
        self.__hass = hass
        self.__log = get_class_method_logger(hass.log)
        self.data_store = None
        self.__log("ApiServer created.")

    async def initialise(self):
        """Register API endpoints on AppDaemon's HTTP server."""
        self.__hass.register_endpoint(self.__handle_aggregated_data, "v2g_data")
        self.__log("API endpoint registered: /api/appdaemon/v2g_data")

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

            # Validate ISO 8601 timestamps
            try:
                datetime.fromisoformat(start)
                datetime.fromisoformat(end)
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
