"""Unit test (pytest) for data_store module."""

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from appdaemon.plugins.hass.hassapi import Hass

import pandas as pd

from apps.v2g_liberty.data_store import (
    CURRENT_SCHEMA_VERSION,
    DataStore,
    calculate_price_ratings,
)

# pylint: disable=C0116,W0621
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)

# Fixed timezone for deterministic tests
TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 21, 12, 0, 0, tzinfo=TEST_TZ)


def get_log_message(log_call):
    """Extract the log message from a single call object."""
    if log_call and log_call.args:
        return log_call.args[0]
    elif log_call and log_call.kwargs:
        return log_call.kwargs.get("msg", "")
    return ""


def find_log_containing(hass, text):
    """Search all log calls for a message containing the given text."""
    for call in hass.log.call_args_list:
        msg = get_log_message(call)
        if text in msg:
            return call
    return None


@pytest.fixture
def hass():
    return AsyncMock(spec=Hass)


@pytest.fixture
def data_store(hass, tmp_path):
    """Create a DataStore using an in-memory or temp directory database."""
    store = DataStore(hass)
    # Override DB_PATH to use temp directory for test isolation
    store.DB_PATH = str(tmp_path / "test_v2g_liberty_data.db")
    return store


class TestInitialisation:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_initialise_creates_database(self, mock_now, data_store):
        await data_store.initialise()
        assert data_store.connection is not None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_initialise_sets_wal_journal_mode(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        cursor.close()
        assert mode == "wal"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_initialise_sets_synchronous_normal(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA synchronous")
        # NORMAL = 1
        value = cursor.fetchone()[0]
        cursor.close()
        assert value == 1

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_initialise_sets_temp_store_memory(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA temp_store")
        # MEMORY = 2
        value = cursor.fetchone()[0]
        cursor.close()
        assert value == 2


class TestTableCreation:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_all_four_tables_created(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        assert "schema_version" in tables
        assert "interval_log" in tables
        assert "price_log" in tables
        assert "reservation_log" in tables

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_interval_log_columns(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA table_info(interval_log)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        cursor.close()
        assert columns["timestamp"] == "TEXT"
        assert columns["power_kw"] == "REAL"
        assert columns["energy_kwh"] == "REAL"
        assert columns["app_state"] == "TEXT"
        assert columns["soc_pct"] == "REAL"
        assert columns["availability_pct"] == "REAL"
        assert columns["consumption_price_kwh"] == "REAL"
        assert columns["production_price_kwh"] == "REAL"
        assert columns["price_rating"] == "TEXT"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_price_log_columns(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA table_info(price_log)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        cursor.close()
        assert columns["timestamp"] == "TEXT"
        assert columns["consumption_price_kwh"] == "REAL"
        assert columns["production_price_kwh"] == "REAL"
        assert columns["price_rating"] == "TEXT"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_reservation_log_columns(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA table_info(reservation_log)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}
        cursor.close()
        assert columns["timestamp"] == "TEXT"
        assert columns["start_timestamp"] == "TEXT"
        assert columns["end_timestamp"] == "TEXT"
        assert columns["target_soc_pct"] == "REAL"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_interval_log_has_primary_key_on_timestamp(
        self, mock_now, data_store
    ):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA table_info(interval_log)")
        # Column index 5 is the 'pk' flag (1 = primary key)
        pk_columns = [row[1] for row in cursor.fetchall() if row[5] == 1]
        cursor.close()
        assert pk_columns == ["timestamp"]

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_price_log_has_primary_key_on_timestamp(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("PRAGMA table_info(price_log)")
        pk_columns = [row[1] for row in cursor.fetchall() if row[5] == 1]
        cursor.close()
        assert pk_columns == ["timestamp"]


class TestSchemaVersion:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_first_start_inserts_version_1(self, mock_now, data_store):
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT version, applied_at FROM schema_version")
        row = cursor.fetchone()
        cursor.close()
        assert row is not None
        assert row[0] == CURRENT_SCHEMA_VERSION
        assert row[1] == TEST_NOW.isoformat()

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_second_start_does_not_duplicate_version(self, mock_now, data_store):
        await data_store.initialise()
        # Close and reinitialise to simulate restart
        data_store.close()
        await data_store.initialise()
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM schema_version")
        count = cursor.fetchone()[0]
        cursor.close()
        # Should still be exactly one row
        assert count == 1

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_second_start_logs_up_to_date(self, mock_now, data_store, hass):
        await data_store.initialise()
        data_store.close()
        await data_store.initialise()
        assert find_log_containing(hass, "up to date") is not None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_newer_version_logs_warning(self, mock_now, data_store, hass):
        await data_store.initialise()
        # Manually bump version to simulate a downgrade scenario
        cursor = data_store.connection.cursor()
        cursor.execute(
            "UPDATE schema_version SET version = ?",
            (CURRENT_SCHEMA_VERSION + 1,),
        )
        data_store.connection.commit()
        cursor.close()
        # Reinitialise
        data_store.close()
        await data_store.initialise()
        warning_call = find_log_containing(hass, "newer than expected")
        assert warning_call is not None
        assert warning_call.kwargs.get("level") == "WARNING"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_tables_preserved_after_reinitialise(self, mock_now, data_store):
        """Verify that CREATE TABLE IF NOT EXISTS does not drop existing data."""
        await data_store.initialise()
        # Insert a test row into price_log
        cursor = data_store.connection.cursor()
        cursor.execute(
            "INSERT INTO price_log (timestamp, consumption_price_kwh, "
            "production_price_kwh, price_rating) VALUES (?, ?, ?, ?)",
            ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "average"),
        )
        data_store.connection.commit()
        cursor.close()
        # Reinitialise
        data_store.close()
        await data_store.initialise()
        # Verify data is still there
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM price_log")
        count = cursor.fetchone()[0]
        cursor.close()
        assert count == 1


class TestClose:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_close_sets_connection_to_none(self, mock_now, data_store):
        await data_store.initialise()
        data_store.close()
        assert data_store.connection is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_close_without_initialise_does_not_error(self, mock_now, data_store):
        # Should not raise
        data_store.close()
        assert data_store.connection is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_double_close_does_not_error(self, mock_now, data_store):
        await data_store.initialise()
        data_store.close()
        # Second close should not raise
        data_store.close()
        assert data_store.connection is None


class TestInsertInterval:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_insert_interval_stores_all_fields(self, mock_now, data_store):
        await data_store.initialise()
        data_store.insert_interval(
            timestamp="2026-02-21T12:00:00+01:00",
            power_kw=3.5,
            energy_kwh=0.292,
            app_state="automatic",
            soc_pct=55.0,
            availability_pct=100.0,
            consumption_price_kwh=0.25,
            production_price_kwh=0.10,
            price_rating="low",
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT * FROM interval_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["timestamp"] == "2026-02-21T12:00:00+01:00"
        assert row["power_kw"] == 3.5
        assert row["energy_kwh"] == 0.292
        assert row["app_state"] == "automatic"
        assert row["soc_pct"] == 55.0
        assert row["availability_pct"] == 100.0
        assert row["consumption_price_kwh"] == 0.25
        assert row["production_price_kwh"] == 0.10
        assert row["price_rating"] == "low"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_insert_interval_nullable_fields(self, mock_now, data_store):
        await data_store.initialise()
        data_store.insert_interval(
            timestamp="2026-02-21T12:00:00+01:00",
            power_kw=0.0,
            energy_kwh=0.0,
            app_state="not_connected",
            soc_pct=None,
            availability_pct=0.0,
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT * FROM interval_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["soc_pct"] is None
        assert row["consumption_price_kwh"] is None
        assert row["production_price_kwh"] is None
        assert row["price_rating"] is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_insert_interval_duplicate_timestamp_raises(
        self, mock_now, data_store
    ):
        await data_store.initialise()
        data_store.insert_interval(
            timestamp="2026-02-21T12:00:00+01:00",
            power_kw=1.0,
            energy_kwh=0.083,
            app_state="automatic",
            soc_pct=50.0,
            availability_pct=100.0,
        )
        with pytest.raises(Exception):
            data_store.insert_interval(
                timestamp="2026-02-21T12:00:00+01:00",
                power_kw=2.0,
                energy_kwh=0.167,
                app_state="automatic",
                soc_pct=51.0,
                availability_pct=100.0,
            )


class TestUpsertPrices:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_upsert_prices_inserts_rows(self, mock_now, data_store):
        await data_store.initialise()
        rows = [
            ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "average"),
            ("2026-02-21T12:05:00+01:00", 0.26, 0.11, "average"),
            ("2026-02-21T12:10:00+01:00", 0.30, 0.15, "high"),
        ]
        data_store.upsert_prices(rows)
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM price_log")
        count = cursor.fetchone()[0]
        cursor.close()
        assert count == 3

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_upsert_prices_overwrites_existing(self, mock_now, data_store):
        """Verify UPSERT: forecast price is overwritten by definitive price."""
        await data_store.initialise()
        # Insert forecast
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "average"),
            ],
            recalculate_ratings=False,
        )
        # Overwrite with definitive
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.28, 0.12, "high"),
            ],
            recalculate_ratings=False,
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT * FROM price_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["consumption_price_kwh"] == 0.28
        assert row["production_price_kwh"] == 0.12
        assert row["price_rating"] == "high"
        # Should still be exactly one row
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT COUNT(*) FROM price_log")
        count = cursor.fetchone()[0]
        cursor.close()
        assert count == 1

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_upsert_prices_with_null_rating(self, mock_now, data_store):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, None),
            ],
            recalculate_ratings=False,
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT price_rating FROM price_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["price_rating"] is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_upsert_prices_logs_count(self, mock_now, data_store, hass):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, None),
                ("2026-02-21T12:05:00+01:00", 0.26, 0.11, None),
            ]
        )
        assert find_log_containing(hass, "2 price row(s)") is not None


class TestInsertReservation:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_insert_reservation_stores_all_fields(self, mock_now, data_store):
        await data_store.initialise()
        data_store.insert_reservation(
            timestamp="2026-02-21T12:00:00+01:00",
            start_timestamp="2026-02-22T08:00:00+01:00",
            end_timestamp="2026-02-22T17:00:00+01:00",
            target_soc_pct=90.0,
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT * FROM reservation_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["timestamp"] == "2026-02-21T12:00:00+01:00"
        assert row["start_timestamp"] == "2026-02-22T08:00:00+01:00"
        assert row["end_timestamp"] == "2026-02-22T17:00:00+01:00"
        assert row["target_soc_pct"] == 90.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_insert_reservation_nullable_soc(self, mock_now, data_store):
        await data_store.initialise()
        data_store.insert_reservation(
            timestamp="2026-02-21T12:00:00+01:00",
            start_timestamp="2026-02-22T08:00:00+01:00",
            end_timestamp="2026-02-22T17:00:00+01:00",
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT target_soc_pct FROM reservation_log")
        row = cursor.fetchone()
        cursor.close()
        assert row["target_soc_pct"] is None


class TestGetPriceAt:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_get_price_at_existing(self, mock_now, data_store):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "low"),
            ],
            recalculate_ratings=False,
        )
        result = data_store.get_price_at("2026-02-21T12:00:00+01:00")
        assert result == (0.25, 0.10, "low")

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_get_price_at_missing(self, mock_now, data_store):
        await data_store.initialise()
        result = data_store.get_price_at("2026-02-21T12:00:00+01:00")
        assert result is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_get_price_at_null_rating(self, mock_now, data_store):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, None),
            ],
            recalculate_ratings=False,
        )
        result = data_store.get_price_at("2026-02-21T12:00:00+01:00")
        assert result == (0.25, 0.10, None)


class TestGetPricesInWindow:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_empty_window(self, mock_now, data_store):
        await data_store.initialise()
        df = data_store.get_prices_in_window(
            "2026-02-21T12:00:00+01:00", "2026-02-21T13:00:00+01:00"
        )
        assert len(df) == 0
        assert list(df.columns) == [
            "timestamp",
            "consumption_price_kwh",
            "production_price_kwh",
            "price_rating",
        ]

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_window_returns_matching_rows(self, mock_now, data_store):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T11:55:00+01:00", 0.20, 0.08, "very_low"),
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "average"),
                ("2026-02-21T12:05:00+01:00", 0.26, 0.11, "average"),
                ("2026-02-21T12:10:00+01:00", 0.30, 0.15, "high"),
                ("2026-02-21T12:15:00+01:00", 0.35, 0.20, "very_high"),
            ]
        )
        df = data_store.get_prices_in_window(
            "2026-02-21T12:00:00+01:00", "2026-02-21T12:10:00+01:00"
        )
        assert len(df) == 3
        assert df.iloc[0]["timestamp"] == "2026-02-21T12:00:00+01:00"
        assert df.iloc[2]["timestamp"] == "2026-02-21T12:10:00+01:00"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_window_returns_ordered_by_timestamp(self, mock_now, data_store):
        await data_store.initialise()
        # Insert out of order
        data_store.upsert_prices(
            [
                ("2026-02-21T12:10:00+01:00", 0.30, 0.15, None),
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, None),
                ("2026-02-21T12:05:00+01:00", 0.26, 0.11, None),
            ]
        )
        df = data_store.get_prices_in_window(
            "2026-02-21T12:00:00+01:00", "2026-02-21T12:10:00+01:00"
        )
        timestamps = df["timestamp"].tolist()
        assert timestamps == [
            "2026-02-21T12:00:00+01:00",
            "2026-02-21T12:05:00+01:00",
            "2026-02-21T12:10:00+01:00",
        ]

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_window_dataframe_has_correct_dtypes(self, mock_now, data_store):
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, "low"),
            ],
            recalculate_ratings=False,
        )
        df = data_store.get_prices_in_window(
            "2026-02-21T12:00:00+01:00", "2026-02-21T12:00:00+01:00"
        )
        assert len(df) == 1
        assert df.iloc[0]["consumption_price_kwh"] == 0.25
        assert df.iloc[0]["production_price_kwh"] == 0.10
        assert df.iloc[0]["price_rating"] == "low"


class TestCalculatePriceRatings:
    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["consumption_price_kwh"])
        result = calculate_price_ratings(df)
        assert len(result) == 0

    def test_twenty_distinct_prices(self):
        """With 20 distinct prices, verify exact rating distribution.

        rank(pct=True) for 20 items gives [0.05, 0.10, ..., 1.0].
        Expected: 3 very_low, 4 low, 6 average, 4 high, 3 very_high.
        """
        prices = [i * 0.01 for i in range(1, 21)]
        df = pd.DataFrame({"consumption_price_kwh": prices})
        result = calculate_price_ratings(df)
        assert list(result[:3]) == ["very_low"] * 3
        assert list(result[3:7]) == ["low"] * 4
        assert list(result[7:13]) == ["average"] * 6
        assert list(result[13:17]) == ["high"] * 4
        assert list(result[17:20]) == ["very_high"] * 3

    def test_all_equal_prices(self):
        """With 20 equal prices, all get 'average' (pct rank ~0.525)."""
        df = pd.DataFrame({"consumption_price_kwh": [0.25] * 20})
        result = calculate_price_ratings(df)
        assert all(r == "average" for r in result)

    def test_single_price(self):
        """A single price gets 'very_high' (rank 1.0)."""
        df = pd.DataFrame({"consumption_price_kwh": [0.25]})
        result = calculate_price_ratings(df)
        assert result.iloc[0] == "very_high"

    def test_two_prices(self):
        """With two distinct prices: lowest gets 'average', highest 'very_high'."""
        df = pd.DataFrame({"consumption_price_kwh": [0.10, 0.30]})
        result = calculate_price_ratings(df)
        # rank(pct=True): [0.5, 1.0] → average, very_high
        assert result.iloc[0] == "average"
        assert result.iloc[1] == "very_high"


class TestUpsertPricesRecalculation:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_upsert_recalculates_ratings_in_db(self, mock_now, data_store):
        """After upserting with recalculate_ratings=True, DB has calculated ratings."""
        await data_store.initialise()
        # 20 distinct prices → predictable rating distribution
        base = datetime(2026, 2, 21, 12, 0, tzinfo=timezone(timedelta(hours=1)))
        rows = [
            (
                (base + timedelta(minutes=5 * i)).isoformat(),
                0.01 * (i + 1),
                0.005 * (i + 1),
                None,
            )
            for i in range(20)
        ]
        data_store.upsert_prices(rows)

        # Verify ratings in DB
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT price_rating FROM price_log ORDER BY timestamp")
        db_ratings = [row["price_rating"] for row in cursor.fetchall()]
        cursor.close()

        assert db_ratings[:3] == ["very_low"] * 3
        assert db_ratings[3:7] == ["low"] * 4
        assert db_ratings[7:13] == ["average"] * 6
        assert db_ratings[13:17] == ["high"] * 4
        assert db_ratings[17:20] == ["very_high"] * 3

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_recalculate_false_preserves_input_rating(self, mock_now, data_store):
        """With recalculate_ratings=False, input ratings are preserved as-is."""
        await data_store.initialise()
        data_store.upsert_prices(
            [
                ("2026-02-21T12:00:00+01:00", 0.25, 0.10, None),
                ("2026-02-21T12:05:00+01:00", 0.30, 0.15, "custom"),
            ],
            recalculate_ratings=False,
        )
        cursor = data_store.connection.cursor()
        cursor.execute("SELECT price_rating FROM price_log ORDER BY timestamp")
        ratings = [row["price_rating"] for row in cursor.fetchall()]
        cursor.close()
        assert ratings == [None, "custom"]

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_overwrite_triggers_recalculation(self, mock_now, data_store):
        """When forecast is replaced by definitive, ratings are recalculated."""
        await data_store.initialise()
        base = datetime(2026, 2, 21, 12, 0, tzinfo=timezone(timedelta(hours=1)))
        # Insert 3 forecast prices: all equal → all "average"
        forecast_rows = [
            ((base + timedelta(minutes=5 * i)).isoformat(), 0.25, 0.10, None)
            for i in range(3)
        ]
        data_store.upsert_prices(forecast_rows)

        cursor = data_store.connection.cursor()
        cursor.execute("SELECT price_rating FROM price_log ORDER BY timestamp")
        before = [row["price_rating"] for row in cursor.fetchall()]
        cursor.close()
        # 3 equal prices with rank ~0.667 → all "high"
        # (pct rank for 3 equal = 2/3 = 0.667, falls in (0.65, 0.85])
        assert all(r == "high" for r in before)

        # Overwrite middle price with a much lower value
        data_store.upsert_prices(
            [((base + timedelta(minutes=5)).isoformat(), 0.05, 0.02, None)]
        )

        cursor = data_store.connection.cursor()
        cursor.execute("SELECT price_rating FROM price_log ORDER BY timestamp")
        after = [row["price_rating"] for row in cursor.fetchall()]
        cursor.close()
        # Prices by timestamp: 0.25, 0.05, 0.25
        # Ranks (pct): 0.25→0.833, 0.05→0.333, 0.25→0.833
        assert after[0] == "high"  # 0.25: rank 0.833 → (0.65, 0.85]
        assert after[1] == "low"  # 0.05: rank 0.333 → (0.15, 0.35]
        assert after[2] == "high"  # 0.25: rank 0.833 → (0.65, 0.85]
        # Key: the overwritten price now has a different rating than before
