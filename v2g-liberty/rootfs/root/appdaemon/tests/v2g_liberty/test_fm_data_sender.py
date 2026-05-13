"""Unit tests for FMDataSender (T16).

Tests cover:
- Grouping contiguous intervals into blocks
- Power kW → MW conversion
- None values passed through correctly
- Send: success path with post_measurements calls + last_sent updated
- Send: FM failure → last_sent not advanced
- Send: no fm_client_app → skip
- Send: no unsent data → skip
- Send: recovery when last_sent_up_to is None
- DataStore: get_fm_last_sent / set_fm_last_sent roundtrip
- DataStore: get_intervals_since filters correctly
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.fm_data_sender import FMDataSender, _len_to_iso_duration

# pylint: disable=C0116,W0621
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 23, 3, 0, 0, tzinfo=TEST_TZ)


@pytest.fixture(autouse=True)
def _set_constants():
    """Set runtime constants that are normally initialised by V2GLibertyGlobals."""
    c.FM_ACCOUNT_POWER_SENSOR_ID = 101
    c.FM_ACCOUNT_SOC_SENSOR_ID = 102
    c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID = 103
    c.FM_GRID_CONSUMPTION_SENSOR_IDS = {}
    c.FM_GRID_PRODUCTION_SENSOR_IDS = {}


@pytest.fixture
def hass():
    mock_hass = AsyncMock(spec=Hass)
    mock_hass.log = MagicMock()
    mock_hass.run_every = AsyncMock()
    return mock_hass


@pytest.fixture
def fm_client():
    mock_client = AsyncMock()
    mock_client.post_measurements = AsyncMock(return_value=True)
    return mock_client


@pytest.fixture
def data_store():
    mock_store = MagicMock()
    # Default: charger has a timestamp, grid returns None (not configured)
    _last_sent = {}

    def fake_get(data_type="charger"):
        return _last_sent.get(data_type)

    def fake_set(timestamp, data_type="charger"):
        _last_sent[data_type] = timestamp

    mock_store.get_fm_last_sent = MagicMock(side_effect=fake_get)
    mock_store.set_fm_last_sent = MagicMock(side_effect=fake_set)
    mock_store.get_intervals_since = MagicMock(return_value=[])
    mock_store.get_grid_intervals_since = MagicMock(return_value=[])
    mock_store._last_sent = _last_sent
    return mock_store


@pytest.fixture
def sender(hass, fm_client, data_store):
    s = FMDataSender(hass)
    s.fm_client_app = fm_client
    s.data_store = data_store
    return s


def _make_interval(ts_str, energy_kwh=0.125, soc_pct=50.0, availability_pct=100.0):
    """Helper to create an interval dict."""
    return {
        "timestamp": ts_str,
        "energy_kwh": energy_kwh,
        "soc_pct": soc_pct,
        "availability_pct": availability_pct,
    }


def _ts(hour, minute):
    """Create ISO timestamp string for 2026-02-22 at given hour:minute."""
    return datetime(2026, 2, 22, hour, minute, 0, tzinfo=TEST_TZ).isoformat()


# ──────────────────────────────────────────────────────────
# _len_to_iso_duration
# ──────────────────────────────────────────────────────────


class TestLenToIsoDuration:
    def test_single_interval(self):
        assert _len_to_iso_duration(1) == "PT0H5M"

    def test_twelve_intervals_one_hour(self):
        assert _len_to_iso_duration(12) == "PT1H0M"

    def test_288_intervals_full_day(self):
        assert _len_to_iso_duration(288) == "P1DT0H0M"

    def test_large_block_uses_day_notation(self):
        # 5313 intervals = 442h45m = 18d10h45m
        assert _len_to_iso_duration(5313) == "P18DT10H45M"

    def test_over_one_day_with_remainder(self):
        # 300 intervals = 25h = 1d1h
        assert _len_to_iso_duration(300) == "P1DT1H0M"

    def test_15_intervals(self):
        # 15 × 5 = 75 min = 1h15m
        assert _len_to_iso_duration(15) == "PT1H15M"


# ──────────────────────────────────────────────────────────
# _group_contiguous_blocks
# ──────────────────────────────────────────────────────────


class TestGroupContiguousBlocks:
    def test_empty_list(self, sender):
        assert sender._group_contiguous_blocks([]) == []

    def test_single_interval(self, sender):
        intervals = [_make_interval(_ts(10, 0))]
        blocks = sender._group_contiguous_blocks(intervals)
        assert len(blocks) == 1
        assert len(blocks[0]) == 1

    def test_contiguous_block(self, sender):
        intervals = [
            _make_interval(_ts(10, 0)),
            _make_interval(_ts(10, 5)),
            _make_interval(_ts(10, 10)),
        ]
        blocks = sender._group_contiguous_blocks(intervals)
        assert len(blocks) == 1
        assert len(blocks[0]) == 3

    def test_splits_at_max_block_size(self, sender):
        """Contiguous block is split at MAX_BLOCK_SIZE (288 = 24h)."""
        # Create 300 contiguous intervals (exceeds 288)
        intervals = [
            _make_interval(
                datetime(2026, 2, 22, 0, 0, 0, tzinfo=TEST_TZ)
                .__add__(timedelta(minutes=5 * i))
                .isoformat()
            )
            for i in range(300)
        ]
        blocks = sender._group_contiguous_blocks(intervals)
        assert len(blocks) == 2
        assert len(blocks[0]) == 288
        assert len(blocks[1]) == 12

    def test_gap_creates_two_blocks(self, sender):
        intervals = [
            _make_interval(_ts(10, 0)),
            _make_interval(_ts(10, 5)),
            # gap: 10:10 missing
            _make_interval(_ts(10, 15)),
            _make_interval(_ts(10, 20)),
        ]
        blocks = sender._group_contiguous_blocks(intervals)
        assert len(blocks) == 2
        assert len(blocks[0]) == 2
        assert len(blocks[1]) == 2

    def test_multiple_gaps(self, sender):
        intervals = [
            _make_interval(_ts(10, 0)),
            # gap
            _make_interval(_ts(10, 15)),
            # gap
            _make_interval(_ts(11, 0)),
        ]
        blocks = sender._group_contiguous_blocks(intervals)
        assert len(blocks) == 3
        assert all(len(b) == 1 for b in blocks)


# ──────────────────────────────────────────────────────────
# _send_charger_block
# ──────────────────────────────────────────────────────────


class TestSendBlock:
    @pytest.mark.asyncio
    async def test_sends_three_measurements(self, sender, fm_client):
        block = [
            _make_interval(
                _ts(10, 0), energy_kwh=0.167, soc_pct=60.0, availability_pct=80.0
            ),
            _make_interval(
                _ts(10, 5), energy_kwh=0.25, soc_pct=61.0, availability_pct=90.0
            ),
        ]
        result = await sender._send_charger_block(block)
        assert result is True
        assert fm_client.post_measurements.call_count == 3

    @pytest.mark.asyncio
    async def test_power_converted_to_mw(self, sender, fm_client):
        # energy_kwh=0.417 → power_kw = 0.417 × 12 = 5.004 → MW = 0.005004
        block = [_make_interval(_ts(10, 0), energy_kwh=0.417)]
        await sender._send_charger_block(block)

        # First call is power
        power_call = fm_client.post_measurements.call_args_list[0]
        power_values = power_call.kwargs["values"]
        # 0.417 × 60 / (5 × 1000) = 0.005004
        assert power_values == [round(0.417 * 60 / (5 * 1000), 5)]

    @pytest.mark.asyncio
    async def test_none_power_passed_through(self, sender, fm_client):
        block = [_make_interval(_ts(10, 0), energy_kwh=None)]
        await sender._send_charger_block(block)

        power_call = fm_client.post_measurements.call_args_list[0]
        assert power_call.kwargs["values"] == [None]

    @pytest.mark.asyncio
    async def test_none_soc_passed_through(self, sender, fm_client):
        block = [_make_interval(_ts(10, 0), soc_pct=None)]
        await sender._send_charger_block(block)

        soc_call = fm_client.post_measurements.call_args_list[1]
        assert soc_call.kwargs["values"] == [None]

    @pytest.mark.asyncio
    async def test_correct_sensor_ids(self, sender, fm_client):
        block = [_make_interval(_ts(10, 0))]
        await sender._send_charger_block(block)

        calls = fm_client.post_measurements.call_args_list
        assert calls[0].kwargs["sensor_id"] == 101  # power
        assert calls[1].kwargs["sensor_id"] == 102  # soc
        assert calls[2].kwargs["sensor_id"] == 103  # availability

    @pytest.mark.asyncio
    async def test_correct_start_and_duration(self, sender, fm_client):
        block = [
            _make_interval(_ts(10, 0)),
            _make_interval(_ts(10, 5)),
            _make_interval(_ts(10, 10)),
        ]
        await sender._send_charger_block(block)

        call = fm_client.post_measurements.call_args_list[0]
        assert call.kwargs["start"] == _ts(10, 0)
        assert call.kwargs["duration"] == "PT0H15M"

    @pytest.mark.asyncio
    async def test_power_failure_returns_false(self, sender, fm_client):
        fm_client.post_measurements = AsyncMock(return_value=False)
        block = [_make_interval(_ts(10, 0))]
        result = await sender._send_charger_block(block)
        assert result is False
        # Only one call made (power failed, didn't attempt soc/availability)
        assert fm_client.post_measurements.call_count == 1

    @pytest.mark.asyncio
    async def test_soc_failure_returns_false(self, sender, fm_client):
        fm_client.post_measurements = AsyncMock(
            side_effect=[True, False]  # power ok, soc fails
        )
        block = [_make_interval(_ts(10, 0))]
        result = await sender._send_charger_block(block)
        assert result is False
        assert fm_client.post_measurements.call_count == 2

    @pytest.mark.asyncio
    async def test_availability_failure_returns_false(self, sender, fm_client):
        fm_client.post_measurements = AsyncMock(
            side_effect=[True, True, False]  # power ok, soc ok, availability fails
        )
        block = [_make_interval(_ts(10, 0))]
        result = await sender._send_charger_block(block)
        assert result is False
        assert fm_client.post_measurements.call_count == 3


# ──────────────────────────────────────────────────────────
# _send_unsent_data
# ──────────────────────────────────────────────────────────


class TestSendUnsentData:
    @pytest.mark.asyncio
    async def test_skip_when_no_fm_client(self, sender):
        sender.fm_client_app = None
        await sender._send_unsent_data()
        # Should not interact with data_store at all
        sender.data_store.get_fm_last_sent.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_when_no_data_store(self, sender):
        sender.data_store = None
        await sender._send_unsent_data()
        # Should not crash

    @pytest.mark.asyncio
    async def test_recovers_when_last_sent_is_none(self, sender, data_store):
        # charger has no last_sent → should recover
        await sender._send_charger_data()
        data_store.set_fm_last_sent.assert_called_once()

    @pytest.mark.asyncio
    async def test_skip_when_no_unsent_data(self, sender, data_store):
        data_store._last_sent["charger"] = _ts(10, 0)
        data_store.get_intervals_since.return_value = []
        await sender._send_charger_data()
        # set_fm_last_sent should not be called (no data to advance)
        data_store.set_fm_last_sent.assert_not_called()

    @pytest.mark.asyncio
    async def test_success_updates_last_sent(self, sender, data_store, fm_client):
        data_store._last_sent["charger"] = _ts(9, 55)
        data_store.get_intervals_since.return_value = [
            _make_interval(_ts(10, 0)),
            _make_interval(_ts(10, 5)),
        ]
        await sender._send_charger_data()

        data_store.set_fm_last_sent.assert_called_with(_ts(10, 5), "charger")
        assert fm_client.post_measurements.call_count == 3

    @pytest.mark.asyncio
    async def test_failure_does_not_update_last_sent(
        self, sender, data_store, fm_client
    ):
        data_store._last_sent["charger"] = _ts(9, 55)
        data_store.get_intervals_since.return_value = [
            _make_interval(_ts(10, 0)),
        ]
        fm_client.post_measurements = AsyncMock(return_value=False)

        await sender._send_charger_data()
        # Only the recovery call in _send_unsent_data, not advancement
        assert data_store._last_sent["charger"] == _ts(9, 55)

    @pytest.mark.asyncio
    async def test_multiple_blocks_sent_sequentially(
        self, sender, data_store, fm_client
    ):
        data_store._last_sent["charger"] = _ts(9, 55)
        data_store.get_intervals_since.return_value = [
            _make_interval(_ts(10, 0)),
            # gap
            _make_interval(_ts(10, 15)),
        ]
        await sender._send_charger_data()

        # 2 blocks × 3 calls = 6
        assert fm_client.post_measurements.call_count == 6
        assert data_store._last_sent["charger"] == _ts(10, 15)

    @pytest.mark.asyncio
    async def test_second_block_failure_stops_processing(
        self, sender, data_store, fm_client
    ):
        data_store._last_sent["charger"] = _ts(9, 55)
        data_store.get_intervals_since.return_value = [
            _make_interval(_ts(10, 0)),
            # gap
            _make_interval(_ts(10, 15)),
        ]
        # First block succeeds (3 calls), second block fails on power
        fm_client.post_measurements = AsyncMock(side_effect=[True, True, True, False])

        await sender._send_charger_data()

        # last_sent advanced only for first block
        assert data_store._last_sent["charger"] == _ts(10, 0)


# ──────────────────────────────────────────────────────────
# initialize
# ──────────────────────────────────────────────────────────


class TestInitialize:
    @pytest.mark.asyncio
    async def test_first_start_sets_last_sent_for_all_types(self, sender, data_store):
        # Both charger and grid have no last_sent → both should be set
        await sender.initialize()

        # set_fm_last_sent called for charger and grid
        assert data_store.set_fm_last_sent.call_count == 2

    @pytest.mark.asyncio
    async def test_existing_last_sent_not_overwritten(self, sender, data_store):
        data_store._last_sent["charger"] = _ts(10, 0)
        data_store._last_sent["grid"] = _ts(10, 0)
        await sender.initialize()

        data_store.set_fm_last_sent.assert_not_called()

    @pytest.mark.asyncio
    async def test_schedules_hourly_task(self, sender, hass):
        await sender.initialize()
        hass.run_every.assert_called_once()
        call_args = hass.run_every.call_args
        assert call_args.args[2] == 3600  # interval in seconds


# ──────────────────────────────────────────────────────────
# Grid data sending
# ──────────────────────────────────────────────────────────


def _make_grid_row(ts_str, phase, consumption_kw=1.0, production_kw=0.0):
    """Helper to create a grid_interval_log row dict."""
    return {
        "timestamp": ts_str,
        "phase": phase,
        "consumption_kw": consumption_kw,
        "production_kw": production_kw,
    }


class TestSendGridData:
    @pytest.mark.asyncio
    async def test_skips_when_grid_not_configured(self, sender, data_store):
        """No grid sending when FM_GRID_CONSUMPTION_SENSOR_IDS is empty."""
        c.FM_GRID_CONSUMPTION_SENSOR_IDS = {}
        await sender._send_grid_data()
        data_store.get_grid_intervals_since.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_1_phase_grid_data(self, sender, data_store, fm_client):
        """1-phase grid: 2 post_measurements calls (1 cons + 1 prod)."""
        c.FM_GRID_CONSUMPTION_SENSOR_IDS = {1: 501}
        c.FM_GRID_PRODUCTION_SENSOR_IDS = {1: 502}
        data_store._last_sent["grid"] = _ts(9, 55)
        data_store.get_grid_intervals_since.return_value = [
            _make_grid_row(_ts(10, 0), 1, 2.5, 0.1),
            _make_grid_row(_ts(10, 5), 1, 2.3, 0.2),
        ]

        await sender._send_grid_data()

        assert fm_client.post_measurements.call_count == 2
        # Consumption call
        cons_call = fm_client.post_measurements.call_args_list[0]
        assert cons_call.kwargs["sensor_id"] == 501
        assert cons_call.kwargs["values"] == [2.5, 2.3]
        assert cons_call.kwargs["uom"] == "kW"
        # Production call
        prod_call = fm_client.post_measurements.call_args_list[1]
        assert prod_call.kwargs["sensor_id"] == 502
        assert prod_call.kwargs["values"] == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_sends_3_phase_grid_data(self, sender, data_store, fm_client):
        """3-phase grid: 6 post_measurements calls (3 cons + 3 prod)."""
        c.FM_GRID_CONSUMPTION_SENSOR_IDS = {1: 501, 2: 503, 3: 505}
        c.FM_GRID_PRODUCTION_SENSOR_IDS = {1: 502, 2: 504, 3: 506}
        data_store._last_sent["grid"] = _ts(9, 55)
        data_store.get_grid_intervals_since.return_value = [
            _make_grid_row(_ts(10, 0), 1, 2.5, 0.1),
            _make_grid_row(_ts(10, 0), 2, 1.8, 0.0),
            _make_grid_row(_ts(10, 0), 3, 0.9, 0.5),
        ]

        await sender._send_grid_data()

        # 3 consumption + 3 production = 6
        assert fm_client.post_measurements.call_count == 6
        assert data_store._last_sent["grid"] == _ts(10, 0)

    @pytest.mark.asyncio
    async def test_grid_failure_does_not_advance(self, sender, data_store, fm_client):
        """Failed grid post does not advance last_sent."""
        c.FM_GRID_CONSUMPTION_SENSOR_IDS = {1: 501}
        c.FM_GRID_PRODUCTION_SENSOR_IDS = {1: 502}
        data_store._last_sent["grid"] = _ts(9, 55)
        data_store.get_grid_intervals_since.return_value = [
            _make_grid_row(_ts(10, 0), 1, 2.5, 0.1),
        ]
        fm_client.post_measurements = AsyncMock(return_value=False)

        await sender._send_grid_data()

        assert data_store._last_sent["grid"] == _ts(9, 55)

    @pytest.mark.asyncio
    async def test_grid_gap_creates_two_blocks(self, sender, data_store, fm_client):
        """Gap in timestamps creates two separate blocks."""
        c.FM_GRID_CONSUMPTION_SENSOR_IDS = {1: 501}
        c.FM_GRID_PRODUCTION_SENSOR_IDS = {1: 502}
        data_store._last_sent["grid"] = _ts(9, 55)
        data_store.get_grid_intervals_since.return_value = [
            _make_grid_row(_ts(10, 0), 1, 2.5, 0.1),
            # gap: 10:05 missing
            _make_grid_row(_ts(10, 10), 1, 2.3, 0.2),
        ]

        await sender._send_grid_data()

        # 2 blocks × 2 calls (1 cons + 1 prod) = 4
        assert fm_client.post_measurements.call_count == 4
        assert data_store._last_sent["grid"] == _ts(10, 10)


# ──────────────────────────────────────────────────────────
# DataStore integration (fm_send_status table)
# ──────────────────────────────────────────────────────────


class TestDataStoreFmSendStatus:
    """Tests for the DataStore fm_send_status methods using a real SQLite DB."""

    @pytest.fixture
    def real_data_store(self, tmp_path):
        from apps.v2g_liberty.data_store import DataStore

        mock_hass = AsyncMock(spec=Hass)
        store = DataStore(mock_hass)
        store.DB_PATH = str(tmp_path / "test_v2g_liberty_data.db")
        return store

    @pytest.mark.asyncio
    async def test_get_fm_last_sent_returns_none_initially(self, real_data_store):
        await real_data_store.initialise()
        assert real_data_store.get_fm_last_sent() is None

    @pytest.mark.asyncio
    async def test_set_and_get_fm_last_sent(self, real_data_store):
        await real_data_store.initialise()
        real_data_store.set_fm_last_sent(_ts(10, 0))
        assert real_data_store.get_fm_last_sent() == _ts(10, 0)

    @pytest.mark.asyncio
    async def test_set_fm_last_sent_updates_existing(self, real_data_store):
        await real_data_store.initialise()
        real_data_store.set_fm_last_sent(_ts(10, 0))
        real_data_store.set_fm_last_sent(_ts(12, 0))
        assert real_data_store.get_fm_last_sent() == _ts(12, 0)

    @pytest.mark.asyncio
    async def test_independent_data_types(self, real_data_store):
        """Each data_type has its own independent last_sent timestamp."""
        await real_data_store.initialise()
        real_data_store.set_fm_last_sent(_ts(10, 0), data_type="charger")
        real_data_store.set_fm_last_sent(_ts(11, 0), data_type="grid")
        real_data_store.set_fm_last_sent(_ts(12, 0), data_type="pv")

        assert real_data_store.get_fm_last_sent("charger") == _ts(10, 0)
        assert real_data_store.get_fm_last_sent("grid") == _ts(11, 0)
        assert real_data_store.get_fm_last_sent("pv") == _ts(12, 0)

    @pytest.mark.asyncio
    async def test_unknown_data_type_returns_none(self, real_data_store):
        """Querying a data_type that was never set returns None."""
        await real_data_store.initialise()
        real_data_store.set_fm_last_sent(_ts(10, 0), data_type="charger")
        assert real_data_store.get_fm_last_sent("grid") is None

    @pytest.mark.asyncio
    async def test_update_one_type_does_not_affect_others(self, real_data_store):
        """Updating one data_type does not change others."""
        await real_data_store.initialise()
        real_data_store.set_fm_last_sent(_ts(10, 0), data_type="charger")
        real_data_store.set_fm_last_sent(_ts(10, 0), data_type="grid")

        real_data_store.set_fm_last_sent(_ts(12, 0), data_type="charger")

        assert real_data_store.get_fm_last_sent("charger") == _ts(12, 0)
        assert real_data_store.get_fm_last_sent("grid") == _ts(10, 0)

    @pytest.mark.asyncio
    async def test_schema_migration_v1_to_v2(self, tmp_path):
        """Migrating from v1 preserves existing charger send status."""
        import sqlite3

        db_path = str(tmp_path / "migrate_test.db")

        # Create a v1 database manually (old fm_send_status without data_type)
        con = sqlite3.connect(db_path)
        con.execute(
            "CREATE TABLE schema_version "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-01-01')"
        )
        con.execute("CREATE TABLE fm_send_status (last_sent_up_to TEXT NOT NULL)")
        con.execute(
            "INSERT INTO fm_send_status (last_sent_up_to) VALUES (?)",
            (_ts(9, 30),),
        )
        con.commit()
        con.close()

        # Now open with DataStore — should migrate to v2
        from apps.v2g_liberty.data_store import DataStore

        mock_hass = AsyncMock(spec=Hass)
        store = DataStore(mock_hass)
        store.DB_PATH = db_path
        await store.initialise()

        # Charger data should be preserved
        assert store.get_fm_last_sent("charger") == _ts(9, 30)
        # Grid and PV should be None (not yet set)
        assert store.get_fm_last_sent("grid") is None
        assert store.get_fm_last_sent("pv") is None

    @pytest.mark.asyncio
    async def test_get_intervals_since_returns_matching_rows(self, real_data_store):
        await real_data_store.initialise()
        real_data_store.insert_interval(
            timestamp=_ts(10, 0),
            energy_kwh=0.125,
            app_state="automatic",
            soc_pct=50.0,
            availability_pct=100.0,
        )
        real_data_store.insert_interval(
            timestamp=_ts(10, 5),
            energy_kwh=0.167,
            app_state="automatic",
            soc_pct=51.0,
            availability_pct=100.0,
        )

        # Query since 09:55 → should return both
        rows = real_data_store.get_intervals_since(_ts(9, 55))
        assert len(rows) == 2
        assert rows[0]["timestamp"] == _ts(10, 0)
        assert rows[0]["energy_kwh"] == 0.125

    @pytest.mark.asyncio
    async def test_get_intervals_since_excludes_older_rows(self, real_data_store):
        await real_data_store.initialise()
        real_data_store.insert_interval(
            timestamp=_ts(10, 0),
            energy_kwh=0.125,
            app_state="automatic",
            soc_pct=50.0,
            availability_pct=100.0,
        )
        real_data_store.insert_interval(
            timestamp=_ts(10, 5),
            energy_kwh=0.167,
            app_state="automatic",
            soc_pct=51.0,
            availability_pct=100.0,
        )

        # Query since 10:00 → should only return 10:05
        rows = real_data_store.get_intervals_since(_ts(10, 0))
        assert len(rows) == 1
        assert rows[0]["timestamp"] == _ts(10, 5)

    @pytest.mark.asyncio
    async def test_get_intervals_since_returns_only_needed_columns(
        self, real_data_store
    ):
        await real_data_store.initialise()
        real_data_store.insert_interval(
            timestamp=_ts(10, 0),
            energy_kwh=0.125,
            app_state="automatic",
            soc_pct=50.0,
            availability_pct=100.0,
        )

        rows = real_data_store.get_intervals_since(_ts(9, 55))
        assert len(rows) == 1
        row = rows[0]
        assert set(row.keys()) == {
            "timestamp",
            "energy_kwh",
            "soc_pct",
            "availability_pct",
        }

    @pytest.mark.asyncio
    async def test_get_intervals_since_none_soc(self, real_data_store):
        await real_data_store.initialise()
        real_data_store.insert_interval(
            timestamp=_ts(10, 0),
            energy_kwh=0.0,
            app_state="not_connected",
            soc_pct=None,
            availability_pct=0.0,
        )

        rows = real_data_store.get_intervals_since(_ts(9, 55))
        assert rows[0]["soc_pct"] is None
