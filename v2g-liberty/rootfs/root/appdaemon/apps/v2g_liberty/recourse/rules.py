"""The reaction rules FlexMeasures may serve with a schedule.

The design calls them recourse rules. Next to ``values`` a schedule may
carry a ``reaction`` array: one rule per slot, ``{reference, react, band}``.
The rule says how the device may deviate from its plan when the uncontrolled
part of the house differs from what the plan assumed (the surprise)::

    surprise = (meter - realized device power) - reference
    setpoint = p - react * surprise, held inside the band

This module is the contract: the dataclasses, the parser that checks a
schedule's rules against it, and ``react()``, the pure function the engine
applies on every meter sample. The JSON fixtures in
``tests/v2g_liberty/fixtures/fm/`` are the examples the contract is tested
against. Nothing here talks to FlexMeasures or to the charger, and nothing
here keeps state. The flexmeasures-client is to offer the same parser and
``react()`` behind the same names; ours goes when that lands.

The contract, as agreed with Seita (kick-off document, 24 November 2026):

- The opt-in is ``"react": true`` on the device's flex-model item, in the
  trigger or stored on the asset. The flex-context changes not at all.
  Without the opt-in the schedule is today's: no array.
- ``reaction`` has exactly one entry per value, at the schedule's start and
  resolution. An absent array means no rules.
- ``reference``: the uncontrolled power at the connection the plan assumed,
  import positive. A number, never null: 0.0 when there was no forecast.
- ``react``: 0 (follow the plan whatever the house does) or 1 (absorb the
  surprise one to one). Shares in between are reserved for later.
- ``band``: the device powers the slot allows, as ascending ``[lo, hi]``
  ranges. ``null`` on a side means no bound from the planner, which only
  happens for a device without a known capacity. Never empty: a device
  that must follow its plan gets ``[[p, p]]``. A charger with operation
  modes gets a list that leaves out its dead zone.
- Nothing per slot about sensors or retriggers: the meter is the one the
  flex-context already names, and retriggering on a band exit is on by
  default.
- Numbers are plain, in the schedule's ``unit``, at full precision. This
  module converts them to W.
- Signs: device power positive is consumption (charging), meter power
  positive is import.
- A FlexMeasures without the feature rejects ``react`` with HTTP 422, so
  nothing new goes into the trigger until the server version says it can.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

import isodate

_UNIT_TO_W = {"W": 1.0, "kW": 1_000.0, "MW": 1_000_000.0}

# Two edges whose distances to a setpoint differ by less than this are
# equally near: the schedule is served in MW at full precision, the charger
# takes whole watts.
_TIE_W = 0.5


class RulesError(ValueError):
    """The ``reaction`` array of a schedule does not follow the contract."""


@dataclass(frozen=True)
class Band:
    """The device powers a slot allows, in W.

    ``ranges`` are ascending, non-overlapping ``(lo, hi)`` pairs. ``None``
    on a side means no bound from the planner. A charger with operation
    modes gets ``((-7400, -1400), (0, 0), (1400, 7400))``: the dead zone
    between idle and the minimum charge power is not in the band.
    """

    ranges: tuple[tuple[float | None, float | None], ...]

    def contains(self, power_w: float) -> bool:
        return any(
            (lo is None or lo <= power_w) and (hi is None or power_w <= hi)
            for lo, hi in self.ranges
        )

    def nearest(self, power_w: float, plan_w: float) -> float:
        """The allowed power nearest to ``power_w``.

        Inside the band that is ``power_w`` itself. Outside it is the nearest
        edge of the nearest range. When two edges are equally near, in the
        middle of a dead zone, it is the one on the side of the plan.
        """
        if self.contains(power_w):
            return power_w
        best: float | None = None
        best_distance = math.inf
        for lo, hi in self.ranges:
            for edge in (lo, hi):
                if edge is None:
                    continue
                distance = abs(edge - power_w)
                if distance < best_distance - _TIE_W:
                    best, best_distance = edge, distance
                elif abs(distance - best_distance) <= _TIE_W and abs(
                    edge - plan_w
                ) < abs(best - plan_w):
                    best = edge
        if best is None:
            # A band without edges contains everything, so this is only
            # reached for a Band built with no ranges at all.
            return power_w
        return best


@dataclass(frozen=True)
class ReactionRule:
    """What the device may do in one slot.

    All powers in W. ``p_w`` is the planned device power (consumption
    positive). ``reference_w`` is the uncontrolled power at the connection
    the plan assumed (import positive); the surprise is measured against
    it, and it is 0.0 when FlexMeasures had no forecast. ``react`` is 0
    (follow the plan) or 1 (absorb the surprise).
    """

    start: datetime
    p_w: float
    reference_w: float
    react: float
    band: Band


@dataclass(frozen=True)
class ReactionSchedule:
    """The rules of one schedule for one device: a rule per slot, aligned
    with the schedule's ``values``."""

    sensor_id: int
    start: datetime
    resolution: timedelta
    rules: tuple[ReactionRule, ...]

    def __repr__(self) -> str:
        minutes = int(self.resolution.total_seconds()) // 60
        return (
            f"ReactionSchedule(sensor_id={self.sensor_id}, "
            f"start={self.start.isoformat()}, resolution={minutes} min, "
            f"slots={len(self.rules)})"
        )

    def rule_at(self, when: datetime) -> ReactionRule | None:
        """The rule of the slot that contains ``when``, or None when ``when``
        falls outside the schedule."""
        index = math.floor((when - self.start) / self.resolution)
        if 0 <= index < len(self.rules):
            return self.rules[index]
        return None


def parse_reaction(schedule: dict, sensor_id: int) -> ReactionSchedule | None:
    """The reaction rules of a schedule as FlexMeasures serves it, in W.

    Returns None when the schedule carries no ``reaction`` array, which is
    today's normal case. Raises RulesError when the array does not follow
    the contract; what that means for the schedule is the caller's call.
    """
    reaction = schedule.get("reaction")
    if reaction is None:
        return None
    if not isinstance(reaction, list):
        raise RulesError(f"reaction is not a list but {type(reaction).__name__}")

    values = schedule.get("values")
    if not isinstance(values, list) or not values:
        raise RulesError("the schedule has no values")
    if len(reaction) != len(values):
        raise RulesError(f"{len(reaction)} rules for {len(values)} values")

    factor = _unit_factor(schedule.get("unit"))
    try:
        start = isodate.parse_datetime(schedule["start"])
        duration = isodate.parse_duration(schedule["duration"])
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise RulesError(f"the schedule has no usable start and duration: {e}") from e
    resolution = duration / len(values)

    rules = tuple(
        _parse_slot(index, entry, values[index], start + index * resolution, factor)
        for index, entry in enumerate(reaction)
    )
    return ReactionSchedule(
        sensor_id=sensor_id, start=start, resolution=resolution, rules=rules
    )


def react(rule: ReactionRule, surprise_w: float) -> float:
    """The setpoint for the device under ``rule``, in W.

    ``surprise_w`` is the measured uncontrolled power minus the reference,
    import positive. The setpoint is the plan minus the share of the
    surprise the rule says to absorb, held inside the band. Pure: the
    engine adds the sampling, the dwell and the hysteresis around it.
    """
    return rule.band.nearest(rule.p_w - rule.react * surprise_w, rule.p_w)


def _unit_factor(unit) -> float:
    try:
        return _UNIT_TO_W[unit]
    except (KeyError, TypeError):
        raise RulesError(f"unknown unit {unit!r}") from None


def _parse_slot(
    index: int, entry, value, start: datetime, factor: float
) -> ReactionRule:
    what = f"slot {index}"
    if not isinstance(entry, dict):
        raise RulesError(f"{what}: the rule is not an object")
    missing = [key for key in ("reference", "react", "band") if key not in entry]
    if missing:
        raise RulesError(f"{what}: the rule lacks {', '.join(missing)}")
    return ReactionRule(
        start=start,
        p_w=_number(value, f"{what}: value") * factor,
        reference_w=_number(entry["reference"], f"{what}: reference") * factor,
        react=_number(entry["react"], f"{what}: react"),
        band=_parse_band(entry["band"], factor, f"{what}: band"),
    )


def _number(raw, what: str) -> float:
    # A bool is an int in Python; true and false are not numbers in JSON.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RulesError(f"{what} is not a number but {raw!r}")
    return float(raw)


def _parse_band(raw, factor: float, what: str) -> Band:
    if not isinstance(raw, list) or not raw:
        raise RulesError(f"{what} is not a non-empty list of ranges")
    ranges: list[tuple[float | None, float | None]] = []
    previous_max: float | None = None
    for position, pair in enumerate(raw):
        if not isinstance(pair, list) or len(pair) != 2:
            raise RulesError(f"{what}: range {position} is not a [lo, hi] pair")
        lo = _side(pair[0], factor, f"{what}: range {position} lo")
        hi = _side(pair[1], factor, f"{what}: range {position} hi")
        if lo is not None and hi is not None and lo > hi:
            raise RulesError(f"{what}: range {position} has its lo above its hi")
        if position > 0 and (previous_max is None or lo is None or lo < previous_max):
            raise RulesError(f"{what}: ranges are not ascending and non-overlapping")
        ranges.append((lo, hi))
        previous_max = hi
    return Band(tuple(ranges))


def _side(raw, factor: float, what: str) -> float | None:
    if raw is None:
        return None
    return _number(raw, what) * factor
