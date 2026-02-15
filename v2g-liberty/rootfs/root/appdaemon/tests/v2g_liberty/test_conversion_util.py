"""Unit test (pytest) for conversion_util module."""

from datetime import timedelta

import pytest

from apps.v2g_liberty.util.conversion_util import convert_mw_to_percentage_points


class TestConvertMwToPercentagePoints:
    """Test suite for convert_mw_to_percentage_points function."""

    @pytest.mark.parametrize(
        "description, values_in_mw, resolution, max_soc_in_kwh, round_trip_efficiency, expected",
        [
            (
                "Single positive value charging",
                [0.00575],
                timedelta(minutes=15),
                62.0,
                0.85,
                [2.1376],
            ),
            (
                "Single negative value discharging",
                [-0.00575],
                timedelta(minutes=15),
                62.0,
                0.85,
                [-2.3186],
            ),
            (
                "Mixed positive and negative values",
                [0.010, -0.010, 0.005, -0.005],
                timedelta(minutes=15),
                50.0,
                0.85,
                [4.6098, -5.0, 2.3049, -2.5],
            ),
            (
                "Zero values",
                [0.0, 0.0, 0.0],
                timedelta(minutes=15),
                60.0,
                0.85,
                [0.0, 0.0, 0.0],
            ),
            (
                "Different resolution (5 minutes)",
                [0.006],
                timedelta(minutes=5),
                62.0,
                0.85,
                [0.7435],
            ),
            (
                "Different resolution (30 minutes)",
                [0.006],
                timedelta(minutes=30),
                62.0,
                0.85,
                [4.4611],
            ),
            (
                "Battery capacity 59 kWh",
                [0.010],
                timedelta(minutes=15),
                59.0,
                0.85,
                [3.9066],
            ),
            (
                "Battery capacity 77 kWh",
                [0.010],
                timedelta(minutes=15),
                77.0,
                0.85,
                [2.9934],
            ),
            (
                "Round-trip efficiency 70%",
                [0.010, -0.010],
                timedelta(minutes=15),
                50.0,
                0.7,
                [4.1833, -5.0],
            ),
            (
                "Round-trip efficiency 85%",
                [0.010, -0.010],
                timedelta(minutes=15),
                50.0,
                0.85,
                [4.6098, -5.0],
            ),
            (
                "Large list of 200 values",
                [0.005] * 200,
                timedelta(minutes=15),
                60.0,
                0.85,
                [1.9207] * 200,
            ),
            (
                "Empty list",
                [],
                timedelta(minutes=15),
                60.0,
                0.85,
                [],
            ),
            (
                "Asymmetric charging/discharging due to efficiency",
                [0.007, -0.007],
                timedelta(minutes=15),
                62.0,
                0.85,
                [2.6023, -2.8226],
            ),
        ],
    )
    def test_convert_mw_to_percentage_points(
        self,
        description,
        values_in_mw,
        resolution,
        max_soc_in_kwh,
        round_trip_efficiency,
        expected,
    ):
        """Test conversion from MW to percentage points."""
        result = convert_mw_to_percentage_points(
            values_in_mw, resolution, max_soc_in_kwh, round_trip_efficiency
        )

        assert len(result) == len(expected), (
            f"Test failed for '{description}': "
            f"Length mismatch - got {len(result)}, expected {len(expected)}"
        )

        for i, (res, exp) in enumerate(zip(result, expected)):
            assert abs(res - exp) < 0.0001, (
                f"Test failed for '{description}' at index {i}: "
                f"got {res:.4f}, expected {exp:.4f}"
            )

    def test_efficiency_factor_applied_correctly(self):
        """Test that efficiency factor is applied correctly for charging vs discharging."""
        values = [0.010, -0.010]
        resolution = timedelta(minutes=15)
        max_soc = 50.0
        efficiency = 0.81  # 0.81^0.5 = 0.9

        result = convert_mw_to_percentage_points(
            values, resolution, max_soc, efficiency
        )

        # For positive (charging): multiplied by sqrt(efficiency) = 0.9
        # For negative (discharging): no efficiency applied
        # Expected: [0.010 * 0.25 * 1000 * 100 / 50 * 0.9, -0.010 * 0.25 * 1000 * 100 / 50]
        assert abs(result[0] - 4.5) < 0.0001
        assert abs(result[1] - (-5.0)) < 0.0001
        # Discharging should have larger absolute value due to no efficiency factor
        assert abs(result[1]) > abs(result[0])

    def test_scalar_calculation(self):
        """Test that the scalar is calculated correctly based on resolution and max_soc."""
        # For 15 min resolution and 60 kWh battery:
        # scalar = (15/60) * 1000 * 100 / 60 = 0.25 * 1000 * 100 / 60 = 416.6667
        values = [0.001]
        result = convert_mw_to_percentage_points(
            values,
            timedelta(minutes=15),
            60.0,
            1.0,  # Perfect efficiency to isolate scalar
        )
        expected_scalar = (15 / 60) * 1000 * 100 / 60
        expected_result = 0.001 * expected_scalar * 1.0
        assert abs(result[0] - expected_result) < 0.0001

    def test_maintains_list_order(self):
        """Test that the function maintains the order of input values."""
        values = [0.001, 0.002, 0.003, -0.001, -0.002, 0.004]
        result = convert_mw_to_percentage_points(
            values, timedelta(minutes=15), 60.0, 0.85
        )

        # Check that positive values increase (before applying to negative)
        assert result[0] < result[1] < result[2]
        # Check that negative values maintain order
        assert result[3] > result[4]  # More negative means smaller value
        # Check that last value is largest positive
        assert result[5] > result[2]
