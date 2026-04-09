"""Module for daily batch export of interval data to FlexMeasures."""

import math
from datetime import datetime, timedelta, timezone

from appdaemon.plugins.hass.hassapi import Hass

from . import constants as c
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
        """Initialise send status and schedule daily export."""
        self.__log("Initialising FMDataSender.")

        # Ensure fm_send_status has an initial value on first start
        if self.data_store is not None:
            last_sent = self.data_store.get_fm_last_sent()
            if last_sent is None:
                now = datetime.now(timezone.utc).isoformat()
                self.data_store.set_fm_last_sent(now)
                self.__log(
                    "First start: set last_sent_up_to to now, "
                    "no historical data will be sent."
                )

        await self.hass.run_daily(self._daily_send, start="03:00:00")
        self.__log("Completed initialising FMDataSender, scheduled daily at 03:00.")

    async def _daily_send(self, *args):
        """Send all unsent interval data to FlexMeasures."""
        if self.fm_client_app is None:
            self.__log("FM client not available, skipping daily send.")
            return

        if self.data_store is None:
            self.__log("DataStore not available, skipping daily send.")
            return

        last_sent = self.data_store.get_fm_last_sent()
        if last_sent is None:
            self.__log("No last_sent_up_to set, skipping.", level="WARNING")
            return

        intervals = self.data_store.get_intervals_since(last_sent)
        if not intervals:
            self.__log("No unsent intervals, nothing to send.")
            return

        self.__log(f"Found {len(intervals)} unsent interval(s) to send to FM.")

        blocks = self._group_contiguous_blocks(intervals)
        self.__log(f"Grouped into {len(blocks)} contiguous block(s).")

        for block in blocks:
            success = await self._send_block(block)
            if success:
                last_timestamp = block[-1]["timestamp"]
                self.data_store.set_fm_last_sent(last_timestamp)
                self.__log(
                    f"Block sent successfully, advanced last_sent to {last_timestamp}."
                )
            else:
                self.__log(
                    "Block send failed, stopping. Will retry next run.",
                    level="WARNING",
                )
                # Stop processing further blocks — next run will pick up from here
                break

    def _group_contiguous_blocks(self, intervals: list[dict]) -> list[list[dict]]:
        """Group intervals into contiguous blocks of 5-minute timestamps.

        A gap (missing interval) starts a new block.
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

            if curr_ts == expected_next:
                current_block.append(intervals[i])
            else:
                blocks.append(current_block)
                current_block = [intervals[i]]

        blocks.append(current_block)
        return blocks

    async def _send_block(self, block: list[dict]) -> bool:
        """Send a contiguous block of intervals to FlexMeasures.

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

        return True


def _len_to_iso_duration(nr_of_intervals: int) -> str:
    """Convert number of 5-minute intervals to ISO 8601 duration string."""
    total_minutes = nr_of_intervals * c.FM_EVENT_RESOLUTION_IN_MINUTES
    hours = math.floor(total_minutes / 60)
    minutes = total_minutes - hours * 60
    return f"PT{hours}H{minutes}M"
