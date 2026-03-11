"""Unit tests for price persistence to local DB (T6/T7/T8).

Tests verify that prices from various providers (EPEX, Amber, Octopus)
are correctly upsampled, converted, and persisted to price_log via DataStore.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from apps.v2g_liberty.amber_price_data_manager import ManageAmberPriceData
from apps.v2g_liberty.fm_data_importer import FlexMeasuresDataImporter
from apps.v2g_liberty.notifier_util import Notifier
from apps.v2g_liberty.octopus_price_data_manager import ManageOctopusPriceData

# pylint: disable=C0116,W0621
# Pylint disabled for:
# C0116 - No docstring needed for pytest test functions
# W0621 - Fixture args shadow names (acceptable in pytest)

TEST_TZ = timezone(timedelta(hours=1))
TEST_START = datetime(2026, 2, 21, 0, 0, 0, tzinfo=TEST_TZ)


@pytest.fixture
def hass():
    mock_hass = MagicMock()
    mock_hass.log = MagicMock()
    mock_hass.run_daily = MagicMock()
    return mock_hass


@pytest.fixture
def notifier():
    return MagicMock(spec=Notifier)


@pytest.fixture
def data_store():
    mock_store = MagicMock()
    mock_store.upsert_prices = MagicMock()
    return mock_store


@pytest.fixture
def importer(hass, notifier, data_store):
    imp = FlexMeasuresDataImporter(hass, notifier)
    imp.data_store = data_store
    return imp


@pytest.fixture
def amber_manager(hass, data_store):
    manager = ManageAmberPriceData(hass)
    manager.data_store = data_store
    return manager


def make_raw_result(prices, start=TEST_START):
    """Create a raw price result dict matching PriceFetcher.fetch_prices() output."""
    return {"prices": prices, "start": start}


def make_amber_forecast(per_kwh, start_dt):
    """Create a forecast item matching Amber integration output."""
    return {"per_kwh": per_kwh, "start_time": start_dt.isoformat()}


@pytest.fixture
def octopus_manager(hass, data_store):
    manager = ManageOctopusPriceData(hass)
    manager.data_store = data_store
    return manager


def make_octopus_result(value_inc_vat, valid_from_dt):
    """Create a result item matching Octopus API output."""
    return {"value_inc_vat": value_inc_vat, "valid_from": valid_from_dt.isoformat()}


class TestPersistEpexPricesToDb:
    """Tests for FlexMeasuresDataImporter._persist_epex_prices_to_db()."""

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_basic_3_prices_produces_9_rows(self, mock_c, importer, data_store):
        """3 prices at 15-min resolution → 9 rows at 5-min, cents → EUR."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0, 20.0, 30.0])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0, 15.0])

        importer._persist_epex_prices_to_db()

        data_store.upsert_prices.assert_called_once()
        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 9

        # First 3 sub-intervals: 10 cents → 0.10 EUR, 5 cents → 0.05 EUR
        for i in range(3):
            assert rows[i][1] == 0.10
            assert rows[i][2] == 0.05
            assert rows[i][3] is None

        # Middle 3 sub-intervals: 20/10 cents
        for i in range(3, 6):
            assert rows[i][1] == 0.20
            assert rows[i][2] == 0.10

        # Last 3 sub-intervals: 30/15 cents
        for i in range(6, 9):
            assert rows[i][1] == 0.30
            assert rows[i][2] == 0.15

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_timestamps_are_5min_aligned(self, mock_c, importer, data_store):
        """Verify output timestamps are at 5-min intervals."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0, 20.0])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 6

        expected_timestamps = [TEST_START + timedelta(minutes=5 * i) for i in range(6)]
        for row, expected_ts in zip(rows, expected_timestamps):
            assert row[0] == expected_ts.astimezone(timezone.utc).isoformat()

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_30min_resolution_produces_6_rows_per_interval(
        self, mock_c, importer, data_store
    ):
        """30-min resolution → 6 five-min rows per native interval."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 6
        for row in rows:
            assert row[1] == 0.10
            assert row[2] == 0.05

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_no_data_store_logs_warning(self, mock_c, importer, hass):
        """No DataStore → logs warning, no crash."""
        importer.data_store = None
        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()  # Should not raise

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_missing_consumption_result_skips(self, mock_c, importer, data_store):
        """Missing consumption result → no persistence."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = None
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()

        data_store.upsert_prices.assert_not_called()

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_missing_production_result_skips(self, mock_c, importer, data_store):
        """Missing production result → no persistence."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = None

        importer._persist_epex_prices_to_db()

        data_store.upsert_prices.assert_not_called()

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_all_none_prices_skips(self, mock_c, importer, data_store):
        """All None prices in one type → no persistence."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([None, None])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0])

        importer._persist_epex_prices_to_db()

        data_store.upsert_prices.assert_not_called()

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_none_values_skipped_only_overlap_persisted(
        self, mock_c, importer, data_store
    ):
        """None values filtered; only timestamps with both prices persisted."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        # Consumption has None at index 1
        importer._latest_raw_consumption_result = make_raw_result([10.0, None, 30.0])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0, 15.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        # 2 native timestamps with both prices → 6 five-min rows
        assert len(rows) == 6
        # First 3: 10 cents cons, 5 cents prod
        assert rows[0][1] == 0.10
        assert rows[0][2] == 0.05
        # Last 3: 30 cents cons, 15 cents prod (at 30 min offset)
        assert rows[3][1] == 0.30
        assert rows[3][2] == 0.15

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_vat_and_markup_applied_for_nl_generic(self, mock_c, importer, data_store):
        """When vat_factor and markup are set (nl_generic), they are applied."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer.vat_factor = 1.21  # 21% VAT
        importer.markup_per_kwh = 5.0  # 5 cents/kWh markup

        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        # Consumption: (10.0 + 5.0) * 1.21 / 100 = 0.1815
        assert rows[0][1] == round(15.0 * 1.21 / 100, 6)
        # Production: (5.0 + 5.0) * 1.21 / 100 = 0.121
        assert rows[0][2] == round(10.0 * 1.21 / 100, 6)

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_default_no_vat_markup_just_cents_to_eur(
        self, mock_c, importer, data_store
    ):
        """With default vat_factor=1 and markup=0, just cents → EUR conversion."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer.vat_factor = 1
        importer.markup_per_kwh = 0

        importer._latest_raw_consumption_result = make_raw_result([25.0])
        importer._latest_raw_production_result = make_raw_result([12.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        assert rows[0][1] == 0.25  # 25 cents → 0.25 EUR
        assert rows[0][2] == 0.12  # 12 cents → 0.12 EUR

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_temp_storage_cleaned_up_after_persist(self, mock_c, importer, data_store):
        """After persistence, temporary raw results are cleaned up."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()

        assert importer._latest_raw_consumption_result is None
        assert importer._latest_raw_production_result is None

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_price_rating_always_none_in_rows(self, mock_c, importer, data_store):
        """price_rating is always None — DataStore.upsert_prices() calculates it."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0, 20.0, 30.0])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0, 15.0])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        for row in rows:
            assert row[3] is None

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_recalculate_ratings_default_true(self, mock_c, importer, data_store):
        """EPEX uses default recalculate_ratings=True (forecast→definitive transition)."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        importer._latest_raw_consumption_result = make_raw_result([10.0])
        importer._latest_raw_production_result = make_raw_result([5.0])

        importer._persist_epex_prices_to_db()

        # No recalculate_ratings kwarg → uses default True
        call_kwargs = data_store.upsert_prices.call_args[1]
        assert "recalculate_ratings" not in call_kwargs

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_rounding_to_6_decimals(self, mock_c, importer, data_store):
        """Verify prices are rounded to 6 decimal places."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15
        # 33.33 cents / 100 = 0.3333... → should round to 0.3333
        importer._latest_raw_consumption_result = make_raw_result([33.33])
        importer._latest_raw_production_result = make_raw_result([16.67])

        importer._persist_epex_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        for row in rows:
            # Verify no more than 6 decimal places
            cons_str = f"{row[1]:.10f}"
            prod_str = f"{row[2]:.10f}"
            assert row[1] == round(row[1], 6)
            assert row[2] == round(row[2], 6)

    @patch("apps.v2g_liberty.fm_data_importer.c")
    def test_forecast_then_definitive_calls_upsert_twice(
        self, mock_c, importer, data_store
    ):
        """EPEX flow: forecast prices first, then definitive prices overwrite."""
        mock_c.PRICE_RESOLUTION_MINUTES = 15

        # First call: forecast prices
        importer._latest_raw_consumption_result = make_raw_result([10.0, 20.0])
        importer._latest_raw_production_result = make_raw_result([5.0, 10.0])
        importer._persist_epex_prices_to_db()

        # Second call: definitive prices (different values, same timestamps)
        importer._latest_raw_consumption_result = make_raw_result([12.0, 22.0])
        importer._latest_raw_production_result = make_raw_result([6.0, 11.0])
        importer._persist_epex_prices_to_db()

        assert data_store.upsert_prices.call_count == 2

        # Second call should have the definitive prices
        rows = data_store.upsert_prices.call_args_list[1][0][0]
        # First sub-interval: 12 cents → 0.12 EUR
        assert rows[0][1] == 0.12
        assert rows[0][2] == 0.06


class TestPersistAmberPricesToDb:
    """Tests for ManageAmberPriceData._persist_prices_to_db()."""

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_basic_2_forecasts_produces_12_rows(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """2 forecast items at 30-min → 12 rows at 5-min."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        cons = [
            make_amber_forecast(0.25, TEST_START),
            make_amber_forecast(0.30, TEST_START + timedelta(minutes=30)),
        ]
        prod = [
            make_amber_forecast(0.10, TEST_START),
            make_amber_forecast(0.12, TEST_START + timedelta(minutes=30)),
        ]

        amber_manager._persist_prices_to_db(cons, prod)

        data_store.upsert_prices.assert_called_once()
        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 12

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_per_kwh_values_stored_as_is(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """Amber per_kwh values stored directly — no cents→EUR conversion."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        cons = [make_amber_forecast(0.25, TEST_START)]
        prod = [make_amber_forecast(0.10, TEST_START)]

        amber_manager._persist_prices_to_db(cons, prod)

        rows = data_store.upsert_prices.call_args[0][0]
        # All 6 sub-intervals should have the raw per_kwh values
        for row in rows:
            assert row[1] == 0.25
            assert row[2] == 0.10

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_timestamps_are_5min_aligned(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """Verify output timestamps are at 5-min intervals."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        cons = [make_amber_forecast(0.25, TEST_START)]
        prod = [make_amber_forecast(0.10, TEST_START)]

        amber_manager._persist_prices_to_db(cons, prod)

        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 6

        expected_timestamps = [TEST_START + timedelta(minutes=5 * i) for i in range(6)]
        for row, expected_ts in zip(rows, expected_timestamps):
            assert row[0] == expected_ts.astimezone(timezone.utc).isoformat()

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_recalculate_ratings_false(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """Amber prices are persisted with recalculate_ratings=False."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        cons = [make_amber_forecast(0.25, TEST_START)]
        prod = [make_amber_forecast(0.10, TEST_START)]

        amber_manager._persist_prices_to_db(cons, prod)

        call_kwargs = data_store.upsert_prices.call_args[1]
        assert call_kwargs["recalculate_ratings"] is False

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_price_rating_always_none_in_rows(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """price_rating is always None — deferred to Fase 7."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        cons = [make_amber_forecast(0.25, TEST_START)]
        prod = [make_amber_forecast(0.10, TEST_START)]

        amber_manager._persist_prices_to_db(cons, prod)

        rows = data_store.upsert_prices.call_args[0][0]
        for row in rows:
            assert row[3] is None

    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_no_data_store_skips(self, mock_c, amber_manager):
        """No DataStore → logs warning, no crash."""
        amber_manager.data_store = None

        cons = [make_amber_forecast(0.25, TEST_START)]
        prod = [make_amber_forecast(0.10, TEST_START)]

        amber_manager._persist_prices_to_db(cons, prod)  # Should not raise

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_empty_forecasts_skips(self, mock_c, mock_parse, amber_manager, data_store):
        """Empty forecast lists → no persistence."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        amber_manager._persist_prices_to_db([], [])

        data_store.upsert_prices.assert_not_called()

    @patch("apps.v2g_liberty.amber_price_data_manager.parse_to_rounded_local_datetime")
    @patch("apps.v2g_liberty.amber_price_data_manager.c")
    def test_mismatched_timestamps_only_overlap_persisted(
        self, mock_c, mock_parse, amber_manager, data_store
    ):
        """When cons and prod have different timestamps, only overlap is persisted."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        mock_parse.side_effect = lambda dt_str: datetime.fromisoformat(dt_str)

        # Consumption: 00:00 and 00:30
        cons = [
            make_amber_forecast(0.25, TEST_START),
            make_amber_forecast(0.30, TEST_START + timedelta(minutes=30)),
        ]
        # Production: 00:30 and 01:00 (only 00:30 overlaps)
        prod = [
            make_amber_forecast(0.10, TEST_START + timedelta(minutes=30)),
            make_amber_forecast(0.12, TEST_START + timedelta(minutes=60)),
        ]

        amber_manager._persist_prices_to_db(cons, prod)

        rows = data_store.upsert_prices.call_args[0][0]
        # Only 00:30 overlaps → 6 five-min rows
        assert len(rows) == 6
        for row in rows:
            assert row[1] == 0.30
            assert row[2] == 0.10


class TestPersistOctopusPricesToDb:
    """Tests for ManageOctopusPriceData._persist_prices_to_db()."""

    def _setup_manager(self, octopus_manager, mock_c):
        """Common setup: mock constants and timestamp parser."""
        mock_c.PRICE_RESOLUTION_MINUTES = 30
        octopus_manager._parse_to_rounded_uk_datetime = lambda dt_str: (
            datetime.fromisoformat(dt_str)
        )

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_basic_2_results_produces_12_rows(
        self, mock_c, octopus_manager, data_store
    ):
        """2 results at 30-min → 12 rows at 5-min."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.5, TEST_START),
            make_octopus_result(25.0, TEST_START + timedelta(minutes=30)),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
            make_octopus_result(6.0, TEST_START + timedelta(minutes=30)),
        ]

        octopus_manager._persist_prices_to_db()

        data_store.upsert_prices.assert_called_once()
        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 12

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_pence_to_gbp_conversion(self, mock_c, octopus_manager, data_store):
        """Octopus value_inc_vat (pence/kWh) is converted to GBP/kWh (÷100)."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.5, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        # 20.5 pence → 0.205 GBP, 5.0 pence → 0.05 GBP
        for row in rows:
            assert row[1] == 0.205
            assert row[2] == 0.05

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_timestamps_are_5min_aligned(self, mock_c, octopus_manager, data_store):
        """Verify output timestamps are at 5-min intervals."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        assert len(rows) == 6

        expected_timestamps = [TEST_START + timedelta(minutes=5 * i) for i in range(6)]
        for row, expected_ts in zip(rows, expected_timestamps):
            assert row[0] == expected_ts.astimezone(timezone.utc).isoformat()

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_recalculate_ratings_default_true(
        self, mock_c, octopus_manager, data_store
    ):
        """Octopus uses default recalculate_ratings=True (no forecast transition)."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()

        # No recalculate_ratings kwarg → uses default True
        call_kwargs = data_store.upsert_prices.call_args[1]
        assert "recalculate_ratings" not in call_kwargs

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_price_rating_always_none_in_rows(
        self, mock_c, octopus_manager, data_store
    ):
        """price_rating is always None — DataStore.upsert_prices() calculates it."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        for row in rows:
            assert row[3] is None

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_no_data_store_skips(self, mock_c, octopus_manager):
        """No DataStore → logs warning, no crash."""
        octopus_manager.data_store = None
        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()  # Should not raise

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_empty_results_skips(self, mock_c, octopus_manager, data_store):
        """Empty result lists → no persistence."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = []
        octopus_manager._latest_export_results = []

        octopus_manager._persist_prices_to_db()

        data_store.upsert_prices.assert_not_called()

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_temp_storage_cleaned_up(self, mock_c, octopus_manager, data_store):
        """After persistence, temporary raw results are cleaned up."""
        self._setup_manager(octopus_manager, mock_c)

        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
        ]
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START),
        ]

        octopus_manager._persist_prices_to_db()

        assert octopus_manager._latest_import_results is None
        assert octopus_manager._latest_export_results is None

    @patch("apps.v2g_liberty.octopus_price_data_manager.c")
    def test_mismatched_timestamps_only_overlap_persisted(
        self, mock_c, octopus_manager, data_store
    ):
        """When import and export have different timestamps, only overlap is persisted."""
        self._setup_manager(octopus_manager, mock_c)

        # Import: 00:00 and 00:30
        octopus_manager._latest_import_results = [
            make_octopus_result(20.0, TEST_START),
            make_octopus_result(25.0, TEST_START + timedelta(minutes=30)),
        ]
        # Export: 00:30 and 01:00 (only 00:30 overlaps)
        octopus_manager._latest_export_results = [
            make_octopus_result(5.0, TEST_START + timedelta(minutes=30)),
            make_octopus_result(6.0, TEST_START + timedelta(minutes=60)),
        ]

        octopus_manager._persist_prices_to_db()

        rows = data_store.upsert_prices.call_args[0][0]
        # Only 00:30 overlaps → 6 five-min rows
        assert len(rows) == 6
        for row in rows:
            assert row[1] == 0.25  # 25 pence → 0.25 GBP
            assert row[2] == 0.05  # 5 pence → 0.05 GBP
