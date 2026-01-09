from datetime import timedelta


def convert_mw_to_percentage_points(
    values_in_mw: list[float],
    resolution: timedelta,
    max_soc_in_kwh: float,
    round_trip_efficiency: float,
) -> list[float]:
    """
    Convert a list of power values (in MW) to percentage points of SOC.

    For example, if a 62 kWh battery charges at 0.00575 MW for a period of 15 minutes,
    its SoC increases by ≈ 2.13%-point (when rte=0.85).

    Args:
        values_in_mw: List of power values in megawatts (MW).
        resolution: Time step between each value (timedelta).
        max_soc_in_kwh: Maximum battery capacity in kilowatt-hours (kWh).
        round_trip_efficiency: Efficiency factor (0.0 to 1.0).

    Returns:
        List of percentage point changes for each time step.
    """
    e = round_trip_efficiency**0.5
    scalar = resolution / timedelta(hours=1) * 1000 * 100 / max_soc_in_kwh
    lst = []
    for v in values_in_mw:
        if v >= 0:
            lst.append(v * scalar * e)
        else:
            lst.append(v * scalar)
    return lst
