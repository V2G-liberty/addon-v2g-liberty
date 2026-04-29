"""Automatic detection of which grid phase(s) the charger is connected to.

Runs a short charge (and optionally discharge) test while observing which
grid consumption entities respond, to determine the charger's phase.

The algorithm:
1. Baseline: snapshot of grid consumption (instant, single get_state read)
2. Charge test: charge at minimum power, poll every 5 sec until a clear
   result is found or max 2 min timeout. Charger may take time to ramp up.
3. Discharge test (bidi only): same approach as charge test
4. Determine result: the phase(s) closest to the expected power change win

Results are stored via the provided save callback and reported via HA events.
"""

import asyncio
from datetime import datetime

from . import constants as c


class ChargerPhaseDetector:
    """Detects which grid phase(s) the charger is connected to."""

    _POLL_INTERVAL = 5  # seconds between polls
    _TEST_TIMEOUT = 120  # max seconds per charge/discharge test
    _MIN_DELTA_FACTOR = 0.5  # delta must be >= 50% of charge power

    def __init__(self, hass, evse_client, log, grid_entities, charge_power_w):
        """Initialise the detector.

        Args:
            hass: AppDaemon Hass instance (for get_state, fire_event, run_in)
            evse_client: EVSE client (start_charge_with_power, stop_charging, etc.)
            log: logging callable
            grid_entities: list of consumption entity IDs (1 or 3)
            charge_power_w: minimum charge power in watts (e.g. 1380)
        """
        self._hass = hass
        self._evse = evse_client
        self._log = log
        self._grid_entities = grid_entities
        self._charge_power = charge_power_w

    async def run(self) -> dict:
        """Run the full phase detection sequence.

        Automatically determines if the charger is bidirectional based on
        whether a discharge power limit is configured.

        Returns:
            dict with keys:
                connected_to_phase: int | list[int] | None
                detected_at: str (ISO 8601) | None
                success: bool
                error: str | None
        """
        self._log("Starting charger phase detection")

        # Check preconditions
        if not await self._evse.is_car_connected():
            return self._failure("No car connected")

        if len(self._grid_entities) != 3:
            return self._failure(
                f"Expected 3 grid consumption entities, got {len(self._grid_entities)}"
            )

        try:
            # Step 1: Baseline (instant snapshot via get_state)
            self._fire_progress("baseline")
            baseline = self._read_grid_entities()
            if baseline is None:
                return self._failure("Could not read grid entities for baseline")

            # Step 2: Charge test (poll until clear result or timeout)
            self._fire_progress("charge_test")
            await self._evse.start_charge_with_power(
                charge_power=self._charge_power, source="phase_detection"
            )
            charge_delta = await self._poll_until_clear(baseline)
            await self._evse.stop_charging()

            if charge_delta is None:
                return self._failure("Charge test timed out without a clear result")

            # Step 3: Discharge test (bidi only, poll until clear or timeout)
            discharge_delta = None
            is_bidi = c.CHARGER_MAX_DISCHARGE_POWER > 0
            if is_bidi:
                self._fire_progress("discharge_test")
                await self._evse.start_charge_with_power(
                    charge_power=-self._charge_power, source="phase_detection"
                )
                discharge_delta = await self._poll_until_clear(baseline)
                await self._evse.stop_charging()

                if discharge_delta is None:
                    return self._failure(
                        "Discharge test timed out without a clear result"
                    )

            # Step 4: Determine result
            result = self._determine_phase(charge_delta, discharge_delta)
            return result

        except Exception as e:
            # Ensure charger is stopped on any error
            try:
                await self._evse.stop_charging()
            except Exception:
                pass
            return self._failure(f"Detection failed: {e}")

    async def _poll_until_clear(
        self, baseline: dict[int, float]
    ) -> dict[int, float] | None:
        """Poll grid entities until a clear delta is detected or timeout.

        Returns the delta per phase if a clear result is found, or None on timeout.
        A "clear result" means at least one phase has a delta >= 50% of the
        expected charge power (single phase) or all phases have a delta >= 50%
        of the expected per-phase power (3-phase charger).
        """
        min_delta = self._charge_power * self._MIN_DELTA_FACTOR
        min_delta_3p = (self._charge_power / 3) * self._MIN_DELTA_FACTOR
        elapsed = 0

        while elapsed < self._TEST_TIMEOUT:
            await asyncio.sleep(self._POLL_INTERVAL)
            elapsed += self._POLL_INTERVAL

            readings = self._read_grid_entities()
            if readings is None:
                continue  # skip bad reading, try again

            delta = {phase: readings[phase] - baseline[phase] for phase in baseline}

            # Check: any single phase above threshold?
            max_phase = max(delta, key=lambda p: abs(delta[p]))
            if abs(delta[max_phase]) >= min_delta:
                self._log(
                    f"Clear result after {elapsed}s: "
                    f"L{max_phase} delta={delta[max_phase]:.0f}W"
                )
                return delta

            # Check: all phases above 3-phase threshold?
            if all(abs(delta[p]) >= min_delta_3p for p in delta):
                self._log(f"Clear 3-phase result after {elapsed}s: deltas={delta}")
                return delta

        self._log(
            f"Timeout after {self._TEST_TIMEOUT}s, no clear result",
            level="WARNING",
        )
        return None

    def _read_grid_entities(self) -> dict[int, float] | None:
        """Read current values from all grid consumption entities.

        Returns dict {1: watts, 2: watts, 3: watts} or None if any read fails.
        """
        readings = {}
        for i, entity_id in enumerate(self._grid_entities, start=1):
            state = self._hass.get_state(entity_id)
            if state in (None, "", "unknown", "unavailable"):
                self._log(
                    f"Grid entity {entity_id} returned '{state}'",
                    level="WARNING",
                )
                return None
            try:
                readings[i] = float(state)
            except (TypeError, ValueError):
                self._log(
                    f"Grid entity {entity_id} returned non-numeric: '{state}'",
                    level="WARNING",
                )
                return None
        return readings

    def _determine_phase(
        self,
        charge_delta: dict[int, float],
        discharge_delta: dict[int, float] | None,
    ) -> dict:
        """Determine which phase(s) the charger is connected to.

        Logic:
        - Sort phases by abs(charge_delta), descending
        - Check if the top phase has delta >= 50% of charge power
        - Check if all 3 phases have similar delta (3-phase charger)
        - For bidi: verify discharge test is consistent
        """
        min_delta = self._charge_power * self._MIN_DELTA_FACTOR
        expected_per_phase_3p = self._charge_power / 3

        self._log(
            f"Charge deltas: {charge_delta}, "
            f"min_delta: {min_delta}, "
            f"discharge deltas: {discharge_delta}"
        )

        # Check for 3-phase charger: all phases have similar delta
        all_above_threshold = all(
            abs(charge_delta[p]) >= expected_per_phase_3p * self._MIN_DELTA_FACTOR
            for p in charge_delta
        )
        if all_above_threshold:
            # All phases respond significantly — likely 3-phase charger
            if discharge_delta is not None:
                # Verify discharge is also on all phases
                all_discharge = all(
                    abs(discharge_delta[p])
                    >= expected_per_phase_3p * self._MIN_DELTA_FACTOR
                    for p in discharge_delta
                )
                if not all_discharge:
                    return self._failure(
                        "Charge and discharge results inconsistent (3-phase)"
                    )
            return self._success([1, 2, 3])

        # Single phase: find the phase with the largest delta
        sorted_phases = sorted(
            charge_delta, key=lambda p: abs(charge_delta[p]), reverse=True
        )
        best_phase = sorted_phases[0]
        best_delta = abs(charge_delta[best_phase])

        if best_delta < min_delta:
            return self._failure(
                f"No phase reached 50% threshold "
                f"(best: L{best_phase} at {best_delta:.0f}W, "
                f"needed: {min_delta:.0f}W)"
            )

        # Verify with discharge test if available
        if discharge_delta is not None:
            discharge_sorted = sorted(
                discharge_delta,
                key=lambda p: abs(discharge_delta[p]),
                reverse=True,
            )
            if discharge_sorted[0] != best_phase:
                return self._failure(
                    f"Charge test points to L{best_phase}, "
                    f"discharge test points to L{discharge_sorted[0]}"
                )

        return self._success(best_phase)

    def _success(self, connected_to_phase) -> dict:
        now = datetime.now().astimezone().isoformat()
        self._log(f"Phase detection succeeded: {connected_to_phase}")
        return {
            "connected_to_phase": connected_to_phase,
            "detected_at": now,
            "success": True,
            "error": None,
        }

    def _failure(self, error: str) -> dict:
        self._log(f"Phase detection failed: {error}", level="WARNING")
        return {
            "connected_to_phase": None,
            "detected_at": None,
            "success": False,
            "error": error,
        }

    def _fire_progress(self, step: str):
        self._hass.fire_event("charger_phase_detection.progress", step=step)
