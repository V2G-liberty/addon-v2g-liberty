"""Unit tests for meter_energy pure helpers."""

from apps.v2g_liberty.grid_connection.meter_energy import (
    normalise_to_kwh,
    sum_register_readings,
    interval_delta,
)


class TestNormaliseToKwh:
    def test_kwh_passthrough(self):
        assert normalise_to_kwh(18441.05, "kWh") == 18441.05

    def test_wh_to_kwh(self):
        assert normalise_to_kwh(5000, "Wh") == 5.0

    def test_mwh_to_kwh(self):
        assert normalise_to_kwh(2, "MWh") == 2000.0

    def test_string_value_coerced(self):
        # HA states are strings.
        assert normalise_to_kwh("18441.05", "kWh") == 18441.05

    def test_unknown_unit_is_none(self):
        assert normalise_to_kwh(10, "J") is None

    def test_non_numeric_is_none(self):
        assert normalise_to_kwh("unavailable", "kWh") is None
        assert normalise_to_kwh(None, "kWh") is None


class TestSumRegisterReadings:
    def test_sums_tariffs(self):
        assert sum_register_readings([1200.0, 800.5]) == 2000.5

    def test_single_register(self):
        assert sum_register_readings([18441.05]) == 18441.05

    def test_none_when_any_missing(self):
        # One tariff register unavailable → skip the whole interval.
        assert sum_register_readings([1200.0, None]) is None

    def test_none_when_empty(self):
        assert sum_register_readings([]) is None


class TestIntervalDelta:
    def test_normal_delta(self):
        delta, is_reset = interval_delta(18441.30, 18441.05)
        assert delta == 0.25
        assert is_reset is False

    def test_no_baseline_first_interval(self):
        assert interval_delta(18441.05, None) == (None, False)

    def test_unavailable_current_keeps_baseline(self):
        assert interval_delta(None, 18441.05) == (None, False)

    def test_reset_detected(self):
        # Meter replacement: register drops below the previous total.
        delta, is_reset = interval_delta(5.0, 18441.05)
        assert delta is None
        assert is_reset is True

    def test_zero_delta(self):
        # No import this interval (e.g. exporting only) → exactly zero.
        assert interval_delta(18441.05, 18441.05) == (0.0, False)
