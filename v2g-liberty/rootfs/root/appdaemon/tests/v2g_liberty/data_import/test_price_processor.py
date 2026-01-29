"""Tests for PriceProcessor class."""

import pytest
from datetime import datetime, timedelta
import pytz
from apps.v2g_liberty.data_import.processors.price_processor import PriceProcessor


@pytest.fixture
def price_processor():
    """Create a PriceProcessor instance with 15-minute resolution."""
    return PriceProcessor(price_resolution_minutes=15)


class TestPriceProcessorInitialisation:
    """Test PriceProcessor initialisation."""

    def test_initialisation_with_resolution(self):
        """Test PriceProcessor initialisation with specified resolution."""
        processor = PriceProcessor(price_resolution_minutes=30)
        assert processor.price_resolution_minutes == 30

    def test_initialisation_default_resolution(self):
        """Test PriceProcessor initialisation with 15-minute resolution."""
        processor = PriceProcessor(price_resolution_minutes=15)
        assert processor.price_resolution_minutes == 15


class TestProcessPrices:
    """Test process_prices method."""

    def test_process_prices_with_vat_and_markup(self, price_processor):
        """Test price processing applies VAT and markup correctly."""
        raw_prices = [0.10, 0.20, 0.30]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.21  # 21% VAT
        markup_per_kwh = 0.05

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Should have 3 data points + 1 trailing point = 4 total
        assert len(price_points) == 4
        assert none_count == 0
        assert first_negative is None

        # Check first price: (0.10 + 0.05) * 1.21 = 0.1815 -> rounded to 0.18
        assert price_points[0]["price"] == 0.18
        assert price_points[0]["time"] == start.isoformat()

        # Check second price: (0.20 + 0.05) * 1.21 = 0.3025 -> rounded to 0.30
        assert price_points[1]["price"] == 0.30

        # Check third price: (0.30 + 0.05) * 1.21 = 0.4235 -> rounded to 0.42
        assert price_points[2]["price"] == 0.42

        # Check trailing point has same price as last point
        assert price_points[3]["price"] == 0.42
        assert (
            price_points[3]["time"]
            == (start + timedelta(minutes=45)).isoformat()  # 3 * 15 minutes
        )

    def test_process_prices_with_none_values(self, price_processor):
        """Test price processing filters out None values correctly."""
        raw_prices = [0.10, None, 0.20, None, None, 0.30]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Should have 3 data points (skipping 3 Nones) + 1 trailing point = 4 total
        assert len(price_points) == 4
        assert none_count == 3
        assert first_negative is None

        # Check that None values are skipped but indices still advance
        assert price_points[0]["time"] == start.isoformat()  # Index 0
        assert (
            price_points[1]["time"] == (start + timedelta(minutes=30)).isoformat()
        )  # Index 2 (skipped 1)
        assert (
            price_points[2]["time"] == (start + timedelta(minutes=75)).isoformat()
        )  # Index 5 (skipped 3,4)

    def test_process_prices_detects_negative_future_price(self, price_processor):
        """Test detection of first negative price in the future."""
        raw_prices = [0.10, 0.20, -0.05, -0.10, 0.15]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 25, 0, tzinfo=pytz.UTC)  # After first two prices
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Should detect the first negative price (at index 2)
        assert first_negative is not None
        assert first_negative["price"] == -0.05
        assert first_negative["time"] == start + timedelta(minutes=30)  # Index 2

        # Check that all prices are still included in price_points
        assert len(price_points) == 6  # 5 data points + 1 trailing

    def test_process_prices_ignores_negative_past_price(self, price_processor):
        """Test that negative prices in the past are ignored."""
        raw_prices = [-0.10, -0.05, 0.10, 0.20]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 35, 0, tzinfo=pytz.UTC)  # After first two prices
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Should NOT detect negative prices in the past
        assert first_negative is None

        # Check that all prices are still included in price_points
        assert len(price_points) == 5  # 4 data points + 1 trailing
        assert price_points[0]["price"] == -0.10
        assert price_points[1]["price"] == -0.05

    def test_process_prices_adds_trailing_point(self, price_processor):
        """Test that a trailing point is added to extend the step-line chart."""
        raw_prices = [0.10, 0.20]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Should have 2 data points + 1 trailing point
        assert len(price_points) == 3

        # Last two points should have same price
        assert price_points[-1]["price"] == price_points[-2]["price"]

        # Trailing point should be one resolution period after the last data point
        last_data_time = datetime.fromisoformat(price_points[-2]["time"])
        trailing_time = datetime.fromisoformat(price_points[-1]["time"])
        assert trailing_time == last_data_time + timedelta(minutes=15)

    def test_process_prices_empty_list(self, price_processor):
        """Test processing an empty price list."""
        raw_prices = []
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        assert len(price_points) == 0
        assert first_negative is None
        assert none_count == 0

    def test_process_prices_all_none(self, price_processor):
        """Test processing a list with only None values."""
        raw_prices = [None, None, None]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = price_processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        assert len(price_points) == 0
        assert first_negative is None
        assert none_count == 3

    def test_process_prices_with_30_minute_resolution(self):
        """Test price processing with 30-minute resolution."""
        processor = PriceProcessor(price_resolution_minutes=30)
        raw_prices = [0.10, 0.20]
        start = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        now = datetime(2026, 1, 28, 0, 0, 0, tzinfo=pytz.UTC)
        vat_factor = 1.0
        markup_per_kwh = 0.0

        price_points, first_negative, none_count = processor.process_prices(
            raw_prices, start, now, vat_factor, markup_per_kwh
        )

        # Check that time increments are 30 minutes
        assert price_points[0]["time"] == start.isoformat()
        assert price_points[1]["time"] == (start + timedelta(minutes=30)).isoformat()
        assert (
            price_points[2]["time"] == (start + timedelta(minutes=60)).isoformat()
        )  # Trailing point
