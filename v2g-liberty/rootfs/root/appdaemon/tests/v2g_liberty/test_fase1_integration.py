"""Integration tests for Fase 1: end-to-end data flow (T18).

Tests the complete chain:
  1. Prices received → written to price_log (with price_rating)
  2. DataMonitor concludes interval → interval_log row
  3. Calendar change → reservation_log row

Uses a REAL DataStore with SQLite (temp directory) — no mocking of the DB layer.
DataMonitor's hass/event_bus/evse_client remain mocked.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.data_monitor import DataMonitor
from apps.v2g_liberty.data_store import DataStore
from apps.v2g_liberty.event_bus import EventBus

# pylint: disable=C0116,W0621

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _set_constants():
    """Set runtime constants that are normally initialised by V2GLibertyGlobals."""
    c.EVENT_RESOLUTION = timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)


@pytest.fixture
def hass():
    mock_hass = AsyncMock(spec=Hass)
    mock_hass.log = MagicMock()
    mock_hass.get_state = AsyncMock(return_value="Automatic")
    mock_hass.listen_state = AsyncMock()
    mock_hass.run_every = AsyncMock()
    return mock_hass


@pytest.fixture
def event_bus():
    eb = MagicMock(spec=EventBus)
    eb.add_event_listener = MagicMock()
    return eb


@pytest.fixture
def evse_client():
    mock_evse = MagicMock()
    mock_evse.ERROR_STATES = [8, 9, 10]
    mock_evse.DISCONNECTED_STATES = [0, 1]
    mock_evse.is_available_for_automated_charging = MagicMock(return_value=True)
    return mock_evse


@pytest.fixture
def data_store(hass, tmp_path):
    """Real DataStore with SQLite in a temp directory.

    Initialises synchronously by calling the internal setup methods directly,
    since the async wrapper is just an AppDaemon convention.
    """
    store = DataStore(hass)
    store.DB_PATH = str(tmp_path / "test_v2g_data.db")
    import sqlite3

    conn = sqlite3.connect(store.DB_PATH)
    conn.row_factory = sqlite3.Row
    store._DataStore__connection = conn
    store._DataStore__set_pragmas()
    store._DataStore__create_tables()
    with patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW):
        store._DataStore__check_schema_version()
    return store


@pytest.fixture
def monitor(hass, event_bus, evse_client, data_store):
    """DataMonitor wired to a real DataStore (not mocked)."""
    dm = DataMonitor(hass, event_bus)
    dm.evse_client_app = evse_client
    dm.data_store = data_store
    # Pre-initialise state as initialize() would
    dm._current_charger_state = None
    dm._current_charge_mode = "Automatic"
    dm._current_app_state = "automatic"
    dm._current_app_state_since = TEST_NOW
    dm._app_state_durations = {}
    dm.connected_car_soc = 60
    # Availability: simulate a full 5-min interval of availability
    dm.availability_duration_in_current_interval = 300_000
    dm.un_availability_duration_in_current_interval = 0
    dm.current_availability = True
    dm.current_availability_since = TEST_NOW
    # Power tracking
    dm.current_power = 0
    dm.current_power_since = TEST_NOW
    dm.power_period_duration = 0
    dm.period_power_x_duration = 0
    return dm


def _query_all(data_store, table):
    """Helper: fetch all rows from a table as list of dicts."""
    cursor = data_store.connection.cursor()
    cursor.execute(f"SELECT * FROM {table} ORDER BY timestamp")  # noqa: S608
    rows = [dict(row) for row in cursor.fetchall()]
    cursor.close()
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIntervalWriteFlow:
    """End-to-end: DataMonitor concludes interval → interval_log row."""

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_interval_written_to_db(self, mock_now, monitor, data_store):
        """Conclude interval → interval_log row with correct fields."""
        mock_now.return_value = TEST_NOW  # 12:00

        await monitor._write_interval_to_db(
            power_kw=3.5,
            energy_kwh=round(3.5 * 5 / 60, 6),
            availability_pct=100.0,
            soc=60,
            app_state="automatic",
        )

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 1

        row = intervals[0]
        assert row["timestamp"] == "2026-02-22T11:55:00+01:00"
        assert row["power_kw"] == 3.5
        assert row["app_state"] == "automatic"
        assert row["soc_pct"] == 60.0
        assert row["availability_pct"] == 100.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_interval_with_null_soc(self, mock_now, monitor, data_store):
        """When car is disconnected, soc_pct should be NULL."""
        mock_now.return_value = TEST_NOW

        await monitor._write_interval_to_db(
            power_kw=0.0,
            energy_kwh=0.0,
            availability_pct=50.0,
            soc=None,
            app_state="not_connected",
        )

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 1
        assert intervals[0]["soc_pct"] is None


class TestPriceRatingIntegration:
    """Price rating calculation across the full stack."""

    @pytest.mark.asyncio
    async def test_upserted_prices_get_rating_recalculated(self, data_store):
        """After upsert_prices with recalculate_ratings=True, all rows in the
        24h window get a price_rating assigned."""
        base = datetime(2026, 2, 22, 0, 0, 0, tzinfo=TEST_TZ)
        rows = []
        for i in range(24):  # 2 hours of 5-min prices
            ts = (base + timedelta(minutes=i * 5)).isoformat()
            cons_price = 0.10 + i * 0.005  # spread from 0.10 to 0.215
            prod_price = cons_price * 0.7
            rows.append((ts, cons_price, prod_price, None))

        data_store.upsert_prices(rows, recalculate_ratings=True)

        # All rows should now have a price_rating
        all_prices = _query_all(data_store, "price_log")
        assert len(all_prices) == 24
        for price_row in all_prices:
            assert price_row["price_rating"] in (
                "very_low",
                "low",
                "average",
                "high",
                "very_high",
            ), f"Row {price_row['timestamp']} has no rating"

    @pytest.mark.asyncio
    async def test_cheapest_prices_rated_very_low(self, data_store):
        """The cheapest prices in a window should be rated very_low."""
        base = datetime(2026, 2, 22, 0, 0, 0, tzinfo=TEST_TZ)
        rows = []
        for i in range(100):  # ~8h20m of 5-min prices
            ts = (base + timedelta(minutes=i * 5)).isoformat()
            cons_price = 0.10 + i * 0.002
            prod_price = cons_price * 0.7
            rows.append((ts, cons_price, prod_price, None))

        data_store.upsert_prices(rows)

        # The very first entries (lowest prices) should be very_low
        cheapest = data_store.get_price_at(base.isoformat())
        assert cheapest is not None
        assert cheapest[2] == "very_low"

        # The last entries (highest prices) should be very_high
        most_expensive_ts = (base + timedelta(minutes=99 * 5)).isoformat()
        expensive = data_store.get_price_at(most_expensive_ts)
        assert expensive is not None
        assert expensive[2] == "very_high"


class TestReservationIntegration:
    """Calendar change → reservation_log via real DataStore."""

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_calendar_change_writes_reservation(
        self, mock_now, monitor, data_store
    ):
        """A calendar_change event writes a reservation snapshot to DB."""
        mock_now.return_value = TEST_NOW

        v2g_events = [
            {
                "start": datetime(2026, 2, 23, 8, 0, 0, tzinfo=TEST_TZ),
                "end": datetime(2026, 2, 23, 18, 0, 0, tzinfo=TEST_TZ),
                "target_soc_percent": 80,
            },
        ]

        await monitor._handle_calendar_change(v2g_events=v2g_events)

        reservations = _query_all(data_store, "reservation_log")
        assert len(reservations) == 1
        row = reservations[0]
        assert row["start_timestamp"] == "2026-02-23T08:00:00+01:00"
        assert row["end_timestamp"] == "2026-02-23T18:00:00+01:00"
        assert row["target_soc_pct"] == 80.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_dismissed_events_are_skipped(self, mock_now, monitor, data_store):
        """Dismissed calendar events are not written to the DB."""
        mock_now.return_value = TEST_NOW

        v2g_events = [
            {
                "start": datetime(2026, 2, 23, 8, 0, 0, tzinfo=TEST_TZ),
                "end": datetime(2026, 2, 23, 18, 0, 0, tzinfo=TEST_TZ),
                "target_soc_percent": 80,
                "dismissed": True,
            },
            "un-initiated",
        ]

        await monitor._handle_calendar_change(v2g_events=v2g_events)

        reservations = _query_all(data_store, "reservation_log")
        assert len(reservations) == 0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_multiple_events_written_as_separate_rows(
        self, mock_now, monitor, data_store
    ):
        """Multiple active reservations each get their own row."""
        mock_now.return_value = TEST_NOW

        v2g_events = [
            {
                "start": datetime(2026, 2, 23, 8, 0, 0, tzinfo=TEST_TZ),
                "end": datetime(2026, 2, 23, 12, 0, 0, tzinfo=TEST_TZ),
                "target_soc_percent": 90,
            },
            {
                "start": datetime(2026, 2, 24, 7, 0, 0, tzinfo=TEST_TZ),
                "end": datetime(2026, 2, 24, 17, 0, 0, tzinfo=TEST_TZ),
                "target_soc_percent": 80,
            },
        ]

        await monitor._handle_calendar_change(v2g_events=v2g_events)

        reservations = _query_all(data_store, "reservation_log")
        assert len(reservations) == 2
        assert reservations[0]["target_soc_pct"] == 90.0
        assert reservations[1]["target_soc_pct"] == 80.0


class TestFullConcludeInterval:
    """Test the complete __conclude_interval flow with real DB."""

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_conclude_interval_end_to_end(self, mock_now, monitor, data_store):
        """Simulate power changes + conclude → verify the DB row."""
        # Simulate: at 11:57 power changes to 3000W, at 12:00 interval concludes.
        t_power_change = TEST_NOW - timedelta(minutes=3)

        # Set up monitor as if power changed at 11:57
        monitor.current_power = 3000  # Watts
        monitor.current_power_since = t_power_change
        monitor.power_period_duration = 0
        monitor.period_power_x_duration = 0

        # Conclude at 12:00
        mock_now.return_value = TEST_NOW

        # Availability: full interval (5 min = 300,000 ms)
        monitor.availability_duration_in_current_interval = 300_000
        monitor.un_availability_duration_in_current_interval = 0
        monitor.current_availability = True
        monitor.current_availability_since = TEST_NOW - timedelta(minutes=5)

        await monitor._DataMonitor__conclude_interval()

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 1
        row = intervals[0]
        assert row["timestamp"] == "2026-02-22T11:55:00+01:00"
        # Power: 3000W for 3 minutes out of 3 min tracked duration → 3.0 kW avg
        assert row["power_kw"] == 3.0
        assert row["app_state"] == "automatic"
        assert row["soc_pct"] == 60.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_short_interval_is_discarded(self, mock_now, monitor, data_store):
        """An interval shorter than 95% of 5 minutes is discarded."""
        mock_now.return_value = TEST_NOW

        # Durations are in ms. Threshold = FM_EVENT_RESOLUTION * 60 * 0.95 = 285.
        # Set total well below 285 to trigger the "too short" discard.
        monitor.availability_duration_in_current_interval = 100
        monitor.un_availability_duration_in_current_interval = 100

        await monitor._DataMonitor__conclude_interval()

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 0


class TestAppStateThroughInterval:
    """App state tracking through a full interval conclusion with real DB."""

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_error_state_wins_over_automatic(self, mock_now, monitor, data_store):
        """If charger enters error state mid-interval, 'error' wins by priority."""
        mock_now.return_value = TEST_NOW

        # Simulate: automatic for 4 min, then error for 1 min
        monitor._app_state_durations = {
            "automatic": 240_000,
            "error": 60_000,
        }
        monitor._current_app_state = "error"
        monitor._current_app_state_since = TEST_NOW

        await monitor._write_interval_to_db(
            power_kw=1.0,
            energy_kwh=round(1.0 * 5 / 60, 6),
            availability_pct=80.0,
            soc=50,
            app_state=monitor._conclude_app_state(),
        )

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 1
        assert intervals[0]["app_state"] == "error"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_longest_equal_priority_wins(self, mock_now, monitor, data_store):
        """Among states with equal priority, the longest duration wins."""
        mock_now.return_value = TEST_NOW

        # charge (prio 4) for 2 min, automatic (prio 4) for 3 min → automatic wins
        monitor._app_state_durations = {
            "charge": 120_000,
            "automatic": 180_000,
        }
        monitor._current_app_state = "automatic"
        monitor._current_app_state_since = TEST_NOW

        await monitor._write_interval_to_db(
            power_kw=0.5,
            energy_kwh=round(0.5 * 5 / 60, 6),
            availability_pct=100.0,
            soc=70,
            app_state=monitor._conclude_app_state(),
        )

        intervals = _query_all(data_store, "interval_log")
        assert len(intervals) == 1
        assert intervals[0]["app_state"] == "automatic"
