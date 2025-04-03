from datetime import datetime, timedelta


def consolidate_time_ranges(
    ranges, event_resolution: timedelta, min_or_max: str = "max"
):
    """
    Make ranges non-overlapping and, for the overlapping parts, use min/max value from the ranges.

    :param ranges: dicts with start(datetime), end(datetime) and value (int)
                   The start and end must be snapped to the resolution.
    :param event_resolution: A time_delta (usually 5 min).
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values
                       should be used for the overlapping parts.
    :return: a list of dicts with start(datetime), end(datetime) and value (int) that are
             none-overlapping, but possibly 'touching' (end of A = start of B).

    Note!
    It is not possible yet to correctly process non-overlapping ranges with a one-resolution
    distance. As this is very rare in this context, and it's impact relatively small it has not been
    solved yet and accepted as a not-perfect output.
    """
    if len(ranges) == 0:
        return []
    elif len(ranges) == 1:
        return ranges

    generated_slots = _generate_time_slots(ranges, event_resolution)
    return _combine_time_slots(generated_slots, event_resolution, min_or_max=min_or_max)


def _generate_time_slots(ranges, event_resolution: timedelta):
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


def _combine_time_slots(
    time_slots: dict, event_resolution: timedelta, min_or_max: str = "max"
):
    """
    Merges time slots into ranges with a constant value. The value to use is based on min_or_max
    parameter.

    :param time_slots: dict, with the format:
                       key: [min, max] where key is a datetime and min/max are int values
    :param min_or_max: str, "min" or "max" (default) to indicate if the minimum or maximum values
                       should be used for the overlapping parts.
    :return: dicts with start(datetime), end(datetime) and value (int) that are none-overlapping,
             but possibly 'touching' (end of A = start of B).
    """

    combined_ranges = []
    sorted_times = sorted(time_slots.keys())

    min_max_index = 1 if min_or_max == "max" else 0

    # Initialize the first time slot
    current_range_start = sorted_times[0]

    # Choose the first value based on min or max
    current_range_value = time_slots[current_range_start][min_max_index]

    for i in range(1, len(sorted_times)):
        current_time = sorted_times[i]
        expected_time = sorted_times[i - 1] + event_resolution

        # Determine the current value based on min or max
        time_slot_value = time_slots[current_time][min_max_index]

        # If there's a break in the range times, close the current range
        if current_time != expected_time:
            combined_ranges.append(
                {
                    "start": current_range_start,
                    "end": sorted_times[i - 1],
                    "value": current_range_value,
                }
            )
            # Start a new range
            current_range_start = current_time
            current_range_value = time_slot_value

        # If there's a break in the range value changes, close the current range
        elif time_slot_value != current_range_value:
            range_end_time = current_time
            if (min_or_max != "max" and time_slot_value > current_range_value) or (
                min_or_max == "max" and time_slot_value < current_range_value
            ):
                range_end_time = sorted_times[i - 1]

            combined_ranges.append(
                {
                    "start": current_range_start,
                    "end": range_end_time,
                    "value": current_range_value,
                }
            )
            # Start a new range
            current_range_start = range_end_time
            current_range_value = time_slot_value

    # Add the last range
    combined_ranges.append(
        {
            "start": current_range_start,
            "end": sorted_times[-1],
            "value": current_range_value,
        }
    )

    return combined_ranges


def convert_dates_to_iso_format(data):
    for entry in data:
        dts = entry.get("start", None)
        if dts is not None and isinstance(dts, datetime):
            entry["start"] = dts.isoformat()
        dte = entry.get("end", None)
        if dte is not None and isinstance(dte, datetime):
            entry["end"] = dte.isoformat()
    return data


def add_unit_to_values(data, unit: str):
    for entry in data:
        value = entry.get("value", None)
        if value is not None:
            entry["value"] = f"{value} {unit}"
    return data
