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
  surprise one to one). Shares in between are reserved for later; the
  parser accepts [0, 1] and refuses anything outside, so a sign error on
  either side can never amplify a surprise.
- ``band``: the device powers the slot allows, as ascending ``[lo, hi]``
  ranges of finite numbers. The planner requires a power capacity for
  every device, so it never serves an open (``null``) side; one is refused,
  as the client does. Never empty: a device that must follow its plan gets
  ``[[p, p]]`` with ``react`` 0, which is also what a band that leaves no
  room comes with. A charger with operation modes gets a list that leaves
  out its dead zone.
- Nothing per slot about sensors or retriggers: the meter is the one the
  flex-context already names, and retriggering on a band exit is on by
  default.
- The plan lies inside its own band: with no surprise the device follows
  its plan exactly. The parser enforces it, with half a watt of tolerance
  for rounding between ``values`` and the band's edges.
- Numbers are plain and finite, in the schedule's ``unit``, at full
  precision. This module converts them to W.
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


class InvalidReactionError(ValueError):
    """The ``reaction`` array of a schedule does not follow the contract.
    The client raises an error of the same name."""


@dataclass(frozen=True)
class Band:
    """The device powers a slot allows, in W.

    ``ranges`` are ascending ``(lo, hi)`` pairs of finite numbers; a range
    may touch the next but not overlap it, and there is always at least
    one. A charger with operation modes gets
    ``((-7400, -1400), (0, 0), (1400, 7400))``: the dead zone between idle
    and the minimum charge power is not in the band.
    """

    ranges: tuple[tuple[float, float], ...]

    def contains(self, power_w: float) -> bool:
        return any(lo <= power_w <= hi for lo, hi in self.ranges)

    def nearest(self, power_w: float, plan_w: float) -> float:
        """The allowed power nearest to ``power_w``.

        Inside the band that is ``power_w`` itself. Outside it is the nearest
        edge of the nearest range. When two edges are equally near, in the
        middle of a dead zone, it is the one on the side of the plan.
        """
        if self.contains(power_w):
            return power_w
        best = self.ranges[0][0]
        best_distance = math.inf
        for lo, hi in self.ranges:
            for edge in (lo, hi):
                distance = abs(edge - power_w)
                if distance < best_distance - _TIE_W:
                    best, best_distance = edge, distance
                elif abs(distance - best_distance) <= _TIE_W and abs(
                    edge - plan_w
                ) < abs(best - plan_w):
                    best = edge
        return best


@dataclass(frozen=True)
class ReactionRule:
    """What the device may do in one slot.

    All powers in W. ``p_w`` is the planned device power (consumption
    positive). ``reference_w`` is the uncontrolled power at the connection
    the plan assumed (import positive); the surprise is measured against
    it, and it is 0.0 when FlexMeasures had no forecast. ``react`` is 0
    (follow the plan), 1 (absorb the surprise) or a share in between.
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
        falls outside the schedule. ``when`` must carry a timezone, like the
        schedule's start; a naive datetime raises TypeError."""
        index = math.floor((when - self.start) / self.resolution)
        if 0 <= index < len(self.rules):
            return self.rules[index]
        return None


def parse_reaction(schedule: dict, sensor_id: int) -> ReactionSchedule | None:
    """The reaction rules of a schedule as FlexMeasures serves it, in W.

    Returns None when the schedule carries no ``reaction`` array, which is
    today's normal case. Raises InvalidReactionError when the array does not follow
    the contract; what that means for the schedule is the caller's call.
    """
    reaction = schedule.get("reaction")
    if reaction is None:
        return None
    if not isinstance(reaction, list):
        raise InvalidReactionError(
            f"reaction is not a list but {type(reaction).__name__}"
        )

    values = schedule.get("values")
    if not isinstance(values, list) or not values:
        raise InvalidReactionError("the schedule has no values")
    if len(reaction) != len(values):
        raise InvalidReactionError(f"{len(reaction)} rules for {len(values)} values")

    factor = _unit_factor(schedule.get("unit"))
    try:
        start = isodate.parse_datetime(schedule["start"])
        duration = isodate.parse_duration(schedule["duration"])
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        raise InvalidReactionError(
            f"the schedule has no usable start and duration: {e}"
        ) from e
    if not isinstance(duration, timedelta):
        # isodate gives a Duration for months and years; no slot length
        # follows from that.
        raise InvalidReactionError("the schedule's duration is not a fixed length")
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
        raise InvalidReactionError(f"unknown unit {unit!r}") from None


def _parse_slot(
    index: int, entry, value, start: datetime, factor: float
) -> ReactionRule:
    what = f"slot {index}"
    if not isinstance(entry, dict):
        raise InvalidReactionError(f"{what}: the rule is not an object")
    missing = [key for key in ("reference", "react", "band") if key not in entry]
    if missing:
        raise InvalidReactionError(f"{what}: the rule lacks {', '.join(missing)}")
    p_w = _number(value, f"{what}: value") * factor
    share = _number(entry["react"], f"{what}: react")
    if not 0.0 <= share <= 1.0:
        # Outside [0, 1] a share would amplify the surprise or invert it.
        raise InvalidReactionError(f"{what}: react {share} is outside [0, 1]")
    band = _parse_band(entry["band"], factor, f"{what}: band")
    if abs(band.nearest(p_w, p_w) - p_w) > _TIE_W:
        # The band is the device's own limits and the plan respects them, so
        # with no surprise react() must give the plan back.
        raise InvalidReactionError(
            f"{what}: the plan ({p_w:.0f} W) lies outside the band"
        )
    return ReactionRule(
        start=start,
        p_w=p_w,
        reference_w=_number(entry["reference"], f"{what}: reference") * factor,
        react=share,
        band=band,
    )


def _number(raw, what: str) -> float:
    # A bool is an int in Python; true and false are not numbers in JSON.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise InvalidReactionError(f"{what} is not a number but {raw!r}")
    if not math.isfinite(raw):
        # json.loads lets NaN and Infinity through; a NaN setpoint would
        # pass every comparison in nearest() and come out the other end.
        raise InvalidReactionError(f"{what} is not a finite number but {raw!r}")
    return float(raw)


def _parse_band(raw, factor: float, what: str) -> Band:
    if not isinstance(raw, list) or not raw:
        raise InvalidReactionError(f"{what} is not a non-empty list of ranges")
    ranges: list[tuple[float, float]] = []
    previous_hi: float | None = None
    for position, pair in enumerate(raw):
        if not isinstance(pair, list) or len(pair) != 2:
            raise InvalidReactionError(
                f"{what}: range {position} is not a [lo, hi] pair"
            )
        lo = _number(pair[0], f"{what}: range {position} lo") * factor
        hi = _number(pair[1], f"{what}: range {position} hi") * factor
        if lo > hi:
            raise InvalidReactionError(
                f"{what}: range {position} has its lo above its hi"
            )
        if previous_hi is not None and lo < previous_hi:
            raise InvalidReactionError(
                f"{what}: ranges are not ascending and non-overlapping"
            )
        ranges.append((lo, hi))
        previous_hi = hi
    return Band(tuple(ranges))
