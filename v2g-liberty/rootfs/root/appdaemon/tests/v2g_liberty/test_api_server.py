"""Unit tests for ApiServer REST endpoint and HA event handler (T23/T24/T40/T41)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty.api_server import ApiServer, VALID_GRANULARITIES

# pylint: disable=C0116,W0621

TEST_TZ = timezone(timedelta(hours=1))


def ts(hour: int, minute: int = 0) -> str:
    """Create a local ISO 8601 timestamp for 2026-02-23 at given hour:minute +01:00."""
    return datetime(2026, 2, 23, hour, minute, 0, tzinfo=TEST_TZ).isoformat()


def utc_ts(hour: int, minute: int = 0) -> str:
    """Create the UTC equivalent of ts() for assertion against API responses."""
    dt = datetime(2026, 2, 23, hour, minute, 0, tzinfo=TEST_TZ)
    return dt.astimezone(timezone.utc).isoformat()


def _make_kwargs(start=None, end=None, granularity=None):
    """Build kwargs dict mimicking an AppDaemon endpoint call with query params."""
    query = {}
    if start is not None:
        query["start"] = start
    if end is not None:
        query["end"] = end
    if granularity is not None:
        query["granularity"] = granularity

    request = MagicMock()
    request.query = query
    return {"request": request}


@pytest.fixture
def hass():
    mock = AsyncMock(spec=Hass)
    mock.listen_event = AsyncMock()
    return mock


@pytest.fixture
def api_server(hass):
    server = ApiServer(hass)
    server.data_store = MagicMock()
    return server


# ── Initialisation ────────────────────────────────────────────────


class TestInitialisation:
    @pytest.mark.asyncio
    async def test_initialise_registers_endpoint_and_event_listener(
        self, api_server, hass
    ):
        await api_server.initialise()
        hass.register_endpoint.assert_called_once()
        assert hass.register_endpoint.call_args[0][1] == "v2g_data"
        # Two event listeners are registered: data query + on-demand repair.
        assert hass.listen_event.call_count == 2
        registered_events = {
            call.args[1] for call in hass.listen_event.call_args_list
        }
        assert registered_events == {"v2g_data_query", "v2g_run_full_repair"}

    @pytest.mark.asyncio
    async def test_valid_granularities_constant(self):
        assert "quarter_hours" in VALID_GRANULARITIES
        assert "hours" in VALID_GRANULARITIES
        assert "days" in VALID_GRANULARITIES
        assert "weeks" in VALID_GRANULARITIES
        assert "months" in VALID_GRANULARITIES
        assert "years" in VALID_GRANULARITIES
        assert len(VALID_GRANULARITIES) == 6


# ── Parameter validation ──────────────────────────────────────────


class TestParameterValidation:
    @pytest.mark.asyncio
    async def test_missing_all_params(self, api_server):
        kwargs = _make_kwargs()
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "start" in response["error"]
        assert "end" in response["error"]
        assert "granularity" in response["error"]

    @pytest.mark.asyncio
    async def test_missing_start(self, api_server):
        kwargs = _make_kwargs(end=ts(9, 0), granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "start" in response["error"]

    @pytest.mark.asyncio
    async def test_missing_end(self, api_server):
        kwargs = _make_kwargs(start=ts(8, 0), granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "end" in response["error"]

    @pytest.mark.asyncio
    async def test_missing_granularity(self, api_server):
        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0))
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "granularity" in response["error"]

    @pytest.mark.asyncio
    async def test_invalid_granularity(self, api_server):
        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity="minute")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "Invalid granularity" in response["error"]
        assert "minute" in response["error"]

    @pytest.mark.asyncio
    async def test_invalid_start_timestamp(self, api_server):
        kwargs = _make_kwargs(start="not-a-date", end=ts(9, 0), granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "Invalid timestamp" in response["error"]

    @pytest.mark.asyncio
    async def test_invalid_end_timestamp(self, api_server):
        kwargs = _make_kwargs(start=ts(8, 0), end="not-a-date", granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 400
        assert "Invalid timestamp" in response["error"]

    @pytest.mark.asyncio
    async def test_no_request_object(self, api_server):
        response, status = await api_server._ApiServer__handle_aggregated_data(None, {})
        assert status == 400
        assert "No request object" in response["error"]


# ── Successful requests ───────────────────────────────────────────


class TestSuccessfulRequests:
    @pytest.mark.asyncio
    async def test_returns_data_from_data_store(self, api_server):
        mock_result = [
            {"period_start": ts(8, 0), "app_state": "automatic", "charge_wh": 1500}
        ]
        api_server.data_store.get_aggregated_data.return_value = mock_result

        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 200
        assert response["data"] == mock_result
        assert response["granularity"] == "hours"
        assert response["start"] == utc_ts(8, 0)
        assert response["end"] == utc_ts(9, 0)

    @pytest.mark.asyncio
    async def test_passes_params_to_data_store(self, api_server):
        api_server.data_store.get_aggregated_data.return_value = []

        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity="days")
        await api_server._ApiServer__handle_aggregated_data(None, kwargs)

        api_server.data_store.get_aggregated_data.assert_called_once_with(
            utc_ts(8, 0), utc_ts(9, 0), "days"
        )

    @pytest.mark.asyncio
    async def test_empty_result(self, api_server):
        api_server.data_store.get_aggregated_data.return_value = []

        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity="quarter_hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 200
        assert response["data"] == []

    @pytest.mark.asyncio
    async def test_all_granularities_accepted(self, api_server):
        api_server.data_store.get_aggregated_data.return_value = []
        for granularity in VALID_GRANULARITIES:
            kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity=granularity)
            response, status = await api_server._ApiServer__handle_aggregated_data(
                None, kwargs
            )
            assert status == 200, f"Granularity '{granularity}' should be accepted"


# ── Error handling ────────────────────────────────────────────────


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_data_store_exception_returns_500(self, api_server):
        api_server.data_store.get_aggregated_data.side_effect = RuntimeError("DB error")

        kwargs = _make_kwargs(start=ts(8, 0), end=ts(9, 0), granularity="hours")
        response, status = await api_server._ApiServer__handle_aggregated_data(
            None, kwargs
        )
        assert status == 500
        assert "Internal server error" in response["error"]


# ── Event handler tests ──────────────────────────────────────────


def _fire_event_data(hass):
    """Extract the kwargs from the most recent fire_event call."""
    return hass.fire_event.call_args


class TestEventHandlerValidation:
    @pytest.mark.asyncio
    async def test_missing_all_params(self, api_server, hass):
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", {}, {})
        hass.fire_event.assert_called_once()
        call_kwargs = hass.fire_event.call_args
        assert "error" in call_kwargs.kwargs
        assert "start" in call_kwargs.kwargs["error"]

    @pytest.mark.asyncio
    async def test_missing_start(self, api_server, hass):
        data = {"end": ts(9, 0), "granularity": "hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "start" in hass.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_missing_end(self, api_server, hass):
        data = {"start": ts(8, 0), "granularity": "hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "end" in hass.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_missing_granularity(self, api_server, hass):
        data = {"start": ts(8, 0), "end": ts(9, 0)}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "granularity" in hass.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_invalid_granularity(self, api_server, hass):
        data = {"start": ts(8, 0), "end": ts(9, 0), "granularity": "seconds"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "Invalid granularity" in hass.fire_event.call_args.kwargs["error"]

    @pytest.mark.asyncio
    async def test_invalid_timestamp(self, api_server, hass):
        data = {"start": "not-a-date", "end": ts(9, 0), "granularity": "hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "Invalid timestamp" in hass.fire_event.call_args.kwargs["error"]


class TestEventHandlerSuccess:
    @pytest.mark.asyncio
    async def test_returns_data_via_fire_event(self, api_server, hass):
        mock_result = [{"period_start": ts(8, 0), "charge_wh": 1500}]
        api_server.data_store.get_aggregated_data.return_value = mock_result

        data = {"start": ts(8, 0), "end": ts(9, 0), "granularity": "hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})

        call = hass.fire_event.call_args
        assert call.args[0] == "v2g_data_query.result"
        assert call.kwargs["data"] == mock_result
        assert call.kwargs["granularity"] == "hours"
        assert call.kwargs["start"] == utc_ts(8, 0)
        assert call.kwargs["end"] == utc_ts(9, 0)

    @pytest.mark.asyncio
    async def test_passes_params_to_data_store(self, api_server, hass):
        api_server.data_store.get_aggregated_data.return_value = []

        data = {"start": ts(8, 0), "end": ts(9, 0), "granularity": "days"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        api_server.data_store.get_aggregated_data.assert_called_once_with(
            utc_ts(8, 0), utc_ts(9, 0), "days"
        )

    @pytest.mark.asyncio
    async def test_empty_result(self, api_server, hass):
        api_server.data_store.get_aggregated_data.return_value = []

        data = {"start": ts(8, 0), "end": ts(9, 0), "granularity": "quarter_hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert hass.fire_event.call_args.kwargs["data"] == []


class TestEventHandlerErrors:
    @pytest.mark.asyncio
    async def test_data_store_exception_fires_error(self, api_server, hass):
        api_server.data_store.get_aggregated_data.side_effect = RuntimeError("DB error")

        data = {"start": ts(8, 0), "end": ts(9, 0), "granularity": "hours"}
        await api_server._ApiServer__handle_data_query_event("v2g_data_query", data, {})
        assert "Internal server error" in hass.fire_event.call_args.kwargs["error"]
