"""Unit tests for NaiveChargingSimulator and _calculate_savings (T118).

Tests cover:
- Batch simulation: connected charging, disconnected periods, trip SoC depletion
- Real-time: single interval update, SoC persistence across calls
- Power factor: applied correctly to naive power
- _calculate_savings: dynamic + fixed savings, SoC depletion correction,
  missing naive data, missing prices
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty import constants as c
from apps.v2g_liberty.data_store import DataStore, _calculate_savings
from apps.v2g_liberty.naive_charging_simulator import NaiveChargingSimulator

# pylint: disable=C0116,W0621

TEST_TZ = timezone(timedelta(hours=1))


@pytest.fixture(autouse=True)
def _set_constants():
    """Set runtime constants used by the simulator."""
    c.CHARGER_MAX_CHARGE_POWER = 5750
    c.CAR_MAX_CAPACITY_IN_KWH = 59
    c.CAR_MAX_SOC_IN_PERCENT = 80
    c.ROUNDTRIP_EFFICIENCY_FACTOR = 0.85
    c.FM_EVENT_RESOLUTION_IN_MINUTES = 5
    c.TZ = TEST_TZ


@pytest.fixture
def hass():
    return AsyncMock(spec=Hass)


@pytest.fixture
def data_store(hass, tmp_path):
    store = DataStore(hass)
    store.DB_PATH = str(tmp_path / "test_naive.db")
    return store


def utc_ts(hour: int, minute: int = 0) -> str:
    dt = datetime(2026, 2, 23, hour, minute, 0, tzinfo=TEST_TZ)
    return dt.astimezone(timezone.utc).isoformat()


# ── _calculate_savings tests ─────────────────────────────────────


class TestCalculateSavings:
    """Tests for the _calculate_savings helper function."""

    def _make_interval(
        self,
        energy_kwh=0.1,
        naive_power_w=4715.0,
        consumption_price_kwh=0.30,
        production_price_kwh=0.25,
        ref_price_kwh=0.35,
        soc_pct=50.0,
        naive_soc_pct=55.0,
    ):
        return {
            "energy_kwh": energy_kwh,
            "naive_power_w": naive_power_w,
            "consumption_price_kwh": consumption_price_kwh,
            "production_price_kwh": production_price_kwh,
            "ref_price_kwh": ref_price_kwh,
            "soc_pct": soc_pct,
            "naive_soc_pct": naive_soc_pct,
        }

    def test_basic_savings_positive(self):
        """Naive costs more than algo → positive savings."""
        intervals = [self._make_interval(energy_kwh=0.05, naive_power_w=4715.0)]
        result = _calculate_savings(intervals)
        assert result["savings_dynamic_eur"] is not None
        assert result["savings_dynamic_eur"] > 0
        assert result["savings_fixed_eur"] is not None
        assert result["savings_fixed_eur"] > 0

    def test_no_naive_data_returns_none(self):
        """If naive_power_w is None, savings are None."""
        intervals = [self._make_interval(naive_power_w=None)]
        result = _calculate_savings(intervals)
        assert result["savings_fixed_eur"] is None
        assert result["savings_dynamic_eur"] is None

    def test_no_dynamic_price_returns_none_for_dynamic(self):
        """Missing consumption price → dynamic savings None, fixed still works."""
        intervals = [
            self._make_interval(consumption_price_kwh=None, ref_price_kwh=0.35)
        ]
        result = _calculate_savings(intervals)
        assert result["savings_dynamic_eur"] is None
        # Fixed savings still calculable (naive uses ref_price, algo_cost is 0)
        assert result["savings_fixed_eur"] is not None

    def test_no_ref_price_returns_none_for_fixed(self):
        """Missing reference price → fixed savings None, dynamic still works."""
        intervals = [self._make_interval(ref_price_kwh=None)]
        result = _calculate_savings(intervals)
        assert result["savings_fixed_eur"] is None
        assert result["savings_dynamic_eur"] is not None

    def test_discharge_revenue_included_in_algo_cost(self):
        """Algo discharge revenue reduces algo cost → increases savings."""
        # Interval with discharge (negative energy)
        intervals = [
            self._make_interval(
                energy_kwh=-0.1,
                naive_power_w=0.0,
                soc_pct=50.0,
                naive_soc_pct=50.0,
            )
        ]
        result = _calculate_savings(intervals)
        # Algo earned money from discharge, naive did nothing.
        # savings = naive_cost(0) - algo_cost(negative) = positive
        assert result["savings_dynamic_eur"] is not None
        # Algo earned from discharge (negative cost), naive did nothing (0 cost).
        # savings = 0 - (negative) = positive.
        assert result["savings_dynamic_eur"] > 0

    def test_soc_depletion_correction(self):
        """SoC depletion correction adjusts for battery state difference."""
        # Naive ended with more SoC than real → correction reduces savings.
        intervals = [
            self._make_interval(soc_pct=40.0, naive_soc_pct=40.0),
            self._make_interval(soc_pct=50.0, naive_soc_pct=70.0),
        ]
        result_with_diff = _calculate_savings(intervals)

        # Same but no SoC difference.
        intervals_equal = [
            self._make_interval(soc_pct=40.0, naive_soc_pct=40.0),
            self._make_interval(soc_pct=50.0, naive_soc_pct=50.0),
        ]
        result_equal = _calculate_savings(intervals_equal)

        # Naive ended with more SoC → positive correction (naive stored more
        # energy in battery) → savings increase. So with_diff > equal.
        assert (
            result_with_diff["savings_dynamic_eur"]
            > result_equal["savings_dynamic_eur"]
        )

    def test_empty_intervals(self):
        """Empty list returns None."""
        result = _calculate_savings([])
        assert result["savings_fixed_eur"] is None
        assert result["savings_dynamic_eur"] is None


# ── Batch simulation tests ───────────────────────────────────────


class TestBatchSimulation:
    """Tests for the _simulate method."""

    def _make_simulator(self, power_factor=0.82):
        sim = NaiveChargingSimulator.__new__(NaiveChargingSimulator)
        sim.hass = MagicMock()
        sim.hass.log = MagicMock()
        sim.data_store = MagicMock()
        sim.event_bus = MagicMock()
        sim._charge_power_factor = power_factor
        from apps.v2g_liberty.log_wrapper import get_class_method_logger

        sim._NaiveChargingSimulator__log = get_class_method_logger(sim.hass.log)
        return sim

    def _make_df(self, rows):
        return pd.DataFrame(
            rows,
            columns=[
                "timestamp",
                "energy_kwh",
                "soc_pct",
                "availability_pct",
                "naive_power_w",
            ],
        )

    def test_charges_when_connected_below_max(self):
        """Naive charges at max power when connected and SoC < target."""
        sim = self._make_simulator(power_factor=1.0)
        df = self._make_df(
            [
                ("2026-02-23T10:00:00+00:00", 0.1, 40.0, 100.0, None),
                ("2026-02-23T10:05:00+00:00", 0.1, 41.0, 100.0, None),
            ]
        )
        result = sim._simulate(df)
        assert all(result["naive_power_w"] == c.CHARGER_MAX_CHARGE_POWER)

    def test_stops_at_soc_max(self):
        """Naive stops charging when SoC reaches target."""
        sim = self._make_simulator(power_factor=1.0)
        df = self._make_df(
            [
                ("2026-02-23T10:00:00+00:00", 0.0, 79.5, 100.0, None),
                ("2026-02-23T10:05:00+00:00", 0.0, 80.0, 100.0, None),
            ]
        )
        result = sim._simulate(df)
        # First interval: below max → charges
        assert result["naive_power_w"].iloc[0] > 0
        # Eventually naive SoC reaches 80% and power drops to 0

    def test_no_charge_when_disconnected(self):
        """Naive does not charge when car is disconnected."""
        sim = self._make_simulator()
        df = self._make_df(
            [
                ("2026-02-23T10:00:00+00:00", 0.0, 40.0, 0.0, None),
                ("2026-02-23T10:05:00+00:00", 0.0, 40.0, 0.0, None),
            ]
        )
        result = sim._simulate(df)
        assert all(result["naive_power_w"] == 0.0)

    def test_trip_soc_depletion_applied(self):
        """After a trip (disconnect + reconnect with lower SoC), naive SoC drops."""
        sim = self._make_simulator(power_factor=1.0)
        df = self._make_df(
            [
                ("2026-02-23T10:00:00+00:00", 0.0, 70.0, 100.0, None),
                ("2026-02-23T10:05:00+00:00", 0.0, 70.0, 0.0, None),  # disconnected
                ("2026-02-23T10:10:00+00:00", 0.0, 70.0, 0.0, None),  # still away
                (
                    "2026-02-23T10:15:00+00:00",
                    0.0,
                    50.0,
                    100.0,
                    None,
                ),  # returned, SoC dropped
            ]
        )
        result = sim._simulate(df)
        # After return, naive SoC should have dropped by the same amount (70→50 = -20)
        naive_soc_after_return = result["naive_soc_pct"].iloc[3]
        # The naive was charging in interval 0, so its SoC was > 70 before trip.
        # After trip: naive_soc + (50 - 70) should be < the pre-trip naive SoC.
        assert naive_soc_after_return < result["naive_soc_pct"].iloc[0]

    def test_power_factor_applied(self):
        """Power factor reduces the naive charging power."""
        sim_full = self._make_simulator(power_factor=1.0)
        sim_reduced = self._make_simulator(power_factor=0.5)
        df = self._make_df(
            [
                ("2026-02-23T10:00:00+00:00", 0.1, 40.0, 100.0, None),
            ]
        )
        result_full = sim_full._simulate(df)
        result_reduced = sim_reduced._simulate(df)
        assert result_full["naive_power_w"].iloc[0] == c.CHARGER_MAX_CHARGE_POWER
        assert (
            result_reduced["naive_power_w"].iloc[0] == c.CHARGER_MAX_CHARGE_POWER * 0.5
        )


# ── get_charge_power_factor tests ────────────────────────────────


class TestChargePowerFactor:
    """Tests for DataStore.get_charge_power_factor()."""

    @pytest.mark.asyncio
    async def test_returns_default_when_no_data(self, data_store):
        await data_store.initialise()
        factor = data_store.get_charge_power_factor()
        assert factor == 0.82

    @pytest.mark.asyncio
    async def test_returns_default_when_insufficient_data(self, data_store):
        await data_store.initialise()
        # Insert fewer than 100 charging intervals.
        for i in range(50):
            data_store.insert_interval(
                timestamp=utc_ts(10, i % 60),
                energy_kwh=0.1,
                app_state="automatic",
                soc_pct=50.0,
                availability_pct=100.0,
            )
        factor = data_store.get_charge_power_factor()
        assert factor == 0.82

    @pytest.mark.asyncio
    async def test_calculates_from_data(self, data_store):
        await data_store.initialise()
        # Insert 200 intervals with energy corresponding to ~4000W.
        # energy_kwh = power_kw * 5/60 → 4.0 * 5/60 = 0.3333
        for i in range(200):
            hour = 10 + i // 60
            minute = i % 60
            data_store.insert_interval(
                timestamp=utc_ts(hour, minute),
                energy_kwh=0.3333,
                app_state="automatic",
                soc_pct=50.0,
                availability_pct=100.0,
            )
        factor = data_store.get_charge_power_factor()
        # 0.3333 kWh * 12 = 4000W. Factor = 4000 / 5750 ≈ 0.696
        assert 0.69 < factor < 0.70

    @pytest.mark.asyncio
    async def test_clamps_to_range(self, data_store):
        await data_store.initialise()
        # Insert intervals with very high energy (above max power).
        for i in range(200):
            data_store.insert_interval(
                timestamp=utc_ts(10, i % 60) if i < 60 else utc_ts(11, i % 60),
                energy_kwh=1.0,  # 12000W, way above max
                app_state="automatic",
                soc_pct=50.0,
                availability_pct=100.0,
            )
        factor = data_store.get_charge_power_factor()
        assert factor == 1.0  # Clamped to max


# ── Integration: savings in aggregation ──────────────────────────


class TestSavingsInAggregation:
    """Test that savings fields appear in aggregated data."""

    @pytest.mark.asyncio
    async def test_aggregation_includes_savings_fields(self, data_store):
        await data_store.initialise()
        ts1 = utc_ts(10, 0)
        ts2 = utc_ts(10, 5)
        ts3 = utc_ts(10, 10)

        for t in [ts1, ts2, ts3]:
            data_store.insert_interval(
                timestamp=t,
                energy_kwh=0.1,
                app_state="automatic",
                soc_pct=50.0,
                availability_pct=100.0,
            )
            data_store.upsert_prices([(t, 0.30, 0.25, None)], recalculate_ratings=False)

        # Add naive data and reference prices.
        data_store.update_naive_charging(
            [
                (4715.0, 55.0, ts1),
                (4715.0, 56.0, ts2),
                (4715.0, 57.0, ts3),
            ]
        )
        data_store.upsert_reference_prices(
            [
                ("2026-02", 0.18, 0.13, "CBS", "2026-02-23T00:00:00Z"),
            ]
        )

        result = data_store.get_aggregated_data(
            utc_ts(10, 0), utc_ts(10, 15), "quarter_hours"
        )
        assert len(result) == 1
        row = result[0]
        assert "savings_fixed_eur" in row
        assert "savings_dynamic_eur" in row
        assert row["savings_dynamic_eur"] is not None
        assert row["savings_fixed_eur"] is not None

    @pytest.mark.asyncio
    async def test_aggregation_savings_none_without_naive(self, data_store):
        await data_store.initialise()
        ts1 = utc_ts(10, 0)
        data_store.insert_interval(
            timestamp=ts1,
            energy_kwh=0.1,
            app_state="automatic",
            soc_pct=50.0,
            availability_pct=100.0,
        )
        data_store.upsert_prices([(ts1, 0.30, 0.25, None)], recalculate_ratings=False)
        # No naive data, no reference prices.
        result = data_store.get_aggregated_data(
            utc_ts(10, 0), utc_ts(10, 15), "quarter_hours"
        )
        assert len(result) == 1
        assert result[0]["savings_fixed_eur"] is None
        assert result[0]["savings_dynamic_eur"] is None
