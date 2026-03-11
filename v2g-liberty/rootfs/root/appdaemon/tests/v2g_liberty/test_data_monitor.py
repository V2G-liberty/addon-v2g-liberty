"""Unit tests for DataMonitor DB write logic and app_state tracking (T12).

Tests cover:
- _derive_app_state: priority-based state derivation
- _update_app_state: duration tracking on state changes
- _conclude_app_state: winner selection and reset
- _pick_winning_state: priority + duration logic
- _write_interval_to_db: correct row written to DB
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.data_monitor import DataMonitor
from apps.v2g_liberty.event_bus import EventBus

# pylint: disable=C0116,W0621
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 22, 12, 0, 0, tzinfo=TEST_TZ)


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
def data_store():
    mock_store = MagicMock()
    mock_store.insert_interval = MagicMock()
    return mock_store


@pytest.fixture
def monitor(hass, event_bus, evse_client, data_store):
    """Create a DataMonitor with mocked dependencies, pre-initialised."""
    dm = DataMonitor(hass, event_bus)
    dm.evse_client_app = evse_client
    dm.data_store = data_store
    # Set up initial state as initialize() would
    dm._current_charger_state = None
    dm._current_charge_mode = "Automatic"
    dm._current_app_state = "automatic"
    dm._current_app_state_since = TEST_NOW
    dm._app_state_durations = {}
    dm.connected_car_soc = 60
    return dm


# =====================================================================
# _derive_app_state tests
# =====================================================================


class TestDeriveAppState:
    def test_error_state_highest_priority(self, monitor, evse_client):
        monitor._current_charger_state = 8  # In ERROR_STATES
        assert monitor._derive_app_state() == "error"

    def test_not_connected_state(self, monitor, evse_client):
        monitor._current_charger_state = 0  # In DISCONNECTED_STATES
        assert monitor._derive_app_state() == "not_connected"

    def test_max_boost_when_soc_below_minimum(self, monitor):
        monitor._current_charger_state = 5  # Connected, no error
        monitor.connected_car_soc = 10  # Below CAR_MIN_SOC_IN_PERCENT (20)
        assert monitor._derive_app_state() == "max_boost"

    def test_automatic_mode(self, monitor):
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Automatic"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "automatic"

    def test_stop_mode_maps_to_pause(self, monitor):
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Stop"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "pause"

    def test_max_boost_now_maps_to_charge(self, monitor):
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Max boost now"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "charge"

    def test_max_discharge_now_maps_to_discharge(self, monitor):
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Max discharge now"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "discharge"

    def test_unknown_charge_mode_returns_unknown(self, monitor):
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "SomeInvalidMode"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "unknown"

    def test_error_beats_not_connected(self, monitor):
        """Error has higher priority than not_connected."""
        monitor._current_charger_state = 8  # In both ERROR and tested
        monitor.evse_client_app.DISCONNECTED_STATES = [0, 1, 8]
        assert monitor._derive_app_state() == "error"

    def test_not_connected_beats_max_boost(self, monitor):
        """Not connected has higher priority than max_boost."""
        monitor._current_charger_state = 0  # DISCONNECTED
        monitor.connected_car_soc = 10  # Below minimum
        assert monitor._derive_app_state() == "not_connected"

    def test_max_boost_beats_charge_mode(self, monitor):
        """max_boost has higher priority than charge_mode-based states."""
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Stop"
        monitor.connected_car_soc = 10  # Below minimum
        assert monitor._derive_app_state() == "max_boost"

    def test_no_evse_client_skips_error_check(self, monitor):
        """When evse_client_app is None, error/disconnected checks are skipped."""
        monitor.evse_client_app = None
        monitor._current_charger_state = 8
        monitor._current_charge_mode = "Automatic"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "automatic"

    def test_charger_state_none_skips_error_check(self, monitor):
        """When charger_state is None, error/disconnected checks are skipped."""
        monitor._current_charger_state = None
        monitor._current_charge_mode = "Automatic"
        monitor.connected_car_soc = 60
        assert monitor._derive_app_state() == "automatic"

    def test_soc_none_skips_max_boost(self, monitor):
        """When SoC is None, max_boost is not triggered."""
        monitor._current_charger_state = 5
        monitor.connected_car_soc = None
        monitor._current_charge_mode = "Automatic"
        assert monitor._derive_app_state() == "automatic"

    def test_soc_at_minimum_is_not_max_boost(self, monitor):
        """SoC equal to minimum should NOT trigger max_boost (only strictly below)."""
        monitor._current_charger_state = 5
        monitor.connected_car_soc = 20  # Equals CAR_MIN_SOC_IN_PERCENT
        monitor._current_charge_mode = "Automatic"
        assert monitor._derive_app_state() == "automatic"


# =====================================================================
# _update_app_state tests
# =====================================================================


class TestUpdateAppState:
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_no_update_when_state_unchanged(self, mock_now, monitor):
        """If derived state equals current, no duration is recorded."""
        mock_now.return_value = TEST_NOW + timedelta(seconds=30)
        monitor._current_app_state = "automatic"
        monitor._current_charge_mode = "Automatic"
        monitor._current_charger_state = 5
        monitor.connected_car_soc = 60

        monitor._update_app_state()

        assert monitor._app_state_durations == {}
        assert monitor._current_app_state == "automatic"

    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_records_duration_on_state_change(self, mock_now, monitor):
        mock_now.return_value = TEST_NOW + timedelta(seconds=120)
        monitor._current_app_state = "automatic"
        monitor._current_app_state_since = TEST_NOW
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Stop"
        monitor.connected_car_soc = 60

        monitor._update_app_state()

        assert "automatic" in monitor._app_state_durations
        assert monitor._app_state_durations["automatic"] == 120_000  # ms
        assert monitor._current_app_state == "pause"

    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_accumulates_durations_for_same_state(self, mock_now, monitor):
        """If state returns to a previous state, durations accumulate."""
        # Start: automatic for 60s
        mock_now.return_value = TEST_NOW + timedelta(seconds=60)
        monitor._current_app_state = "automatic"
        monitor._current_app_state_since = TEST_NOW
        monitor._current_charger_state = 5
        monitor._current_charge_mode = "Stop"
        monitor.connected_car_soc = 60
        monitor._update_app_state()

        # Now: pause for 30s, then back to automatic
        mock_now.return_value = TEST_NOW + timedelta(seconds=90)
        monitor._current_charge_mode = "Automatic"
        monitor._update_app_state()

        # Then: automatic for 60s more, change to stop again
        mock_now.return_value = TEST_NOW + timedelta(seconds=150)
        monitor._current_charge_mode = "Stop"
        monitor._update_app_state()

        assert monitor._app_state_durations["automatic"] == 120_000  # 60s + 60s
        assert monitor._app_state_durations["pause"] == 30_000


# =====================================================================
# _pick_winning_state tests
# =====================================================================


class TestPickWinningState:
    def test_empty_durations_returns_unknown(self, monitor):
        monitor._app_state_durations = {}
        assert monitor._pick_winning_state() == "unknown"

    def test_single_state_wins(self, monitor):
        monitor._app_state_durations = {"automatic": 300_000}
        assert monitor._pick_winning_state() == "automatic"

    def test_higher_priority_wins_even_with_shorter_duration(self, monitor):
        """error (prio 1) beats automatic (prio 4) even if shorter."""
        monitor._app_state_durations = {
            "automatic": 250_000,
            "error": 50_000,
        }
        assert monitor._pick_winning_state() == "error"

    def test_same_priority_longest_duration_wins(self, monitor):
        """charge and pause both have prio 4: longest duration wins."""
        monitor._app_state_durations = {
            "charge": 100_000,
            "pause": 200_000,
        }
        assert monitor._pick_winning_state() == "pause"

    def test_not_connected_beats_max_boost(self, monitor):
        monitor._app_state_durations = {
            "not_connected": 10_000,
            "max_boost": 290_000,
        }
        assert monitor._pick_winning_state() == "not_connected"

    def test_equal_priority_equal_duration_picks_one(self, monitor):
        """When states share priority and duration, one should still be picked."""
        monitor._app_state_durations = {
            "charge": 150_000,
            "discharge": 150_000,
        }
        result = monitor._pick_winning_state()
        assert result in ("charge", "discharge")


# =====================================================================
# _conclude_app_state tests
# =====================================================================


class TestConcludeAppState:
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_conclude_returns_winner_and_resets(self, mock_now, monitor):
        mock_now.return_value = TEST_NOW + timedelta(minutes=5)
        monitor._current_app_state = "automatic"
        monitor._current_app_state_since = TEST_NOW
        monitor._app_state_durations = {}

        result = monitor._conclude_app_state()

        assert result == "automatic"
        assert monitor._app_state_durations == {}
        assert monitor._current_app_state_since == TEST_NOW + timedelta(minutes=5)

    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_conclude_includes_current_state_duration(self, mock_now, monitor):
        """The current running state's duration is included before picking winner."""
        mock_now.return_value = TEST_NOW + timedelta(minutes=5)
        monitor._current_app_state = "pause"
        monitor._current_app_state_since = TEST_NOW
        # error ran for 60s earlier in this interval
        monitor._app_state_durations = {"error": 60_000}

        result = monitor._conclude_app_state()

        # error (prio 1) beats pause (prio 4)
        assert result == "error"

    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    def test_conclude_when_only_current_state(self, mock_now, monitor):
        """If no state changes occurred, current state wins the full interval."""
        mock_now.return_value = TEST_NOW + timedelta(minutes=5)
        monitor._current_app_state = "not_connected"
        monitor._current_app_state_since = TEST_NOW
        monitor._app_state_durations = {}

        result = monitor._conclude_app_state()

        assert result == "not_connected"


# =====================================================================
# _write_interval_to_db tests
# =====================================================================


class TestWriteIntervalToDb:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_writes_correct_row(self, mock_now, monitor, data_store):
        """Interval data should be written to interval_log."""
        # Interval ends at 12:05, so start = 12:00
        mock_now.return_value = datetime(2026, 2, 22, 12, 5, 0, tzinfo=TEST_TZ)

        await monitor._write_interval_to_db(
            energy_kwh=0.291667,
            availability_pct=95.0,
            soc=75,
            app_state="automatic",
        )

        data_store.insert_interval.assert_called_once()
        call_kwargs = data_store.insert_interval.call_args[1]
        assert call_kwargs["timestamp"] == "2026-02-22T11:00:00+00:00"
        assert call_kwargs["energy_kwh"] == 0.291667
        assert call_kwargs["app_state"] == "automatic"
        assert call_kwargs["soc_pct"] == 75.0
        assert call_kwargs["availability_pct"] == 95.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_writes_null_soc_when_disconnected(
        self, mock_now, monitor, data_store
    ):
        """When car is disconnected, soc_pct should be NULL."""
        mock_now.return_value = datetime(2026, 2, 22, 12, 5, 0, tzinfo=TEST_TZ)

        await monitor._write_interval_to_db(
            energy_kwh=0.0,
            availability_pct=0.0,
            soc=None,
            app_state="not_connected",
        )

        call_kwargs = data_store.insert_interval.call_args[1]
        assert call_kwargs["soc_pct"] is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_no_write_when_data_store_is_none(self, mock_now, monitor):
        """When data_store is not set, nothing should happen."""
        mock_now.return_value = datetime(2026, 2, 22, 12, 5, 0, tzinfo=TEST_TZ)
        monitor.data_store = None

        # Should not raise
        await monitor._write_interval_to_db(
            energy_kwh=0.083,
            availability_pct=100.0,
            soc=60,
            app_state="automatic",
        )

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_handles_db_exception_gracefully(self, mock_now, monitor, data_store):
        """When DB write fails, it should log a warning, not crash."""
        mock_now.return_value = datetime(2026, 2, 22, 12, 5, 0, tzinfo=TEST_TZ)
        data_store.insert_interval.side_effect = Exception("DB locked")

        # Should not raise
        await monitor._write_interval_to_db(
            energy_kwh=0.083,
            availability_pct=100.0,
            soc=60,
            app_state="automatic",
        )

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_timestamp_calculation_correct(self, mock_now, monitor, data_store):
        """Timestamp should be the start of the 5-min interval, not the end."""
        # If now is 12:10:00, interval start should be 12:05:00
        mock_now.return_value = datetime(2026, 2, 22, 12, 10, 0, tzinfo=TEST_TZ)

        await monitor._write_interval_to_db(
            energy_kwh=0.0,
            availability_pct=100.0,
            soc=50,
            app_state="automatic",
        )

        call_kwargs = data_store.insert_interval.call_args[1]
        assert call_kwargs["timestamp"] == "2026-02-22T11:05:00+00:00"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_writes_negative_power_for_discharge(
        self, mock_now, monitor, data_store
    ):
        """Negative power (discharge) should be written as-is."""
        mock_now.return_value = datetime(2026, 2, 22, 12, 5, 0, tzinfo=TEST_TZ)

        await monitor._write_interval_to_db(
            energy_kwh=-0.208333,
            availability_pct=100.0,
            soc=80,
            app_state="discharge",
        )

        call_kwargs = data_store.insert_interval.call_args[1]
        assert call_kwargs["energy_kwh"] == -0.208333


# =====================================================================
# _handle_calendar_change tests (T14)
# =====================================================================


class TestHandleCalendarChange:
    """Tests for reservation logging via calendar_change events."""

    def _make_event(
        self,
        start=None,
        end=None,
        target_soc_percent=80,
        dismissed=False,
    ):
        """Create a v2g_event dict matching the reservations_client format."""
        if start is None:
            start = datetime(2026, 2, 23, 8, 0, 0, tzinfo=TEST_TZ)
        if end is None:
            end = datetime(2026, 2, 23, 17, 0, 0, tzinfo=TEST_TZ)
        return {
            "start": start,
            "end": end,
            "summary": "Work",
            "description": "SoC: 80",
            "target_soc_percent": target_soc_percent,
            "hash_id": "abc123",
            "dismissed": dismissed,
        }

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_writes_reservation_to_db(self, mock_now, monitor, data_store):
        event = self._make_event()
        await monitor._handle_calendar_change(v2g_events=[event])

        data_store.insert_reservation.assert_called_once()
        call_kwargs = data_store.insert_reservation.call_args[1]
        assert call_kwargs["timestamp"].endswith("+00:00")  # UTC
        assert call_kwargs["start_timestamp"] == "2026-02-23T07:00:00+00:00"
        assert call_kwargs["end_timestamp"] == "2026-02-23T16:00:00+00:00"
        assert call_kwargs["target_soc_pct"] == 80.0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_skips_uninitiated_events(self, mock_now, monitor, data_store):
        await monitor._handle_calendar_change(v2g_events=["un-initiated"])

        data_store.insert_reservation.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_skips_dismissed_events(self, mock_now, monitor, data_store):
        event = self._make_event(dismissed=True)
        await monitor._handle_calendar_change(v2g_events=[event])

        data_store.insert_reservation.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_no_write_when_data_store_is_none(self, mock_now, monitor):
        monitor.data_store = None
        event = self._make_event()

        # Should not raise
        await monitor._handle_calendar_change(v2g_events=[event])

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_no_write_when_v2g_events_is_none(
        self, mock_now, monitor, data_store
    ):
        await monitor._handle_calendar_change(v2g_events=None)

        data_store.insert_reservation.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_rounds_timestamps_to_5_minutes(self, mock_now, monitor, data_store):
        """Timestamps are rounded to nearest 5-min boundary."""
        event = self._make_event(
            start=datetime(2026, 2, 23, 8, 3, 0, tzinfo=TEST_TZ),
            end=datetime(2026, 2, 23, 17, 7, 0, tzinfo=TEST_TZ),
        )
        await monitor._handle_calendar_change(v2g_events=[event])

        call_kwargs = data_store.insert_reservation.call_args[1]
        assert call_kwargs["start_timestamp"] == "2026-02-23T07:05:00+00:00"
        assert call_kwargs["end_timestamp"] == "2026-02-23T16:05:00+00:00"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_rounds_down_when_below_half(self, mock_now, monitor, data_store):
        """Timestamps round down when less than 2.5 min past a boundary."""
        event = self._make_event(
            start=datetime(2026, 2, 23, 8, 2, 0, tzinfo=TEST_TZ),
            end=datetime(2026, 2, 23, 17, 1, 0, tzinfo=TEST_TZ),
        )
        await monitor._handle_calendar_change(v2g_events=[event])

        call_kwargs = data_store.insert_reservation.call_args[1]
        assert call_kwargs["start_timestamp"] == "2026-02-23T07:00:00+00:00"
        assert call_kwargs["end_timestamp"] == "2026-02-23T16:00:00+00:00"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_handles_none_target_soc(self, mock_now, monitor, data_store):
        event = self._make_event(target_soc_percent=None)
        await monitor._handle_calendar_change(v2g_events=[event])

        call_kwargs = data_store.insert_reservation.call_args[1]
        assert call_kwargs["target_soc_pct"] is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_processes_multiple_events(self, mock_now, monitor, data_store):
        events = [
            self._make_event(
                start=datetime(2026, 2, 23, 8, 0, 0, tzinfo=TEST_TZ),
                end=datetime(2026, 2, 23, 12, 0, 0, tzinfo=TEST_TZ),
                target_soc_percent=80,
            ),
            self._make_event(
                start=datetime(2026, 2, 24, 9, 0, 0, tzinfo=TEST_TZ),
                end=datetime(2026, 2, 24, 18, 0, 0, tzinfo=TEST_TZ),
                target_soc_percent=90,
            ),
        ]
        await monitor._handle_calendar_change(v2g_events=events)

        assert data_store.insert_reservation.call_count == 2

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_skips_dismissed_keeps_active(self, mock_now, monitor, data_store):
        """In a mixed list, dismissed events are skipped, active ones are written."""
        events = [
            self._make_event(dismissed=True),
            self._make_event(
                start=datetime(2026, 2, 24, 9, 0, 0, tzinfo=TEST_TZ),
                end=datetime(2026, 2, 24, 18, 0, 0, tzinfo=TEST_TZ),
                dismissed=False,
            ),
        ]
        await monitor._handle_calendar_change(v2g_events=events)

        assert data_store.insert_reservation.call_count == 1

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now", return_value=TEST_NOW)
    async def test_handles_db_exception_gracefully(
        self, mock_now, monitor, data_store, hass
    ):
        """DB exception should be caught and logged as WARNING."""
        data_store.insert_reservation.side_effect = Exception("DB locked")
        event = self._make_event()

        # Should not raise
        await monitor._handle_calendar_change(v2g_events=[event])

        # Check that a warning was logged (log_wrapper passes msg= and level= as kwargs)
        warning_logged = False
        for call in hass.log.call_args_list:
            kwargs = call.kwargs or {}
            msg = kwargs.get("msg", "")
            if (
                "Failed to write reservation" in msg
                and kwargs.get("level") == "WARNING"
            ):
                warning_logged = True
                break
        assert warning_logged


# =====================================================================
# _emit_today_totals tests (T33)
# =====================================================================


class TestEmitTodayTotals:
    """Tests for today's energy totals event emission."""

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_emits_today_totals_with_data(
        self, mock_now, monitor, data_store, event_bus
    ):
        """When data exists for today, emit the aggregated values."""
        mock_now.return_value = datetime(2026, 2, 22, 14, 0, 0, tzinfo=TEST_TZ)
        data_store.get_aggregated_data = MagicMock(
            return_value=[
                {
                    "period_start": "2026-02-22",
                    "availability_pct": 95.0,
                    "charge_kwh": 5.5,
                    "charge_cost": 1.23,
                    "discharge_kwh": 3.2,
                    "discharge_revenue": 0.89,
                    "net_kwh": 2.3,
                    "net_cost": 0.34,
                    "co2_kg": -5.0,
                }
            ]
        )

        await monitor._emit_today_totals()

        event_bus.emit_event.assert_called_with(
            "today_energy_update",
            charge_kwh=5.5,
            charge_cost=1.23,
            discharge_kwh=3.2,
            discharge_revenue=0.89,
        )

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_emits_zeros_when_no_data(
        self, mock_now, monitor, data_store, event_bus
    ):
        """When no data exists for today (e.g. just after midnight), emit zeros."""
        mock_now.return_value = datetime(2026, 2, 22, 0, 5, 0, tzinfo=TEST_TZ)
        data_store.get_aggregated_data = MagicMock(return_value=[])

        await monitor._emit_today_totals()

        event_bus.emit_event.assert_called_with(
            "today_energy_update",
            charge_kwh=0.0,
            charge_cost=0.0,
            discharge_kwh=0.0,
            discharge_revenue=0.0,
        )

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_queries_correct_date_range(
        self, mock_now, monitor, data_store, event_bus
    ):
        """Should query from today 00:00 to tomorrow 00:00."""
        mock_now.return_value = datetime(2026, 2, 22, 14, 30, 0, tzinfo=TEST_TZ)
        data_store.get_aggregated_data = MagicMock(return_value=[])

        await monitor._emit_today_totals()

        call_kwargs = data_store.get_aggregated_data.call_args
        start = call_kwargs[1]["start"]
        end = call_kwargs[1]["end"]
        granularity = call_kwargs[1]["granularity"]

        assert start == "2026-02-21T23:00:00+00:00"
        assert end == "2026-02-22T23:00:00+00:00"
        assert granularity == "days"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_no_emit_when_data_store_is_none(self, mock_now, monitor, event_bus):
        """When data_store is not set, no event should be emitted."""
        mock_now.return_value = datetime(2026, 2, 22, 14, 0, 0, tzinfo=TEST_TZ)
        monitor.data_store = None

        await monitor._emit_today_totals()

        event_bus.emit_event.assert_not_called()

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_monitor.get_local_now")
    async def test_handles_db_exception_gracefully(
        self, mock_now, monitor, data_store, event_bus
    ):
        """DB exception should be caught gracefully, no event emitted."""
        mock_now.return_value = datetime(2026, 2, 22, 14, 0, 0, tzinfo=TEST_TZ)
        data_store.get_aggregated_data = MagicMock(side_effect=Exception("DB locked"))

        # Should not raise
        await monitor._emit_today_totals()

        event_bus.emit_event.assert_not_called()
