
####################################### README #######################################
#                                                                                    #
#  This is a separate unit test for __combine_time_slots() function.                 #
#  The function is an exact copy of the one in test_fm_client-ranges_integral.py,    #
#  and as such this test forms a fundament for the integral test.                    #
#                                                                                    #
######################################################################################

from datetime import datetime, timedelta

c = type('c', (), {'EVENT_RESOLUTION': timedelta(minutes=5)})
DTF = "%H:%M:%S"

class TestConvertTimeSlotsToRanges():
    def __init__(self):
        self.test_convert_time_slots_to_ranges()


    def test_convert_time_slots_to_ranges(self):
        test_cases = [
            {
                "description": "Contained with max base",
                "input":{
                    '00:00:00': [35, 35],
                    '00:05:00': [35, 35],
                    '00:10:00': [15, 35],
                    '00:15:00': [15, 35],
                    '00:20:00': [15, 35],
                    '00:25:00': [25, 35],
                    '00:30:00': [25, 35],
                    '00:35:00': [35, 35],
                    '00:40:00': [20, 35],
                    '00:45:00': [20, 35],
                    '00:50:00': [20, 35],
                    '00:55:00': [35, 35],
                    '01:00:00': [35, 35],
                },
                "expected_max": [
                    {'start': "00:00:00", 'end': "01:00:00", 'value': 35},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 35},
                    {'start': "00:10:00", 'end': "00:20:00", 'value': 15},
                    {'start': "00:20:00", 'end': "00:30:00", 'value': 25},
                    {'start': "00:30:00", 'end': "00:40:00", 'value': 35},
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},
                    {'start': "00:50:00", 'end': "01:00:00", 'value': 35},
                ]
            },
            # 2. Variants with higher/lower/equal values over longer time frames
            {
                "description": "Different value comparisons",
                "input": {
                    '00:00:00': [3, 5],
                    '00:05:00': [3, 5],
                    '00:10:00': [3, 5],
                    '00:15:00': [3, 3],
                },
                "expected_max": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},  # Take max
                    {'start': "00:10:00", 'end': "00:15:00", 'value': 3},
                ],
                "expected_min": [
                    {'start': "00:00:00", 'end': "00:15:00", 'value': 3 },  # Take min
                ]
            },
            # 3. Non-overlapping with equal values but longer ranges
            {
                "description": " Non-overlapping",
                "input":{
                    '00:05:00': [35, 35],
                    '00:10:00': [35, 35],
                    '00:20:00': [40, 40],
                    '00:40:00': [20, 20],
                    '00:45:00': [20, 20],
                    '00:50:00': [20, 20],
                },
                "expected_max": [
                    {'start': "00:05:00", 'end': "00:10:00", 'value': 35},
                    {'start': "00:20:00", 'end': "00:20:00", 'value': 40},
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},
                ],
                "expected_min": [
                    {'start': "00:05:00", 'end': "00:10:00", 'value': 35},
                    {'start': "00:20:00", 'end': "00:20:00", 'value': 40},
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},
                ]
            },
            # 4. Contained with varying min and max
            # For convenience, these are the ranges that the timeslots input is based upon.
            # {'start': "00:00:00", 'end': "01:00:00", 'value': 5},  # Container
            # {'start': "00:00:00", 'end': "00:10:00", 'value': 0},  # Contained
            # {'start': "00:10:00", 'end': "00:20:00", 'value': 15},  # Contained
            # {'start': "00:15:00", 'end': "00:30:00", 'value': 25},  # Contained
            # {'start': "00:40:00", 'end': "00:50:00", 'value': 20},  # Contained
            # {'start': "00:50:00", 'end': "01:00:00", 'value': 0},  # Contained
            {
                "description": "Contained with varying min and max",
                "input":{
                    '00:00:00': [0, 5],
                    '00:05:00': [0, 5],
                    '00:10:00': [0, 15],
                    '00:15:00': [5, 25],
                    '00:20:00': [5, 25],
                    '00:25:00': [5, 25],
                    '00:30:00': [5, 25],
                    '00:35:00': [5, 5],
                    '00:40:00': [5, 20],
                    '00:45:00': [5, 20],
                    '00:50:00': [0, 20],
                    '00:55:00': [0, 5],
                    '01:00:00': [0, 5],
                },
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
            }
        ]

        for case in test_cases:
            print(f"Testing case: {case['description']}")

            input_slots = {
                datetime.strptime(key, DTF): value for key, value in case["input"].items()
            }

            result_max = combine_time_slots(input_slots, min_or_max="max")
            result_max = format_ranges(result_max)
            expected_max = strip_ranges_labels(case['expected_max'])
            try:
                assert result_max == expected_max
                print("Max test passed!")
            except AssertionError:
                print(f"Max test failed:")
                print(f"Exp: {expected_max}")
                print(f"Got: {result_max}")
                print()

            result_min = combine_time_slots(input_slots, min_or_max="min")
            result_min = format_ranges(result_min)
            expected_min = strip_ranges_labels(case['expected_min'])
            try:
                assert result_min == expected_min
                print("Min test passed!\n")
            except AssertionError:
                print(f"Min test failed:")
                print(f"Exp: {expected_min}")
                print(f"Got: {result_min}\n")
                print()


def combine_time_slots(time_slots: dict, min_or_max: str = 'max'):
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


# Utility function for pretty printing
def format_ranges(ranges):
    return [(entry['start'].strftime(DTF), entry['end'].strftime(DTF), entry['value']) for entry in ranges]

def strip_ranges_labels(ranges):
    return [(entry['start'], entry['end'], entry['value']) for entry in ranges]


# Run the tests
if __name__ == "__main__":
    TestConvertTimeSlotsToRanges()
