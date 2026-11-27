"""The reaction engine: what the reaction rules say the charger should do,
on every meter sample.

A schedule may carry a reaction rule per slot (see rules.py). The engine
holds the rules of the current schedule and, on every sample of the grid
meter, computes the setpoint the rules give right now::

    deviation = (meter - charger) - reference
    setpoint = react(rule, deviation)

In 0.9.1 the engine runs in **shadow**: it computes, logs and publishes
``sensor.reaction_setpoint``, and writes nothing to the charger. It holds no
reference to the driver, so it cannot. The schedule's own timers keep
setting the plan. Live mode, where the setpoint goes to the charger, comes
in 0.9.2 together with the request side and a mode setting.

- The meter is the sum of the per-phase samples the data monitor emits
  (``grid_power_sample``, import positive), so the deviation is judged at
  the connection as a whole, as FlexMeasures plans it. The charger's
  realised power is the driver's (``charge_power_change``), which it
  reports only when it changes, the first time during the charger's
  first poll; the engine therefore registers its listeners before the
  settings kick-off starts the charger (v2g_app), or a charger idling
  through a restart would stay unknown until its next move. In live mode
  the charger's share must be the commanded power during the charger's
  settle time and the realised power after it, or its own ramp towards a
  new setpoint reads as a deviation; the drivers measure those settle times.
  Shadow uses the realised power throughout.
- A DSMR 5 meter gives a sample per second per phase, and Home Assistant
  records every write. The sensor is rewritten when the setpoint moved by
  COALESCE_W or more, at most once per MIN_WRITE_GAP_S; for a smaller move
  at most once per HEARTBEAT_S; at once when the slot, the band-exit state
  or the meter's health changes; never for the attributes alone, so a
  follow-the-plan night writes nothing. The log gets band exits only.
- Every evaluated sample also goes out as ``reaction_sample`` on the event
  bus, when something listens: the dev tools' recorder writes those to a
  CSV a probe can follow live. The add-on ships no listener, so a normal
  installation writes nothing.
- Absorbing a deviation inside the band never shows as a band exit, but it
  moves the car's state of charge away from the plan, and with one-sided
  bands in one direction only. The **drift** is the integral of (setpoint
  minus plan) since the schedule arrived, in kWh, positive when the car
  would have taken more than planned; in shadow it is what the car would
  have built up. It is published as ``drift_kwh``, reset by every new
  schedule, and logged once it passes DRIFT_LOG_KWH, the client's default
  threshold. A spell without a verdict, a stale meter, a lost charger or a
  schedule that ran out, counts for nothing: the clock restarts at the next
  verdict. The integration runs on every phase sample; the half-updated
  totals of the first two phases in a second largely cancel out. In the
  shadow week it is the second retrigger reason next to the band exit (the
  client's DriftGate). 0.9.1 logs the first crossing per schedule; 0.9.2,
  where the drift asks for a new plan, acts on every crossing.
- A setpoint beyond the band, below its lowest or above its highest bound,
  is a **band exit** once it has lasted DWELL_S: the deviation is more than
  the charger may absorb. Beyond means more than EXIT_TOLERANCE_W past the
  edge: float noise on a reference served in MW, and meter noise, are not
  exits. A setpoint in the charger's dead zone is not either: it is rounded
  onto the band, never counted. Shorter excursions are counted, and the
  energy past the band is integrated over every excursion, so the shadow
  week yields the numbers a hysteresis at the band edge and an energy
  threshold in front of the retrigger are set from. 0.9.1 logs and counts;
  0.9.2 asks FlexMeasures for a new plan.
- Without a sample for STALE_AFTER_S the engine is blind and follows the
  plan, as it does before every phase has delivered a first sample. The
  charger's power is unknown until the driver reports it and again when
  the driver loses the charger; without it there is no setpoint.
- Whether to follow a setpoint into another range of the band, a mode
  change, is the engine's policy, and so is the hysteresis that keeps the
  charger from flapping at a range's edge when it does; both come with
  live mode. The rounding rule already holds the plan's mode through a
  short deviation (``Band.nearest`` in rules.py). The driver's translator,
  also live mode, keeps only the local device bounds: a full car cannot
  absorb a surplus.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .. import constants as c
from ..log_wrapper import get_class_method_logger
from ..timer_utils import set_recurring_timer
from .rules import ReactionRule, ReactionSchedule, react

SENSOR_REACTION_SETPOINT = "sensor.reaction_setpoint"
# Every write carries every key, None for the ones that do not apply.
# AppDaemon merges the attributes into the entity's, which keeps the name,
# unit, device class and icon the package gives the sensor; a key left out
# would keep its old value too, so the reason of an unavailable spell would
# outlive it.
ATTRIBUTE_KEYS = (
    "mode",
    "reason",
    "plan_w",
    "reference_w",
    "react",
    "band_w",
    "slot_start",
    "meter_w",
    "charger_w",
    "deviation_w",
    "outside_band",
    "band_exits",
    "short_excursions",
    "last_exit_wh",
    "drift_kwh",
    "meter_stale",
)
REACTION_SAMPLE_EVENT = "reaction_sample"
MODE = "shadow"
# Rewrite the sensor for a setpoint move of at least this many watts, at most
# once per MIN_WRITE_GAP_S; for a smaller move at most once per HEARTBEAT_S.
# A change of slot, band-exit state or meter health goes at once.
COALESCE_W = 50
MIN_WRITE_GAP_S = 5
HEARTBEAT_S = 60
# A clipped setpoint is a band exit once it has lasted this long; shorter
# excursions are a kettle or the charger settling.
DWELL_S = 30
# A raw setpoint this close to the band's edge is not outside it: the
# tolerance the client's exit gate uses, in the order of meter noise.
EXIT_TOLERANCE_W = 50
# No sample for this long and the engine is blind: follow the plan.
STALE_AFTER_S = 60
STALE_CHECK_EVERY_S = 30
# Log the drift once it passes this, in either direction: the client's
# default DriftGate threshold. The sensor is rewritten per DRIFT_STEP_KWH.
DRIFT_LOG_KWH = 0.5
DRIFT_STEP_KWH = 0.1


@dataclass(frozen=True)
class Evaluation:
    """The rules' verdict at one moment, in W. ``raw_w`` is the setpoint
    before the band, ``setpoint_w`` after it; ``outside`` is True when
    ``raw_w`` lies beyond the band, so the deviation is more than the charger
    may absorb. Rounding out of the dead zone does not count."""

    rule: ReactionRule
    meter_w: int
    charger_w: int
    deviation_w: float
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
        # The charger's realised power; None until the driver reports it and
        # again when the driver loses the charger.
        self._charger_w: int | None = None
        # Band-exit bookkeeping: since when the band clips, the previous
        # sample in that excursion and the energy past the band so far,
        # whether this exit has been logged, and the counts: exits that
        # lasted the dwell and excursions that did not.
        self._exit_since: datetime | None = None
        self._exit_last_at: datetime | None = None
        self._exit_wh = 0.0
        self._exit_logged = False
        self._exits = 0
        self._short_excursions = 0
        self._last_exit_wh: float | None = None
        # Drift bookkeeping: the energy the setpoint has moved away from the
        # plan since the schedule arrived, the previous sample it was
        # integrated to, and whether its passing the threshold was logged.
        self._drift_wh = 0.0
        self._drift_last_at: datetime | None = None
        self._drift_held_w = 0.0
        self._drift_logged = False
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
        self.event_bus.add_event_listener(
            "charger_communication_state_change",
            self._on_charger_communication_state_change,
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
        self._reset_excursion()
        self._reset_drift()
        self._log(f"Reaction rules installed, mode {MODE}: {rules!r}.")
        await self._publish(force=True)

    async def clear(self):
        """Drop the rules: the schedule's timers were cancelled, or a schedule
        without rules arrived."""
        if self._rules is None:
            return
        self._rules = None
        self._reset_excursion()
        self._reset_drift()
        self._log("Reaction rules cleared.")
        await self._publish(force=True)

    # ── Listeners ────────────────────────────────────────────────────────

    async def _on_grid_sample(self, phase: int, net_power_w: int):
        self._net_w[int(phase)] = int(net_power_w)
        first = self._last_sample_at is None  # the start is not a return
        self._last_sample_at = self._now()
        if self._stale:
            self._stale = False
            if self._rules is not None and not first:
                self._log("Meter back; reacting again.")
        await self._publish(sample_phase=int(phase))

    def _on_charge_power_change(self, new_power):
        if isinstance(new_power, (int, float)) and not isinstance(new_power, bool):
            self._charger_w = int(new_power)

    async def _on_charger_communication_state_change(self, can_communicate: bool):
        """The driver lost the charger: its last power says nothing anymore,
        and no event will come to say so. Known again at the next report."""
        if can_communicate or self._charger_w is None:
            return
        self._charger_w = None
        if self._rules is not None:
            self._log("Charger unreachable; no setpoint until its power is known.")
        await self._publish(force=True)

    async def _tick(self, kwargs=None):
        if self._went_stale(self._now()):
            await self._publish(force=True)

    def _went_stale(self, now: datetime) -> bool:
        if self._stale or self._last_sample_at is None:
            return False
        if (now - self._last_sample_at).total_seconds() > STALE_AFTER_S:
            self._stale = True
            self._drift_last_at = None  # the gap counts for nothing
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
        if self._charger_w is None:
            return "charger power unknown"
        meter_w = sum(self._net_w.values())
        deviation_w = (meter_w - self._charger_w) - rule.reference_w
        if self._stale:
            # Blind: the plan, whatever the last samples said.
            raw_w = setpoint_w = rule.p_w
            outside = False
        else:
            raw_w = rule.p_w - rule.react * deviation_w
            setpoint_w = react(rule, deviation_w)
            outside = (
                rule.band.exceeds(raw_w) and abs(raw_w - setpoint_w) > EXIT_TOLERANCE_W
            )
        return Evaluation(
            rule, meter_w, self._charger_w, deviation_w, raw_w, setpoint_w, outside
        )

    def _reset_drift(self):
        self._drift_wh = 0.0
        self._drift_last_at = None
        self._drift_held_w = 0.0
        self._drift_logged = False

    def _track_drift(self, verdict: Evaluation, now: datetime):
        """Integrate (setpoint minus plan) as a zero-order hold: the previous
        sample's value counts until this one. The first sample after a reset
        or a stale spell only starts the clock. Logged once per schedule when
        the drift passes DRIFT_LOG_KWH."""
        if self._drift_last_at is not None:
            dt_s = (now - self._drift_last_at).total_seconds()
            self._drift_wh += self._drift_held_w * dt_s / 3600
        self._drift_last_at = now
        self._drift_held_w = verdict.setpoint_w - verdict.rule.p_w
        if not self._drift_logged and abs(self._drift_wh) >= DRIFT_LOG_KWH * 1000:
            self._drift_logged = True
            more_or_less = "more" if self._drift_wh > 0 else "less"
            self._log(
                f"Drift of {self._drift_wh / 1000:+.2f} kWh since the schedule "
                f"arrived: the setpoint would have given the car {more_or_less} "
                f"than the plan, past the {DRIFT_LOG_KWH} kWh threshold. "
                f"{MODE}: no retrigger."
            )

    def _drift_kwh_rounded(self) -> float:
        return round(self._drift_wh / 1000, 3)

    def _reset_excursion(self):
        self._exit_since = None
        self._exit_last_at = None
        self._exit_wh = 0.0
        self._exit_logged = False

    def _track_exit(self, verdict: Evaluation, now: datetime):
        if verdict.outside:
            past_w = abs(verdict.raw_w - verdict.setpoint_w)
            if self._exit_since is None:
                self._exit_since = now
            else:
                dt_s = (now - self._exit_last_at).total_seconds()
                self._exit_wh += past_w * dt_s / 3600
            self._exit_last_at = now
            if (
                not self._exit_logged
                and (now - self._exit_since).total_seconds() >= DWELL_S
            ):
                self._exit_logged = True
                self._exits += 1
                self._log(
                    f"Band exit #{self._exits}: the rules ask {verdict.raw_w:.0f} W, "
                    f"the band allows {verdict.setpoint_w:.0f} W, for {DWELL_S} s "
                    f"now, {self._exit_wh:.1f} Wh past the band (deviation "
                    f"{verdict.deviation_w:.0f} W, slot "
                    f"{verdict.rule.start.isoformat()}). {MODE}: no retrigger."
                )
        elif self._exit_since is not None:
            seconds = (now - self._exit_since).total_seconds()
            if self._exit_logged:
                self._last_exit_wh = self._exit_wh
                self._log(
                    f"Band exit over after {seconds:.0f} s, {self._exit_wh:.1f} Wh "
                    "past the band."
                )
            else:
                self._short_excursions += 1
                self._log(
                    f"Excursion past the band of {seconds:.0f} s, {self._exit_wh:.1f} "
                    f"Wh; shorter than the dwell of {DWELL_S} s, no exit.",
                    level="DEBUG",
                )
            self._reset_excursion()

    # ── Output ───────────────────────────────────────────────────────────

    def _last_exit_wh_rounded(self) -> float | None:
        return None if self._last_exit_wh is None else round(self._last_exit_wh, 1)

    def _emit_sample(self, now: datetime, verdict: Evaluation, phase: int):
        """One event per evaluated meter sample, for a recorder. Only when
        something listens: the bus warns about an event nobody hears, and a
        normal installation has no recorder. ``time`` is when the engine
        evaluated the sample, milliseconds after the meter read it; the
        meter's own timestamp does not travel on the bus. ``phase`` is the
        phase whose sample this was: the other phases still hold the previous
        second's values, so a reader wanting one row per second keeps the
        last phase."""
        if not self.event_bus.listeners(REACTION_SAMPLE_EVENT):
            return
        self.event_bus.emit_event(
            REACTION_SAMPLE_EVENT,
            time=now,
            phase=phase,
            deviation_w=round(verdict.deviation_w),
            raw_w=round(verdict.raw_w),
            setpoint_w=round(verdict.setpoint_w),
            plan_w=round(verdict.rule.p_w),
            meter_w=verdict.meter_w,
            charger_w=verdict.charger_w,
        )

    async def _publish(self, force: bool = False, sample_phase: int | None = None):
        """Write the sensor when something in it changed, sparingly: Home
        Assistant records every write and the meter samples once a second.
        A call for a fresh meter sample (``sample_phase``) also emits the
        sample event; the forced calls for a new schedule, a stale meter or
        a lost charger evaluate the same old samples again and emit none."""
        now = self._now()
        verdict = self.evaluate(now)
        if isinstance(verdict, str):
            # No verdict, so nothing to hold: the gap until the next verdict
            # counts for nothing, as a stale spell does.
            self._drift_last_at = None
            setpoint = None
            state = "unavailable"
            attributes = {
                "mode": MODE,
                "reason": verdict,
                "meter_stale": self._stale,
                "band_exits": self._exits,
                "short_excursions": self._short_excursions,
                "last_exit_wh": self._last_exit_wh_rounded(),
                "drift_kwh": self._drift_kwh_rounded(),
            }
        else:
            # The raw per-sample deviation, for sizing the smoother that comes
            # with live mode; visible only while debug logging is on.
            self._log(
                f"deviation {verdict.deviation_w:.0f} W, raw {verdict.raw_w:.0f} W, "
                f"setpoint {verdict.setpoint_w:.0f} W.",
                level="DEBUG",
            )
            if sample_phase is not None:
                self._emit_sample(now, verdict, sample_phase)
                self._track_drift(verdict, now)
            self._track_exit(verdict, now)
            setpoint = verdict.setpoint_w
            state = round(setpoint)
            attributes = {
                "mode": MODE,
                "plan_w": round(verdict.rule.p_w),
                "reference_w": round(verdict.rule.reference_w),
                "react": verdict.rule.react,
                "band_w": [
                    [round(lo), round(hi)] for lo, hi in verdict.rule.band.ranges
                ],
                "slot_start": verdict.rule.start.isoformat(),
                "meter_w": verdict.meter_w,
                "charger_w": verdict.charger_w,
                # Blind: the deviation is whatever the last samples said, so
                # it is not shown.
                "deviation_w": None if self._stale else round(verdict.deviation_w),
                "outside_band": verdict.outside,
                "band_exits": self._exits,
                "short_excursions": self._short_excursions,
                "last_exit_wh": self._last_exit_wh_rounded(),
                "drift_kwh": self._drift_kwh_rounded(),
                "meter_stale": self._stale,
            }
        attributes = {key: attributes.get(key) for key in ATTRIBUTE_KEYS}
        flags = (
            state == "unavailable",
            attributes.get("reason"),
            attributes.get("outside_band"),
            attributes.get("band_exits"),
            attributes.get("meter_stale"),
            attributes.get("slot_start"),
            # The drift grows without the setpoint moving; one write per step.
            # Steps of equal width centred on zero, so leaving zero is not a
            # write by itself; float noise rounded away.
            math.floor(round(self._drift_wh / (DRIFT_STEP_KWH * 1000) + 0.5, 6)),
        )
        snapshot = (state, tuple(sorted((k, str(v)) for k, v in attributes.items())))
        if snapshot == self._published:
            return
        if not force and self._published_at is not None:
            since_write = (now - self._published_at).total_seconds()
            if flags != self._published_flags:
                pass  # a change of state goes at once
            elif since_write < MIN_WRITE_GAP_S:
                return
            else:
                # Same flags, so both setpoints are known.
                moved = abs((setpoint or 0.0) - (self._published_setpoint or 0.0))
                if moved < COALESCE_W and (moved == 0 or since_write < HEARTBEAT_S):
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
