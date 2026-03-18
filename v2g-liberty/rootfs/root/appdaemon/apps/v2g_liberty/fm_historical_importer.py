"""Temporary module: import historical FM sensor data into the local database.

Fetches power, SoC, availability, price, and emission data from FlexMeasures.
Runs once per installation of this version. A flag file is written after a
successful import; deleting the flag file forces a re-import on next startup
(useful for hotfixes).

This module is temporary and will be removed once all users have upgraded.
"""

import asyncio
import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import constants as c

# Never fetch data older than this date.
_EARLIEST_DATE = date(2024, 9, 1)
# Treat a month as empty if fewer than this many events are returned.
_EMPTY_MONTH_THRESHOLD = 50
# 5-minute intervals: energy_kwh = power_kW * 5 / 60
_INTERVAL_MINUTES = 5
_ENERGY_FROM_POWER_FACTOR = _INTERVAL_MINUTES / 60
# Written after a successful import; delete to force a re-import.
_REPORT_FILE = Path("/data/fm_historical_import_report.txt")
# EU day-ahead electricity market switched from 60-min to 15-min on this date.
_PRICE_MARKET_CUTOVER = date(2025, 10, 1)
# Retry settings for sensor fetches.
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 10
# Pause between months to avoid overwhelming the FM server.
_MONTH_PAUSE_SECONDS = 30
# Maximum chunk size for FM API queries to avoid server-side timeouts.
_CHUNK_DAYS = 7
# Pause between individual sensor fetches within a chunk (seconds).
_SENSOR_PAUSE_SECONDS = 2
# Pause between weekly chunks (seconds).
_CHUNK_PAUSE_SECONDS = 5


def clear_import_flag() -> None:
    """Delete the historical import flag, forcing a re-import on next startup."""
    _REPORT_FILE.unlink(missing_ok=True)


def append_to_report(text: str) -> None:
    """Append text to the historical import report file."""
    with open(_REPORT_FILE, "a") as f:
        f.write(text)


async def run_historical_import(
    data_store, log_fn, fm_client=None, on_complete=None, on_notify=None
) -> None:
    """Import historical FM data into the local database.

    Skipped if the flag file exists. After a successful import the flag file
    is written containing the completion timestamp. To force a re-import
    (e.g. after a hotfix), delete the file:
      /data/fm_historical_import_report.txt

    When ``on_complete`` is provided it is called after a successful import.
    This is used to trigger the DataRepairer to review imported rows.

    When ``on_notify`` is provided it is called with a single string message
    after the import finishes (success or failure).  This is used to send a
    persistent notification to the user.
    """
    if _REPORT_FILE.exists():
        log_fn("Historical import: already done (flag file exists), skipping.")
        return

    if fm_client is None:
        log_fn(
            "Historical import: no FM client available, aborting.",
            level="WARNING",
        )
        return

    if c.FM_ACCOUNT_POWER_SOURCE_ID is None:
        log_fn(
            "Historical import: power source_id unknown -- import blocked to prevent "
            "contaminated data. Discovery may retry on next startup.",
            level="WARNING",
        )
        if on_notify is not None:
            on_notify(
                "Historical import cancelled: the FlexMeasures data source for "
                "power measurements could not be determined. "
                "Please contact support if this problem persists."
            )
        return

    log_fn(
        f"Historical import: starting (power source_id={c.FM_ACCOUNT_POWER_SOURCE_ID})."
    )

    # Remove any rows from a previous historical import so that a fresh
    # import doesn't leave duplicates in interval_log.
    deleted = data_store.delete_historical_intervals()
    if deleted:
        log_fn(f"Historical import: removed {deleted} stale 'unknown' interval rows.")

    month = date.today().replace(day=1)
    consecutive_empty = 0
    months_processed = 0

    while month >= _EARLIEST_DATE:
        # Pause between months to avoid overwhelming the FM server.
        if months_processed > 0:
            await asyncio.sleep(_MONTH_PAUSE_SECONDS)

        month_start = datetime(month.year, month.month, 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(month.year, month.month)[1]
        month_end = datetime(
            month.year, month.month, last_day, tzinfo=timezone.utc
        ) + timedelta(days=1)

        # Cap to start of today to avoid importing future price forecasts.
        today_midnight = datetime.combine(date.today(), datetime.min.time()).replace(
            tzinfo=timezone.utc
        )
        effective_end = min(month_end, today_midnight)

        errors = []
        rows = await _fetch_month_rows(
            fm_client, month_start, effective_end, log_fn, errors=errors
        )

        # Count rows where at least one sensor has actual data.
        rows_with_data = sum(
            1
            for r in rows
            if r["energy_kwh"] is not None
            or r["soc_pct"] is not None
            or r["availability_pct"] is not None
        )
        if rows_with_data < _EMPTY_MONTH_THRESHOLD:
            consecutive_empty += 1
            if consecutive_empty >= 2:
                log_fn(
                    f"Historical import: two empty months in a row, "
                    f"stopping at {month.strftime('%Y-%m')}."
                )
                break
        else:
            consecutive_empty = 0
            inserted = data_store.bulk_insert_or_ignore_intervals(rows)
            log_fn(
                f"Historical import: {month.strftime('%Y-%m')} - "
                f"{inserted}/{len(rows)} interval rows inserted."
            )

        # Import prices and emissions for this month regardless of interval count.
        await _import_prices_for_month(
            fm_client, data_store, month_start, effective_end, log_fn, errors=errors
        )
        await _import_emissions_for_month(
            fm_client, data_store, month_start, effective_end, log_fn, errors=errors
        )

        # Stop import if any sensor fetch failed after all retries.
        if errors:
            log_fn(
                f"Historical import: stopping due to API errors "
                f"(sensors: {errors}) at {month.strftime('%Y-%m')}.",
                level="WARNING",
            )
            if on_notify is not None:
                on_notify(
                    "Historische import gestopt vanwege API-fouten. "
                    "Herstart V2G Liberty om het opnieuw te proberen."
                )
            return

        months_processed += 1

        # Step back one month.
        month = (month - timedelta(days=1)).replace(day=1)

    _REPORT_FILE.write_text(
        f"Historical import completed at {datetime.now(timezone.utc).isoformat()}\n"
    )
    log_fn("Historical import: complete.")

    if on_notify is not None:
        on_notify("Historische data-import is succesvol afgerond.")

    if on_complete is not None:
        log_fn("Historical import: triggering post-import review.")
        on_complete()


async def _fetch_month_rows(
    fm_client,
    month_start: datetime,
    month_end: datetime,
    log_fn,
    errors: list | None = None,
) -> list[dict]:
    """Fetch and merge power, SoC, and availability data for one month.

    Returns a list of interval dicts ready for bulk_insert_or_ignore_intervals().

    Filters power data by ``FM_ACCOUNT_POWER_SOURCE_ID`` when known, so only
    actual charger measurements are returned rather than near-constant scheduler
    beliefs.  SoC and availability come from a single source and need no filter.
    """
    power_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_POWER_SENSOR_ID,
        "kW",
        month_start,
        month_end,
        log_fn,
        source_id=c.FM_ACCOUNT_POWER_SOURCE_ID,
        errors=errors,
    )
    await asyncio.sleep(_SENSOR_PAUSE_SECONDS)
    soc_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_SOC_SENSOR_ID,
        "%",
        month_start,
        month_end,
        log_fn,
        errors=errors,
    )
    await asyncio.sleep(_SENSOR_PAUSE_SECONDS)
    avail_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
        "%",
        month_start,
        month_end,
        log_fn,
        errors=errors,
    )

    # Build a complete 5-minute grid for the month.  Every slot gets a row;
    # energy_kwh is None when no power measurement exists (not 0.0).
    rows = []
    slot = month_start
    while slot < month_end:
        ts = slot.isoformat()
        slot += timedelta(minutes=_INTERVAL_MINUTES)

        power_kw = power_events.get(ts)
        energy_kwh = (
            power_kw * _ENERGY_FROM_POWER_FACTOR if power_kw is not None else None
        )
        rows.append(
            {
                "timestamp": ts,
                "energy_kwh": energy_kwh,
                "app_state": "unknown",
                "soc_pct": soc_events.get(ts),
                "availability_pct": avail_events.get(ts),
                "is_repaired": 2,  # Pending review by DataRepairer
            }
        )

    return rows


async def _import_prices_for_month(
    fm_client,
    data_store,
    month_start: datetime,
    month_end: datetime,
    log_fn,
    errors: list | None = None,
) -> None:
    """Fetch consumption and production prices for one month and store them.

    Uses 60-min resolution for months before the EU market cutover (2025-10-01),
    and the configured PRICE_RESOLUTION_MINUTES afterwards. Upsamples to 5-min
    by forward-filling within each price slot.
    """
    if not (c.FM_PRICE_CONSUMPTION_SENSOR_ID and c.FM_PRICE_PRODUCTION_SENSOR_ID):
        return

    # Before 2025-10-01 the EU day-ahead market published hourly prices.
    if month_start.date() < _PRICE_MARKET_CUTOVER:
        step = 60
    else:
        step = c.PRICE_RESOLUTION_MINUTES  # e.g. 15 or 30 depending on provider

    unit = f"c{c.CURRENCY}/kWh"

    cons = await _fetch_sensor_events(
        fm_client,
        c.FM_PRICE_CONSUMPTION_SENSOR_ID,
        unit,
        month_start,
        month_end,
        log_fn,
        step_minutes=step,
        errors=errors,
    )
    await asyncio.sleep(_SENSOR_PAUSE_SECONDS)
    prod = await _fetch_sensor_events(
        fm_client,
        c.FM_PRICE_PRODUCTION_SENSOR_ID,
        unit,
        month_start,
        month_end,
        log_fn,
        step_minutes=step,
        errors=errors,
    )

    all_ts = set(cons) | set(prod)
    if not all_ts:
        return

    # Upsample to 5-min by forward-filling each price slot.
    # Divide by 100: FM returns cEUR/kWh, price_log stores EUR/kWh.
    upsample_steps = step // c.FM_EVENT_RESOLUTION_IN_MINUTES  # e.g. 60//5=12, 15//5=3
    rows = []
    for ts_iso in sorted(all_ts):
        c_price = cons.get(ts_iso)
        p_price = prod.get(ts_iso)
        if c_price is None and p_price is None:
            continue
        c_price_eur = c_price / 100 if c_price is not None else None
        p_price_eur = p_price / 100 if p_price is not None else None
        ts_dt = datetime.fromisoformat(ts_iso)
        for i in range(upsample_steps):
            offset_ts = (ts_dt + timedelta(minutes=_INTERVAL_MINUTES * i)).isoformat()
            rows.append((offset_ts, c_price_eur, p_price_eur, None))  # rating = None

    data_store.upsert_prices(rows, recalculate_ratings=True)
    log_fn(
        f"Historical import: {month_start.strftime('%Y-%m')} - {len(rows)} price rows."
    )


async def _import_emissions_for_month(
    fm_client,
    data_store,
    month_start: datetime,
    month_end: datetime,
    log_fn,
    errors: list | None = None,
) -> None:
    """Fetch CO2 emission intensity for one month and store it."""
    if not c.FM_EMISSIONS_SENSOR_ID:
        return

    events = await _fetch_sensor_events(
        fm_client,
        c.FM_EMISSIONS_SENSOR_ID,
        c.EMISSIONS_UOM,
        month_start,
        month_end,
        log_fn,
        errors=errors,
    )
    if not events:
        return

    rows = [(ts, val) for ts, val in events.items()]
    data_store.upsert_emissions(rows)
    log_fn(
        f"Historical import: {month_start.strftime('%Y-%m')} - {len(rows)} emission rows."
    )


async def _fetch_sensor_events(
    fm_client,
    sensor_id: int,
    unit: str,
    start: datetime,
    end: datetime,
    log_fn,
    step_minutes: int = _INTERVAL_MINUTES,
    source_id: int | None = None,
    errors: list | None = None,
) -> dict[str, float]:
    """Fetch sensor data and return a {timestamp_iso: value} dict.

    Splits the requested period into chunks of ``_CHUNK_DAYS`` to avoid
    server-side timeouts on large queries.  Uses the FlexMeasures client to
    request data at the given resolution.  Null values (gaps in the data)
    are omitted from the result.

    When ``source_id`` is provided only beliefs from that source are returned.
    Pass ``c.FM_ACCOUNT_POWER_SOURCE_ID`` for the power sensor to filter out
    near-constant scheduler beliefs and retrieve actual charger measurements.

    Retries up to ``_MAX_RETRIES`` times per chunk on failure, with a
    ``_RETRY_DELAY_SECONDS`` pause between attempts.  When all retries are
    exhausted the sensor_id is appended to ``errors`` (if provided) and the
    results collected so far are returned.
    """
    if not sensor_id:
        return {}

    result: dict[str, float] = {}
    now_utc = datetime.now(timezone.utc)

    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=_CHUNK_DAYS), end)
        duration = chunk_end - chunk_start

        data = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                kwargs = {
                    "sensor_id": sensor_id,
                    "start": chunk_start,
                    "duration": duration,
                    "unit": unit,
                    "resolution": f"PT{step_minutes}M",
                }
                if source_id is not None:
                    kwargs["source"] = source_id
                data = await fm_client.get_sensor_data(**kwargs)
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    log_fn(
                        f"Historical import: fetching sensor {sensor_id} failed "
                        f"(attempt {attempt}/{_MAX_RETRIES}): {exc}. "
                        f"Retrying in {_RETRY_DELAY_SECONDS}s...",
                        level="WARNING",
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                else:
                    log_fn(
                        f"Historical import: fetching sensor {sensor_id} failed "
                        f"after {_MAX_RETRIES} attempts: {exc}",
                        level="WARNING",
                    )
                    if errors is not None:
                        errors.append(sensor_id)
                    return result

        if data is not None:
            start_dt = datetime.fromisoformat(data["start"]).astimezone(timezone.utc)
            for i, value in enumerate(data.get("values", [])):
                utc_dt = start_dt + timedelta(minutes=step_minutes * i)
                # Never import future data.
                if utc_dt >= now_utc:
                    break
                if value is not None:
                    result[utc_dt.isoformat()] = value

        chunk_start = chunk_end
        # Pause before the next chunk to avoid overwhelming the FM server.
        if chunk_start < end:
            await asyncio.sleep(_CHUNK_PAUSE_SECONDS)

    return result
