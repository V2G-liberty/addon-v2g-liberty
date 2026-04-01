"""Module for simulating naive (non-smart) charging behaviour.

Simulates what would happen if the charger simply charges at maximum power
whenever the car is connected and below the SoC target — no schedule
optimisation, no V2G discharge. The simulated power and SoC are stored
per interval so that savings calculations can be done as simple aggregations.

Runs in two modes:
- **Batch** (after data_repairer): fills naive_power_w for all intervals
  where it is still NULL (historical/repaired data). Uses pandas.
- **Real-time** (after each interval conclusion): calculates naive_power
  for the just-written interval, keeping SoC state in memory.
"""

import asyncio
import math

import numpy as np
import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .event_bus import EventBus
from .log_wrapper import get_class_method_logger


class NaiveChargingSimulator:
    """Simulates naive charging and stores results in interval_log.

    Naive charging rule: charge at max power whenever connected and
    SoC < target. No discharge, no schedule awareness.
    """

    data_store = None
    hass: Hass = None
    event_bus: EventBus = None

    # In-memory state for real-time tracking.
    _naive_soc: float | None = None
    _prev_connected: bool = False
    _charge_power_factor: float = 0.82

    def __init__(self, hass: Hass, event_bus: EventBus):
        self.hass = hass
        self.event_bus = event_bus
        self.__log = get_class_method_logger(hass.log)

    async def initialise(self):
        """Subscribe to events and run initial batch in the background."""
        if self.data_store is None or not self.data_store.is_available:
            self.__log("Skipped: DataStore not available.", level="WARNING")
            return

        # Calculate charge power factor from historical data.
        self._charge_power_factor = self.data_store.get_charge_power_factor()
        self.__log(
            f"Charge power factor: {self._charge_power_factor:.3f} "
            f"(effective naive power: "
            f"{c.CHARGER_MAX_CHARGE_POWER * self._charge_power_factor:.0f}W)."
        )

        # Subscribe to events.
        self.event_bus.on("interval_concluded", self._on_interval_concluded)
        self.event_bus.on("repairer_complete", self._on_repairer_complete)

        # Run initial batch in the background.
        asyncio.ensure_future(self._run_batch())
        self.__log("Initialised, batch running in background.")

    # ------------------------------------------------------------------
    #  Real-time: called after each DataMonitor interval conclusion
    # ------------------------------------------------------------------

    def _on_interval_concluded(self, timestamp: str, **kwargs):
        """Calculate naive charging for the just-written interval."""
        if self.data_store is None or not self.data_store.is_available:
            return

        # Read the interval that was just written.
        conn = self.data_store.connection
        if conn is None:
            return
        cursor = conn.cursor()
        cursor.execute(
            "SELECT energy_kwh, soc_pct, availability_pct "
            "FROM interval_log WHERE timestamp = ?",
            (timestamp,),
        )
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return

        # Initialise naive SoC from DB on first call after (re)start.
        if self._naive_soc is None:
            stored = self.data_store.get_last_naive_soc()
            if stored is not None:
                self._naive_soc = stored
            elif row["soc_pct"] is not None:
                self._naive_soc = row["soc_pct"]
            else:
                self._naive_soc = 0.0

        connected = (row["availability_pct"] or 0) > 0
        measured_soc = row["soc_pct"]

        # Detect return from trip: apply real SoC consumption to naive SoC.
        if connected and not self._prev_connected and measured_soc is not None:
            # The car was away and just reconnected.
            # We don't know the SoC when it left in real-time mode,
            # so we sync naive SoC to the measured SoC on reconnect
            # (conservative: assumes naive driver also depleted the same).
            self._naive_soc = min(measured_soc, self._naive_soc)

        # Naive charging logic.
        soc_max = c.CAR_MAX_SOC_IN_PERCENT
        max_power = c.CHARGER_MAX_CHARGE_POWER * self._charge_power_factor
        efficiency = math.sqrt(c.ROUNDTRIP_EFFICIENCY_FACTOR)

        if connected and self._naive_soc < soc_max:
            naive_power = float(max_power)
        else:
            naive_power = 0.0

        # Update naive SoC.
        dt_hours = c.FM_EVENT_RESOLUTION_IN_MINUTES / 60
        energy_kwh = (naive_power / 1000.0) * dt_hours * efficiency
        capacity = c.CAR_MAX_CAPACITY_IN_KWH
        soc_increase = (energy_kwh / capacity) * 100 if capacity > 0 else 0
        self._naive_soc = min(self._naive_soc + soc_increase, soc_max)

        self._prev_connected = connected

        # Write to database.
        self.data_store.update_naive_charging(
            [(naive_power, round(self._naive_soc, 2), timestamp)]
        )

    # ------------------------------------------------------------------
    #  Batch: called after data_repairer completes
    # ------------------------------------------------------------------

    def _on_repairer_complete(self, **kwargs):
        """Run batch simulation after the repairer has finished."""
        asyncio.ensure_future(self._run_batch())

    async def _run_batch(self):
        """Simulate naive charging for all intervals missing naive_power_w."""
        if self.data_store is None or not self.data_store.is_available:
            return

        conn = self.data_store.connection
        if conn is None:
            return

        # Fetch all intervals (need full history for SoC tracking).
        cursor = conn.cursor()
        cursor.execute(
            "SELECT timestamp, energy_kwh, soc_pct, availability_pct, "
            "       naive_power_w "
            "FROM interval_log "
            "WHERE is_repaired < 2 "
            "ORDER BY timestamp"
        )
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            self.__log("Batch: no intervals to process.")
            return

        df = pd.DataFrame(
            [dict(r) for r in rows],
            columns=[
                "timestamp",
                "energy_kwh",
                "soc_pct",
                "availability_pct",
                "naive_power_w",
            ],
        )

        # Check how many need simulation.
        needs_sim = df["naive_power_w"].isna()
        if not needs_sim.any():
            self.__log("Batch: all intervals already have naive charging data.")
            return

        self.__log(
            f"Batch: simulating naive charging for {needs_sim.sum()} of "
            f"{len(df)} intervals."
        )

        # Run simulation over all rows (need full history for SoC state).
        result = self._simulate(df)

        # Only write rows that were missing.
        to_update = result.loc[
            needs_sim, ["naive_power_w", "naive_soc_pct", "timestamp"]
        ]
        update_rows = list(to_update.itertuples(index=False, name=None))
        self.data_store.update_naive_charging(update_rows)

        # Update in-memory state to the last simulated value.
        self._naive_soc = float(result["naive_soc_pct"].iloc[-1])
        self._prev_connected = (result["availability_pct"].iloc[-1] or 0) > 0

        self.__log(f"Batch: completed, updated {len(update_rows)} row(s).")

    def _simulate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run naive charging simulation over a DataFrame.

        Adds naive_power_w and naive_soc_pct columns.
        Uses the same algorithm as the MichaMand analysis codebase.
        """
        soc_max = float(c.CAR_MAX_SOC_IN_PERCENT)
        max_power = float(c.CHARGER_MAX_CHARGE_POWER) * self._charge_power_factor
        efficiency = math.sqrt(c.ROUNDTRIP_EFFICIENCY_FACTOR)
        capacity = float(c.CAR_MAX_CAPACITY_IN_KWH)
        dt_hours = c.FM_EVENT_RESOLUTION_IN_MINUTES / 60

        connected = df["availability_pct"].fillna(0) > 0
        soc = df["soc_pct"]

        # Initialise naive SoC from first valid measured SoC.
        first_valid = soc.first_valid_index()
        current_soc = float(soc.iloc[first_valid]) if first_valid is not None else 0.0
        prev_connected = False
        soc_when_left: float | None = None
        last_valid_soc: float | None = None

        naive_power_arr = np.zeros(len(df))
        naive_soc_arr = np.zeros(len(df))

        for i in range(len(df)):
            is_conn = bool(connected.iloc[i])
            measured = soc.iloc[i]

            if not pd.isna(measured):
                last_valid_soc = float(measured)

            # Detect leaving.
            if not is_conn and prev_connected:
                soc_when_left = last_valid_soc

            # Detect return: apply SoC consumption from trip.
            if is_conn and not prev_connected and not pd.isna(measured):
                if soc_when_left is not None:
                    soc_change = float(measured) - soc_when_left
                    current_soc += soc_change
                current_soc = max(0.0, min(current_soc, soc_max))

            # Naive charging: charge at max power if below target.
            if is_conn and current_soc < soc_max:
                power = max_power
            else:
                power = 0.0

            naive_power_arr[i] = power

            # Update SoC.
            energy_kwh = (power / 1000.0) * dt_hours * efficiency
            soc_increase = (energy_kwh / capacity) * 100 if capacity > 0 else 0
            current_soc = min(current_soc + soc_increase, soc_max)
            naive_soc_arr[i] = round(current_soc, 2)

            prev_connected = is_conn

        df = df.copy()
        df["naive_power_w"] = naive_power_arr
        df["naive_soc_pct"] = naive_soc_arr
        return df
