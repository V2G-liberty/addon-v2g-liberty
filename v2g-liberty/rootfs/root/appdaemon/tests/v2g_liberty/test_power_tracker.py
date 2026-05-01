"""Unit tests for PowerTracker."""

from datetime import datetime, timedelta

import pytest

from apps.v2g_liberty.grid_connection.power_tracker import PowerTracker


@pytest.fixture
def tracker():
    return PowerTracker()


class TestUpdate:
    def test_first_update_no_accumulation(self, tracker):
        """First update sets baseline, no accumulation yet."""
        now = datetime(2026, 5, 1, 12, 0, 0)
        tracker.update(1000.0, now)

        assert tracker._current_power == 1000.0
        assert tracker._total_duration == 0.0
        assert tracker._power_x_duration == 0.0

    def test_second_update_accumulates(self, tracker):
        """Second update accumulates power × elapsed time."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=10)

        tracker.update(1000.0, t0)
        tracker.update(2000.0, t1)

        assert tracker._power_x_duration == 1000.0 * 10  # 10000
        assert tracker._total_duration == 10.0
        assert tracker._current_power == 2000.0

    def test_multiple_updates(self, tracker):
        """Multiple updates accumulate correctly."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=10)
        t2 = t1 + timedelta(seconds=20)

        tracker.update(100.0, t0)
        tracker.update(200.0, t1)  # 100 × 10 = 1000
        tracker.update(300.0, t2)  # 200 × 20 = 4000

        assert tracker._power_x_duration == 5000.0
        assert tracker._total_duration == 30.0


class TestConclude:
    def test_conclude_returns_weighted_average(self, tracker):
        """Conclude returns the weighted average power."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=30)
        t2 = t1 + timedelta(seconds=30)
        t_end = t2 + timedelta(seconds=240)

        tracker.update(100.0, t0)  # 100W for 30s
        tracker.update(500.0, t1)  # 500W for 30s
        tracker.update(200.0, t2)  # 200W for 240s
        avg = tracker.conclude(t_end)

        # (100×30 + 500×30 + 200×240) / 300 = (3000 + 15000 + 48000) / 300 = 220
        assert avg == 220.0

    def test_conclude_with_constant_power(self, tracker):
        """Constant power returns that power."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t_end = t0 + timedelta(minutes=5)

        tracker.update(1500.0, t0)
        avg = tracker.conclude(t_end)

        assert avg == 1500.0

    def test_conclude_resets(self, tracker):
        """After conclude, tracker is reset for next interval."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(minutes=5)
        t2 = t1 + timedelta(minutes=5)

        tracker.update(1000.0, t0)
        tracker.conclude(t1)

        # New interval
        tracker.update(2000.0, t1)
        avg = tracker.conclude(t2)

        assert avg == 2000.0

    def test_conclude_no_data_returns_none(self, tracker):
        """Conclude with no updates returns None."""
        now = datetime(2026, 5, 1, 12, 0, 0)
        avg = tracker.conclude(now)

        assert avg is None

    def test_conclude_single_update_returns_that_power(self, tracker):
        """Single update then conclude returns that power value."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t_end = t0 + timedelta(minutes=5)

        tracker.update(750.0, t0)
        avg = tracker.conclude(t_end)

        assert avg == 750.0

    def test_conclude_zero_power(self, tracker):
        """Zero power is a valid value."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t_end = t0 + timedelta(minutes=5)

        tracker.update(0.0, t0)
        avg = tracker.conclude(t_end)

        assert avg == 0.0

    def test_conclude_preserves_current_power_for_next_interval(self, tracker):
        """After conclude, the current power carries over as baseline."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t1 = t0 + timedelta(minutes=5)
        t2 = t1 + timedelta(minutes=5)

        tracker.update(1000.0, t0)
        tracker.update(2000.0, t0 + timedelta(minutes=2))
        tracker.conclude(t1)

        # No new update in next interval — power stays at 2000
        avg = tracker.conclude(t2)
        assert avg == 2000.0


class TestReset:
    def test_reset_clears_accumulation(self, tracker):
        """Reset clears accumulated values."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)

        tracker.update(1000.0, t0)
        tracker.update(2000.0, t0 + timedelta(seconds=10))
        tracker.reset(t0 + timedelta(seconds=10))

        assert tracker._power_x_duration == 0.0
        assert tracker._total_duration == 0.0

    def test_reset_keeps_last_update_time(self, tracker):
        """Reset sets last_update to the given time."""
        t0 = datetime(2026, 5, 1, 12, 0, 0)
        t_reset = t0 + timedelta(seconds=30)

        tracker.update(1000.0, t0)
        tracker.reset(t_reset)

        assert tracker._last_update == t_reset
