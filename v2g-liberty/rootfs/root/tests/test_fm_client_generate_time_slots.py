
####################################### README #######################################
#                                                                                    #
#  This is a separate unit test for __generate_time_slots().                         #
#  The function is an exact copy of the one in test_fm_client-ranges_integral.py,    #
#  and as such this test forms a fundament for the integral test.                    #
#                                                                                    #
######################################################################################

from datetime import datetime, timedelta

c = type('c', (), {'EVENT_RESOLUTION': timedelta(minutes=5)})
DTF = "%H:%M:%S"


class TestGenerateTimeSlots():
    def __init__(self):
        self.test_generate_time_slots()

    def test_generate_time_slots(self):
        test_cases = [
            {
                "description": "Single range",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                ],
                "expected": {
                    '00:00:00': [5, 5],
                    '00:05:00': [5, 5],
                    '00:10:00': [5, 5],
                }
            },
            {
                "description": "Non-overlapping ranges with one-resolution distance",
                "input": [
                    {'start': "00:00:00", 'end': "00:05:00", 'value': 10},
                    {'start': "00:10:00", 'end': "00:15:00", 'value': 5},
                    {'start': "00:25:00", 'end': "00:35:00", 'value': 15},
                ],
                "expected": {
                    '00:00:00': [10, 10],
                    '00:05:00': [10, 10],
                    '00:10:00': [5, 5],
                    '00:15:00': [5, 5],
                    '00:25:00': [15, 15],
                    '00:30:00': [15, 15],
                    '00:35:00': [15, 15],
                }
            },
            {
                "description": "Touching ranges with max value",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 10},
                    {'start': "00:10:00", 'end': "00:20:00", 'value': 15},
                ],
                "expected": {
                    '00:00:00': [10, 10],
                    '00:05:00': [10, 10],
                    '00:10:00': [10, 15],
                    '00:15:00': [15, 15],
                    '00:20:00': [15, 15],
                }
            },
            {
                "description": "Overlapping ranges with min value",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 5},
                    {'start': "00:05:00", 'end': "00:15:00", 'value': 3},
                ],
                "expected": {
                    '00:00:00': [5, 5],
                    '00:05:00': [3, 5],
                    '00:10:00': [3, 5],
                    '00:15:00': [3, 3],
                }
            },
            {
                "description": "Contained ranges",
                "input": [
                    {'start': "00:10:00", 'end': "00:20:00", 'value': 15},  # Contained
                    {'start': "00:15:00", 'end': "00:30:00", 'value': 25},  # Contained
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},  # Contained
                    {'start': "00:00:00", 'end': "01:00:00", 'value': 35},  # Container
                ],
                "expected": {
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
                }
            },
            {
                "description": "Contained with varying min and max",
                "input": [
                    {'start': "00:00:00", 'end': "00:10:00", 'value': 0},  # Contained
                    {'start': "00:10:00", 'end': "00:20:00", 'value': 15},  # Contained
                    {'start': "00:15:00", 'end': "00:30:00", 'value': 25},  # Contained
                    {'start': "00:40:00", 'end': "00:50:00", 'value': 20},  # Contained
                    {'start': "00:50:00", 'end': "01:00:00", 'value': 0},  # Contained
                    {'start': "00:00:00", 'end': "01:00:00", 'value': 5},  # Container
                ],
                "expected": {
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
                }
            }
        ]

        for case in test_cases:
            print(f"Testing generate_time_slots: {case['description']}")
            input_ranges = [
                {
                    'start': datetime.strptime(entry['start'], DTF),
                    'end': datetime.strptime(entry['end'], DTF),
                    'value': entry['value']
                } for entry in case["input"]
            ]

            result = generate_time_slots(input_ranges)
            result = format_time_slot(result)
            try:
                assert result == case['expected']
                print("Passed!\n")
            except AssertionError:
                print(f"Test failed for '{case['description']}':")
                print(f"Exp: {case['expected']}")
                print(f"Got: {result} \n \n")


def generate_time_slots(ranges):
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


def format_time_slot(time_slot):
    return {k.strftime(DTF): [v] if isinstance(v, int) else v for k, v in time_slot.items()}


def format_ranges(ranges):
    return [(entry['start'].strftime(DTF), entry['end'].strftime(DTF), entry['value']) for entry in ranges]


# Run the tests
if __name__ == "__main__":
    TestGenerateTimeSlots()
