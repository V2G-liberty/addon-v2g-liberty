"""Unit tests for FMClient.post_sensor_data error classification.

A failed post to one sensor (e.g. a 403 for a stale/foreign sensor id) must not
mark the whole FlexMeasures connection as down. Only genuine connection/auth
failures (transport, timeout, 5xx, 401) set the connection status to down;
operational 4xx (403/404/422) raise a persistent "fm_data_issue" warning while
leaving the connection status untouched.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from apps.v2g_liberty.fm_client import (
    FMClient,
    _fm_http_status,
    _is_connection_error,
)

# Arbitrary valid-looking arguments; the underlying client is mocked so the
# exact values do not matter.
_START = "2026-08-06T12:00:00+02:00"
_DURATION = "PT5M"
_UOM = "kW"
_SENSOR_ID = 531


@pytest.fixture
def hass_mock():
    mock = AsyncMock()
    mock.log = MagicMock()
    return mock


@pytest.fixture
def fm(hass_mock):
    """Create FMClient with the parts post_sensor_data touches mocked out."""
    with patch("apps.v2g_liberty.fm_client.isodate"):
        client = FMClient(hass_mock, MagicMock())
    client.client = AsyncMock()
    client.set_fm_connection_status = AsyncMock()
    client.emit = MagicMock()
    return client


async def _post(fm, sensor_id=_SENSOR_ID):
    return await fm.post_sensor_data(
        sensor_id=sensor_id,
        values=[1.0],
        start=_START,
        duration=_DURATION,
        uom=_UOM,
    )


class TestFmHttpStatus:
    @pytest.mark.parametrize(
        "message, expected",
        [
            ("Request failed with status code 403", 403),
            (
                "Error occurred while communicating with the API: 403, message=x",
                403,
            ),
            ("Request failed with status code 502", 502),
            ("Something failed with status code 202", 202),
            ("Cannot connect to host example.com:443", None),
            ("Timeout while reading", None),
        ],
    )
    def test_parses_both_formats(self, message, expected):
        assert _fm_http_status(Exception(message)) == expected


class TestIsConnectionError:
    @pytest.mark.parametrize(
        "message, is_connection",
        [
            ("Request failed with status code 403", False),
            ("Request failed with status code 404", False),
            ("Request failed with status code 422", False),
            (
                "Error occurred while communicating with the API: 403, message=x",
                False,
            ),
            ("Request failed with status code 401", True),
            ("Request failed with status code 500", True),
            ("Request failed with status code 502", True),
            ("Cannot connect to host", True),
        ],
    )
    def test_classification(self, message, is_connection):
        assert _is_connection_error(Exception(message)) is is_connection


class TestPostSensorDataClassification:
    @pytest.mark.asyncio
    async def test_operational_403_does_not_mark_connection_down(self, fm):
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 403")
        )
        result = await _post(fm)
        assert result is False
        fm.set_fm_connection_status.assert_not_called()
        fm.emit.assert_called_once_with(
            "fm_data_issue", active=True, sensor_id=_SENSOR_ID, detail="HTTP 403"
        )
        assert _SENSOR_ID in fm._sensors_with_data_issue

    @pytest.mark.asyncio
    async def test_operational_403_api_format(self, fm):
        # The "API: NNN" wording is the exact shape the original bug slipped
        # through (the old 2xx regex only matched "status code NNN").
        fm.client.post_sensor_data = AsyncMock(
            side_effect=Exception(
                "Error occurred while communicating with the API: 403, "
                "message='Forbidden'"
            )
        )
        result = await _post(fm)
        assert result is False
        fm.set_fm_connection_status.assert_not_called()
        fm.emit.assert_called_once_with(
            "fm_data_issue", active=True, sensor_id=_SENSOR_ID, detail="HTTP 403"
        )

    @pytest.mark.asyncio
    async def test_5xx_marks_connection_down(self, fm):
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 502")
        )
        result = await _post(fm)
        assert result is False
        fm.set_fm_connection_status.assert_called_once_with(connected=False)
        fm.emit.assert_not_called()
        assert _SENSOR_ID not in fm._sensors_with_data_issue

    @pytest.mark.asyncio
    async def test_401_marks_connection_down(self, fm):
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 401")
        )
        result = await _post(fm)
        assert result is False
        fm.set_fm_connection_status.assert_called_once_with(connected=False)
        fm.emit.assert_not_called()

    @pytest.mark.asyncio
    async def test_transport_error_marks_connection_down(self, fm):
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ConnectionError("Cannot connect to host example.com")
        )
        result = await _post(fm)
        assert result is False
        fm.set_fm_connection_status.assert_called_once_with(connected=False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message",
        [
            "Request failed with status code 202",
            "Error occurred while communicating with the API: 202, message=ok",
        ],
    )
    async def test_202_accepted_is_success(self, fm, message):
        fm.client.post_sensor_data = AsyncMock(side_effect=ValueError(message))
        result = await _post(fm)
        assert result is True
        fm.set_fm_connection_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_for_healthy_sensor_emits_nothing(self, fm):
        # No exception -> normal success; the happy path must stay silent.
        result = await _post(fm)
        assert result is True
        fm.emit.assert_not_called()
        fm.set_fm_connection_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_after_403_clears_the_issue(self, fm):
        # First post is rejected (403) -> issue raised.
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 403")
        )
        await _post(fm)
        assert _SENSOR_ID in fm._sensors_with_data_issue
        fm.emit.reset_mock()

        # A later post for the same sensor succeeds -> issue cleared.
        fm.client.post_sensor_data = AsyncMock(return_value=None)
        result = await _post(fm)
        assert result is True
        assert _SENSOR_ID not in fm._sensors_with_data_issue
        fm.emit.assert_called_once_with("fm_data_issue", active=False)

    @pytest.mark.asyncio
    async def test_other_healthy_sensor_keeps_issue_active(self, fm):
        # Sensor 531 keeps failing; a *different* healthy sensor succeeding must
        # not dismiss the warning while 531 is still failing.
        fm.client.post_sensor_data = AsyncMock(
            side_effect=ValueError("Request failed with status code 403")
        )
        await _post(fm, sensor_id=531)
        fm.emit.reset_mock()

        fm.client.post_sensor_data = AsyncMock(return_value=None)
        await _post(fm, sensor_id=999)
        assert 531 in fm._sensors_with_data_issue
        fm.emit.assert_not_called()
