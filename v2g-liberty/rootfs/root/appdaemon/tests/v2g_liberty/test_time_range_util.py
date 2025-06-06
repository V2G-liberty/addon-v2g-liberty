"""Unit test (pytest) for time_range_util module."""

from datetime import datetime, timedelta
import pytest
from apps.v2g_liberty.time_range_util import (
    _generate_time_slots,
    _combine_time_slots,
    consolidate_time_ranges,
)

EVENT_RESOLUTION = timedelta(minutes=5)
DTF = "%H:%M:%S"


class TestGenerateTimeSlots:
    @pytest.mark.parametrize(
        "description, input_ranges, expected",
        [
            (
                "Single range",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                ],
                {
                    "00:00:00": [5, 5],
                    "00:05:00": [5, 5],
                    "00:10:00": [5, 5],
                },
            ),
            (
                "Non-overlapping ranges with one-resolution distance",
                [
                    {"start": "00:00:00", "end": "00:05:00", "value": 10},
                    {"start": "00:10:00", "end": "00:15:00", "value": 5},
                    {"start": "00:25:00", "end": "00:35:00", "value": 15},
                ],
                {
                    "00:00:00": [10, 10],
                    "00:05:00": [10, 10],
                    "00:10:00": [5, 5],
                    "00:15:00": [5, 5],
                    "00:25:00": [15, 15],
                    "00:30:00": [15, 15],
                    "00:35:00": [15, 15],
                },
            ),
            (
                "Touching ranges with max value",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 10},
                    {"start": "00:10:00", "end": "00:20:00", "value": 15},
                ],
                {
                    "00:00:00": [10, 10],
                    "00:05:00": [10, 10],
                    "00:10:00": [10, 15],
                    "00:15:00": [15, 15],
                    "00:20:00": [15, 15],
                },
            ),
            (
                "Overlapping ranges with min value",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:05:00", "end": "00:15:00", "value": 3},
                ],
                {
                    "00:00:00": [5, 5],
                    "00:05:00": [3, 5],
                    "00:10:00": [3, 5],
                    "00:15:00": [3, 3],
                },
            ),
            (
                "Contained ranges",
                [
                    {"start": "00:10:00", "end": "00:20:00", "value": 15},  # Contained
                    {"start": "00:15:00", "end": "00:30:00", "value": 25},  # Contained
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},  # Contained
                    {"start": "00:00:00", "end": "01:00:00", "value": 35},  # Container
                ],
                {
                    "00:00:00": [35, 35],
                    "00:05:00": [35, 35],
                    "00:10:00": [15, 35],
                    "00:15:00": [15, 35],
                    "00:20:00": [15, 35],
                    "00:25:00": [25, 35],
                    "00:30:00": [25, 35],
                    "00:35:00": [35, 35],
                    "00:40:00": [20, 35],
                    "00:45:00": [20, 35],
                    "00:50:00": [20, 35],
                    "00:55:00": [35, 35],
                    "01:00:00": [35, 35],
                },
            ),
            (
                "Contained with varying min and max",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 0},  # Contained
                    {"start": "00:10:00", "end": "00:20:00", "value": 15},  # Contained
                    {"start": "00:15:00", "end": "00:30:00", "value": 25},  # Contained
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},  # Contained
                    {"start": "00:50:00", "end": "01:00:00", "value": 0},  # Contained
                    {"start": "00:00:00", "end": "01:00:00", "value": 5},  # Container
                ],
                {
                    "00:00:00": [0, 5],
                    "00:05:00": [0, 5],
                    "00:10:00": [0, 15],
                    "00:15:00": [5, 25],
                    "00:20:00": [5, 25],
                    "00:25:00": [5, 25],
                    "00:30:00": [5, 25],
                    "00:35:00": [5, 5],
                    "00:40:00": [5, 20],
                    "00:45:00": [5, 20],
                    "00:50:00": [0, 20],
                    "00:55:00": [0, 5],
                    "01:00:00": [0, 5],
                },
            ),
        ],
    )
    def test_generate_time_slots(self, description, input_ranges, expected):
        """Docstring"""
        input_ranges = [
            {
                "start": datetime.strptime(entry["start"], DTF),
                "end": datetime.strptime(entry["end"], DTF),
                "value": entry["value"],
            }
            for entry in input_ranges
        ]

        result = _generate_time_slots(input_ranges, EVENT_RESOLUTION)
        result = format_time_slot(result)

        assert result == expected, f"Test failed for '{description}': {result}"


class TestCombineTimeSlots:
    """DocString"""

    @pytest.mark.parametrize(
        "description, input_slots, expected_max, expected_min",
        [
            (
                "Contained with max base",
                {
                    "00:00:00": [35, 35],
                    "00:05:00": [35, 35],
                    "00:10:00": [15, 35],
                    "00:15:00": [15, 35],
                    "00:20:00": [15, 35],
                    "00:25:00": [25, 35],
                    "00:30:00": [25, 35],
                    "00:35:00": [35, 35],
                    "00:40:00": [20, 35],
                    "00:45:00": [20, 35],
                    "00:50:00": [20, 35],
                    "00:55:00": [35, 35],
                    "01:00:00": [35, 35],
                },
                [
                    {"start": "00:00:00", "end": "01:00:00", "value": 35},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 35},
                    {"start": "00:10:00", "end": "00:20:00", "value": 15},
                    {"start": "00:20:00", "end": "00:30:00", "value": 25},
                    {"start": "00:30:00", "end": "00:40:00", "value": 35},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                    {"start": "00:50:00", "end": "01:00:00", "value": 35},
                ],
            ),
            (
                "Different value comparisons",
                {
                    "00:00:00": [3, 5],
                    "00:05:00": [3, 5],
                    "00:10:00": [3, 5],
                    "00:15:00": [3, 3],
                },
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:10:00", "end": "00:15:00", "value": 3},
                ],
                [
                    {"start": "00:00:00", "end": "00:15:00", "value": 3},
                ],
            ),
            (
                "Non-overlapping",
                {
                    "00:05:00": [35, 35],
                    "00:10:00": [35, 35],
                    "00:20:00": [40, 40],
                    "00:40:00": [20, 20],
                    "00:45:00": [20, 20],
                    "00:50:00": [20, 20],
                },
                [
                    {"start": "00:05:00", "end": "00:10:00", "value": 35},
                    {"start": "00:20:00", "end": "00:20:00", "value": 40},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                ],
                [
                    {"start": "00:05:00", "end": "00:10:00", "value": 35},
                    {"start": "00:20:00", "end": "00:20:00", "value": 40},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                ],
            ),
            (
                "Contained with varying min and max",
                {
                    "00:00:00": [0, 5],
                    "00:05:00": [0, 5],
                    "00:10:00": [0, 15],
                    "00:15:00": [5, 25],
                    "00:20:00": [5, 25],
                    "00:25:00": [5, 25],
                    "00:30:00": [5, 25],
                    "00:35:00": [5, 5],
                    "00:40:00": [5, 20],
                    "00:45:00": [5, 20],
                    "00:50:00": [0, 20],
                    "00:55:00": [0, 5],
                    "01:00:00": [0, 5],
                },
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:10:00", "end": "00:15:00", "value": 15},
                    {"start": "00:15:00", "end": "00:30:00", "value": 25},
                    {"start": "00:30:00", "end": "00:40:00", "value": 5},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                    {"start": "00:50:00", "end": "01:00:00", "value": 5},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 0},
                    {"start": "00:10:00", "end": "00:50:00", "value": 5},
                    {"start": "00:50:00", "end": "01:00:00", "value": 0},
                ],
            ),
        ],
    )
    def test_combine_time_slots(
        self, description, input_slots, expected_max, expected_min
    ):
        """DocString"""
        input_slots = {
            datetime.strptime(key, DTF): value for key, value in input_slots.items()
        }

        result_max = _combine_time_slots(
            input_slots, EVENT_RESOLUTION, min_or_max="max"
        )
        result_max = format_ranges(result_max)
        expected_max = strip_ranges_labels(expected_max)

        assert result_max == expected_max, (
            f"Max test failed for '{description}': {result_max}"
        )

        result_min = _combine_time_slots(
            input_slots, EVENT_RESOLUTION, min_or_max="min"
        )
        result_min = format_ranges(result_min)
        expected_min = strip_ranges_labels(expected_min)

        assert result_min == expected_min, (
            f"Min test failed for '{description}': {result_min}"
        )


class TestConsolidateTimeRanges:
    @pytest.mark.parametrize(
        "description, input_ranges, expected_max, expected_min",
        [
            (
                "Single range",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                ],
            ),
            (
                "Non-overlapping ranges",
                [
                    {"start": "00:00:00", "end": "00:05:00", "value": 10},
                    {"start": "00:15:00", "end": "00:15:00", "value": 5},
                    {"start": "00:25:00", "end": "00:35:00", "value": 15},
                ],
                [
                    {"start": "00:00:00", "end": "00:05:00", "value": 10},
                    {"start": "00:15:00", "end": "00:15:00", "value": 5},
                    {"start": "00:25:00", "end": "00:35:00", "value": 15},
                ],
                [
                    {"start": "00:00:00", "end": "00:05:00", "value": 10},
                    {"start": "00:15:00", "end": "00:15:00", "value": 5},
                    {"start": "00:25:00", "end": "00:35:00", "value": 15},
                ],
            ),
            (
                "Overlapping ranges with min and max",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:05:00", "end": "00:15:00", "value": 3},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:10:00", "end": "00:15:00", "value": 3},
                ],
                [
                    {"start": "00:00:00", "end": "00:05:00", "value": 5},
                    {"start": "00:05:00", "end": "00:15:00", "value": 3},
                ],
            ),
            (
                "Contained ranges with varying min and max",
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 0},
                    {"start": "00:10:00", "end": "00:20:00", "value": 15},
                    {"start": "00:15:00", "end": "00:30:00", "value": 25},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                    {"start": "00:50:00", "end": "01:00:00", "value": 0},
                    {"start": "00:00:00", "end": "01:00:00", "value": 5},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 5},
                    {"start": "00:10:00", "end": "00:15:00", "value": 15},
                    {"start": "00:15:00", "end": "00:30:00", "value": 25},
                    {"start": "00:30:00", "end": "00:40:00", "value": 5},
                    {"start": "00:40:00", "end": "00:50:00", "value": 20},
                    {"start": "00:50:00", "end": "01:00:00", "value": 5},
                ],
                [
                    {"start": "00:00:00", "end": "00:10:00", "value": 0},
                    {"start": "00:10:00", "end": "00:50:00", "value": 5},
                    {"start": "00:50:00", "end": "01:00:00", "value": 0},
                ],
            ),
        ],
    )
    def test_consolidate_time_ranges(
        self, description, input_ranges, expected_max, expected_min
    ):
        """Convert input times into datetime objects"""
        input_ranges = [
            {
                "start": datetime.strptime(entry["start"], DTF),
                "end": datetime.strptime(entry["end"], DTF),
                "value": entry["value"],
            }
            for entry in input_ranges
        ]

        result_max = consolidate_time_ranges(
            input_ranges, EVENT_RESOLUTION, min_or_max="max"
        )
        result_max = format_ranges(result_max)
        expected_max = strip_ranges_labels(expected_max)

        assert result_max == expected_max, (
            f"Max test failed for '{description}': {result_max}"
        )

        result_min = consolidate_time_ranges(
            input_ranges, EVENT_RESOLUTION, min_or_max="min"
        )
        result_min = format_ranges(result_min)
        expected_min = strip_ranges_labels(expected_min)

        assert result_min == expected_min, (
            f"Min test failed for '{description}': {result_min}"
        )


def format_time_slot(time_slot):
    return {
        k.strftime(DTF): [v] if isinstance(v, int) else v for k, v in time_slot.items()
    }


def format_ranges(ranges):
    return [
        {
            "start": entry["start"].strftime(DTF),
            "end": entry["end"].strftime(DTF),
            "value": entry["value"],
        }
        for entry in ranges
    ]


def strip_ranges_labels(ranges):
    return [
        {"start": entry["start"], "end": entry["end"], "value": entry["value"]}
        for entry in ranges
    ]
