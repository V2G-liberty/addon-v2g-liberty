"""Module for data validation and repair of the interval_log table.

Ensures completeness and physical consistency of the 5-minute time series:
  Step 0:  Fill gaps — insert missing 5-min slots with context-aware defaults
  Step 0a: Bounds correction — clamp physically impossible values
  Step 1:  Type A — blank/reconstruct false constant SoC (sensor stuck)
  Step 2:  Type B — interpolate energy in gap-filled rows
  Step 3:  Type C — reconstruct SoC from energy (energy-validated)
  Step 4:  Type D — fill constant SoC where energy ≈ 0 and availability > 95%
  Step 5:  Bounds validation — log impossible states (no modification)
  Step 6:  app_state inference — derive state from energy + availability

Runs at startup (full repair) and periodically (incremental).
Repaired/added rows are marked with is_repaired=1 in interval_log.
"""

from datetime import timedelta

import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .data_store import DataStore
from .fm_historical_importer import append_to_report
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

# Linear SoC interpolation
MAX_LINEAR_SOC_GAP: int = 10  # Max slots for linear interpolation (10 × 5 min)
LINEAR_SOC_JUMP_LIMIT: float = 30.0  # Max pp SoC diff for linear interpolation

# Type A & D: negligible energy threshold
CONSTANT_POWER_TOLERANCE: float = 0.05  # 5% of max charger energy/interval

# Type A-up: upward jump reconstruction tolerance
UPWARD_JUMP_TOLERANCE: float = 10.0  # Max pp mismatch theoretical vs actual

# Type A-down: exceedance counter for downward jump blanking
EXCEEDANCE_THRESHOLD: int = 2  # Observations proving real charger activity


def _negligible_energy_threshold() -> float:
    """Energy threshold below which flow is considered negligible.

    Returns kWh: 5% of max charger energy per 5-min interval.
    """
    max_energy = c.CHARGER_MAX_CHARGE_POWER / 1000 * 5 / 60
    return max_energy * CONSTANT_POWER_TOLERANCE


class DataRepairer:
    """Validates and repairs gaps in interval_log data."""

    _MEMO_ID = "data_repairer_db_issue"

    data_store: DataStore = None

    def __init__(self, hass: Hass):
        self.__hass = hass
        self.__log = get_class_method_logger(hass.log)

    async def initialise(self):
        """Run full repair at startup, then schedule periodic incremental runs."""
        try:
            summary = self.run_full_repair()
        except Exception as e:
            self.__log(
                f"Data repair failed: {e}. Repair is disabled for this session.",
                level="WARNING",
            )
            self.__hass.call_service(
                "persistent_notification/create",
                title="V2G Liberty: database update needed",
                message=(
                    "V2G Liberty detected that its database needs updating. "
                    "The app is running, but some background data maintenance "
                    "is temporarily disabled.\n\n"
                    "**What to do:**\n"
                    "1. Make sure V2G Liberty is up to date\n"
                    "2. Restart V2G Liberty\n\n"
                    "If this message keeps appearing after restarting:\n"
                    "1. Stop V2G Liberty\n"
                    "2. Delete the files `v2g_liberty_data.db` and "
                    "`v2g_liberty_settings.txt` from the data folder\n"
                    "3. Start V2G Liberty again\n\n"
                    "Note: this will remove historical charging data and "
                    "settings. A fresh database will be created automatically."
                ),
                notification_id=self._MEMO_ID,
            )
            return

        # Success — dismiss any leftover notification from a previous failure.
        self.__hass.call_service(
            "persistent_notification/dismiss",
            notification_id=self._MEMO_ID,
        )

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
            f"  Period:                {row['first_ts']} -- {row['last_ts']}",
            f"  Oldest row:            {row['first_ts']}",
            f"  Bounds corrected:      {summary['bounds_corrected']}",
            f"  SoC upward reconstr.:  {summary['soc_reconstructed_up']} (Type A-up)",
            f"  SoC values blanked:    {summary['soc_blanked']} (Type A-down)",
            f"  SoC linear interp.:    {summary['soc_linear_filled']} (Linear)",
            f"  Energy interpolated:   {summary['energy_interpolated']} (Type B)",
            f"  SoC reconstructed:     {summary['soc_reconstructed']} (Type C)",
            f"  SoC constant filled:   {summary['soc_constant_filled']} (Type D)",
            f"  app_state inferred:    {summary['app_state_inferred']}",
            f"  Gaps filled:           {summary['gaps_filled']}",
        ]
        violations = summary.get("violations", {})
        if violations:
            lines.append(f"  Violations logged:     {sum(violations.values())}")
        lines.append("")

        append_to_report("\n".join(lines))

        self.__log("Repair report written to historical import report file.")

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

        # Timestamps are stored in UTC; parse and index directly.
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df = df.set_index("timestamp").sort_index()

        # Step 0: Fill gaps (insert missing 5-min rows)
        df, gaps_filled = self._fill_gaps(df)
        summary["gaps_filled"] = gaps_filled

        # Step 0a: Correct physically impossible values (clamp)
        df, bounds_corrected = self._correct_bounds(df)
        summary["bounds_corrected"] = bounds_corrected

        # Step 1a: Type A-up — reconstruct SoC for upward jumps
        df, soc_reconstructed_up = self._reconstruct_upward_jumps(df)
        summary["soc_reconstructed_up"] = soc_reconstructed_up

        # Step 1b: Type A-down — blank false constant SoC before downward jumps
        df, soc_blanked = self._blank_false_constant_soc(df)
        summary["soc_blanked"] = soc_blanked

        # Step 1c: Linear SoC interpolation for small gaps
        df, soc_linear_filled = self._interpolate_soc_linear(df)
        summary["soc_linear_filled"] = soc_linear_filled

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

        # Step 6: Infer app_state for historical "unknown" rows
        df, app_state_inferred = self._infer_app_state(df)
        summary["app_state_inferred"] = app_state_inferred

        # Write repaired rows back to DB (timestamps remain in UTC).
        written = self._write_repaired_rows(df)
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
                    "energy_kwh": None,
                    "app_state": "unknown",
                    "availability_pct": None,
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
    # Step 0a: Bounds correction — clamp impossible values
    # ------------------------------------------------------------------

    # Absolute upper bound for home charging: 25 kW.  Any energy value above
    # this per 5-min interval is certainly erroneous.  We intentionally do NOT
    # use the current charger settings because those may have changed over time
    # and would incorrectly clamp historical data recorded with a different
    # charger or different max-power configuration.
    _MAX_HOME_POWER_KW = 25
    _MAX_ENERGY_KWH_PER_INTERVAL = _MAX_HOME_POWER_KW * 5 / 60  # 2.083 kWh

    def _correct_bounds(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Clamp physically impossible values and mark rows as repaired.

        Corrects SoC (1–100%), energy (absolute home-charging bound), and
        availability (0–100%). NaN values are skipped. Returns
        (df, count_of_corrections).
        """
        corrected = 0

        max_energy_kwh = self._MAX_ENERGY_KWH_PER_INTERVAL

        # SoC < 1% → 1%
        mask = df["soc_pct"].notna() & (df["soc_pct"] < 1)
        if mask.any():
            n = mask.sum()
            self.__log(f"Bounds: clamped {n} SoC values below 1%.", level="WARNING")
            df.loc[mask, "soc_pct"] = 1.0
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        # SoC > 100% → 100%
        mask = df["soc_pct"].notna() & (df["soc_pct"] > 100)
        if mask.any():
            n = mask.sum()
            self.__log(f"Bounds: clamped {n} SoC values above 100%.", level="WARNING")
            df.loc[mask, "soc_pct"] = 100.0
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        # Energy > max → clamp
        mask = df["energy_kwh"].notna() & (df["energy_kwh"] > max_energy_kwh)
        if mask.any():
            n = mask.sum()
            self.__log(
                f"Bounds: clamped {n} energy values above {max_energy_kwh:.3f} kWh "
                f"({self._MAX_HOME_POWER_KW} kW limit).",
                level="WARNING",
            )
            df.loc[mask, "energy_kwh"] = max_energy_kwh
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        # Energy < -max → clamp
        mask = df["energy_kwh"].notna() & (df["energy_kwh"] < -max_energy_kwh)
        if mask.any():
            n = mask.sum()
            self.__log(
                f"Bounds: clamped {n} energy values below -{max_energy_kwh:.3f} kWh "
                f"({self._MAX_HOME_POWER_KW} kW limit).",
                level="WARNING",
            )
            df.loc[mask, "energy_kwh"] = -max_energy_kwh
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        # Availability < 0% → 0%
        mask = df["availability_pct"].notna() & (df["availability_pct"] < 0)
        if mask.any():
            n = mask.sum()
            self.__log(
                f"Bounds: clamped {n} availability values below 0%.",
                level="WARNING",
            )
            df.loc[mask, "availability_pct"] = 0.0
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        # Availability > 100% → 100%
        mask = df["availability_pct"].notna() & (df["availability_pct"] > 100)
        if mask.any():
            n = mask.sum()
            self.__log(
                f"Bounds: clamped {n} availability values above 100%.",
                level="WARNING",
            )
            df.loc[mask, "availability_pct"] = 100.0
            df.loc[mask, "is_repaired"] = 1
            corrected += n

        return df, corrected

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

            # Calculate theoretical jump from energy in the block.
            # NaN energy is treated as 0 (no measurement → no known change).
            block_energy = df.loc[block_indices, "energy_kwh"].fillna(0.0)
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
        the constant run leading up to the jump.  Walks backwards over the
        **full** DataFrame (including NaN-SoC rows) to check for evidence of
        real charger activity via two exceedance counters:

        - ``energy_exceeded``: incremented when |energy_kwh| >= threshold
        - ``availability_exceeded``: incremented when availability_pct == 100

        Blanking stops when **either** counter reaches EXCEEDANCE_THRESHOLD
        (OR logic), meaning the charger was genuinely active at that point.

        Only modifies soc_pct (sets to NaN). Marks modified rows as repaired.
        """
        soc = df["soc_pct"].copy()
        soc_valid = soc.dropna()

        if len(soc_valid) < 2:
            return df, 0

        threshold = _negligible_energy_threshold()

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

            # Walk backwards over the FULL DataFrame from the row before
            # the jump, collecting indices to blank.
            df_pos = df.index.get_loc(prev_idx)
            blank_indices = []
            energy_exceeded = 0
            availability_exceeded = 0

            i = df_pos
            while i >= 0:
                row = df.iloc[i]
                row_soc = row["soc_pct"]

                # Stop if SoC deviates from constant value
                if pd.notna(row_soc) and abs(row_soc - constant_value) > 0.1:
                    break

                # Update exceedance counters and check BEFORE blanking
                energy = row["energy_kwh"]
                if pd.notna(energy) and abs(energy) >= threshold:
                    energy_exceeded += 1

                avail = row["availability_pct"]
                if pd.notna(avail) and avail == 100:
                    availability_exceeded += 1

                if (
                    energy_exceeded >= EXCEEDANCE_THRESHOLD
                    or availability_exceeded >= EXCEEDANCE_THRESHOLD
                ):
                    break  # Real activity proven — stop blanking

                # Only blank rows that have the constant SoC value
                if pd.notna(row_soc):
                    blank_indices.append(df.index[i])

                i -= 1

            # Only blank if there's a run of constant values (>= 2).
            # A single value before a jump is not a "constant run".
            if len(blank_indices) >= 2:
                for idx in blank_indices:
                    df.loc[idx, "soc_pct"] = None
                    df.loc[idx, "is_repaired"] = 1
                    blanked += 1

        return df, blanked

    # ------------------------------------------------------------------
    # Step 1c: Linear SoC interpolation
    # ------------------------------------------------------------------

    def _interpolate_soc_linear(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Linearly interpolate small SoC gaps with known endpoints.

        For NULL SoC gaps ≤ MAX_LINEAR_SOC_GAP:
        1. Both endpoints must be known (non-NULL)
        2. SoC difference must be ≤ LINEAR_SOC_JUMP_LIMIT
        3. Energy direction must be consistent with SoC direction:
           - SoC rises → energy after gap must not be negative
           - SoC drops → energy after gap must not be positive
        4. Fill with np.linspace between endpoints

        Returns (df, count_of_filled_rows).
        """
        import numpy as np

        is_null_soc = df["soc_pct"].isna()
        if not is_null_soc.any():
            return df, 0

        nan_groups = (is_null_soc != is_null_soc.shift()).cumsum()
        filled = 0

        for _, block_idx in df[is_null_soc].groupby(nan_groups).groups.items():
            block = df.loc[block_idx]
            if len(block) > MAX_LINEAR_SOC_GAP:
                continue

            start_pos = df.index.get_loc(block.index[0])
            end_pos = df.index.get_loc(block.index[-1])

            # Skip if block is at the very beginning or end
            if start_pos == 0 or end_pos >= len(df) - 1:
                continue

            soc_before = df.iloc[start_pos - 1]["soc_pct"]
            soc_after = df.iloc[end_pos + 1]["soc_pct"]

            if pd.isna(soc_before) or pd.isna(soc_after):
                continue

            # SoC difference must be within limit
            soc_diff = soc_after - soc_before
            if abs(soc_diff) > LINEAR_SOC_JUMP_LIMIT:
                continue

            # Energy direction check: energy in the interval after the gap
            # must be consistent with SoC direction
            energy_after = df.iloc[end_pos + 1]["energy_kwh"]
            if pd.isna(energy_after):
                continue

            if soc_diff > 0 and energy_after < 0:
                continue  # SoC rises but next interval discharges
            if soc_diff < 0 and energy_after > 0:
                continue  # SoC drops but next interval charges

            # Linearly interpolate
            interpolated = np.linspace(soc_before, soc_after, len(block) + 2)[1:-1]

            for idx, val in zip(block.index, interpolated):
                df.loc[idx, "soc_pct"] = round(float(val), 1)
                df.loc[idx, "is_repaired"] = 1

            filled += len(block)

        return df, filled

    # ------------------------------------------------------------------
    # Step 2: Type B — Energy interpolation
    # ------------------------------------------------------------------

    def _interpolate_energy(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Interpolate energy in rows with unknown energy.

        Targets two kinds of rows:
        - Gap-filled rows (is_repaired=1, energy_kwh=0.0)
        - Imported rows without power data (is_repaired=2, energy_kwh=NaN)

        If the surrounding real energy values are within 5% (relative),
        fill the gap linearly.

        Max gap length: MAX_ENERGY_INTERPOLATION_GAP.
        Endpoints must be same direction (both positive or both negative).
        """
        # Rows needing interpolation: gap-filled (energy=None) or imported
        # without power data (energy=NaN). Both have is_repaired in (1, 2).
        needs_interpolation = df["energy_kwh"].isna() & df["is_repaired"].isin([1, 2])
        if not needs_interpolation.any():
            return df, 0

        gap_groups = (needs_interpolation != needs_interpolation.shift()).cumsum()
        filled = 0

        for _, block_idx in df[needs_interpolation].groupby(gap_groups).groups.items():
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
                df.loc[idx, "is_repaired"] = 1

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

            # Check that at least some rows have significant energy.
            # NaN energy counts as negligible (no measurement available).
            block_energy = df.loc[block.index, "energy_kwh"]
            known_energy = block_energy.dropna()
            if known_energy.empty or (known_energy.abs() <= threshold).all():
                continue  # All negligible/unknown → Type D, not Type C

            # Get SoC endpoints
            before_soc = _get_soc_before(df, block.index[0])
            after_soc = _get_soc_after(df, block.index[-1])
            if before_soc is None or after_soc is None:
                continue

            # Reconstruct SoC cumulatively from energy.
            # NaN energy → zero delta (no measurement, assume no change).
            cumulative_soc = float(before_soc)
            soc_values = []
            for idx in block.index:
                energy_val = df.loc[idx, "energy_kwh"]
                energy = 0.0 if pd.isna(energy_val) else float(energy_val)
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
        Requires availability > 95% (car was connected), BOTH SoC endpoints,
        and they must be consistent (within SOC_JUMP_THRESHOLD).
        If availability is NaN/None for any row in the block → skip.
        """
        is_null_soc = df["soc_pct"].isna()
        if not is_null_soc.any():
            return df, 0

        threshold = _negligible_energy_threshold()
        nan_groups = (is_null_soc != is_null_soc.shift()).cumsum()
        filled = 0

        for _, block_idx in df[is_null_soc].groupby(nan_groups).groups.items():
            block = df.loc[block_idx]

            # Availability must be known and > 95% for all rows in the block.
            block_avail = df.loc[block.index, "availability_pct"]
            if block_avail.isna().any():
                continue  # Unknown availability → skip
            if not (block_avail > 95).all():
                continue  # Car was not (fully) connected → skip

            # All energy must be negligible (NaN counts as negligible).
            block_energy = df.loc[block.index, "energy_kwh"].dropna()
            if not block_energy.empty and not (block_energy.abs() <= threshold).all():
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

        # Energy exceeds absolute home-charging bound per interval
        max_energy_kwh = self._MAX_ENERGY_KWH_PER_INTERVAL

        energy_too_high = df[df["energy_kwh"] > max_energy_kwh]
        energy_too_low = df[df["energy_kwh"] < -max_energy_kwh]
        if len(energy_too_high) > 0:
            violations["energy_exceeds_charge_limit"] = len(energy_too_high)
            self.__log(
                f"{len(energy_too_high)} intervals with energy above "
                f"{max_energy_kwh:.3f} kWh ({self._MAX_HOME_POWER_KW} kW limit).",
                level="WARNING",
            )
        if len(energy_too_low) > 0:
            violations["energy_exceeds_discharge_limit"] = len(energy_too_low)
            self.__log(
                f"{len(energy_too_low)} intervals with energy below "
                f"-{max_energy_kwh:.3f} kWh ({self._MAX_HOME_POWER_KW} kW limit).",
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

        # Impossible states: energy ≠ 0 (exact) when not_connected
        nc_with_energy = df[
            (df["app_state"] == "not_connected")
            & df["energy_kwh"].notna()
            & (df["energy_kwh"] != 0)
        ]
        if len(nc_with_energy) > 0:
            violations["energy_while_not_connected"] = len(nc_with_energy)
            self.__log(
                f"{len(nc_with_energy)} intervals with energy while not_connected.",
                level="WARNING",
            )

        # Impossible states: SoC direction inconsistent with energy direction
        soc_series = pd.to_numeric(df["soc_pct"], errors="coerce")
        energy_series = pd.to_numeric(df["energy_kwh"], errors="coerce")
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
    # Step 6: app_state inference for historical imports
    # ------------------------------------------------------------------

    def _infer_app_state(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Infer app_state for rows with state "unknown" (historical import).

        Rules (in priority order):
        1. availability=0, energy=0        → not_connected
        2. availability=0, energy>threshold → charge
        3. availability=0, energy<-threshold→ discharge
        4. availability>95, |energy|>threshold → automatic
        5. Otherwise                       → unknown (unchanged)

        If availability is NaN → skip (not enough information).
        Only modifies rows where app_state == "unknown".
        """
        unknown_mask = df["app_state"] == "unknown"
        if not unknown_mask.any():
            return df, 0

        threshold = _negligible_energy_threshold()
        inferred = 0

        for idx in df[unknown_mask].index:
            avail = df.loc[idx, "availability_pct"]
            energy = df.loc[idx, "energy_kwh"]

            if pd.isna(avail):
                continue

            if pd.isna(energy):
                energy_val = 0.0
            else:
                energy_val = float(energy)

            new_state = None
            if avail == 0:
                if energy_val == 0:
                    new_state = "not_connected"
                elif energy_val > threshold:
                    new_state = "charge"
                elif energy_val < -threshold:
                    new_state = "discharge"
            elif avail > 95 and abs(energy_val) > threshold:
                new_state = "automatic"

            if new_state is not None:
                df.loc[idx, "app_state"] = new_state
                df.loc[idx, "is_repaired"] = 1
                inferred += 1

        return df, inferred

    # ------------------------------------------------------------------
    # Write-back
    # ------------------------------------------------------------------

    def _write_repaired_rows(self, df: pd.DataFrame) -> int:
        """Write repaired/new rows back to interval_log.

        Only writes rows where is_repaired=1.
        Uses INSERT OR REPLACE for both new gap-fill rows and updated rows.
        Timestamps are kept in UTC.
        """
        repaired = df[df["is_repaired"] == 1]
        if repaired.empty:
            return 0

        rows = []
        for ts, row in repaired.iterrows():
            soc = None if pd.isna(row["soc_pct"]) else float(row["soc_pct"])
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "energy_kwh": (
                        None if pd.isna(row["energy_kwh"]) else float(row["energy_kwh"])
                    ),
                    "app_state": str(row["app_state"]),
                    "soc_pct": soc,
                    "availability_pct": (
                        None
                        if pd.isna(row["availability_pct"])
                        else float(row["availability_pct"])
                    ),
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
        "bounds_corrected": 0,
        "soc_reconstructed_up": 0,
        "soc_blanked": 0,
        "soc_linear_filled": 0,
        "energy_interpolated": 0,
        "soc_reconstructed": 0,
        "soc_constant_filled": 0,
        "app_state_inferred": 0,
        "violations": {},
    }


def _total_repairs(summary: dict) -> int:
    """Return total number of repairs performed."""
    return (
        summary["gaps_filled"]
        + summary["bounds_corrected"]
        + summary["soc_reconstructed_up"]
        + summary["soc_blanked"]
        + summary["soc_linear_filled"]
        + summary["energy_interpolated"]
        + summary["soc_reconstructed"]
        + summary["soc_constant_filled"]
        + summary["app_state_inferred"]
    )


def _format_summary(summary: dict) -> str:
    """Format repair summary for logging."""
    parts = []
    if summary["gaps_filled"]:
        parts.append(f"{summary['gaps_filled']} gaps filled")
    if summary["bounds_corrected"]:
        parts.append(f"{summary['bounds_corrected']} bounds corrected")
    if summary["soc_reconstructed_up"]:
        parts.append(f"{summary['soc_reconstructed_up']} SoC upward reconstructed")
    if summary["soc_blanked"]:
        parts.append(f"{summary['soc_blanked']} SoC blanked")
    if summary["soc_linear_filled"]:
        parts.append(f"{summary['soc_linear_filled']} SoC linear interpolated")
    if summary["energy_interpolated"]:
        parts.append(f"{summary['energy_interpolated']} energy interpolated")
    if summary["soc_reconstructed"]:
        parts.append(f"{summary['soc_reconstructed']} SoC reconstructed")
    if summary["soc_constant_filled"]:
        parts.append(f"{summary['soc_constant_filled']} SoC constant filled")
    if summary["app_state_inferred"]:
        parts.append(f"{summary['app_state_inferred']} app_state inferred")
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
        "energy_kwh": None,
        "app_state": "unknown",
        "availability_pct": None,
    }

    if before is None or after is None:
        return defaults

    b_state = before.get("app_state", "unknown")
    a_state = after.get("app_state", "unknown")

    if b_state == a_state:
        defaults["app_state"] = b_state
        if b_state in ("not_connected", "error"):
            # No charger activity — energy is known to be 0.
            defaults["energy_kwh"] = 0.0
        elif b_state != "unknown":
            # Connected state: average availability if both sides known.
            # Energy stays None (unknown — let Type B interpolate).
            b_avail = before.get("availability_pct")
            a_avail = after.get("availability_pct")
            if b_avail is not None and a_avail is not None:
                defaults["availability_pct"] = (b_avail + a_avail) / 2
        return defaults

    if "not_connected" in (b_state, a_state):
        defaults["app_state"] = "not_connected"
        defaults["energy_kwh"] = 0.0
        return defaults

    if "error" in (b_state, a_state):
        defaults["app_state"] = "error"
        defaults["energy_kwh"] = 0.0
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
    """Get the energy value from the adjacent row with known energy.

    Skips gap-filled rows (is_repaired=1, energy=0) and rows with NaN energy.
    direction: -1 for before, +1 for after.
    """
    pos = df.index.get_loc(idx)
    step = direction
    i = pos + step
    while 0 <= i < len(df):
        row = df.iloc[i]
        energy = row["energy_kwh"]
        # Skip rows without usable energy: gap-filled zeros or NaN
        if pd.isna(energy):
            i += step
            continue
        if row["is_repaired"] == 1 and energy == 0.0:
            i += step
            continue
        return float(energy)
    return None
