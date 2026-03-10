"""Module for data validation and repair of the interval_log table.

Ensures completeness and physical consistency of the 5-minute time series:
  Step 0: Fill gaps — insert missing 5-min slots with context-aware defaults
  Step 1: Type A — blank false constant SoC (sensor stuck, energy ≈ 0)
  Step 2: Type B — interpolate energy in gap-filled rows
  Step 3: Type C — reconstruct SoC from energy (energy-validated)
  Step 4: Type D — fill constant SoC where energy ≈ 0
  Step 5: Bounds validation — log impossible states (no modification)

Runs at startup (full repair) and periodically (incremental).
Repaired/added rows are marked with is_repaired=1 in interval_log.
"""

from datetime import timedelta, timezone
from pathlib import Path

import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .data_store import DataStore
from .log_wrapper import get_class_method_logger
from .v2g_globals import get_local_now

# ---------------------------------------------------------------------------
# Configuration — adjustable per deployment
# ---------------------------------------------------------------------------
MAX_GAP_LENGTH: int = 20  # Max gap slots to fill (20 × 5 min = 100 min)
SOC_JUMP_THRESHOLD: float = 10.0  # % SoC change considered a jump
PERIODIC_LOOKBACK_HOURS: int = 48  # Scope for periodic (incremental) runs

# Type B: energy interpolation
MAX_ENERGY_INTERPOLATION_GAP: int = 12  # Max slots for energy interpolation
ENERGY_ENDPOINT_TOLERANCE: float = 0.05  # Max 5% relative difference

# Type C: SoC reconstruction from energy
MAX_SOC_RECONSTRUCTION_GAP: int = 100  # Max slots for SoC reconstruction
SOC_RECONSTRUCTION_TOLERANCE: float = 5.0  # Max pp deviation at endpoint

# Type A & D: negligible energy threshold
CONSTANT_POWER_TOLERANCE: float = 0.05  # 5% of max charger energy/interval

# Type A-up: upward jump reconstruction tolerance
UPWARD_JUMP_TOLERANCE: float = 10.0  # Max pp mismatch theoretical vs actual

# Report file written after historical import + repair
_REPORT_FILE = Path("/data/fm_historical_import_report.txt")


def _negligible_energy_threshold() -> float:
    """Energy threshold below which flow is considered negligible.

    Returns kWh: 5% of max charger energy per 5-min interval.
    """
    max_energy = c.CHARGER_MAX_CHARGE_POWER / 1000 * 5 / 60
    return max_energy * CONSTANT_POWER_TOLERANCE


class DataRepairer:
    """Validates and repairs gaps in interval_log data."""

    data_store: DataStore = None

    def __init__(self, hass: Hass):
        self.__hass = hass
        self.__log = get_class_method_logger(hass.log)

    async def initialise(self):
        """Run full repair at startup, then schedule periodic incremental runs."""
        summary = self.run_full_repair()
        total = _total_repairs(summary)
        if total > 0:
            self.__log(f"Startup repair complete: {_format_summary(summary)}")
        else:
            self.__log("Startup repair: no repairs needed.")
        violations = summary.get("violations", {})
        if violations:
            self.__log(
                f"Validation found {sum(violations.values())} issues: {violations}",
                level="WARNING",
            )

        # Schedule incremental repair every 6 hours.
        six_hours_in_seconds = 6 * 60 * 60
        await self.__hass.run_every(
            self.run_incremental_repair, "now+3600", six_hours_in_seconds
        )

    def run_full_repair(self) -> dict:
        """Repair entire interval_log from first to last record."""
        conn = self.data_store.connection
        cursor = conn.cursor()
        cursor.execute(
            "SELECT MIN(timestamp) AS first_ts, MAX(timestamp) AS last_ts "
            "FROM interval_log"
        )
        row = cursor.fetchone()
        cursor.close()

        if row is None or row["first_ts"] is None:
            self.__log("No interval data to repair.")
            return _empty_summary()

        return self._repair_range(row["first_ts"], row["last_ts"])

    async def run_incremental_repair(self, _kwargs=None):
        """Repair the last PERIODIC_LOOKBACK_HOURS hours."""
        now = get_local_now()
        start = (now - timedelta(hours=PERIODIC_LOOKBACK_HOURS)).isoformat()
        end = now.isoformat()

        summary = self._repair_range(start, end)
        total = _total_repairs(summary)
        if total > 0:
            self.__log(f"Incremental repair: {_format_summary(summary)}")

    def write_report(self, summary: dict):
        """Append repair summary to the report file.

        Called after a post-import full repair to write statistics.
        """
        conn = self.data_store.connection
        cursor = conn.cursor()
        cursor.execute(
            "SELECT COUNT(*) AS cnt, MIN(timestamp) AS first_ts, "
            "MAX(timestamp) AS last_ts FROM interval_log"
        )
        row = cursor.fetchone()
        cursor.close()

        lines = [
            f"Repair completed at {get_local_now().isoformat()}",
            "",
            "Summary:",
            f"  Total rows:            {row['cnt']}",
            f"  Period:                {row['first_ts']} — {row['last_ts']}",
            f"  Oldest row:            {row['first_ts']}",
            f"  SoC upward reconstr.: {summary['soc_reconstructed_up']} (Type A-up)",
            f"  SoC values blanked:    {summary['soc_blanked']} (Type A-down)",
            f"  Energy interpolated:   {summary['energy_interpolated']} (Type B)",
            f"  SoC reconstructed:     {summary['soc_reconstructed']} (Type C)",
            f"  SoC constant filled:   {summary['soc_constant_filled']} (Type D)",
            f"  Gaps filled:           {summary['gaps_filled']}",
        ]
        violations = summary.get("violations", {})
        if violations:
            lines.append(f"  Violations logged:     {sum(violations.values())}")
        lines.append("")

        with open(_REPORT_FILE, "a") as f:
            f.write("\n".join(lines))

        self.__log(f"Repair report written to {_REPORT_FILE}.")

    # ------------------------------------------------------------------
    # Core repair pipeline
    # ------------------------------------------------------------------

    def _repair_range(self, start: str, end: str) -> dict:
        """Core repair logic for a time range. Returns summary stats."""
        summary = _empty_summary()

        df = pd.read_sql_query(
            "SELECT timestamp, energy_kwh, app_state, soc_pct, "
            "availability_pct, is_repaired FROM interval_log "
            "WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            self.data_store.connection,
            params=(start, end),
        )

        if df.empty:
            return summary

        # Detect original timezone from the first timestamp so we can
        # convert back when writing repaired rows (timestamps in interval_log
        # are stored in local timezone, not UTC).
        first_ts = pd.Timestamp(df["timestamp"].iloc[0])
        local_tz = first_ts.tzinfo or timezone.utc

        # Convert to UTC for consistent processing (pd.date_range works
        # cleanly with a single timezone).
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()

        # Step 0: Fill gaps (insert missing 5-min rows)
        df, gaps_filled = self._fill_gaps(df)
        summary["gaps_filled"] = gaps_filled

        # Step 1a: Type A-up — reconstruct SoC for upward jumps
        df, soc_reconstructed_up = self._reconstruct_upward_jumps(df)
        summary["soc_reconstructed_up"] = soc_reconstructed_up

        # Step 1b: Type A-down — blank false constant SoC before downward jumps
        df, soc_blanked = self._blank_false_constant_soc(df)
        summary["soc_blanked"] = soc_blanked

        # Step 2: Type B — interpolate energy in gap-filled rows
        df, energy_interpolated = self._interpolate_energy(df)
        summary["energy_interpolated"] = energy_interpolated

        # Step 3: Type C — reconstruct SoC from energy
        df, soc_reconstructed = self._reconstruct_soc_from_energy(df)
        summary["soc_reconstructed"] = soc_reconstructed

        # Step 4: Type D — fill constant SoC (energy ≈ 0)
        df, soc_constant_filled = self._fill_constant_soc(df)
        summary["soc_constant_filled"] = soc_constant_filled

        # Step 5: Validate bounds (log only)
        summary["violations"] = self._validate_bounds(df)

        # Write repaired rows back to DB (convert timestamps back to
        # the original local timezone to match existing rows).
        written = self._write_repaired_rows(df, local_tz)
        if written > 0:
            self.__log(f"Wrote {written} repaired rows to interval_log.")

        # Mark any remaining pending-review rows (is_repaired=2) as
        # reviewed (is_repaired=0).  These are imported rows that the
        # repairer inspected but did not need to modify.
        reviewed = self._mark_pending_as_reviewed(start, end)
        if reviewed > 0:
            self.__log(f"Marked {reviewed} imported rows as reviewed.")

        return summary

    # ------------------------------------------------------------------
    # Step 0: Gap filling (unchanged)
    # ------------------------------------------------------------------

    def _fill_gaps(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Insert missing 5-min slots with context-aware defaults.

        Returns updated DataFrame and count of inserted rows.
        """
        if len(df) < 2:
            return df, 0

        # Generate complete 5-min index
        full_index = pd.date_range(
            start=df.index.min(),
            end=df.index.max(),
            freq="5min",
        )

        # Find missing timestamps
        missing = full_index.difference(df.index)
        if len(missing) == 0:
            return df, 0

        # Reindex to include gaps (NaN for missing rows)
        df = df.reindex(full_index)

        # Identify gap blocks (consecutive NaN groups)
        is_gap = df["energy_kwh"].isna()
        gap_groups = (is_gap != is_gap.shift()).cumsum()

        filled_count = 0
        for _, block_idx in df[is_gap].groupby(gap_groups).groups.items():
            block = df.loc[block_idx]
            gap_length = len(block)

            # Get context from surrounding rows
            before = _get_row_before(df, block.index[0])
            after = _get_row_after(df, block.index[-1])

            if gap_length > MAX_GAP_LENGTH:
                # Long gap: fill with unknown defaults
                fill = {
                    "energy_kwh": 0.0,
                    "app_state": "unknown",
                    "availability_pct": 0.0,
                }
            else:
                # Short gap: infer from context
                fill = _infer_gap_context(before, after)

            df.loc[block.index, "energy_kwh"] = fill["energy_kwh"]
            df.loc[block.index, "app_state"] = fill["app_state"]
            df.loc[block.index, "availability_pct"] = fill["availability_pct"]
            df.loc[block.index, "soc_pct"] = None
            df.loc[block.index, "is_repaired"] = 1

            filled_count += gap_length

        return df, filled_count

    # ------------------------------------------------------------------
    # Step 1a: Type A-up — Reconstruct SoC for upward jumps
    # ------------------------------------------------------------------

    def _reconstruct_upward_jumps(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Reconstruct or blank false constant SoC before upward jumps.

        For each upward jump (soc_diff > SOC_JUMP_THRESHOLD):
        1. Find the constant SoC block preceding the jump
        2. Calculate the theoretical SoC jump from cumulative energy
        3. If theoretical ≈ actual (within UPWARD_JUMP_TOLERANCE):
           → reconstruct SoC using scaled cumulative energy
        4. Otherwise → blank the block (set SoC to NaN)

        Returns (df, count_of_modified_rows).
        """
        soc = df["soc_pct"].copy()
        soc_valid = soc.dropna()

        if len(soc_valid) < 2:
            return df, 0

        capacity = c.CAR_MAX_CAPACITY_IN_KWH

        # Find upward jumps only
        soc_diff = soc_valid.diff()
        jump_indices = soc_diff[soc_diff > SOC_JUMP_THRESHOLD].index

        modified = 0
        for jump_idx in jump_indices:
            pos = soc_valid.index.get_loc(jump_idx)
            if pos == 0:
                continue

            soc_after = soc_valid.loc[jump_idx]
            prev_idx = soc_valid.index[pos - 1]
            constant_value = soc_valid.loc[prev_idx]

            # Walk backwards to find the constant block
            block_indices = [prev_idx]
            for i in range(pos - 2, -1, -1):
                check_idx = soc_valid.index[i]
                if soc_valid.loc[check_idx] == constant_value:
                    block_indices.append(check_idx)
                else:
                    break

            # Need at least 2 constant values to consider it a frozen block
            if len(block_indices) < 2:
                continue

            # Block indices are in reverse order; sort chronologically
            block_indices.sort()

            # Calculate theoretical jump from energy in the block
            block_energy = df.loc[block_indices, "energy_kwh"]
            charging_energy = block_energy.clip(lower=0).sum()
            theoretical_jump = 100 * charging_energy / capacity
            actual_jump = soc_after - constant_value

            if (
                theoretical_jump > 0
                and abs(theoretical_jump - actual_jump) < UPWARD_JUMP_TOLERANCE
            ):
                # Energy explains the jump → reconstruct SoC with scaling
                scale = actual_jump / theoretical_jump
                cumulative_energy = block_energy.clip(lower=0).cumsum()
                reconstructed = (
                    constant_value + scale * 100 * cumulative_energy / capacity
                )
                for idx in block_indices:
                    df.loc[idx, "soc_pct"] = round(float(reconstructed.loc[idx]), 1)
                    df.loc[idx, "is_repaired"] = 1
                modified += len(block_indices)
            else:
                # Energy does not explain the jump → blank the block
                for idx in block_indices:
                    df.loc[idx, "soc_pct"] = None
                    df.loc[idx, "is_repaired"] = 1
                modified += len(block_indices)

        return df, modified

    # ------------------------------------------------------------------
    # Step 1b: Type A-down — Blank false constant SoC before downward jumps
    # ------------------------------------------------------------------

    def _blank_false_constant_soc(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Blank false constant SoC runs before downward jumps.

        Detects downward SoC jumps (diff < -SOC_JUMP_THRESHOLD) and blanks
        the constant run leading up to the jump.

        Only modifies soc_pct (sets to NaN). Marks modified rows as repaired.
        """
        soc = df["soc_pct"].copy()
        soc_valid = soc.dropna()

        if len(soc_valid) < 2:
            return df, 0

        # Find downward jumps only
        soc_diff = soc_valid.diff()
        jump_indices = soc_diff[soc_diff < -SOC_JUMP_THRESHOLD].index

        blanked = 0
        for jump_idx in jump_indices:
            pos = soc_valid.index.get_loc(jump_idx)
            if pos == 0:
                continue

            prev_idx = soc_valid.index[pos - 1]
            constant_value = soc_valid.loc[prev_idx]

            # Walk backwards through constant values
            blank_indices = [prev_idx]
            for i in range(pos - 2, -1, -1):
                check_idx = soc_valid.index[i]
                if soc_valid.loc[check_idx] == constant_value:
                    blank_indices.append(check_idx)
                else:
                    break

            # Only blank if there's a run of constant values (≥ 2).
            # A single value before a jump is not a "constant run".
            if len(blank_indices) >= 2:
                for idx in blank_indices:
                    df.loc[idx, "soc_pct"] = None
                    df.loc[idx, "is_repaired"] = 1
                    blanked += 1

        return df, blanked

    # ------------------------------------------------------------------
    # Step 2: Type B — Energy interpolation
    # ------------------------------------------------------------------

    def _interpolate_energy(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Interpolate energy in gap-filled rows.

        Gap-filled rows (is_repaired=1, energy_kwh=0.0) likely had real energy
        that we don't know.  If the surrounding real energy values are within
        5% (relative), fill the gap linearly.

        Max gap length: MAX_ENERGY_INTERPOLATION_GAP.
        Endpoints must be same direction (both positive or both negative).
        """
        # Identify gap-filled rows: is_repaired=1 AND energy=0
        is_gap_filled = (df["is_repaired"] == 1) & (df["energy_kwh"] == 0.0)
        if not is_gap_filled.any():
            return df, 0

        gap_groups = (is_gap_filled != is_gap_filled.shift()).cumsum()
        filled = 0

        for _, block_idx in df[is_gap_filled].groupby(gap_groups).groups.items():
            block = df.loc[block_idx]
            if len(block) > MAX_ENERGY_INTERPOLATION_GAP:
                continue

            # Get energy from adjacent real rows
            before_energy = _get_adjacent_energy(df, block.index[0], direction=-1)
            after_energy = _get_adjacent_energy(df, block.index[-1], direction=1)
            if before_energy is None or after_energy is None:
                continue

            # Both must be significant (not negligible)
            threshold = _negligible_energy_threshold()
            if abs(before_energy) < threshold and abs(after_energy) < threshold:
                continue  # Both near zero — no energy to interpolate

            # Must be same direction
            if before_energy * after_energy < 0:
                continue

            # Relative difference must be ≤ 5%
            max_abs = max(abs(before_energy), abs(after_energy))
            if max_abs < 0.001:
                continue
            if abs(before_energy - after_energy) / max_abs > ENERGY_ENDPOINT_TOLERANCE:
                continue

            # Linearly interpolate energy
            n = len(block) + 1
            step = (after_energy - before_energy) / n
            for i, idx in enumerate(block.index, start=1):
                df.loc[idx, "energy_kwh"] = round(before_energy + step * i, 6)

            filled += len(block)

        return df, filled

    # ------------------------------------------------------------------
    # Step 3: Type C — SoC reconstruction from energy
    # ------------------------------------------------------------------

    def _reconstruct_soc_from_energy(
        self, df: pd.DataFrame
    ) -> tuple[pd.DataFrame, int]:
        """Reconstruct SoC from cumulative energy + battery capacity.

        For NULL SoC gaps where energy ≠ 0, calculate:
            soc[i] = soc_before + cumsum(energy[0:i] / capacity × 100)

        Validates the reconstructed endpoint against the actual SoC after the
        gap.  If deviation > SOC_RECONSTRUCTION_TOLERANCE pp, skips the gap.

        Max gap: MAX_SOC_RECONSTRUCTION_GAP.
        """
        is_null_soc = df["soc_pct"].isna()
        if not is_null_soc.any():
            return df, 0

        threshold = _negligible_energy_threshold()
        capacity = c.CAR_MAX_CAPACITY_IN_KWH
        nan_groups = (is_null_soc != is_null_soc.shift()).cumsum()
        reconstructed_count = 0

        for _, block_idx in df[is_null_soc].groupby(nan_groups).groups.items():
            block = df.loc[block_idx]
            if len(block) > MAX_SOC_RECONSTRUCTION_GAP:
                continue

            # Check that at least some rows have significant energy
            block_energy = df.loc[block.index, "energy_kwh"]
            if (block_energy.abs() <= threshold).all():
                continue  # All negligible → Type D, not Type C

            # Get SoC endpoints
            before_soc = _get_soc_before(df, block.index[0])
            after_soc = _get_soc_after(df, block.index[-1])
            if before_soc is None or after_soc is None:
                continue

            # Reconstruct SoC cumulatively from energy
            cumulative_soc = float(before_soc)
            soc_values = []
            for idx in block.index:
                energy = float(df.loc[idx, "energy_kwh"])
                delta_soc = energy / capacity * 100
                cumulative_soc += delta_soc
                soc_values.append(round(cumulative_soc, 1))

            # Validate: does the last reconstructed value match the endpoint?
            if abs(soc_values[-1] - after_soc) > SOC_RECONSTRUCTION_TOLERANCE:
                continue  # Too much deviation → skip

            # Apply reconstructed values
            for idx, soc_val in zip(block.index, soc_values):
                df.loc[idx, "soc_pct"] = soc_val
                df.loc[idx, "is_repaired"] = 1

            reconstructed_count += len(block)

        return df, reconstructed_count

    # ------------------------------------------------------------------
    # Step 4: Type D — SoC constant fill
    # ------------------------------------------------------------------

    def _fill_constant_soc(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Fill NULL SoC with constant value where energy is negligible.

        When there is no significant energy flow, SoC should not change.
        Requires BOTH endpoints and they must be consistent (within
        SOC_JUMP_THRESHOLD) to avoid filling incorrectly blanked gaps.
        """
        is_null_soc = df["soc_pct"].isna()
        if not is_null_soc.any():
            return df, 0

        threshold = _negligible_energy_threshold()
        nan_groups = (is_null_soc != is_null_soc.shift()).cumsum()
        filled = 0

        for _, block_idx in df[is_null_soc].groupby(nan_groups).groups.items():
            block = df.loc[block_idx]

            # All energy must be negligible
            block_energy = df.loc[block.index, "energy_kwh"]
            if not (block_energy.abs() <= threshold).all():
                continue  # Has significant energy → not Type D

            # Require BOTH endpoints
            before_soc = _get_soc_before(df, block.index[0])
            after_soc = _get_soc_after(df, block.index[-1])
            if before_soc is None or after_soc is None:
                continue

            # Endpoints must be consistent (no big jump across the gap)
            if abs(before_soc - after_soc) > SOC_JUMP_THRESHOLD:
                continue

            # Fill with the before value (SoC didn't change)
            df.loc[block.index, "soc_pct"] = before_soc
            df.loc[block.index, "is_repaired"] = 1
            filled += len(block)

        return df, filled

    # ------------------------------------------------------------------
    # Step 5: Bounds validation (log only)
    # ------------------------------------------------------------------

    def _validate_bounds(self, df: pd.DataFrame) -> dict:
        """Check for impossible values and log warnings.

        Does NOT modify data — only reports violations.
        """
        violations = {}

        # SoC out of range (1-100%)
        soc_valid = df["soc_pct"].dropna()
        soc_low = soc_valid[soc_valid < 1]
        soc_high = soc_valid[soc_valid > 100]
        if len(soc_low) > 0:
            violations["soc_below_1"] = len(soc_low)
            self.__log(f"{len(soc_low)} intervals with SoC < 1%.", level="WARNING")
        if len(soc_high) > 0:
            violations["soc_above_100"] = len(soc_high)
            self.__log(f"{len(soc_high)} intervals with SoC > 100%.", level="WARNING")

        # Energy exceeds charger limits per interval
        max_charge_kwh = c.CHARGER_MAX_CHARGE_POWER / 1000 * 5 / 60
        max_discharge_kwh = c.CHARGER_MAX_DISCHARGE_POWER / 1000 * 5 / 60
        tolerance = 1.1  # 10% tolerance

        energy_too_high = df[df["energy_kwh"] > max_charge_kwh * tolerance]
        energy_too_low = df[df["energy_kwh"] < -max_discharge_kwh * tolerance]
        if len(energy_too_high) > 0:
            violations["energy_exceeds_charge_limit"] = len(energy_too_high)
            self.__log(
                f"{len(energy_too_high)} intervals with energy above "
                f"charge limit ({max_charge_kwh:.3f} kWh).",
                level="WARNING",
            )
        if len(energy_too_low) > 0:
            violations["energy_exceeds_discharge_limit"] = len(energy_too_low)
            self.__log(
                f"{len(energy_too_low)} intervals with energy above "
                f"discharge limit ({max_discharge_kwh:.3f} kWh).",
                level="WARNING",
            )

        # Availability out of range (0-100%)
        avail_valid = df["availability_pct"].dropna()
        avail_low = avail_valid[avail_valid < 0]
        avail_high = avail_valid[avail_valid > 100]
        if len(avail_low) > 0:
            violations["availability_below_zero"] = len(avail_low)
            self.__log(
                f"{len(avail_low)} intervals with availability < 0%.",
                level="WARNING",
            )
        if len(avail_high) > 0:
            violations["availability_above_100"] = len(avail_high)
            self.__log(
                f"{len(avail_high)} intervals with availability > 100%.",
                level="WARNING",
            )

        # Impossible states: energy ≠ 0 when not_connected
        nc_with_energy = df[
            (df["app_state"] == "not_connected") & (df["energy_kwh"].abs() > 0.001)
        ]
        if len(nc_with_energy) > 0:
            violations["energy_while_not_connected"] = len(nc_with_energy)
            self.__log(
                f"{len(nc_with_energy)} intervals with energy while not_connected.",
                level="WARNING",
            )

        # Impossible states: SoC direction inconsistent with energy direction
        soc_series = df["soc_pct"]
        energy_series = df["energy_kwh"]
        soc_diff = soc_series.diff()
        # Only check where both SoC values are valid and energy is significant
        threshold = _negligible_energy_threshold()
        valid_mask = soc_diff.notna() & (energy_series.abs() > threshold)
        if valid_mask.any():
            # SoC drops while charging (energy > 0)
            soc_drops_while_charging = (
                (soc_diff < -1.0) & (energy_series > threshold)
            )[valid_mask]
            # SoC rises while discharging (energy < 0)
            soc_rises_while_discharging = (
                (soc_diff > 1.0) & (energy_series < -threshold)
            )[valid_mask]
            direction_violations = (
                soc_drops_while_charging.sum() + soc_rises_while_discharging.sum()
            )
            if direction_violations > 0:
                violations["soc_energy_direction_mismatch"] = int(direction_violations)
                self.__log(
                    f"{direction_violations} intervals with SoC direction "
                    "inconsistent with energy direction.",
                    level="WARNING",
                )

        return violations

    # ------------------------------------------------------------------
    # Write-back
    # ------------------------------------------------------------------

    def _write_repaired_rows(self, df: pd.DataFrame, local_tz=None) -> int:
        """Write repaired/new rows back to interval_log.

        Only writes rows where is_repaired=1.
        Uses INSERT OR REPLACE for both new gap-fill rows and updated rows.
        Converts timestamps back to ``local_tz`` so they match existing rows.
        """
        repaired = df[df["is_repaired"] == 1]
        if repaired.empty:
            return 0

        tz = local_tz or timezone.utc
        rows = []
        for ts, row in repaired.iterrows():
            soc = None if pd.isna(row["soc_pct"]) else float(row["soc_pct"])
            rows.append(
                {
                    "timestamp": ts.tz_convert(tz).isoformat(),
                    "energy_kwh": float(row["energy_kwh"]),
                    "app_state": str(row["app_state"]),
                    "soc_pct": soc,
                    "availability_pct": float(row["availability_pct"]),
                    "is_repaired": 1,
                }
            )

        conn = self.data_store.connection
        cursor = conn.cursor()
        cursor.executemany(
            "INSERT OR REPLACE INTO interval_log "
            "(timestamp, energy_kwh, app_state, soc_pct, "
            "availability_pct, is_repaired) "
            "VALUES (:timestamp, :energy_kwh, :app_state, :soc_pct, "
            ":availability_pct, :is_repaired)",
            rows,
        )
        conn.commit()
        cursor.close()
        return len(rows)

    def _mark_pending_as_reviewed(self, start: str, end: str) -> int:
        """Mark pending-review rows as reviewed (is_repaired 2 → 0).

        Called after the repair pipeline has processed a range.  Rows that
        were modified by the repairer already have is_repaired=1; the
        remaining is_repaired=2 rows were inspected but didn't need changes.
        """
        conn = self.data_store.connection
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE interval_log SET is_repaired = 0 "
            "WHERE is_repaired = 2 AND timestamp >= ? AND timestamp <= ?",
            (start, end),
        )
        count = cursor.rowcount
        conn.commit()
        cursor.close()
        return count


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _empty_summary() -> dict:
    """Return an empty repair summary."""
    return {
        "gaps_filled": 0,
        "soc_reconstructed_up": 0,
        "soc_blanked": 0,
        "energy_interpolated": 0,
        "soc_reconstructed": 0,
        "soc_constant_filled": 0,
        "violations": {},
    }


def _total_repairs(summary: dict) -> int:
    """Return total number of repairs performed."""
    return (
        summary["gaps_filled"]
        + summary["soc_reconstructed_up"]
        + summary["soc_blanked"]
        + summary["energy_interpolated"]
        + summary["soc_reconstructed"]
        + summary["soc_constant_filled"]
    )


def _format_summary(summary: dict) -> str:
    """Format repair summary for logging."""
    parts = []
    if summary["gaps_filled"]:
        parts.append(f"{summary['gaps_filled']} gaps filled")
    if summary["soc_reconstructed_up"]:
        parts.append(f"{summary['soc_reconstructed_up']} SoC upward reconstructed")
    if summary["soc_blanked"]:
        parts.append(f"{summary['soc_blanked']} SoC blanked")
    if summary["energy_interpolated"]:
        parts.append(f"{summary['energy_interpolated']} energy interpolated")
    if summary["soc_reconstructed"]:
        parts.append(f"{summary['soc_reconstructed']} SoC reconstructed")
    if summary["soc_constant_filled"]:
        parts.append(f"{summary['soc_constant_filled']} SoC constant filled")
    return ", ".join(parts) if parts else "no repairs"


def _get_row_before(df: pd.DataFrame, idx) -> dict | None:
    """Get the last non-NaN row before idx."""
    pos = df.index.get_loc(idx)
    if pos == 0:
        return None
    before = df.iloc[pos - 1]
    if pd.isna(before["energy_kwh"]):
        return None
    return before.to_dict()


def _get_row_after(df: pd.DataFrame, idx) -> dict | None:
    """Get the first non-NaN row after idx."""
    pos = df.index.get_loc(idx)
    if pos >= len(df) - 1:
        return None
    after = df.iloc[pos + 1]
    if pd.isna(after["energy_kwh"]):
        return None
    return after.to_dict()


def _infer_gap_context(before: dict | None, after: dict | None) -> dict:
    """Infer fill values from surrounding intervals.

    Rules:
    - Both sides not_connected → fill with not_connected
    - Both sides error → fill with error
    - One side not_connected → fill with not_connected (car likely away)
    - One side error → fill with error
    - Otherwise → fill with unknown (conservative)
    """
    defaults = {
        "energy_kwh": 0.0,
        "app_state": "unknown",
        "availability_pct": 0.0,
    }

    if before is None or after is None:
        return defaults

    b_state = before.get("app_state", "unknown")
    a_state = after.get("app_state", "unknown")

    if b_state == a_state:
        defaults["app_state"] = b_state
        if b_state not in ("not_connected", "error", "unknown"):
            # Connected state: average availability
            b_avail = before.get("availability_pct", 0.0) or 0.0
            a_avail = after.get("availability_pct", 0.0) or 0.0
            defaults["availability_pct"] = (b_avail + a_avail) / 2
        return defaults

    if "not_connected" in (b_state, a_state):
        defaults["app_state"] = "not_connected"
        return defaults

    if "error" in (b_state, a_state):
        defaults["app_state"] = "error"
        return defaults

    return defaults


def _get_soc_before(df: pd.DataFrame, idx) -> float | None:
    """Get the last non-NaN soc_pct before idx."""
    pos = df.index.get_loc(idx)
    for i in range(pos - 1, -1, -1):
        val = df.iloc[i]["soc_pct"]
        if pd.notna(val):
            return float(val)
    return None


def _get_soc_after(df: pd.DataFrame, idx) -> float | None:
    """Get the first non-NaN soc_pct after idx."""
    pos = df.index.get_loc(idx)
    for i in range(pos + 1, len(df)):
        val = df.iloc[i]["soc_pct"]
        if pd.notna(val):
            return float(val)
    return None


def _get_adjacent_energy(df: pd.DataFrame, idx, direction: int) -> float | None:
    """Get the energy value from the adjacent non-gap-filled row.

    direction: -1 for before, +1 for after.
    """
    pos = df.index.get_loc(idx)
    step = direction
    i = pos + step
    while 0 <= i < len(df):
        row = df.iloc[i]
        # Not a gap-filled row (gap-filled: is_repaired=1 AND energy=0)
        if not (row["is_repaired"] == 1 and row["energy_kwh"] == 0.0):
            return float(row["energy_kwh"])
        i += step
    return None
