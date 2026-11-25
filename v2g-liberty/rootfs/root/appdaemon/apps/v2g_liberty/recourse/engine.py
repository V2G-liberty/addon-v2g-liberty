"""The reaction engine: what the reaction rules say the charger should do,
on every meter sample.

A schedule may carry a reaction rule per slot (see rules.py). The engine
holds the rules of the current schedule and, on every sample of the grid
meter, computes the setpoint the rules give right now::

    surprise = (meter - charger) - reference
    setpoint = react(rule, surprise)

In 0.9.1 the engine runs in **shadow**: it computes, logs and publishes
``sensor.reaction_setpoint``, and writes nothing to the charger. It holds no
reference to the driver, so it cannot. The schedule's own timers keep
setting the plan. Live mode, where the setpoint goes to the charger, comes
in 0.9.2 together with the request side and a mode setting.

- The meter is the sum of the per-phase samples the data monitor emits
  (``grid_power_sample``, import positive), so the surprise is judged at
  the connection as a whole, as FlexMeasures plans it. The charger's
  realised power is the driver's (``charge_power_change``).
- A DSMR 5 meter gives a sample per second per phase. The sensor is
  rewritten when the setpoint moved more than COALESCE_W, or by anything
  once COALESCE_S have passed. The log gets band exits only.
- A setpoint the band had to clip is a **band exit** once it has lasted
  DWELL_S: the surprise is more than the charger may absorb. 0.9.1 logs it
  and counts it in the sensor; 0.9.2 asks FlexMeasures for a new plan.
- Without a sample for STALE_AFTER_S the engine is blind and follows the
  plan, as it does before every phase has delivered a first sample.
- The local device bounds (a full car cannot absorb a surplus) and the
  hysteresis around the charger's dead zone belong to the driver's
  translator, which comes with live mode.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .. import constants as c
from ..log_wrapper import get_class_method_logger
from ..timer_utils import set_recurring_timer
from .rules import ReactionRule, ReactionSchedule, react

SENSOR_REACTION_SETPOINT = "sensor.reaction_setpoint"
MODE = "shadow"
# Rewrite the sensor for a setpoint change of at least this many watts at
# once, or for any change once this many seconds have passed.
COALESCE_W = 50
COALESCE_S = 5
# A clipped setpoint is a band exit once it has lasted this long; shorter
# excursions are a kettle or the charger settling.
DWELL_S = 30
# No sample for this long and the engine is blind: follow the plan.
STALE_AFTER_S = 60
STALE_CHECK_EVERY_S = 30


@dataclass(frozen=True)
class Evaluation:
    """The rules' verdict at one moment, in W. ``raw_w`` is the setpoint
    before the band, ``setpoint_w`` after it; ``outside`` is True when the
    band had to clip, so the surprise is more than the charger may absorb."""

    rule: ReactionRule
    meter_w: int
    charger_w: int
    surprise_w: float
    raw_w: float
    setpoint_w: float
    outside: bool


class ReactionEngine:
    """See the module docstring."""

    def __init__(self, hass, event_bus, now: Callable[[], datetime] | None = None):
        self.hass = hass
        self.event_bus = event_bus
        if now is None:
            # Late import: v2g_globals imports from this package's neighbours.
            from ..v2g_globals import get_local_now  # pylint: disable=import-outside-toplevel

            now = get_local_now
        self._now = now
        self._log = get_class_method_logger(module_name="reaction_engine")

        self._rules: ReactionSchedule | None = None
        # Last signed net power per phase in W, import positive.
        self._net_w: dict[int, int] = {}
        self._last_sample_at: datetime | None = None
        self._stale = True  # no sample yet
        self._charger_w = 0
        # Band-exit bookkeeping: since when the band clips, whether this exit
        # has been logged, and how many exits this engine has seen.
        self._exit_since: datetime | None = None
        self._exit_logged = False
        self._exits = 0
        # Coalescing of the sensor writes.
        self._published: tuple | None = None
        self._published_setpoint: float | None = None
        self._published_at: datetime | None = None
        self._published_flags: tuple | None = None
        self._tick_handle = None

    async def initialize(self):
        self.event_bus.add_event_listener("grid_power_sample", self._on_grid_sample)
        self.event_bus.add_event_listener(
            "charge_power_change", self._on_charge_power_change
        )
        self._tick_handle = await set_recurring_timer(
            self.hass, self._tick_handle, self._tick, "now", STALE_CHECK_EVERY_S
        )
        self._log(f"Reaction engine initialised, mode {MODE}.")
        await self._publish(force=True)

    @property
    def rules(self) -> ReactionSchedule | None:
        return self._rules

    async def install(self, rules: ReactionSchedule):
        """Take the rules of a new schedule; the previous ones go."""
        self._rules = rules
        self._exit_since = None
        self._exit_logged = False
        self._log(f"Reaction rules installed, mode {MODE}: {rules!r}.")
        await self._publish(force=True)

    async def clear(self):
        """Drop the rules: the schedule's timers were cancelled, or a schedule
        without rules arrived."""
        if self._rules is None:
            return
        self._rules = None
        self._exit_since = None
        self._exit_logged = False
        self._log("Reaction rules cleared.")
        await self._publish(force=True)

    # ── Listeners ────────────────────────────────────────────────────────

    async def _on_grid_sample(self, phase: int, net_power_w: int):
        self._net_w[int(phase)] = int(net_power_w)
        self._last_sample_at = self._now()
        if self._stale:
            self._stale = False
            if self._rules is not None:
                self._log("Meter back; reacting again.")
        await self._publish()

    def _on_charge_power_change(self, new_power):
        if isinstance(new_power, (int, float)) and not isinstance(new_power, bool):
            self._charger_w = int(new_power)

    async def _tick(self, kwargs=None):
        if self._went_stale(self._now()):
            await self._publish(force=True)

    def _went_stale(self, now: datetime) -> bool:
        if self._stale or self._last_sample_at is None:
            return False
        if (now - self._last_sample_at).total_seconds() > STALE_AFTER_S:
            self._stale = True
            if self._rules is not None:
                self._log(
                    f"No grid sample for {STALE_AFTER_S} s; following the plan "
                    "until the meter is back.",
                    level="WARNING",
                )
            return True
        return False

    # ── Core logic (synchronous, testable) ───────────────────────────────

    def evaluate(self, now: datetime) -> Evaluation | str:
        """The rules' verdict at ``now``, or the reason there is none."""
        if self._rules is None:
            return "no rules installed"
        rule = self._rules.rule_at(now)
        if rule is None:
            return "outside the schedule"
        if len(self._net_w) < c.GRID_PHASES:
            return "waiting for a sample of every phase"
        meter_w = sum(self._net_w.values())
        surprise_w = (meter_w - self._charger_w) - rule.reference_w
        if self._stale:
            # Blind: the plan, whatever the last samples said.
            raw_w = setpoint_w = rule.p_w
            outside = False
        else:
            raw_w = rule.p_w - rule.react * surprise_w
            setpoint_w = react(rule, surprise_w)
            outside = not rule.band.contains(raw_w)
        return Evaluation(
            rule, meter_w, self._charger_w, surprise_w, raw_w, setpoint_w, outside
        )

    def _track_exit(self, verdict: Evaluation, now: datetime):
        if verdict.outside:
            if self._exit_since is None:
                self._exit_since = now
            elif (
                not self._exit_logged
                and (now - self._exit_since).total_seconds() >= DWELL_S
            ):
                self._exit_logged = True
                self._exits += 1
                self._log(
                    f"Band exit #{self._exits}: the rules ask {verdict.raw_w:.0f} W, "
                    f"the band allows {verdict.setpoint_w:.0f} W, for {DWELL_S} s "
                    f"now (surprise {verdict.surprise_w:.0f} W, slot "
                    f"{verdict.rule.start.isoformat()}). {MODE}: no retrigger."
                )
        elif self._exit_since is not None:
            if self._exit_logged:
                seconds = (now - self._exit_since).total_seconds()
                self._log(f"Band exit over after {seconds:.0f} s.")
            self._exit_since = None
            self._exit_logged = False

    # ── Output ───────────────────────────────────────────────────────────

    async def _publish(self, force: bool = False):
        """Write the sensor when something in it changed, coalescing the
        small, fast changes a 1 Hz meter brings. A change of slot, of the
        band-exit state or of the meter's health is written at once."""
        now = self._now()
        verdict = self.evaluate(now)
        if isinstance(verdict, str):
            setpoint = None
            state = "unavailable"
            attributes = {
                "mode": MODE,
                "reason": verdict,
                "meter_stale": self._stale,
                "band_exits": self._exits,
            }
        else:
            self._track_exit(verdict, now)
            setpoint = verdict.setpoint_w
            state = round(setpoint)
            attributes = {
                "mode": MODE,
                "plan_w": round(verdict.rule.p_w),
                "reference_w": round(verdict.rule.reference_w),
                "react": verdict.rule.react,
                "band_w": [
                    [
                        None if lo is None else round(lo),
                        None if hi is None else round(hi),
                    ]
                    for lo, hi in verdict.rule.band.ranges
                ],
                "slot_start": verdict.rule.start.isoformat(),
                "meter_w": verdict.meter_w,
                "charger_w": verdict.charger_w,
                "surprise_w": round(verdict.surprise_w),
                "outside_band": verdict.outside,
                "band_exits": self._exits,
                "meter_stale": self._stale,
            }
        flags = (
            state == "unavailable",
            attributes.get("outside_band"),
            attributes.get("band_exits"),
            attributes.get("meter_stale"),
            attributes.get("slot_start"),
        )
        snapshot = (state, tuple(sorted((k, str(v)) for k, v in attributes.items())))
        if snapshot == self._published:
            return
        if (
            not force
            and flags == self._published_flags
            and setpoint is not None
            and self._published_setpoint is not None
            and self._published_at is not None
            and abs(setpoint - self._published_setpoint) < COALESCE_W
            and (now - self._published_at).total_seconds() < COALESCE_S
        ):
            return
        self._published = snapshot
        self._published_setpoint = setpoint
        self._published_at = now
        self._published_flags = flags
        try:
            await self.hass.set_state(
                SENSOR_REACTION_SETPOINT, state=state, attributes=attributes
            )
        except Exception as e:  # noqa: BLE001  # HA may be restarting
            self._log(
                f"Could not write {SENSOR_REACTION_SETPOINT}: {e}", level="WARNING"
            )
