"""Sign-aware fuse guard: keeps the charger within the grid connection's
capacity, per phase, as the last layer under every charging decision.

The guard replaces the Modbus-proxy load balancer. That proxy clamped the
setpoint on the wire, watched one summed power sensor without a sign, and
only knew the Wallbox Quasar. This guard sits in the app, uses the per-phase
grid consumption and production that the grid connection settings already
provide, and knows the direction of the power:

- an **import** overload (net power on a phase above the capacity) can only
  be caused by charging, so only the charge limit comes down; discharging
  would help and stays free;
- an **export** overload (net power below minus the capacity) can only be
  caused by discharging, so only the discharge limit comes down.

Two mechanisms work together:

1. **Clip at command time.** ``clip(requested_w)`` answers what the charger
   may be asked for right now: the requested power, bounded by the standing
   limit and by the instantaneous headroom on the charger's phase(s). The
   headroom is the capacity minus the load that is *not* the charger, so a
   schedule step that would overload the phase never reaches the charger.
2. **Standing limits with hysteresis.** Samples from the meter lower a limit
   at once when a phase is actually overloaded, in steps of at least one
   ampere per charger phase (``STEP_A``; to 0 W always). Raising it back is
   slower: the headroom must exceed the limit by that same ampere for
   ``RESTORE_DELAY_S`` without interruption, and at least ``COOLDOWN_S``
   after the previous change, and the limit comes back to one ampere under
   the headroom, not onto it. An unsolicited change is published as
   ``fuse_guard_limit_change`` so the main app can re-apply its outstanding
   request with the new bound.

The charger's own share of a phase is taken from its measured power, except
right after a command: for ``SETTLE_S`` the larger of the commanded and the
measured power counts. The meter may already see a new setpoint that the
driver's next poll has not reported (a ramp-up would look like a jump in
household load), or may still carry the old power after a clip (the rest
would look like household load and clip a second time, to 0 W).

Without a meter sample, none yet after a start or none for ``STALE_AFTER_S``,
the guard cannot see the household. The limits then fall back to the blind
bound: ``BLIND_SHARE`` of the capacity of the charger's phase(s), at most the
charger's configured maximum, the static bound the user set with their
electrician. Never "no limit": the configured maximum is always enforced by
the drivers as well.

The guard is active as soon as the per-phase grid sensors and the charger's
phase are known; otherwise every clip passes through unchanged and the sensor
reads unavailable. All state changes happen before the first ``await`` in
each entry point, so two calls arriving together cannot both act on the old
state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from .. import constants as c
from ..log_wrapper import get_class_method_logger
from ..timer_utils import set_recurring_timer

NOMINAL_VOLTAGE_V = 230

# One ampere per charger phase is the smallest step worth acting on, in both
# directions, and the margin a restore keeps under the capacity:
# - the capacity is set in whole amperes, so the bound in watts is already
#   +/- 115 W uncertain by construction;
# - the mains voltage may be anywhere from 207 V to 253 V, so the power that
#   goes with a given current is +/- 10 % off the 230 V assumed here, which
#   is +/- 575 W at 25 A;
# - a gG fuse carries 1.25 x its rating for an hour without blowing and only
#   has to blow within the hour above 1.6 x; the guard is there against a
#   sustained large overload (oven, heat pump and car together), not against
#   0.2 A;
# - the Quasar is controlled in whole amperes, so a smaller step is a Modbus
#   write that changes nothing.
# Lowering by less than this is not done (lowering to 0 W always is), so the
# charger may sit at most one ampere per phase over the capacity, which is
# 4 % on a 25 A fuse. For a 1-phase charger the step is the proxy load
# balancer's 230 W hysteresis, so nothing changes for the current users.
STEP_A = 1
HYSTERESIS_W = STEP_A * NOMINAL_VOLTAGE_V  # per charger phase; see _step_w()

# Without a meter sample (none yet after a start, or none for STALE_AFTER_S)
# the guard cannot see the household, so it bounds the charger to this share
# of the capacity of its phase(s), never above the configured maximum. At
# 100 % the charger alone would sit on the fuse and any household load on
# top is an overload; 70 % keeps 7.5 A of a 25 A phase for the house. Blind
# periods are short (seconds after a start, minutes while HA restarts), so
# the cost is a slower charge then, not a lost one. Only a single-phase
# charger on a 25 A or 35 A phase notices: 3 x 25 A with a 10 kW charger
# stays at 10 kW (70 % of 17250 W).
BLIND_SHARE = 0.7

# From the proxy load balancer, which ran with these for years.
RESTORE_DELAY_S = 10
COOLDOWN_S = 15

# How long a command is assumed to describe the charger's power better than
# the driver's last poll (Quasar polls every 5 s, a reversal settles slower).
SETTLE_S = 10

STALE_AFTER_S = 60
STALE_CHECK_EVERY_S = 30

SENSOR_FUSE_GUARD_LIMIT = "sensor.fuse_guard_limit"

CHARGE = "charge"
DISCHARGE = "discharge"


@dataclass(frozen=True)
class ClipResult:
    """What the charger may be asked for, given a request."""

    requested_w: int
    allowed_w: int
    # The phase that bounds the request, None when nothing was clipped.
    phase: int | None = None

    @property
    def is_clipped(self) -> bool:
        return self.allowed_w != self.requested_w


@dataclass(frozen=True)
class _Headroom:
    """The most the whole charger may draw (charge) or feed in (discharge)
    without any of its phases leaving the capacity, with the phase that
    binds each direction. None when no sample has arrived for its phases."""

    charge_w: int | None
    discharge_w: int | None
    charge_phase: int | None
    discharge_phase: int | None


class FuseGuard:
    """See the module docstring."""

    def __init__(self, hass, event_bus, now: Callable[[], datetime] | None = None):
        self.hass = hass
        self.event_bus = event_bus
        if now is None:
            # Late import: v2g_globals imports from this package.
            from ..v2g_globals import get_local_now  # pylint: disable=import-outside-toplevel

            now = get_local_now
        self._now = now
        self._log = get_class_method_logger(module_name="fuse_guard")

        # Last signed net power per phase in W, import positive.
        self._net_w: dict[int, int] = {}
        self._last_sample_at: datetime | None = None
        # The charger's power: as measured by the driver, and as last commanded.
        self._measured_w: int = 0
        self._commanded_w: int = 0
        self._commanded_at: datetime | None = None
        # Standing limits in W (magnitudes). None means "the configured maximum".
        self._limit: dict[str, int | None] = {CHARGE: None, DISCHARGE: None}
        self._limiting_phase: dict[str, int | None] = {CHARGE: None, DISCHARGE: None}
        # Since when the headroom has continuously allowed a restore.
        self._restore_since: dict[str, datetime | None] = {
            CHARGE: None,
            DISCHARGE: None,
        }
        self._last_change_at: datetime | None = None
        self._stale = True  # no sample yet
        self._stale_warned = False
        self._clip_count = 0
        self._tick_handle = None
        self._published: tuple | None = None
        # Set when the sensor must be rewritten without a limit having moved.
        self._publish_pending = False

    async def initialize(self):
        self.event_bus.add_event_listener("grid_power_sample", self._on_grid_sample)
        self.event_bus.add_event_listener(
            "charge_power_change", self._on_charge_power_change
        )
        self.event_bus.add_event_listener(
            "grid_settings_changed", self._on_grid_settings_changed
        )
        self._tick_handle = await set_recurring_timer(
            self.hass, self._tick_handle, self._tick, "now", STALE_CHECK_EVERY_S
        )
        self._log(
            f"Fuse guard initialised, active: {self.is_active()} "
            f"(grid sensors: {bool(c.GRID_CONSUMPTION_ENTITIES)}, "
            f"charger phases: {self.charger_phases()})."
        )
        await self._publish()

    # ── Configuration ────────────────────────────────────────────────────

    @staticmethod
    def charger_phases() -> list[int] | None:
        """The phases the charger is on, or None when that is not known.
        On a 1-phase grid there is only phase 1."""
        if c.GRID_PHASES == 1:
            return [1]
        raw = c.CHARGER_CONNECTED_TO_PHASE
        candidates = raw if isinstance(raw, list) else [raw]
        phases = sorted(
            {
                p
                for p in candidates
                if isinstance(p, int) and not isinstance(p, bool) and p in (1, 2, 3)
            }
        )
        return phases or None

    def is_active(self) -> bool:
        return bool(c.GRID_CONSUMPTION_ENTITIES) and self.charger_phases() is not None

    @staticmethod
    def capacity_per_phase_w() -> int:
        return int(c.GRID_CAPACITY_PER_PHASE * NOMINAL_VOLTAGE_V)

    def _step_w(self) -> int:
        """One ampere on every phase the charger loads, in charger watts."""
        return HYSTERESIS_W * len(self.charger_phases() or [1])

    @staticmethod
    def _configured_max_w(direction: str) -> int:
        if direction == CHARGE:
            return max(0, int(c.CHARGER_MAX_CHARGE_POWER))
        return max(0, int(c.CHARGER_MAX_DISCHARGE_POWER))

    def _blind_bound_w(self, direction: str) -> int:
        """What the charger may draw while the guard has no meter sample
        (see BLIND_SHARE). Inactive guard: the configured maximum."""
        maximum = self._configured_max_w(direction)
        if not self.is_active():
            return maximum
        phases = len(self.charger_phases() or [1])
        return min(maximum, int(self.capacity_per_phase_w() * phases * BLIND_SHARE))

    def limit_w(self, direction: str) -> int:
        """The standing limit as a magnitude, never above the configured
        maximum; without a meter sample the blind bound."""
        maximum = self._configured_max_w(direction)
        standing = self._limit[direction]
        if standing is None:
            return self._blind_bound_w(direction) if self._stale else maximum
        return min(standing, maximum)

    @property
    def charge_limit_w(self) -> int:
        return self.limit_w(CHARGE)

    @property
    def discharge_limit_w(self) -> int:
        return self.limit_w(DISCHARGE)

    @property
    def meter_is_stale(self) -> bool:
        return self._stale

    # ── The clip ─────────────────────────────────────────────────────────

    async def clip(self, requested_w: int, source: str = "") -> ClipResult:
        """Bound a request to what the connection can take right now.

        Records the outcome as the commanded power (see SETTLE_S). A clip by
        the instantaneous headroom also lowers the standing limit, so the
        restore logic brings it back up once the headroom returns.
        """
        requested_w = int(requested_w)
        now = self._now()
        changed = self._check_stale(now)
        result = ClipResult(requested_w, requested_w)

        if self.is_active() and requested_w != 0 and not self._stale:
            direction = CHARGE if requested_w > 0 else DISCHARGE
            magnitude = abs(requested_w)
            bound = self.limit_w(direction)
            phase = self._limiting_phase[direction]
            room = self._headroom(now)
            room_w = room.charge_w if direction == CHARGE else room.discharge_w
            if room_w is not None and room_w < bound:
                bound = max(0, room_w)
                phase = (
                    room.charge_phase if direction == CHARGE else room.discharge_phase
                )
            if magnitude > bound:
                allowed = bound if direction == CHARGE else -bound
                result = ClipResult(requested_w, allowed, phase)
                # The headroom bound becomes the standing limit, so a later
                # restore is announced and the full request re-applied.
                if self._set_limit(direction, bound, phase, now):
                    changed = True
        elif self.is_active() and requested_w != 0:
            # No meter sample: the blind bound applies. Say so once per period.
            direction = CHARGE if requested_w > 0 else DISCHARGE
            bound = self._blind_bound_w(direction)
            if not self._stale_warned:
                self._stale_warned = True
                self._log(
                    f"No grid samples; the fuse guard bounds the charger to "
                    f"{bound} W ({BLIND_SHARE:.0%} of the capacity of its phases).",
                    level="WARNING",
                )
            if abs(requested_w) > bound:
                allowed = bound if direction == CHARGE else -bound
                result = ClipResult(requested_w, allowed, None)

        self._commanded_w = result.allowed_w
        self._commanded_at = now
        if result.is_clipped:
            self._clip_count += 1
            where = f"on L{result.phase}" if result.phase else "without a meter sample"
            self._log(
                f"Clipped {requested_w} W to {result.allowed_w} W {where} "
                f"(capacity {self.capacity_per_phase_w()} W per phase; {source})."
            )
        if changed:
            await self._publish()
        return result

    # ── Listeners ────────────────────────────────────────────────────────

    async def _on_grid_sample(self, phase: int, net_power_w: int):
        now = self._now()
        change = self._process_sample(int(phase), int(net_power_w), now)
        if change is not None:
            await self._announce(change)
        elif self._publish_pending:
            await self._publish()

    def _on_charge_power_change(self, new_power):
        if isinstance(new_power, (int, float)) and not isinstance(new_power, bool):
            self._measured_w = int(new_power)

    async def _on_grid_settings_changed(self, *args, **kwargs):
        """Phase numbering or sensors changed: old samples say nothing
        anymore. The standing limits stay: a smaller capacity lowers them on
        the next sample, a larger one restores them through the normal delay.
        Dropping them here would send the full request into a connection
        that was just declared smaller. Only a guard that became inactive
        releases its limits, and says so."""
        self._net_w = {}
        for direction in (CHARGE, DISCHARGE):
            self._restore_since[direction] = None
        active = self.is_active()
        self._log(f"Grid settings changed, fuse guard active: {active}.")
        if not active and any(self._limit[d] is not None for d in (CHARGE, DISCHARGE)):
            for direction in (CHARGE, DISCHARGE):
                self._limit[direction] = None
                self._limiting_phase[direction] = None
            self.event_bus.emit_event(
                "fuse_guard_limit_change",
                charge_limit_w=self.charge_limit_w,
                discharge_limit_w=self.discharge_limit_w,
                phase=None,
                reason="fuse guard inactive",
            )
        await self._publish()

    async def _tick(self, kwargs=None):
        now = self._now()
        if self._check_stale(now):
            await self._announce("meter stale")

    # ── Core logic (synchronous, testable) ───────────────────────────────

    def _process_sample(self, phase: int, net_w: int, now: datetime) -> str | None:
        """Take a meter sample. Returns a reason when a standing limit
        changed and must be announced, else None."""
        self._net_w[phase] = net_w
        self._last_sample_at = now
        reason = None
        if self._stale:
            # Back in business: the blind bound makes way for the headroom,
            # so the main app must re-apply its request. Announce, even when
            # this sample is not a charger phase.
            self._stale = False
            self._stale_warned = False
            reason = "meter back"

        phases = self.charger_phases()
        if not self.is_active() or phases is None or phase not in phases:
            return reason

        cap = self.capacity_per_phase_w()
        room = self._headroom(now)

        # Lower at once on a real overload, in the direction that causes it.
        if (
            net_w > cap
            and room.charge_w is not None
            and self._set_limit(CHARGE, room.charge_w, room.charge_phase, now)
        ):
            reason = f"import overload on L{phase}"
        elif (
            net_w < -cap
            and room.discharge_w is not None
            and self._set_limit(DISCHARGE, room.discharge_w, room.discharge_phase, now)
        ):
            reason = f"export overload on L{phase}"

        for direction, room_w in (
            (CHARGE, room.charge_w),
            (DISCHARGE, room.discharge_w),
        ):
            if self._maybe_restore(direction, room_w, now):
                reason = f"{direction} headroom back"
        return reason

    def _headroom(self, now: datetime) -> _Headroom:
        phases = self.charger_phases() or []
        cap = self.capacity_per_phase_w()
        charger_w = self._charger_power_w(now)
        n = len(phases)
        charge_w = discharge_w = None
        charge_phase = discharge_phase = None
        for p in phases:
            net = self._net_w.get(p)
            if net is None:
                continue
            other = net - charger_w / n
            # Whole-charger bounds: every phase carries 1/n of the charger.
            room_c = int((cap - other) * n)
            room_d = int((cap + other) * n)
            if charge_w is None or room_c < charge_w:
                charge_w, charge_phase = room_c, p
            if discharge_w is None or room_d < discharge_w:
                discharge_w, discharge_phase = room_d, p
        return _Headroom(charge_w, discharge_w, charge_phase, discharge_phase)

    def _charger_power_w(self, now: datetime) -> int:
        """The charger's share of the meter reading.

        Within SETTLE_S of a command neither value is reliable on its own: a
        meter sample may still carry the old power (the charger ramps, the
        meter lags) while a fast charger may already draw the new one, which
        the driver's poll has not seen. Taking the larger magnitude attributes
        the doubt to the charger, not to the household. The other way round
        inflates the household load, and that collapses the headroom to 0 W
        right after a clip, in whichever direction the clip went.
        """
        if (
            self._commanded_at is not None
            and (now - self._commanded_at).total_seconds() < SETTLE_S
            and abs(self._commanded_w) > abs(self._measured_w)
        ):
            return self._commanded_w
        return self._measured_w

    def _set_limit(self, direction: str, room_w: int, phase: int | None, now) -> bool:
        """Lower a standing limit to the headroom. Returns True on a change."""
        new = max(0, min(room_w, self._configured_max_w(direction)))
        current = self.limit_w(direction)
        if new >= current:
            return False
        if new > 0 and current - new < self._step_w():
            # Less than an ampere per phase over: not worth a write (STEP_A).
            return False
        self._limit[direction] = new
        self._limiting_phase[direction] = phase
        self._restore_since[direction] = None
        self._last_change_at = now
        self._log(f"{direction.capitalize()} limit {current} W -> {new} W (L{phase}).")
        return True

    def _maybe_restore(self, direction: str, room_w: int | None, now) -> bool:
        """Raise a lowered limit towards the headroom, slowly. Returns True
        on a change."""
        if self._limit[direction] is None or room_w is None:
            self._restore_since[direction] = None
            return False
        maximum = self._configured_max_w(direction)
        current = self.limit_w(direction)
        step = self._step_w()
        # Come back to one ampere per phase under the capacity, not onto it:
        # the next flicker of the household would be an overload again.
        target = min(room_w - step, maximum)
        enough = target >= maximum or target - current >= step
        if target <= current or not enough:
            self._restore_since[direction] = None
            return False
        since = self._restore_since[direction]
        if since is None:
            self._restore_since[direction] = now
            return False
        if (now - since).total_seconds() < RESTORE_DELAY_S:
            return False
        if (
            self._last_change_at is not None
            and (now - self._last_change_at).total_seconds() < COOLDOWN_S
        ):
            return False
        self._limit[direction] = None if target >= maximum else target
        if target >= maximum:
            self._limiting_phase[direction] = None
        self._restore_since[direction] = None
        self._last_change_at = now
        self._log(
            f"{direction.capitalize()} limit {current} W -> {self.limit_w(direction)} W."
        )
        return True

    def _check_stale(self, now: datetime) -> bool:
        """Mark the meter stale after STALE_AFTER_S without a sample; the
        limits fall back to the blind bound (BLIND_SHARE of the capacity, at
        most the configured maximum). Returns True on a change."""
        if self._stale:
            return False
        if (
            self._last_sample_at is None
            or (now - self._last_sample_at).total_seconds() > STALE_AFTER_S
        ):
            self._stale = True
            for direction in (CHARGE, DISCHARGE):
                self._limit[direction] = None
                self._limiting_phase[direction] = None
                self._restore_since[direction] = None
            if self.is_active():
                self._log(
                    f"No grid sample for {STALE_AFTER_S} s; the fuse guard is "
                    f"blind and bounds the charger to {BLIND_SHARE:.0%} of the "
                    "capacity of its phases.",
                    level="WARNING",
                )
            return True
        return False

    # ── Output ───────────────────────────────────────────────────────────

    async def _announce(self, reason: str):
        """Publish an unsolicited change: event first, so the main app can
        re-apply, then the sensor."""
        if self.is_active():
            self.event_bus.emit_event(
                "fuse_guard_limit_change",
                charge_limit_w=self.charge_limit_w,
                discharge_limit_w=self.discharge_limit_w,
                phase=self._limiting_phase[CHARGE] or self._limiting_phase[DISCHARGE],
                reason=reason,
            )
        await self._publish()

    async def _publish(self):
        """Write the sensor, only when something in it changed."""
        active = self.is_active()
        if active:
            state = self.charge_limit_w
            attributes = {
                "discharge_limit_w": self.discharge_limit_w,
                "limiting_phase": (
                    self._limiting_phase[CHARGE] or self._limiting_phase[DISCHARGE]
                ),
                "meter_stale": self._stale,
                "capacity_per_phase_w": self.capacity_per_phase_w(),
                "charger_phases": self.charger_phases(),
                "clips": self._clip_count,
            }
        else:
            state = "unavailable"
            attributes = {"meter_stale": self._stale}
        snapshot = (state, tuple(sorted((k, str(v)) for k, v in attributes.items())))
        self._publish_pending = False
        if snapshot == self._published:
            return
        self._published = snapshot
        try:
            await self.hass.set_state(
                SENSOR_FUSE_GUARD_LIMIT, state=state, attributes=attributes
            )
        except Exception as e:  # noqa: BLE001  # HA may be restarting
            self._log(
                f"Could not write {SENSOR_FUSE_GUARD_LIMIT}: {e}", level="WARNING"
            )
