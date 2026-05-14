"""Module for daily batch export of interval data to FlexMeasures."""

from datetime import datetime, timedelta, timezone

from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
from .data_store import _APP_STATE_PRIORITY
from .log_wrapper import get_class_method_logger


class FMDataSender:
    """Sends interval_log data from SQLite to FlexMeasures daily.

    Reads unsent intervals from the local database and posts power, SoC,
    and availability measurements to FlexMeasures. Tracks the last
    successfully sent timestamp to support automatic retry and catch-up.
    """

    data_store = None
    fm_client_app = None
    hass: Hass = None

    def __init__(self, hass: Hass):
        self.hass = hass
        self.__log = get_class_method_logger(hass.log)

    async def initialize(self):
        """Initialise send status and schedule hourly export."""
        self.__log("Initialising FMDataSender.")

        # Ensure fm_send_status has an initial value on first start
        if self.data_store is not None:
            for data_type in ("charger", "grid"):
                last_sent = self.data_store.get_fm_last_sent(data_type)
                if last_sent is None:
                    now = datetime.now(timezone.utc).isoformat()
                    self.data_store.set_fm_last_sent(now, data_type)
                    self.__log(
                        f"First start ({data_type}): set last_sent_up_to to now."
                    )

        first_run = datetime.now(timezone.utc) + timedelta(seconds=15)
        await self.hass.run_every(self._send_unsent_data, first_run, 60 * 60)
        self.__log("Completed initialising FMDataSender, scheduled every hour.")

    async def _send_unsent_data(self, *args):
        """Send all unsent interval data to FlexMeasures.

        Charger and grid data are sent independently — a failure in one
        does not block the other.
        """
        if self.fm_client_app is None:
            self.__log("FM client not available, skipping send.")
            return

        if self.data_store is None:
            self.__log("DataStore not available, skipping send.")
            return

        await self._send_charger_data()
        await self._send_grid_data()

    async def _send_charger_data(self):
        """Send unsent charger interval data (power, SoC, availability)."""
        last_sent = self.data_store.get_fm_last_sent("charger")
        if last_sent is None:
            now = datetime.now(timezone.utc).isoformat()
            self.data_store.set_fm_last_sent(now, "charger")
            self.__log(
                "Charger: no last_sent_up_to, recovered by setting to now.",
                level="WARNING",
            )
            return

        intervals = self.data_store.get_intervals_since(last_sent)
        if not intervals:
            self.__log("Charger: no unsent intervals.")
            return

        self.__log(f"Charger: {len(intervals)} unsent interval(s).")
        blocks = self._group_contiguous_blocks(intervals)

        for block in blocks:
            success = await self._send_charger_block(block)
            if success:
                last_timestamp = block[-1]["timestamp"]
                self.data_store.set_fm_last_sent(last_timestamp, "charger")
                self.__log(f"Charger: block sent, advanced to {last_timestamp}.")
            else:
                self.__log(
                    "Charger: block failed, will retry next run.",
                    level="WARNING",
                )
                break

    async def _send_grid_data(self):
        """Send unsent grid interval data (consumption + production per phase)."""
        if not c.FM_GRID_CONSUMPTION_SENSOR_IDS:
            return

        last_sent = self.data_store.get_fm_last_sent("grid")
        if last_sent is None:
            now = datetime.now(timezone.utc).isoformat()
            self.data_store.set_fm_last_sent(now, "grid")
            self.__log(
                "Grid: no last_sent_up_to, recovered by setting to now.",
                level="WARNING",
            )
            return

        intervals = self.data_store.get_grid_intervals_since(last_sent)
        if not intervals:
            self.__log("Grid: no unsent intervals.")
            return

        self.__log(f"Grid: {len(intervals)} unsent row(s).")

        # Group by timestamp, then group timestamps into contiguous blocks
        by_timestamp = self._group_grid_by_timestamp(intervals)
        timestamps = sorted(by_timestamp.keys())
        blocks = self._group_contiguous_timestamps(timestamps)

        for block_timestamps in blocks:
            try:
                success = await self._send_grid_block(block_timestamps, by_timestamp)
            except Exception as e:
                self.__log(
                    f"Grid: block send raised exception: {e}",
                    level="WARNING",
                )
                break
            if success:
                last_ts = block_timestamps[-1]
                self.data_store.set_fm_last_sent(last_ts, "grid")
                self.__log(f"Grid: block sent, advanced to {last_ts}.")
            else:
                self.__log(
                    "Grid: block failed, will retry next run.",
                    level="WARNING",
                )
                break

    # Max 288 intervals per block = 24 hours at 5-min resolution.
    MAX_BLOCK_SIZE = 288

    def _group_contiguous_blocks(self, intervals: list[dict]) -> list[list[dict]]:
        """Group intervals into contiguous blocks of 5-minute timestamps.

        A gap (missing interval) starts a new block. Blocks are also split
        at MAX_BLOCK_SIZE to keep FM API payloads manageable.
        """
        if not intervals:
            return []

        blocks = []
        current_block = [intervals[0]]

        for i in range(1, len(intervals)):
            prev_ts = datetime.fromisoformat(intervals[i - 1]["timestamp"])
            curr_ts = datetime.fromisoformat(intervals[i]["timestamp"])
            expected_next = prev_ts + timedelta(
                minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES
            )

            if curr_ts == expected_next and len(current_block) < self.MAX_BLOCK_SIZE:
                current_block.append(intervals[i])
            else:
                blocks.append(current_block)
                current_block = [intervals[i]]

        blocks.append(current_block)
        return blocks

    async def _send_charger_block(self, block: list[dict]) -> bool:
        """Send a contiguous block of charger intervals to FlexMeasures.

        Posts power (MW), SoC (%), and availability (%) as three separate
        measurements. Returns True only if all three succeed.
        """
        start = block[0]["timestamp"]
        nr_intervals = len(block)
        duration = _len_to_iso_duration(nr_intervals)

        # Derive power in MW from energy in kWh.
        # power_kw = energy_kwh × (60 / interval_minutes), then / 1000 for MW.
        interval_minutes = c.FM_EVENT_RESOLUTION_IN_MINUTES
        power_values = [
            round(row["energy_kwh"] * 60 / (interval_minutes * 1000), 5)
            if row["energy_kwh"] is not None
            else None
            for row in block
        ]
        soc_values = [row["soc_pct"] for row in block]
        availability_values = [row["availability_pct"] for row in block]

        power_ok = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_POWER_SENSOR_ID,
            values=power_values,
            start=start,
            duration=duration,
            uom="MW",
        )
        if not power_ok:
            self.__log("Failed to send power data.", level="WARNING")
            return False

        soc_ok = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_SOC_SENSOR_ID,
            values=soc_values,
            start=start,
            duration=duration,
            uom="%",
        )
        if not soc_ok:
            self.__log("Failed to send SoC data.", level="WARNING")
            return False

        availability_ok = await self.fm_client_app.post_measurements(
            sensor_id=c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
            values=availability_values,
            start=start,
            duration=duration,
            uom="%",
        )
        if not availability_ok:
            self.__log("Failed to send availability data.", level="WARNING")
            return False

        # EMS Status: encode app_state strings as integers
        if c.FM_EMS_STATUS_SENSOR_ID:
            ems_values = [
                _APP_STATE_PRIORITY.get(row.get("app_state"), 0) for row in block
            ]
            ems_ok = await self.fm_client_app.post_measurements(
                sensor_id=c.FM_EMS_STATUS_SENSOR_ID,
                values=ems_values,
                start=start,
                duration=duration,
                uom="dimensionless",
            )
            if not ems_ok:
                self.__log("Failed to send EMS status data.", level="WARNING")
                return False

        return True

    @staticmethod
    def _group_grid_by_timestamp(intervals: list[dict]) -> dict[str, dict[int, dict]]:
        """Group grid interval rows by timestamp.

        Returns {timestamp: {phase: {consumption_kw, production_kw}}}.
        """
        by_timestamp: dict[str, dict[int, dict]] = {}
        for row in intervals:
            ts = row["timestamp"]
            if ts not in by_timestamp:
                by_timestamp[ts] = {}
            by_timestamp[ts][row["phase"]] = {
                "consumption_kw": row["consumption_kw"],
                "production_kw": row["production_kw"],
            }
        return by_timestamp

    def _group_contiguous_timestamps(self, timestamps: list[str]) -> list[list[str]]:
        """Group sorted timestamp strings into contiguous 5-minute blocks.

        Also splits at MAX_BLOCK_SIZE.
        """
        if not timestamps:
            return []

        blocks = []
        current_block = [timestamps[0]]

        for i in range(1, len(timestamps)):
            prev_ts = datetime.fromisoformat(timestamps[i - 1])
            curr_ts = datetime.fromisoformat(timestamps[i])
            expected = prev_ts + timedelta(minutes=c.FM_EVENT_RESOLUTION_IN_MINUTES)

            if curr_ts == expected and len(current_block) < self.MAX_BLOCK_SIZE:
                current_block.append(timestamps[i])
            else:
                blocks.append(current_block)
                current_block = [timestamps[i]]

        blocks.append(current_block)
        return blocks

    async def _send_grid_block(
        self,
        timestamps: list[str],
        by_timestamp: dict[str, dict[int, dict]],
    ) -> bool:
        """Send a contiguous block of grid data to FlexMeasures.

        Posts consumption and production per phase. Returns True only if
        all posts succeed.
        """
        start = timestamps[0]
        duration = _len_to_iso_duration(len(timestamps))

        for phase, sensor_id in c.FM_GRID_CONSUMPTION_SENSOR_IDS.items():
            values = [
                by_timestamp[ts].get(phase, {}).get("consumption_kw")
                for ts in timestamps
            ]
            ok = await self.fm_client_app.post_measurements(
                sensor_id=sensor_id,
                values=values,
                start=start,
                duration=duration,
                uom="kW",
            )
            if not ok:
                self.__log(
                    f"Grid: failed to send consumption L{phase}.",
                    level="WARNING",
                )
                return False

        for phase, sensor_id in c.FM_GRID_PRODUCTION_SENSOR_IDS.items():
            values = [
                by_timestamp[ts].get(phase, {}).get("production_kw")
                for ts in timestamps
            ]
            ok = await self.fm_client_app.post_measurements(
                sensor_id=sensor_id,
                values=values,
                start=start,
                duration=duration,
                uom="kW",
            )
            if not ok:
                self.__log(
                    f"Grid: failed to send production L{phase}.",
                    level="WARNING",
                )
                return False

        return True


def _len_to_iso_duration(nr_of_intervals: int) -> str:
    """Convert number of 5-minute intervals to ISO 8601 duration string.

    Uses full P{d}DT{h}H{m}M format when days are needed, otherwise PT{h}H{m}M.
    """
    total_minutes = nr_of_intervals * c.FM_EVENT_RESOLUTION_IN_MINUTES
    days = total_minutes // (24 * 60)
    remaining = total_minutes - days * 24 * 60
    hours = remaining // 60
    minutes = remaining % 60

    if days > 0:
        return f"P{days}DT{hours}H{minutes}M"
    return f"PT{hours}H{minutes}M"
