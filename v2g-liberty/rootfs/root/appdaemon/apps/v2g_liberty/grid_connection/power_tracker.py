"""Power-weighted-duration tracker for calculating average power over intervals.

Tracks power changes and calculates the weighted average power over a
concluded interval. Used for grid consumption/production per phase and
PV production per panel.

Usage:
    tracker = PowerTracker()
    tracker.update(power=1500.0, now=datetime.now())  # power changed
    tracker.update(power=1200.0, now=datetime.now())  # power changed again
    avg = tracker.conclude(now=datetime.now())         # returns weighted avg
    # avg is now ready to store; tracker is reset for next interval
"""

from datetime import datetime


class PowerTracker:
    """Tracks power×duration to calculate weighted average power over intervals."""

    def __init__(self):
        self._power_x_duration: float = 0.0
        self._total_duration: float = 0.0
        self._current_power: float = 0.0
        self._last_update: datetime | None = None

    def update(self, power: float, now: datetime):
        """Record a power change.

        Accumulates the previous power value × elapsed time since last update.

        Args:
            power: current power value (any unit, e.g. W or kW)
            now: current timestamp
        """
        if self._last_update is not None:
            elapsed = (now - self._last_update).total_seconds()
            if elapsed > 0:
                self._power_x_duration += self._current_power * elapsed
                self._total_duration += elapsed
        self._current_power = power
        self._last_update = now

    def conclude(self, now: datetime) -> float | None:
        """Conclude the current interval and return the weighted average power.

        Accumulates the final segment (from last update to now), calculates
        the average, and resets for the next interval.

        Args:
            now: current timestamp (end of interval)

        Returns:
            Weighted average power over the interval, or None if no data.
        """
        # Accumulate final segment
        self.update(self._current_power, now)

        if self._total_duration == 0:
            self.reset(now)
            return None

        avg = self._power_x_duration / self._total_duration
        self.reset(now)
        return round(avg, 3)

    def reset(self, now: datetime):
        """Reset for the next interval, keeping the current power value."""
        self._power_x_duration = 0.0
        self._total_duration = 0.0
        self._last_update = now
