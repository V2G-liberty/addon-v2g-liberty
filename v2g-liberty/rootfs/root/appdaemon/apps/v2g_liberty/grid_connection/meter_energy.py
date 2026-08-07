"""Pure helpers to turn cumulative meter register readings into per-interval
energy (kWh).

The smart meter's import/export registers are ``total_increasing``
(cumulative). The energy used in an interval is the difference between the
register total at the interval boundary and at the previous boundary. This
module holds the unit normalisation and the delta logic (including
meter-reset and unavailable handling); ``data_monitor`` does the stateful
boundary sampling and persistence.
"""

_UNIT_TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}


def normalise_to_kwh(value, unit: str) -> float | None:
    """Convert a single energy reading to kWh.

    Returns None when the value is not numeric or the unit is unknown — the
    caller treats a None reading as unavailable and skips the interval.
    """
    factor = _UNIT_TO_KWH.get(unit)
    if factor is None:
        return None
    try:
        return float(value) * factor
    except (TypeError, ValueError):
        return None


def sum_register_readings(readings: list) -> float | None:
    """Sum a set of kWh-normalised register readings (tariff registers).

    Returns None when the list is empty or any reading is missing. A partial
    sum would corrupt the interval delta (e.g. a tariff register going
    unavailable would look like a drop), so the whole interval is skipped.
    """
    if not readings or any(r is None for r in readings):
        return None
    return sum(readings)


def interval_delta(current, previous) -> tuple:
    """Energy (kWh) consumed between two cumulative boundary readings.

    Returns ``(delta_kwh, is_reset)``:
    - ``previous is None`` (first interval / no baseline) → ``(None, False)``.
    - ``current is None`` (unavailable) → ``(None, False)``; the caller keeps
      the previous baseline, so the next successful read spans the gap and no
      energy is lost.
    - ``current < previous`` (meter reset/replacement) → ``(None, True)``; the
      interval's true energy is unknowable, so it is skipped and the caller
      re-baselines.
    - otherwise → ``(current - previous, False)``.

    The caller advances its baseline to ``current`` whenever ``current`` is not
    None (both the normal and reset cases).
    """
    if current is None or previous is None:
        return None, False
    if current < previous:
        return None, True
    return round(current - previous, 6), False
