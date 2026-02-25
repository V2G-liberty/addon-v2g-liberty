"""Temporary module: import historical FM sensor data into the local database.

Fetches power, SoC, availability, price, and emission data from FlexMeasures.
Runs once per installation of this version. A flag file is written after a
successful import; deleting the flag file forces a re-import on next startup
(useful for hotfixes).

This module is temporary and will be removed once all users have upgraded.
"""

import calendar
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import constants as c

# Never fetch data older than this date.
########################################################################
# TODO:                                                                #
# change to (2024, 9, 1) for production, this value is for testing.    #
########################################################################
_EARLIEST_DATE = date(2025, 9, 1)
# Treat a month as empty if fewer than this many events are returned.
_EMPTY_MONTH_THRESHOLD = 50
# 5-minute intervals: energy_kwh = power_MW * 1000 * 5 / 60
_INTERVAL_MINUTES = 5
_ENERGY_FROM_POWER_FACTOR = 1000 * _INTERVAL_MINUTES / 60
# Written after a successful import; delete to force a re-import.
_IMPORT_DONE_FLAG = Path("/data/fm_historical_import_done")
# EU day-ahead electricity market switched from 60-min to 15-min on this date.
_PRICE_MARKET_CUTOVER = date(2025, 10, 1)


async def run_historical_import(data_store, log_fn, fm_client=None) -> None:
    """Import historical FM data into the local database.

    Skipped if the flag file exists. After a successful import the flag file
    is written. To force a re-import (e.g. after a hotfix), delete the file:
      /data/fm_historical_import_done
    """
    if _IMPORT_DONE_FLAG.exists():
        log_fn("Historical import: already done (flag file exists), skipping.")
        return

    if fm_client is None:
        log_fn(
            "Historical import: no FM client available, aborting.",
            level="WARNING",
        )
        return

    log_fn("Historical import: starting.")

    # Remove any rows from a previous historical import so that the fresh
    # import (now using local-timezone timestamps) doesn't leave duplicates
    # alongside old UTC-format rows in interval_log.
    deleted = data_store.delete_historical_intervals()
    if deleted:
        log_fn(f"Historical import: removed {deleted} stale 'unknown' interval rows.")

    month = date.today().replace(day=1)
    consecutive_empty = 0

    while month >= _EARLIEST_DATE:
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

        rows = await _fetch_month_rows(fm_client, month_start, effective_end, log_fn)

        if len(rows) < _EMPTY_MONTH_THRESHOLD:
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
            fm_client, data_store, month_start, effective_end, log_fn
        )
        await _import_emissions_for_month(
            fm_client, data_store, month_start, effective_end, log_fn
        )

        # Step back one month.
        month = (month - timedelta(days=1)).replace(day=1)

    _IMPORT_DONE_FLAG.touch()
    log_fn("Historical import: complete.")


async def _fetch_month_rows(
    fm_client,
    month_start: datetime,
    month_end: datetime,
    log_fn,
) -> list[dict]:
    """Fetch and merge power, SoC, and availability data for one month.

    Returns a list of interval dicts ready for bulk_insert_or_ignore_intervals().

    Uses the FM ``prior`` belief-time parameter to retrieve actual charger
    measurements rather than retroactively-stored scheduler beliefs.  The
    Seita scheduler re-runs regularly and stores new schedule beliefs with
    belief_time = today; actual measurements were stored shortly after each
    5-minute interval back in the historical month.  By capping ``prior`` to
    24 hours after month_end, those recent scheduler beliefs are excluded and
    FM returns the actual measurements as the most recent eligible belief.
    """
    # 24-hour buffer ensures all measurements for the last interval of the
    # month are captured even if the charger reported them slightly late.
    prior = (month_end + timedelta(hours=24)).isoformat()

    power_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_POWER_SENSOR_ID,
        "MW",
        month_start,
        month_end,
        log_fn,
        prior=prior,
    )
    soc_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_SOC_SENSOR_ID,
        "%",
        month_start,
        month_end,
        log_fn,
        prior=prior,
    )
    avail_events = await _fetch_sensor_events(
        fm_client,
        c.FM_ACCOUNT_AVAILABILITY_SENSOR_ID,
        "%",
        month_start,
        month_end,
        log_fn,
        prior=prior,
    )

    # Union of all timestamps seen across the three sensors.
    all_timestamps = set(power_events) | set(soc_events) | set(avail_events)

    rows = []
    for ts in sorted(all_timestamps):
        power_mw = power_events.get(ts)
        energy_kwh = (
            (power_mw * _ENERGY_FROM_POWER_FACTOR) if power_mw is not None else 0.0
        )
        rows.append(
            {
                "timestamp": ts,
                "energy_kwh": energy_kwh,
                "app_state": "unknown",
                "soc_pct": soc_events.get(ts),
                "availability_pct": avail_events.get(ts) or 0.0,
            }
        )

    return rows


async def _import_prices_for_month(
    fm_client,
    data_store,
    month_start: datetime,
    month_end: datetime,
    log_fn,
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
    )
    prod = await _fetch_sensor_events(
        fm_client,
        c.FM_PRICE_PRODUCTION_SENSOR_ID,
        unit,
        month_start,
        month_end,
        log_fn,
        step_minutes=step,
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

    data_store.upsert_prices(rows, recalculate_ratings=False)
    log_fn(
        f"Historical import: {month_start.strftime('%Y-%m')} - {len(rows)} price rows."
    )


async def _import_emissions_for_month(
    fm_client,
    data_store,
    month_start: datetime,
    month_end: datetime,
    log_fn,
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
        # step_minutes defaults to _INTERVAL_MINUTES (5)
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
    prior: str | None = None,
) -> dict[str, float]:
    """Fetch sensor data and return a {timestamp_iso: value} dict.

    Uses the FlexMeasures client to request data at the given resolution.
    Null values (gaps in the data) are omitted from the result.

    When ``prior`` is provided it is passed to FM as a belief-time upper bound
    so only beliefs formed before that timestamp are considered.  This lets
    callers retrieve actual charger measurements (stored shortly after each
    event) rather than retroactively-updated scheduler beliefs (stored today).
    """
    if not sensor_id:
        return {}

    duration = end - start
    extra = {"prior": prior} if prior is not None else {}
    try:
        data = await fm_client.get_sensor_data(
            sensor_id=sensor_id,
            start=start,
            duration=duration,
            unit=unit,
            resolution=f"PT{step_minutes}M",
            **extra,
        )
    except Exception as exc:
        log_fn(
            f"Historical import: fetching sensor {sensor_id} failed: {exc}",
            level="WARNING",
        )
        return {}

    # Convert to the local timezone so timestamps match those written by the
    # live system (which uses get_local_now() / c.TZ).  Fall back to UTC if
    # c.TZ has not been initialised yet (e.g. in unit tests).
    local_tz = getattr(c, "TZ", None) or timezone.utc
    now_utc = datetime.now(timezone.utc)

    result = {}
    start_dt = datetime.fromisoformat(data["start"]).astimezone(timezone.utc)
    for i, value in enumerate(data.get("values", [])):
        utc_dt = start_dt + timedelta(minutes=step_minutes * i)
        # Never import future data — FM may return day-ahead prices beyond now.
        if utc_dt >= now_utc:
            break
        if value is not None:
            ts = utc_dt.astimezone(local_tz).isoformat()
            result[ts] = value

    return result
