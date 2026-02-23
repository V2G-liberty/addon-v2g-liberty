"""Unit tests for DataStore aggregation queries (T20/T21/T22)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from appdaemon.plugins.hass.hassapi import Hass

from apps.v2g_liberty.data_store import (
    DataStore,
    _dominant_app_state,
    _dominant_price_rating,
    _period_key,
)

# pylint: disable=C0116,W0621

TEST_TZ = timezone(timedelta(hours=1))
TEST_NOW = datetime(2026, 2, 23, 12, 0, 0, tzinfo=TEST_TZ)


def ts(hour: int, minute: int = 0) -> str:
    """Create an ISO 8601 timestamp for 2026-02-23 at given hour:minute +01:00."""
    return datetime(2026, 2, 23, hour, minute, 0, tzinfo=TEST_TZ).isoformat()


@pytest.fixture
def hass():
    return AsyncMock(spec=Hass)


@pytest.fixture
def data_store(hass, tmp_path):
    store = DataStore(hass)
    store.DB_PATH = str(tmp_path / "test_aggregation.db")
    return store


def _insert_interval(
    store,
    timestamp,
    power_kw,
    energy_kwh,
    app_state,
    soc_pct=50.0,
    availability_pct=100.0,
):
    """Helper to insert a single interval."""
    store.insert_interval(
        timestamp=timestamp,
        power_kw=power_kw,
        energy_kwh=energy_kwh,
        app_state=app_state,
        soc_pct=soc_pct,
        availability_pct=availability_pct,
    )


def _insert_price(store, timestamp, cons_price, prod_price, rating=None):
    """Helper to insert a single price row."""
    store.upsert_prices(
        [(timestamp, cons_price, prod_price, rating)],
        recalculate_ratings=False,
    )


def _insert_emission(store, timestamp, intensity):
    """Helper to insert a single emission row."""
    store.upsert_emissions([(timestamp, intensity)])


# ── _period_key tests ──────────────────────────────────────────────


class TestPeriodKey:
    def test_quarter_rounds_down_to_00(self):
        assert _period_key(ts(8, 0), "quarter_hours") == ts(8, 0)

    def test_quarter_rounds_down_to_00_from_05(self):
        assert _period_key(ts(8, 5), "quarter_hours") == ts(8, 0)

    def test_quarter_rounds_down_to_00_from_10(self):
        assert _period_key(ts(8, 10), "quarter_hours") == ts(8, 0)

    def test_quarter_rounds_down_to_15(self):
        assert _period_key(ts(8, 15), "quarter_hours") == ts(8, 15)

    def test_quarter_rounds_down_to_15_from_25(self):
        assert _period_key(ts(8, 25), "quarter_hours") == ts(8, 15)

    def test_quarter_rounds_down_to_30(self):
        assert _period_key(ts(8, 30), "quarter_hours") == ts(8, 30)

    def test_quarter_rounds_down_to_45(self):
        assert _period_key(ts(8, 50), "quarter_hours") == ts(8, 45)

    def test_hour_rounds_down(self):
        assert _period_key(ts(8, 35), "hours") == ts(8, 0)

    def test_hour_already_on_boundary(self):
        assert _period_key(ts(8, 0), "hours") == ts(8, 0)

    def test_day(self):
        assert _period_key(ts(8, 35), "days") == "2026-02-23"

    def test_week(self):
        # 2026-02-23 is a Monday → ISO week 9
        assert _period_key(ts(8, 0), "weeks") == "2026-W09"

    def test_month(self):
        assert _period_key(ts(8, 0), "months") == "2026-02"

    def test_year(self):
        assert _period_key(ts(8, 0), "years") == "2026"

    def test_invalid_granularity_raises(self):
        with pytest.raises(ValueError, match="Unknown granularity"):
            _period_key(ts(8, 0), "invalid")


# ── _dominant_app_state tests ──────────────────────────────────────


class TestDominantAppState:
    def test_single_state(self):
        assert _dominant_app_state(["automatic"]) == "automatic"

    def test_single_state_repeated(self):
        assert (
            _dominant_app_state(["automatic", "automatic", "automatic"]) == "automatic"
        )

    def test_empty_list(self):
        assert _dominant_app_state([]) == "unknown"

    def test_two_states_longer_run_wins(self):
        # automatic has longest contiguous run (2 vs 1)
        result = _dominant_app_state(["automatic", "automatic", "charge"])
        assert result == "automatic+"

    def test_multiple_states_plus_suffix(self):
        result = _dominant_app_state(["automatic", "charge"])
        assert result.endswith("+")

    def test_longest_contiguous_not_most_frequent(self):
        # charge appears 3 times but max contiguous run is 2
        # automatic appears 3 times with max contiguous run of 3
        states = ["charge", "charge", "automatic", "automatic", "automatic", "charge"]
        result = _dominant_app_state(states)
        assert result == "automatic+"

    def test_tiebreak_higher_priority_wins(self):
        # Both runs are length 2, error (priority 1) beats automatic (priority 7)
        states = ["automatic", "automatic", "error", "error"]
        result = _dominant_app_state(states)
        assert result == "error+"

    def test_tiebreak_priority_order(self):
        # All runs length 1: error has highest priority
        states = ["automatic", "error", "charge"]
        result = _dominant_app_state(states)
        assert result == "error+"

    def test_no_plus_when_single_distinct_state(self):
        result = _dominant_app_state(["pause", "pause"])
        assert result == "pause"
        assert "+" not in result

    def test_real_scenario_mixed_hour(self):
        # Typical hour: mostly automatic with a brief charge period
        states = ["automatic"] * 8 + ["charge"] * 3 + ["automatic"]
        result = _dominant_app_state(states)
        assert result == "automatic+"

    def test_contiguous_vs_total_count(self):
        # charge: appears 4 times but in runs of [2, 2]
        # automatic: appears 4 times in runs of [1, 3]
        # Longest contiguous: automatic (3) beats charge (2)
        states = [
            "charge",
            "charge",
            "automatic",
            "automatic",
            "automatic",
            "charge",
            "charge",
            "automatic",
        ]
        result = _dominant_app_state(states)
        assert result == "automatic+"


# ── _dominant_price_rating tests ───────────────────────────────────


class TestDominantPriceRating:
    def test_all_same(self):
        assert _dominant_price_rating(["low", "low", "low"]) == "low"

    def test_most_frequent_wins(self):
        assert _dominant_price_rating(["low", "low", "high"]) == "low"

    def test_all_none(self):
        assert _dominant_price_rating([None, None, None]) is None

    def test_empty(self):
        assert _dominant_price_rating([]) is None

    def test_none_values_ignored(self):
        assert _dominant_price_rating([None, "average", None]) == "average"

    def test_mixed_with_none(self):
        result = _dominant_price_rating(["high", None, "low", "high"])
        assert result == "high"


# ── get_aggregated_data integration tests ──────────────────────────


class TestAggregatedDataQuarter:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_empty_range_returns_empty(self, mock_now, data_store):
        await data_store.initialise()
        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "quarter_hours")
        assert result == []

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_single_interval_quarter(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 3.0, 0.25, "automatic", soc_pct=55.0)
        _insert_price(data_store, ts(8, 0), 0.25, 0.10, "low")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "quarter_hours")
        assert len(result) == 1
        row = result[0]
        assert row["period_start"] == ts(8, 0)
        assert row["app_state"] == "automatic"
        assert row["consumption_price"] == 0.25
        assert row["production_price"] == 0.10
        assert row["price_rating"] == "low"
        assert row["soc_pct"] == 55.0
        assert row["energy_wh"] == 250  # 0.25 kWh * 1000
        assert row["charge_cost"] == round(0.25 * 0.25, 4)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_three_intervals_in_one_quarter(self, mock_now, data_store):
        await data_store.initialise()
        # Three 5-min intervals in the 08:00-08:15 quarter
        _insert_interval(data_store, ts(8, 0), -2.0, -0.167, "automatic", soc_pct=50.0)
        _insert_interval(data_store, ts(8, 5), -2.0, -0.167, "automatic", soc_pct=49.0)
        _insert_interval(data_store, ts(8, 10), -2.0, -0.167, "automatic", soc_pct=48.0)

        _insert_price(data_store, ts(8, 0), 0.25, 0.20, "average")
        _insert_price(data_store, ts(8, 5), 0.25, 0.20, "average")
        _insert_price(data_store, ts(8, 10), 0.25, 0.20, "average")

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 15), "quarter_hours")
        assert len(result) == 1
        row = result[0]
        assert row["period_start"] == ts(8, 0)
        assert row["app_state"] == "automatic"
        assert row["soc_pct"] == 48.0  # last value
        assert row["energy_wh"] == round(-0.167 * 3 * 1000)
        assert row["discharge_revenue"] == round(0.167 * 3 * 0.20, 4)
        assert row["charge_cost"] == 0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_two_quarters_in_range(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic", soc_pct=50.0)
        _insert_interval(data_store, ts(8, 15), 3.0, 0.25, "charge", soc_pct=52.0)

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 30), "quarter_hours")
        assert len(result) == 2
        assert result[0]["period_start"] == ts(8, 0)
        assert result[1]["period_start"] == ts(8, 15)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_quarter_no_prices(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "quarter_hours")
        row = result[0]
        assert row["consumption_price"] is None
        assert row["production_price"] is None
        assert row["price_rating"] is None
        assert row["charge_cost"] == 0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_quarter_soc_null_when_disconnected(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(
            data_store,
            ts(8, 0),
            0.0,
            0.0,
            "not_connected",
            soc_pct=None,
            availability_pct=0.0,
        )
        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "quarter_hours")
        assert result[0]["soc_pct"] is None

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_quarter_app_state_plus_suffix(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")
        _insert_interval(data_store, ts(8, 5), 2.0, 0.167, "automatic")
        _insert_interval(data_store, ts(8, 10), 3.0, 0.25, "charge")

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 15), "quarter_hours")
        assert result[0]["app_state"] == "automatic+"


class TestAggregatedDataHour:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_hourly_charge_and_discharge_split(self, mock_now, data_store):
        await data_store.initialise()
        # 6 intervals of charging, 6 of discharging within one hour
        for m in range(0, 30, 5):
            _insert_interval(
                data_store, ts(8, m), 3.0, 0.25, "automatic", soc_pct=50 + m
            )
            _insert_price(data_store, ts(8, m), 0.20, 0.15, "low")
        for m in range(30, 60, 5):
            _insert_interval(
                data_store, ts(8, m), -2.0, -0.167, "automatic", soc_pct=80 - m
            )
            _insert_price(data_store, ts(8, m), 0.20, 0.15, "low")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "hours")
        assert len(result) == 1
        row = result[0]
        assert row["charge_wh"] == round(0.25 * 6 * 1000)
        assert row["discharge_wh"] == round(0.167 * 6 * 1000)
        assert row["charge_cost"] == round(0.25 * 6 * 0.20, 4)
        assert row["discharge_revenue"] == round(0.167 * 6 * 0.15, 4)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_hourly_weighted_avg_price(self, mock_now, data_store):
        await data_store.initialise()
        # 2 intervals charging at 0.30 cons, 1 interval discharging at 0.10 prod
        _insert_interval(data_store, ts(8, 0), 3.0, 0.25, "automatic")
        _insert_price(data_store, ts(8, 0), 0.30, 0.10, "high")
        _insert_interval(data_store, ts(8, 5), 3.0, 0.25, "automatic")
        _insert_price(data_store, ts(8, 5), 0.30, 0.10, "high")
        _insert_interval(data_store, ts(8, 10), -2.0, -0.167, "automatic")
        _insert_price(data_store, ts(8, 10), 0.30, 0.10, "high")

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 15), "hours")
        row = result[0]

        # Weighted avg = (0.25*2*0.30 + 0.167*0.10) / (0.25*2 + 0.167)
        charge_cost = 0.25 * 2 * 0.30
        discharge_rev = 0.167 * 0.10
        total_kwh = 0.25 * 2 + 0.167
        expected_avg = (charge_cost + discharge_rev) / total_kwh
        assert row["avg_price"] == round(expected_avg, 5)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_hourly_no_energy_uses_simple_avg_price(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 0.0, 0.0, "pause")
        _insert_price(data_store, ts(8, 0), 0.20, 0.10, "low")
        _insert_interval(data_store, ts(8, 5), 0.0, 0.0, "pause")
        _insert_price(data_store, ts(8, 5), 0.30, 0.15, "average")

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 15), "hours")
        row = result[0]
        assert row["avg_price"] == round((0.20 + 0.30) / 2, 5)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_hourly_soc_last_value(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic", soc_pct=50.0)
        _insert_interval(data_store, ts(8, 5), 2.0, 0.167, "automatic", soc_pct=52.0)
        _insert_interval(data_store, ts(8, 10), 0.0, 0.0, "not_connected", soc_pct=None)

        result = data_store.get_aggregated_data(ts(8, 0), ts(8, 15), "hours")
        # Last non-null SoC should be 52.0
        assert result[0]["soc_pct"] == 52.0


class TestAggregatedDataPeriod:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_daily_aggregation(self, mock_now, data_store):
        await data_store.initialise()
        # 3 intervals: 2 charging, 1 discharging
        _insert_interval(
            data_store, ts(8, 0), 3.0, 0.25, "automatic", availability_pct=100.0
        )
        _insert_price(data_store, ts(8, 0), 0.25, 0.10)
        _insert_interval(
            data_store, ts(8, 5), 3.0, 0.25, "automatic", availability_pct=100.0
        )
        _insert_price(data_store, ts(8, 5), 0.25, 0.10)
        _insert_interval(
            data_store, ts(8, 10), -2.0, -0.167, "automatic", availability_pct=50.0
        )
        _insert_price(data_store, ts(8, 10), 0.25, 0.10)

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "days")
        assert len(result) == 1
        row = result[0]
        assert row["period_start"] == "2026-02-23"
        assert row["charge_kwh"] == round(0.25 * 2, 2)
        assert row["discharge_kwh"] == round(0.167, 2)
        assert row["charge_cost"] == round(0.25 * 2 * 0.25, 4)
        assert row["discharge_revenue"] == round(0.167 * 0.10, 4)
        assert row["net_kwh"] == round(0.25 * 2 - 0.167, 2)
        expected_net_cost = (0.25 * 2 * 0.25) - (0.167 * 0.10)
        assert row["net_cost"] == round(expected_net_cost, 4)
        assert row["availability_pct"] == round((100 + 100 + 50) / 3, 1)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_daily_co2_calculation(self, mock_now, data_store):
        await data_store.initialise()
        # Charging: 0.25 kWh at 400 kg/MWh -> 0.25 * 400 / 1000 = 0.1 kg
        _insert_interval(data_store, ts(8, 0), 3.0, 0.25, "automatic")
        _insert_emission(data_store, ts(8, 0), 400.0)
        # Discharging: -0.167 kWh at 350 kg/MWh -> -0.167 * 350 / 1000 = -0.058 kg
        _insert_interval(data_store, ts(8, 5), -2.0, -0.167, "automatic")
        _insert_emission(data_store, ts(8, 5), 350.0)

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "days")
        row = result[0]
        expected_co2 = (0.25 * 400 / 1000) + (-0.167 * 350 / 1000)
        assert row["co2_kg"] == round(expected_co2, 1)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_daily_no_emissions(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 3.0, 0.25, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "days")
        assert result[0]["co2_kg"] == 0

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_weekly_grouping(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")
        _insert_interval(data_store, ts(8, 5), 2.0, 0.167, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "weeks")
        assert len(result) == 1
        assert result[0]["period_start"] == "2026-W09"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_monthly_grouping(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "months")
        assert len(result) == 1
        assert result[0]["period_start"] == "2026-02"

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_yearly_grouping(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "years")
        assert len(result) == 1
        assert result[0]["period_start"] == "2026"


class TestAggregatedDataEdgeCases:
    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_invalid_granularity_raises(self, mock_now, data_store):
        await data_store.initialise()
        with pytest.raises(ValueError, match="Invalid granularity"):
            data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "minute")

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_range_excludes_end_timestamp(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")
        # This interval is AT the end boundary — should be excluded
        _insert_interval(data_store, ts(9, 0), 3.0, 0.25, "charge")

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "hours")
        assert len(result) == 1
        assert result[0]["period_start"] == ts(8, 0)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_only_charging_intervals(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), 3.0, 0.25, "charge")
        _insert_price(data_store, ts(8, 0), 0.30, 0.10)

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "days")
        row = result[0]
        assert row["charge_kwh"] == 0.25
        assert row["discharge_kwh"] == 0
        assert row["discharge_revenue"] == 0
        assert row["net_kwh"] == 0.25

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_only_discharging_intervals(self, mock_now, data_store):
        await data_store.initialise()
        _insert_interval(data_store, ts(8, 0), -2.0, -0.167, "discharge")
        _insert_price(data_store, ts(8, 0), 0.30, 0.15)

        result = data_store.get_aggregated_data(ts(8, 0), ts(9, 0), "days")
        row = result[0]
        assert row["charge_kwh"] == 0
        assert row["charge_cost"] == 0
        assert row["discharge_kwh"] == round(0.167, 2)
        assert row["discharge_revenue"] == round(0.167 * 0.15, 4)
        assert row["net_kwh"] == round(-0.167, 2)

    @pytest.mark.asyncio
    @patch("apps.v2g_liberty.data_store.get_local_now", return_value=TEST_NOW)
    async def test_results_ordered_by_period(self, mock_now, data_store):
        await data_store.initialise()
        # Insert in reverse order
        _insert_interval(data_store, ts(10, 0), 1.0, 0.083, "automatic")
        _insert_interval(data_store, ts(8, 0), 2.0, 0.167, "automatic")

        result = data_store.get_aggregated_data(ts(8, 0), ts(11, 0), "hours")
        assert len(result) == 2
        assert result[0]["period_start"] == ts(8, 0)
        assert result[1]["period_start"] == ts(10, 0)
