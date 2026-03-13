"""Unit tests for fm_historical_importer.

Tests cover:
- Skips import when flag file already exists
- Aborts when no FM client is provided
- Runs even when the database already has intervals (idempotency)
- Stops after two consecutive empty months
- Inserts rows for a month with sufficient data
- Reconstructs UTC timestamps from start + 5-minute offset
- Skips null values (gaps in sensor data)
- Returns empty dict when get_sensor_data raises an exception
- Uses INSERT OR IGNORE (does not overwrite existing rows)
- Complete 5-min grid: all slots from month_start to month_end
- energy_kwh=None when power data is missing (not 0.0)
- SoC/availability preserved even without power data
"""

import sqlite3
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.data_store import DataStore
from apps.v2g_liberty.fm_historical_importer import (
    _ENERGY_FROM_POWER_FACTOR,
    _fetch_month_rows,
    _fetch_sensor_events,
    _import_emissions_for_month,
    _import_prices_for_month,
    run_historical_import,
)

# pylint: disable=C0116,W0621

_ASSET_NAME = "Test EV Asset"
_POWER_ID = 10
_SOC_ID = 11
_AVAIL_ID = 12
_PRICE_CONS_ID = 20
_PRICE_PROD_ID = 21
_EMISSIONS_ID = 22

# A known UTC start timestamp
_TS_ISO = "2024-11-01T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _set_constants():
    c.FM_ASSET_NAME = _ASSET_NAME
    c.FM_BASE_URL = "https://fm.example.com"
    c.FM_ACCOUNT_USERNAME = "user@example.com"
    c.FM_ACCOUNT_PASSWORD = "secret"
    c.FM_ACCOUNT_POWER_SENSOR_ID = _POWER_ID
    c.FM_ACCOUNT_SOC_SENSOR_ID = _SOC_ID
    c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID = _AVAIL_ID
    # Price/emission sensors disabled by default; override in specific tests.
    c.FM_PRICE_CONSUMPTION_SENSOR_ID = 0
    c.FM_PRICE_PRODUCTION_SENSOR_ID = 0
    c.FM_EMISSIONS_SENSOR_ID = 0
    c.PRICE_RESOLUTION_MINUTES = 15
    c.FM_EVENT_RESOLUTION_IN_MINUTES = 5
    c.CURRENCY = "EUR"
    c.EMISSIONS_UOM = "kg/MWh"


@pytest.fixture
def data_store():
    """In-memory SQLite DataStore."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE interval_log (
            timestamp TEXT PRIMARY KEY,
            energy_kwh REAL NOT NULL,
            app_state TEXT NOT NULL,
            soc_pct REAL,
            availability_pct REAL NOT NULL,
            is_repaired INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE schema_version (version INTEGER NOT NULL, applied_at TEXT NOT NULL);
        INSERT INTO schema_version VALUES (2, '2026-01-01T00:00:00');
        """
    )
    store = DataStore.__new__(DataStore)
    store._DataStore__connection = conn  # inject the in-memory connection
    store._DataStore__log = MagicMock()
    return store


@pytest.fixture
def log_fn():
    return MagicMock()


def _make_fm_client(values=None, start=_TS_ISO, unit="MW"):
    """Return a mock FlexMeasuresClient.

    get_sensor_data returns the given values list.
    """
    client = MagicMock()
    client.get_sensor_data = AsyncMock(
        return_value={
            "values": values if values is not None else [],
            "start": start,
            "duration": "PT5M",
            "unit": unit,
        }
    )
    return client


# ---------------------------------------------------------------------------
# run_historical_import — top-level behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skips_when_flag_file_exists(data_store, log_fn, tmp_path):
    """Import is skipped when the flag file already exists."""
    flag = tmp_path / "fm_historical_import_done"
    flag.touch()

    with patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag):
        await run_historical_import(data_store, log_fn)

    count = data_store._DataStore__connection.execute(
        "SELECT COUNT(*) FROM interval_log"
    ).fetchone()[0]
    assert count == 0


@pytest.mark.asyncio
async def test_aborts_when_no_fm_client(data_store, log_fn, tmp_path):
    """Import aborts cleanly when no FM client is provided."""
    flag = tmp_path / "fm_historical_import_done"

    with patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag):
        await run_historical_import(data_store, log_fn)

    count = data_store._DataStore__connection.execute(
        "SELECT COUNT(*) FROM interval_log"
    ).fetchone()[0]
    assert count == 0
    assert not flag.exists()  # flag must NOT be written on failure


@pytest.mark.asyncio
async def test_runs_even_when_db_has_intervals(data_store, log_fn, tmp_path):
    """Import runs even when the database already has some intervals (no flag file)."""
    data_store._DataStore__connection.execute(
        "INSERT INTO interval_log VALUES ('2026-01-01T00:00:00+00:00', 0.1, 'charge', 80.0, 100.0, 0)"
    )
    data_store._DataStore__connection.commit()

    flag = tmp_path / "fm_historical_import_done"
    fm_client = _make_fm_client(values=[])

    with (
        patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag),
        patch("apps.v2g_liberty.fm_historical_importer.date") as mock_date,
    ):
        mock_date.today.return_value = date(2024, 11, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await run_historical_import(data_store, log_fn, fm_client)

    # Import ran (flag file was written).
    assert flag.exists()


@pytest.mark.asyncio
async def test_stops_after_two_empty_months(data_store, log_fn, tmp_path):
    """Import stops looping after two consecutive months with insufficient events."""
    flag = tmp_path / "fm_historical_import_done"
    fm_client = _make_fm_client(values=[])

    with (
        patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag),
        patch("apps.v2g_liberty.fm_historical_importer.date") as mock_date,
    ):
        # Use a date well after _EARLIEST_DATE so the loop runs.
        mock_date.today.return_value = date(2026, 6, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await run_historical_import(data_store, log_fn, fm_client)

    stop_logged = any("two empty months" in str(call) for call in log_fn.call_args_list)
    assert stop_logged
    assert flag.exists()  # flag must be written even after early stop


@pytest.mark.asyncio
async def test_on_complete_called_after_import(data_store, log_fn, tmp_path):
    """The on_complete callback is called after a successful import."""
    flag = tmp_path / "fm_historical_import_done"
    fm_client = _make_fm_client(values=[])
    callback = MagicMock()

    with (
        patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag),
        patch("apps.v2g_liberty.fm_historical_importer.date") as mock_date,
    ):
        mock_date.today.return_value = date(2024, 11, 1)
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        await run_historical_import(data_store, log_fn, fm_client, on_complete=callback)

    callback.assert_called_once()


@pytest.mark.asyncio
async def test_on_complete_not_called_when_skipped(data_store, log_fn, tmp_path):
    """The on_complete callback is NOT called when import is skipped."""
    flag = tmp_path / "fm_historical_import_done"
    flag.write_text("already done")
    callback = MagicMock()

    with patch("apps.v2g_liberty.fm_historical_importer._IMPORT_DONE_FLAG", flag):
        await run_historical_import(data_store, log_fn, None, on_complete=callback)

    callback.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_sensor_events — unit-level behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_reconstructs_timestamps(log_fn):
    """Values are mapped to UTC timestamps reconstructed from start + 5-min offset."""
    fm_client = _make_fm_client(values=[0.005, None, 0.003], start=_TS_ISO)

    result = await _fetch_sensor_events(
        fm_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )

    # None at index 1 is skipped; index 0 → +0 min, index 2 → +10 min.
    assert len(result) == 2
    assert "2024-11-01T00:00:00+00:00" in result
    assert result["2024-11-01T00:00:00+00:00"] == pytest.approx(0.005)
    assert "2024-11-01T00:10:00+00:00" in result
    assert result["2024-11-01T00:10:00+00:00"] == pytest.approx(0.003)


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_exception(log_fn):
    """Returns an empty dict when get_sensor_data raises an exception."""
    fm_client = MagicMock()
    fm_client.get_sensor_data = AsyncMock(side_effect=Exception("server error"))

    result = await _fetch_sensor_events(
        fm_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )

    assert result == {}
    log_fn.assert_called()  # warning should be logged


@pytest.mark.asyncio
async def test_fetch_skips_null_values(log_fn):
    """Null entries in the values list are not included in the result."""
    fm_client = _make_fm_client(values=[None, None, 0.007], start=_TS_ISO)

    result = await _fetch_sensor_events(
        fm_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )

    assert len(result) == 1
    assert "2024-11-01T00:10:00+00:00" in result


@pytest.mark.asyncio
async def test_fetch_skips_future_timestamps(log_fn):
    """Values at or after the current moment are excluded (future data guard)."""
    import apps.v2g_liberty.fm_historical_importer as _module
    from datetime import datetime as real_datetime

    # Three 5-min values; mock now = 00:05 → only 00:00 is in the past.
    fm_client = _make_fm_client(values=[1.0, 2.0, 3.0], start=_TS_ISO)
    cutoff = real_datetime(2024, 11, 1, 0, 5, tzinfo=timezone.utc)

    with patch.object(_module, "datetime") as mock_dt:
        mock_dt.now.return_value = cutoff
        mock_dt.fromisoformat = real_datetime.fromisoformat

        result = await _fetch_sensor_events(
            fm_client,
            _POWER_ID,
            "MW",
            real_datetime(2024, 11, 1, tzinfo=timezone.utc),
            real_datetime(2024, 12, 1, tzinfo=timezone.utc),
            log_fn,
        )

    assert len(result) == 1
    assert "2024-11-01T00:00:00+00:00" in result


# ---------------------------------------------------------------------------
# DataStore helper methods
# ---------------------------------------------------------------------------


def test_delete_historical_intervals_removes_unknown(data_store):
    """delete_historical_intervals() removes rows with app_state='unknown' only."""
    conn = data_store._DataStore__connection
    conn.executescript(
        """
        INSERT INTO interval_log VALUES ('2024-11-01T00:00:00+00:00', 0.1, 'unknown', 50.0, 100.0, 0);
        INSERT INTO interval_log VALUES ('2024-11-01T00:05:00+00:00', 0.2, 'charge', 80.0, 100.0, 0);
        """
    )
    deleted = data_store.delete_historical_intervals()
    assert deleted == 1
    remaining = conn.execute("SELECT COUNT(*) FROM interval_log").fetchone()[0]
    assert remaining == 1  # 'charge' row must be intact


def test_has_any_intervals_empty(data_store):
    assert data_store.has_any_intervals() is False


def test_has_any_intervals_with_row(data_store):
    data_store._DataStore__connection.execute(
        "INSERT INTO interval_log VALUES ('2024-11-01T00:00:00+00:00', 0.1, 'charge', 80.0, 100.0, 0)"
    )
    data_store._DataStore__connection.commit()
    assert data_store.has_any_intervals() is True


def test_bulk_insert_or_ignore_inserts_new_rows(data_store):
    rows = [
        {
            "timestamp": "2024-11-01T00:00:00+00:00",
            "energy_kwh": 0.1,
            "app_state": "unknown",
            "soc_pct": 50.0,
            "availability_pct": 100.0,
            "is_repaired": 2,
        },
        {
            "timestamp": "2024-11-01T00:05:00+00:00",
            "energy_kwh": 0.2,
            "app_state": "unknown",
            "soc_pct": None,
            "availability_pct": 100.0,
            "is_repaired": 2,
        },
    ]
    inserted = data_store.bulk_insert_or_ignore_intervals(rows)
    assert inserted == 2

    count = data_store._DataStore__connection.execute(
        "SELECT COUNT(*) FROM interval_log"
    ).fetchone()[0]
    assert count == 2


def test_bulk_insert_or_ignore_skips_existing(data_store):
    """Existing rows are not overwritten — their values are preserved."""
    ts = "2024-11-01T00:00:00+00:00"
    data_store._DataStore__connection.execute(
        f"INSERT INTO interval_log VALUES ('{ts}', 0.5, 'charge', 80.0, 100.0, 0)"
    )
    data_store._DataStore__connection.commit()

    rows = [
        {
            "timestamp": ts,
            "energy_kwh": 9.9,
            "app_state": "unknown",
            "soc_pct": None,
            "availability_pct": 0.0,
            "is_repaired": 2,
        }
    ]
    inserted = data_store.bulk_insert_or_ignore_intervals(rows)
    assert inserted == 0

    # Original value must be intact.
    row = data_store._DataStore__connection.execute(
        f"SELECT energy_kwh FROM interval_log WHERE timestamp = '{ts}'"
    ).fetchone()
    assert row[0] == pytest.approx(0.5)


def test_energy_kwh_conversion():
    """power_MW * 1000 * 5 / 60 == power_MW * 83.333..."""
    power_mw = 0.006  # 6 kW
    expected_kwh = 0.006 * 1000 * 5 / 60  # = 0.5 kWh
    assert power_mw * _ENERGY_FROM_POWER_FACTOR == pytest.approx(expected_kwh)


# ---------------------------------------------------------------------------
# _fetch_sensor_events with custom step_minutes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_sensor_events_with_step_minutes(log_fn):
    """step_minutes=15 produces timestamps 15 minutes apart."""
    fm_client = _make_fm_client(values=[0.1, 0.2], start=_TS_ISO)

    result = await _fetch_sensor_events(
        fm_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
        step_minutes=15,
    )

    assert len(result) == 2
    assert "2024-11-01T00:00:00+00:00" in result
    assert "2024-11-01T00:15:00+00:00" in result


# ---------------------------------------------------------------------------
# _import_prices_for_month
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_prices_upsample_post_cutover(log_fn):
    """Two 15-min price slots after EU market cutover (2025-10-01) yield 6 upsampled 5-min rows."""
    c.FM_PRICE_CONSUMPTION_SENSOR_ID = _PRICE_CONS_ID
    c.FM_PRICE_PRODUCTION_SENSOR_ID = _PRICE_PROD_ID

    month_start = datetime(2025, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2025, 12, 1, tzinfo=timezone.utc)

    # FM returns two 15-min price slots for both consumption and production.
    fm_client = _make_fm_client(
        values=[10.5, 9.8],
        start="2025-11-01T00:00:00+00:00",
        unit="cEUR/kWh",
    )
    data_store = MagicMock()

    await _import_prices_for_month(
        fm_client, data_store, month_start, month_end, log_fn
    )

    data_store.upsert_prices.assert_called_once()
    rows = data_store.upsert_prices.call_args[0][0]
    # 2 slots × 3 upsample steps (15 // 5) = 6 rows
    assert len(rows) == 6
    # Prices must be divided by 100: FM returns cEUR/kWh, DB stores EUR/kWh.
    assert rows[0][1] == pytest.approx(10.5 / 100)
    assert rows[0][2] == pytest.approx(10.5 / 100)


@pytest.mark.asyncio
async def test_import_prices_upsample_pre_cutover(log_fn):
    """Two 60-min price slots before EU market cutover (2025-10-01) yield 24 upsampled 5-min rows."""
    c.FM_PRICE_CONSUMPTION_SENSOR_ID = _PRICE_CONS_ID
    c.FM_PRICE_PRODUCTION_SENSOR_ID = _PRICE_PROD_ID

    # _TS_ISO is 2024-11-01, well before the cutover date.
    month_start = datetime(2024, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2024, 12, 1, tzinfo=timezone.utc)

    # FM returns two 60-min price slots.
    fm_client = _make_fm_client(
        values=[15.0, 14.5],
        start=_TS_ISO,
        unit="cEUR/kWh",
    )
    data_store = MagicMock()

    await _import_prices_for_month(
        fm_client, data_store, month_start, month_end, log_fn
    )

    data_store.upsert_prices.assert_called_once()
    rows = data_store.upsert_prices.call_args[0][0]
    # 2 slots × 12 upsample steps (60 // 5) = 24 rows
    assert len(rows) == 24
    # Prices must be divided by 100: FM returns cEUR/kWh, DB stores EUR/kWh.
    assert rows[0][1] == pytest.approx(15.0 / 100)
    assert rows[0][2] == pytest.approx(15.0 / 100)


@pytest.mark.asyncio
async def test_import_prices_skipped_when_no_sensor(log_fn):
    """Price import is skipped when FM_PRICE_CONSUMPTION_SENSOR_ID is not configured."""
    # c.FM_PRICE_CONSUMPTION_SENSOR_ID is already 0 from _set_constants.
    month_start = datetime(2024, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2024, 12, 1, tzinfo=timezone.utc)

    data_store = MagicMock()

    await _import_prices_for_month(
        _make_fm_client(), data_store, month_start, month_end, log_fn
    )

    data_store.upsert_prices.assert_not_called()


# ---------------------------------------------------------------------------
# _import_emissions_for_month
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_emissions_inserts_rows(log_fn):
    """Three emission values are stored as three (timestamp, value) tuples."""
    c.FM_EMISSIONS_SENSOR_ID = _EMISSIONS_ID

    month_start = datetime(2024, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2024, 12, 1, tzinfo=timezone.utc)

    fm_client = _make_fm_client(
        values=[120.0, 115.5, 118.3],
        start=_TS_ISO,
        unit="kg/MWh",
    )
    data_store = MagicMock()

    await _import_emissions_for_month(
        fm_client, data_store, month_start, month_end, log_fn
    )

    data_store.upsert_emissions.assert_called_once()
    rows = data_store.upsert_emissions.call_args[0][0]
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# prior belief-time filtering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_sensor_events_passes_prior(log_fn):
    """prior is forwarded to get_sensor_data as a keyword argument."""
    fm_client = _make_fm_client(values=[0.5], start=_TS_ISO)
    prior_str = "2024-12-02T00:00:00+00:00"

    await _fetch_sensor_events(
        fm_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
        prior=prior_str,
    )

    call_kwargs = fm_client.get_sensor_data.call_args.kwargs
    assert call_kwargs.get("prior") == prior_str


# ---------------------------------------------------------------------------
# _fetch_month_rows — power-only timestamp fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_union_of_timestamps_fills_missing_with_none(log_fn):
    """Rows are created for the union of all sensor timestamps.

    Each sensor can have a different set of timestamps. The importer
    creates rows for the union of all timestamps. Where a sensor has
    no value for a given timestamp, its field is set to None.
    """
    start = "2024-11-01T00:00:00+00:00"

    # Power: 2 values (00:00 and 00:05).
    power_client = _make_fm_client(values=[0.005, 0.003], start=start)
    # SoC: 5 values (00:00 through 00:20).
    soc_client = _make_fm_client(values=[50.0, 51.0, 52.0, 53.0, 54.0], start=start)
    # Availability: 5 values.
    avail_client = _make_fm_client(values=[100.0] * 5, start=start)

    # Patch _fetch_sensor_events to return different data per sensor_id.
    power_result = await _fetch_sensor_events(
        power_client,
        _POWER_ID,
        "MW",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )
    soc_result = await _fetch_sensor_events(
        soc_client,
        _SOC_ID,
        "%",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )
    avail_result = await _fetch_sensor_events(
        avail_client,
        _AVAIL_ID,
        "%",
        datetime(2024, 11, 1, tzinfo=timezone.utc),
        datetime(2024, 12, 1, tzinfo=timezone.utc),
        log_fn,
    )

    async def mock_fetch(fm_client, sensor_id, unit, start_dt, end_dt, log, **kwargs):
        if sensor_id == _POWER_ID:
            return power_result
        if sensor_id == _SOC_ID:
            return soc_result
        return avail_result

    month_start = datetime(2024, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2024, 12, 1, tzinfo=timezone.utc)

    with patch(
        "apps.v2g_liberty.fm_historical_importer._fetch_sensor_events", mock_fetch
    ):
        rows = await _fetch_month_rows(MagicMock(), month_start, month_end, log_fn)

    # Complete 5-minute grid for November (30 days × 288 slots/day).
    assert len(rows) == 8640
    # First 2 slots have power data → non-None energy.
    assert rows[0]["energy_kwh"] is not None
    assert rows[1]["energy_kwh"] is not None
    # Third slot onward has no power data → energy_kwh=None.
    assert rows[2]["energy_kwh"] is None
    # SoC populated where available (first 5 slots), None elsewhere.
    for i, expected_soc in enumerate([50.0, 51.0, 52.0, 53.0, 54.0]):
        assert rows[i]["soc_pct"] == pytest.approx(expected_soc)
    assert rows[5]["soc_pct"] is None
    # Availability populated where available (first 5 slots), None elsewhere.
    for i in range(5):
        assert rows[i]["availability_pct"] == pytest.approx(100.0)
    assert rows[5]["availability_pct"] is None


@pytest.mark.asyncio
async def test_month_with_only_soc_data_produces_rows_with_none_energy(log_fn):
    """A month with only SoC/availability data still produces rows.

    The SoC and availability data is preserved; energy_kwh is None
    because no power data is available.
    """

    async def mock_fetch(fm_client, sensor_id, unit, start_dt, end_dt, log, **kwargs):
        if sensor_id == _POWER_ID:
            return {}  # No power data
        # SoC and availability have data.
        return {
            "2024-11-01T00:00:00+00:00": 50.0,
            "2024-11-01T00:05:00+00:00": 51.0,
            "2024-11-01T00:10:00+00:00": 52.0,
        }

    month_start = datetime(2024, 11, 1, tzinfo=timezone.utc)
    month_end = datetime(2024, 12, 1, tzinfo=timezone.utc)

    with patch(
        "apps.v2g_liberty.fm_historical_importer._fetch_sensor_events", mock_fetch
    ):
        rows = await _fetch_month_rows(MagicMock(), month_start, month_end, log_fn)

    # Complete 5-minute grid for November.
    assert len(rows) == 8640
    # All rows have None energy (no power data at all).
    assert all(r["energy_kwh"] is None for r in rows)
    # SoC and availability are preserved where available.
    assert rows[0]["soc_pct"] == pytest.approx(50.0)
    assert rows[1]["soc_pct"] == pytest.approx(51.0)
    assert rows[2]["soc_pct"] == pytest.approx(52.0)
    assert rows[3]["soc_pct"] is None  # No SoC data for this slot
    # Availability uses same mock data as SoC.
    assert rows[0]["availability_pct"] == pytest.approx(50.0)
    assert rows[3]["availability_pct"] is None
