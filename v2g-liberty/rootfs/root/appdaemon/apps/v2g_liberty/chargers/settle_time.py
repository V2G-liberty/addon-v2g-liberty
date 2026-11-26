"""How long the charger takes to follow a new setpoint, per kind of transition.

A charger and a car do not follow a setpoint at once: charging less takes a
few seconds, starting to discharge a few minutes, because charger and car
negotiate first. The reaction engine needs these times as the dwell before
it judges a band exit, and they differ per charger, per car and per kind of
transition. So the drivers measure them on the installation itself.

The clock starts when a setpoint goes to the charger. It stops when the
charger has settled, which is either of two things:

- its power is within tolerance of the setpoint (``TOLERANCE_W`` or
  ``TOLERANCE_SHARE`` of it, whichever is larger), or
- its power moved away from where it started and has then stood still for
  ``STABLE_S``: the car takes less than asked, as a car with a smaller
  on-board charger does, or tapers towards full. Standing still allows the
  readings' jitter, ``STILL_W`` either way; the band is smaller than the
  slowest ramp's step per poll, so a ramp is never still. The transition
  then ended when the power came to rest, and the time is counted up to
  there.

The kind of transition is judged from the power the charger *reported*
last, not from the previous setpoint: after a restart the previous setpoint
is 0 while the charger may be charging, and the Quasar's stop goes through
an action register, so the setpoint alone would misname both. A setpoint
already within tolerance of the reported power is not timed. A setpoint the
charger has not settled on within ``GIVE_UP_AFTER_S`` is dropped, not
counted: the car refuses, or the write failed. A newer setpoint replaces a
pending one.

What the figures are worth. The drivers report the power only when it
changes, once per poll, so a measured time is up to one polling interval
(5 s on both chargers) too long: an upper bound, which is the right side for
a dwell. The Quasar's stop is timed from the setpoint write, which follows
the write to the action register that actually stops it, so a Quasar stop
reads slightly short. The tracker keeps the last ``KEEP`` durations per
kind in one list, whichever car is connected; after a change of car the
medians mix until the old values have aged out. All of it lives in memory
only. The base driver publishes the result as ``sensor.charger_settle_time``:
the slowest median as the state, the median per kind in the attributes,
which is what a wait per kind of transition should be read from.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from statistics import median

# Within this many watts of the setpoint, or this share of it, the charger
# counts as settled. 230 W is one ampere on one phase.
TOLERANCE_W = 230
TOLERANCE_SHARE = 0.10
# A power that moved from where it started and then stood still for this
# long is where the charger settled, setpoint or not: two polls. Still means
# within STILL_W of where it came to rest, as the readings jitter; the Quasar
# discharges at 35-40 W/s, 175-200 W per 5 s poll, so a ramp is never still.
STABLE_S = 10
STILL_W = 100
# A setpoint not settled on within this time is not timed at all.
GIVE_UP_AFTER_S = 300
# Durations kept per kind of transition for the running median.
KEEP = 20


def tolerance_w(target_w: int) -> float:
    return max(TOLERANCE_W, TOLERANCE_SHARE * abs(target_w))


def classify(actual_w: int, target_w: int) -> str | None:
    """Name the transition from the reported power to the setpoint, or None
    when there is nothing to time: start, stop, adjust or reverse, with the
    direction the charger ends in (for a stop: the one it came from)."""
    if abs(actual_w) <= TOLERANCE_W:
        actual_w = 0
    if target_w == 0:
        if actual_w == 0:
            return None
        return "stop_charging" if actual_w > 0 else "stop_discharging"
    direction = "charging" if target_w > 0 else "discharging"
    if actual_w == 0:
        return f"start_{direction}"
    if (actual_w > 0) == (target_w > 0):
        return f"adjust_{direction}"
    return f"reverse_to_{direction}"


@dataclass(frozen=True)
class SettleTime:
    """One timed transition. ``settled`` is False when the tracker gave up
    on it; then ``seconds`` is how long it waited."""

    kind: str
    from_w: int
    to_w: int
    actual_w: int
    seconds: float
    settled: bool


class SettleTimeTracker:
    """Times the transitions of one charger. Clock values are seconds on any
    monotonic clock; the tracker only subtracts them."""

    def __init__(self):
        self._actual_w: int | None = None
        # Where the power came to rest, and since when: readings within
        # STILL_W of it count as standing still.
        self._still_w: int | None = None
        self._still_since: float | None = None
        # The transition being timed: kind, from, to, since.
        self._pending: tuple[str, int, int, float] | None = None
        self._durations: dict[str, deque[float]] = {}
        self.last: SettleTime | None = None

    def note_setpoint(self, target_w: int, at: float) -> str | None:
        """A setpoint went to the charger at ``at``. Returns the kind of
        transition now being timed, or None when nothing is."""
        self._pending = None
        if self._actual_w is None:
            return None  # where the charger is now is not known yet
        kind = classify(self._actual_w, target_w)
        if kind is None or abs(target_w - self._actual_w) <= tolerance_w(target_w):
            return None
        self._pending = (kind, self._actual_w, target_w, at)
        return kind

    def note_actual(self, actual_w: int, at: float) -> SettleTime | None:
        """The charger reported its power at ``at``. Returns the measurement
        when this ends the pending transition, settled or given up."""
        if self._still_w is None or abs(actual_w - self._still_w) > STILL_W:
            self._still_w = actual_w
            self._still_since = at
        self._actual_w = actual_w
        return self._judge(at)

    def note_poll(self, at: float) -> SettleTime | None:
        """The charger was polled at ``at`` and its power did not change (the
        drivers report changes only). Returns the measurement when standing
        still, or the time passed, ends the pending transition."""
        return self._judge(at)

    def _judge(self, at: float) -> SettleTime | None:
        if self._pending is None or self._actual_w is None:
            return None
        kind, from_w, to_w, since = self._pending
        seconds = at - since
        if seconds > GIVE_UP_AFTER_S:
            self._pending = None
            self.last = SettleTime(kind, from_w, to_w, self._actual_w, seconds, False)
            return self.last
        if abs(self._actual_w - to_w) <= tolerance_w(to_w):
            return self._settled(at)
        moved = abs(self._actual_w - from_w) > TOLERANCE_W
        if moved and at - self._still_since >= STABLE_S:
            # Standing still short of the setpoint: settled when it came to rest.
            return self._settled(self._still_since)
        return None

    def _settled(self, when: float) -> SettleTime:
        kind, from_w, to_w, since = self._pending
        self._pending = None
        seconds = when - since
        self._durations.setdefault(kind, deque(maxlen=KEEP)).append(seconds)
        self.last = SettleTime(kind, from_w, to_w, self._actual_w, seconds, True)
        return self.last

    def medians(self) -> dict[str, float]:
        return {kind: median(d) for kind, d in self._durations.items() if d}

    def counts(self) -> dict[str, int]:
        return {kind: len(d) for kind, d in self._durations.items() if d}

    def slowest(self) -> tuple[str, float] | None:
        """The kind with the longest median, and that median."""
        medians = self.medians()
        if not medians:
            return None
        kind = max(medians, key=medians.get)
        return kind, medians[kind]
