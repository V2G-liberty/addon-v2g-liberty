"""Unit tests for FMClient.discover_power_source_id.

Tests cover:
- Happy path: one scheduler + one measured source → returns measured source_id
- Only scheduler sources → scans earlier weeks, eventually returns None
- Multiple non-scheduler sources → returns None with warning
- No data in any week → returns None
- chart_data returns HTTP error → skips week gracefully
- chart_data returns invalid JSON → skips week gracefully
- Response format change (no 'source' field) → returns None with warning
- Client not initialised → returns None
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.v2g_liberty.fm_client import FMClient
from apps.v2g_liberty.log_wrapper import get_class_method_logger

# pylint: disable=C0116,W0621

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 3, 30, 12, 0, 0, tzinfo=TEST_TZ)

# Typical chart_data entries
SCHEDULER_SOURCE = {
    "id": 844,
    "name": "Seita",
    "type": "scheduler",
    "model": "StorageScheduler (v7)",
}
MEASURED_SOURCE = {"id": 57, "name": "devtest", "type": "other", "model": ""}


def _make_entries(sources, count=10):
    """Create a list of chart_data entries with the given sources."""
    entries = []
    for i in range(count):
        src = sources[i % len(sources)]
        entries.append(
            {
                "event_start": 1711800000000 + i * 300000,
                "belief_time": 1711800000000,
                "source": src,
                "cumulative_probability": 0.5,
                "event_value": 0.5 if src["type"] != "scheduler" else 0.005,
                "sensor": {"id": 99, "name": "battery power"},
            }
        )
    return entries


def _make_response(entries, status=200):
    """Create a mock aiohttp response."""
    resp = AsyncMock()
    resp.status = status
    resp.text = AsyncMock(return_value=json.dumps(entries))
    return resp


def _make_fm_client(response_side_effect=None):
    """Create an FMClient with a mocked FM client."""
    fm = FMClient.__new__(FMClient)
    fm.client = MagicMock()
    fm.client.ssl = False
    fm.client.ensure_session = MagicMock()
    fm.client.build_url = MagicMock(
        return_value="https://example.com/api/dev/sensor/99/chart_data"
    )
    fm.client.get_headers = AsyncMock(return_value={"Authorization": "test-token"})
    fm.client.session = MagicMock()
    fm.client.session.get = AsyncMock(side_effect=response_side_effect)

    fm.hass = MagicMock()
    fm.hass.log = MagicMock()
    # Set up __log via the same mechanism as FMClient.__init__.
    fm._FMClient__log = get_class_method_logger(fm.hass.log)
    return fm


@pytest.fixture(autouse=True)
def _freeze_time():
    with patch("apps.v2g_liberty.fm_client.get_local_now", return_value=TEST_NOW):
        yield


@pytest.mark.asyncio
async def test_returns_measured_source_id():
    """One scheduler + one measured source → returns the measured source_id."""
    entries = _make_entries([SCHEDULER_SOURCE, MEASURED_SOURCE])
    fm = _make_fm_client([_make_response(entries)])

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result == 57


@pytest.mark.asyncio
async def test_only_scheduler_sources_scans_earlier_weeks():
    """Only scheduler sources in all weeks → returns None after scanning 26 weeks."""
    scheduler_only = _make_entries([SCHEDULER_SOURCE])
    responses = [_make_response(scheduler_only) for _ in range(26)]
    fm = _make_fm_client(responses)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result is None
    assert fm.client.session.get.call_count == 26


@pytest.mark.asyncio
async def test_multiple_non_scheduler_sources_returns_none():
    """Multiple non-scheduler sources → returns None (ambiguous)."""
    second_measured = {"id": 58, "name": "other-reporter", "type": "other", "model": ""}
    entries = _make_entries(
        [SCHEDULER_SOURCE, MEASURED_SOURCE, second_measured], count=15
    )
    fm = _make_fm_client([_make_response(entries)])

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_no_data_returns_none():
    """Empty responses for all weeks → returns None."""
    responses = [_make_response([]) for _ in range(26)]
    fm = _make_fm_client(responses)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_http_error_skips_week():
    """HTTP 500 for first week, valid data for second → returns source_id."""
    entries = _make_entries([SCHEDULER_SOURCE, MEASURED_SOURCE])
    responses = [_make_response([], status=500), _make_response(entries)]
    fm = _make_fm_client(responses)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result == 57
    assert fm.client.session.get.call_count == 2


@pytest.mark.asyncio
async def test_invalid_json_skips_week():
    """Invalid JSON for first week, valid data for second → returns source_id."""
    bad_resp = AsyncMock()
    bad_resp.status = 200
    bad_resp.text = AsyncMock(return_value="<html>not json</html>")

    entries = _make_entries([SCHEDULER_SOURCE, MEASURED_SOURCE])
    responses = [bad_resp, _make_response(entries)]
    fm = _make_fm_client(responses)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result == 57


@pytest.mark.asyncio
async def test_response_format_change_returns_none():
    """If entries lack 'source' field (API format change) → returns None immediately."""
    bad_entries = [{"event_start": 123, "event_value": 0.5}]
    fm = _make_fm_client([_make_response(bad_entries)])

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_unexpected_response_type_skips_week():
    """If response is a dict instead of list → skips week."""
    dict_resp = AsyncMock()
    dict_resp.status = 200
    dict_resp.text = AsyncMock(return_value=json.dumps({"error": "unexpected"}))

    entries = _make_entries([SCHEDULER_SOURCE, MEASURED_SOURCE])
    responses = [dict_resp, _make_response(entries)]
    fm = _make_fm_client(responses)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result == 57


@pytest.mark.asyncio
async def test_client_not_initialised_returns_none():
    """If FM client is None → returns None."""
    fm = FMClient.__new__(FMClient)
    fm.client = None
    fm.hass = MagicMock()
    fm.hass.log = MagicMock()
    fm._FMClient__log = get_class_method_logger(fm.hass.log)

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result is None


@pytest.mark.asyncio
async def test_network_exception_skips_week():
    """Network exception for first week, valid data for second → returns source_id."""
    entries = _make_entries([SCHEDULER_SOURCE, MEASURED_SOURCE])
    good_resp = _make_response(entries)
    fm = _make_fm_client([Exception("Connection refused"), good_resp])

    result = await fm.discover_power_source_id(sensor_id=99)
    assert result == 57
