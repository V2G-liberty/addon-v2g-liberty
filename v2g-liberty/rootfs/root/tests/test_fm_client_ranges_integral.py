
####################################### README #######################################
#                                                                                    #
#  This is a unit test for combining possibly overlapping time-value ranges.         #
#  into none-overlapping time-value ranges with min or max value.                    #
#  The aim is testing overall processing via the function consolidate_time_ranges(). #
#                                                                                    #
#  There are two partial test that form the fundament of this test:                  #
#  - test_fm_client_generate_time_slots.py                                           #
#  - test_fm_client_time_slot_to_ranges.py                                           #
#  These separately test the functions that are used here:                           #
#  - __generate_time_slots()                                                         #
#  - __combine_time_slots()                                                          #
#  Those and consolidate_time_ranges() are to be used in the fm_client.py module.    #
#                                                                                    #
######################################################################################

from datetime import datetime, timedelta

c = type('c', (), {'EVENT_RESOLUTION': timedelta(minutes=5)})
DTF = "%H:%M:%S"

class TestGenerateAndConvertTimeSlots():
    def __init__(self):
        self.test_generate_and_convert_time_slots()

    def test_generate_and_convert_time_slots(self):
        # Test cases formatted in HH:MM:SS for readability, code needs true datetime objects.
        test_cases = [
            {
                "description": "Single range",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                ],
                "expected_max": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                ]
            },
            {
                "description": "Non-overlapping ranges",
                # It is not possible yet to correctly process non-overlapping ranges with a one-resolution distance
                "input": [
                    {'start': "00:00:00", 'end': "00:05:00", 'value': 10},
                    {'start': "00:15:00", 'end': "00:15:00", 'value': 5},
                    {'start': "00:25:00", 'end': "00:35:00", 'value': 15},
                ],
                "expected_max": [
                    {'start': "00:00:00", 'end': "00:05:00", 'value': 10},
                    {'start': "00:15:00", 'end': "00:15:00", 'value': 5},
                    {'start': "00:25:00", 'end': "00:35:00", 'value': 15},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:05:00", 'value': 10},
                    {'start': "00:15:00", 'end': "00:15:00", 'value': 5},
                    {'start': "00:25:00", 'end': "00:35:00", 'value': 15},
                ]
            },
            {
                "description": "Overlapping ranges with min and max",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                    {'start': "00:05:00", 'end': "00:15:00", 'value': 3},
                ],
                "expected_max": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                    {'start': "00:10:00", 'end': "00:15:00", 'value': 3},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:05:00", 'value': 5},
                    {'start': "00:05:00", 'end': "00:15:00", 'value': 3},
                ]
            },
            {
                "description": "Contained ranges with varying min and max",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 0},
                    {'start': "00:10:00", 'end': "00:20:00", 'value': 15},
                    {'start': "00:15:00", 'end': "00:30:00", 'value': 25},
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},
                    {'start': "00:50:00", 'end': "01:00:00", 'value': 0},
                    {'start': "00:00:00", 'end': "01:00:00", 'value': 5},
                ],
                "expected_max": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                    {'start': "00:10:00", 'end': "00:15:00", 'value': 15},
                    {'start': "00:15:00", 'end': "00:30:00", 'value': 25},
                    {'start': "00:30:00", 'end': "00:40:00", 'value': 5},
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},
                    {'start': "00:50:00", 'end': "01:00:00", 'value': 5},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 0},
                    {'start': "00:10:00", 'end': "00:50:00", 'value': 5},
                    {'start': "00:50:00", 'end': "01:00:00", 'value': 0},
                ]
            },
        ]

        for case in test_cases:
            print(f"Testing case: {case['description']}")

            # Convert input times into datetime objects
            input_ranges = [
                {
                    'start': datetime.strptime(entry['start'], DTF),
                    'end': datetime.strptime(entry['end'], DTF),
                    'value': entry['value']
                } for entry in case["input"]
            ]

            result_max = consolidate_time_ranges(input_ranges, min_or_max="max")
            result_max = format_ranges(result_max)
            expected_max = strip_ranges_labels(case['expected_max'])

            try:
                assert result_max == expected_max
                print("Max test passed!")
            except AssertionError:
                print(f"Max test failed:")
                print(f"Exp: {expected_max}")
                print(f"Got: {result_max}\n")

            result_min = consolidate_time_ranges(input_ranges, min_or_max="min")
            result_min = format_ranges(result_min)
            expected_min = strip_ranges_labels(case['expected_min'])

            try:
                assert result_min == expected_min
                print("Min test passed!\n")
            except AssertionError:
                print(f"Min test failed:")
                print(f"Exp: {expected_min}")
                print(f"Got: {result_min}\n")


def consolidate_time_ranges(ranges, min_or_max: str = 'max'):
    """
    Make ranges non-overlapping and, for the overlapping parts, use min or max value from the ranges.

    :param ranges: dicts with start(datetime), end(datetime) and value (int)
                   The start and end must be snapped to the resolution.
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values should be used for
                       the overlapping parts.
    :return: a list of dicts with start(datetime), end(datetime) and value (int) that are none-overlapping,
             but possibly 'touching' (end of A = start of B).

    Note!
    It is not possible yet to correctly process non-overlapping ranges with a one-resolution distance
    As this is very rare in this context, and it's impact relatively small it has not been solved yet
    and accepted as a not-perfect output.
    """
    if len(ranges) == 0:
        # self.log("consolidate_time_ranges, ranges = [], aborting")
        return []
    elif len(ranges) == 1:
        # self.log("consolidate_time_ranges, only one range so nothing to consolidate, returning ranges untouched.")
        return ranges

    generated_slots = __generate_time_slots(ranges)
    return __combine_time_slots(generated_slots, min_or_max=min_or_max)


def __generate_time_slots(ranges):
    """
    Based on the ranges this function generates a dictionary of time slots with the minimum or maximum value.
    key: [min, max] where key is a datetime and min/max are int values.
    The key must be snapped to the resolution.
    There can be gaps in the keys, the datetime values do not have to be successive.

    :param ranges: dicts with start(datetime), end(datetime) and value (int)
                   The start and end must be snapped to the resolution.
    :return: dict, with the format:
             key: [min, max] where key is a datetime and min/max are int values
    """
    time_slots = {}
    sorted_ranges = sorted(ranges, key=lambda r: r['start'])

    for time_range in sorted_ranges:
        current_time = time_range['start']
        end_time = time_range['end']
        current_value = time_range['value']

        while current_time <= end_time:
            if current_time not in time_slots:
                time_slots[current_time] = [current_value, current_value]
            else:
                min_value_to_add = min(time_slots[current_time][0], current_value)
                max_value_to_add = max(time_slots[current_time][1], current_value)
                time_slots[current_time] = [min_value_to_add, max_value_to_add]

            current_time += c.EVENT_RESOLUTION

    return time_slots


def __combine_time_slots(time_slots: dict, min_or_max: str = 'max'):
    """
    Merges time slots into ranges with a constant value. The value to use is based on min_or_max parameter.

    :param time_slots: dict, with the format:
                       key: [min, max] where key is a datetime and min/max are int values
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values should be used for
                       the overlapping parts.
    :return: dicts with start(datetime), end(datetime) and value (int) that are none-overlapping,
             but possibly 'touching' (end of A = start of B).
    """

    combined_ranges = []
    sorted_times = sorted(time_slots.keys())

    min_max_index = 1 if min_or_max == 'max' else 0

    # Initialize the first time slot
    current_range_start = sorted_times[0]

    # Choose the first value based on min or max
    current_range_value = time_slots[current_range_start][min_max_index]

    for i in range(1, len(sorted_times)):
        current_time = sorted_times[i]
        expected_time = sorted_times[i - 1] + c.EVENT_RESOLUTION

        # Determine the current value based on min or max
        time_slot_value = time_slots[current_time][min_max_index]

        # If there's a break in the range times, close the current range
        if current_time != expected_time:
            combined_ranges.append({
                'start': current_range_start,
                'end': sorted_times[i - 1],
                'value': current_range_value
            })
            # Start a new range
            current_range_start = current_time
            current_range_value = time_slot_value

        # If there's a break in the range value changes, close the current range
        elif time_slot_value != current_range_value:
            range_end_time = current_time
            if ((min_or_max != 'max' and time_slot_value > current_range_value)
               or (min_or_max == 'max' and time_slot_value < current_range_value)):
                 range_end_time = sorted_times[i - 1]

            combined_ranges.append({
                'start': current_range_start,
                'end': range_end_time,
                'value': current_range_value
            })
            # Start a new range
            current_range_start = range_end_time
            current_range_value = time_slot_value

    # Add the last range
    combined_ranges.append({
        'start': current_range_start,
        'end': sorted_times[-1],
        'value': current_range_value
    })

    return combined_ranges

# Utility functions for pretty printing
def format_ranges(ranges):
    return [(entry['start'].strftime(DTF), entry['end'].strftime(DTF), entry['value']) for entry in ranges]


def strip_ranges_labels(ranges):
    return [(entry['start'], entry['end'], entry['value']) for entry in ranges]


# Run the integrated tests
if __name__ == "__main__":
    TestGenerateAndConvertTimeSlots()
