from datetime import timedelta

def generate_time_slots(ranges, event_resolution: timedelta):
    """
    Based on the ranges this function generates a dictionary of time slots with the minimum or
    maximum value.

    :param ranges: dicts with start(datetime), end(datetime) and value (int)
                   The start and end must be snapped to the resolution.
    :return: dict, with the format:
             key: [min, max] where key is a datetime and min/max are int values
             The key must be snapped to the resolution. There can be gaps in the keys, the datetime
             values do not have to be successive.
    """
    time_slots = {}
    sorted_ranges = sorted(ranges, key=lambda r: r["start"])

    for time_range in sorted_ranges:
        current_time = time_range["start"]
        end_time = time_range["end"]
        current_value = time_range["value"]

        while current_time <= end_time:
            if current_time not in time_slots:
                time_slots[current_time] = [current_value, current_value]
            else:
                min_value_to_add = min(time_slots[current_time][0], current_value)
                max_value_to_add = max(time_slots[current_time][1], current_value)
                time_slots[current_time] = [min_value_to_add, max_value_to_add]

            current_time += event_resolution

    return time_slots

