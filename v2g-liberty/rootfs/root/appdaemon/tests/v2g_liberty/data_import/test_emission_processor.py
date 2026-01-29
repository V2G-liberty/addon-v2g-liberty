"""Tests for EmissionProcessor class."""

import pytest
from datetime import datetime, timedelta
import pytz
from apps.v2g_liberty.data_import.processors.emission_processor import (
    EmissionProcessor,
)


@pytest.fixture
def emission_processor():
    """Create an EmissionProcessor instance with 15-minute resolution."""
    return EmissionProcessor(event_resolution_minutes=15)


class TestEmissionProcessorInitialisation:
    """Test EmissionProcessor initialisation."""

    def test_initialisation_with_resolution(self):
        """Test EmissionProcessor initialisation with specified resolution."""
        processor = EmissionProcessor(event_resolution_minutes=15)
        assert processor.event_resolution_minutes == 15

    def test_initialisation_different_resolution(self):
        """Test EmissionProcessor initialisation with different resolution."""
        processor = EmissionProcessor(event_resolution_minutes=60)
        assert processor.event_resolution_minutes == 60


class TestProcessEmissions:
    """Test process_emissions method."""

    def test_process_emissions_full_data(self, emission_processor):
        """Test emission processing with complete data (no Nones)."""
        raw_emissions = [100.0, 150.0, 200.0, 250.0]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 30, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        # All values should be in cache
        assert len(cache) == 4
        assert cache[start] == 100.0
        assert cache[start + timedelta(minutes=15)] == 150.0
        assert cache[start + timedelta(minutes=30)] == 200.0
        assert cache[start + timedelta(minutes=45)] == 250.0

        # All values should be in chart (all are changes and within 5-hour window)
        assert len(chart_points) == 4

        # Values should be scaled (divided by 10 and rounded)
        assert chart_points[0]["emission"] == 10  # 100 / 10 = 10
        assert chart_points[1]["emission"] == 15  # 150 / 10 = 15
        assert chart_points[2]["emission"] == 20  # 200 / 10 = 20
        assert chart_points[3]["emission"] == 25  # 250 / 10 = 25

        # Latest datetime should be the last emission
        assert latest_dt == start + timedelta(minutes=45)

    def test_process_emissions_with_nones(self, emission_processor):
        """Test emission processing filters out None values correctly."""
        raw_emissions = [100.0, None, 200.0, None, None, 300.0]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 30, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        # Only non-None values should be in cache
        assert len(cache) == 3
        assert start in cache
        assert start + timedelta(minutes=30) in cache
        assert start + timedelta(minutes=75) in cache

        # Check that None values are skipped
        assert start + timedelta(minutes=15) not in cache
        assert start + timedelta(minutes=45) not in cache
        assert start + timedelta(minutes=60) not in cache

        # Chart should have 3 points
        assert len(chart_points) == 3

        # Latest datetime should be the last non-None emission
        assert latest_dt == start + timedelta(minutes=75)

    def test_process_emissions_filters_old_data(self, emission_processor):
        """Test that old data (>5 hours) is cached but not added to chart."""
        raw_emissions = [100.0, 150.0, 200.0, 250.0]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 6, 0, 0, tzinfo=pytz.UTC)  # 6 hours after start

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now, history_hours=5
        )

        # All values should still be in cache (for calculations)
        assert len(cache) == 4

        # But chart should be empty (all data is older than 5 hours)
        assert len(chart_points) == 0

        # Latest datetime should still be tracked
        assert latest_dt == start + timedelta(minutes=45)

    def test_process_emissions_only_adds_changes(self, emission_processor):
        """Test that only changed values are added to chart (optimisation)."""
        raw_emissions = [
            100.0,
            100.0,
            100.0,
            150.0,
            150.0,
            200.0,
        ]  # Repeated values
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 30, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        # All values should be in cache
        assert len(cache) == 6

        # But chart should only have 3 points (one for each unique value)
        assert len(chart_points) == 3
        assert chart_points[0]["emission"] == 10  # First 100
        assert chart_points[1]["emission"] == 15  # First 150
        assert chart_points[2]["emission"] == 20  # First 200

    def test_process_emissions_empty_list(self, emission_processor):
        """Test processing an empty emission list."""
        raw_emissions = []
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        assert len(cache) == 0
        assert len(chart_points) == 0
        assert latest_dt is None

    def test_process_emissions_all_none(self, emission_processor):
        """Test processing a list with only None values."""
        raw_emissions = [None, None, None]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        assert len(cache) == 0
        assert len(chart_points) == 0
        assert latest_dt is None

    def test_process_emissions_custom_history_window(self, emission_processor):
        """Test emission processing with custom history window."""
        raw_emissions = [100.0, 150.0, 200.0, 250.0]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 3, 0, 0, tzinfo=pytz.UTC)  # 3 hours after start

        # With 2-hour history window, all data should be excluded from chart
        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now, history_hours=2
        )

        assert len(cache) == 4  # All in cache
        assert len(chart_points) == 0  # None in chart (all too old)

        # With 4-hour history window, all data should be included in chart
        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now, history_hours=4
        )

        assert len(cache) == 4  # All in cache
        assert len(chart_points) == 4  # All in chart

    def test_process_emissions_scaling(self, emission_processor):
        """Test that emission values are correctly scaled for display."""
        raw_emissions = [105.5, 154.9, 155.0, 155.1]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 30, 0, tzinfo=pytz.UTC)

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now
        )

        # Check rounding: divide by 10 and round to nearest integer
        assert chart_points[0]["emission"] == 11  # 105.5 / 10 = 10.55 -> 11
        assert chart_points[1]["emission"] == 15  # 154.9 / 10 = 15.49 -> 15
        # Third value (155.0) rounds to same as second (15), so optimisation skips it
        assert chart_points[2]["emission"] == 16  # 155.1 / 10 = 15.51 -> 16

        # But cache should have original unscaled values
        assert cache[start] == 105.5
        assert cache[start + timedelta(minutes=15)] == 154.9

    def test_process_emissions_partial_history_window(self, emission_processor):
        """Test that only recent emissions appear in chart."""
        raw_emissions = [100.0, 150.0, 200.0, 250.0]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 20, 0, tzinfo=pytz.UTC)  # 20 minutes after start

        cache, chart_points, latest_dt = emission_processor.process_emissions(
            raw_emissions, start, now, history_hours=5
        )

        # All should be in cache
        assert len(cache) == 4

        # All should be in chart (all within 5-hour window from 00:20)
        # show_in_graph_after = 00:20 - 5h = 19:20 previous day
        # All emissions (00:00, 00:15, 00:30, 00:45) are after that
        assert len(chart_points) == 4
