"""Fetcher for monthly reference electricity prices from CBS (Statistics Netherlands).

Fetches two price components per month from the CBS OData v3 API (table 85592NED):
- VariabelLeveringstariefContractprijs_9  — variable delivery tariff (€/kWh excl. BTW)
- Energiebelasting_14                     — energy tax (€/kWh excl. BTW)

Prices are stored including BTW (× VAT_MULTIPLIER).

CBS endpoint: https://opendata.cbs.nl/ODataApi/OData/85592NED/UntypedDataSet
BTW filter:   Btw eq 'A048944'  (excl. BTW rows only)
Period format in CBS: YYYYMMnn  (e.g. "2024MM09" for September 2024)

Only data from 2024-09 onwards is imported.
"""

import urllib.request
import urllib.parse
import json
from datetime import datetime, timezone
from typing import Callable

_CBS_BASE_URL = "https://opendata.cbs.nl/ODataApi/OData/85592NED/UntypedDataSet"

# CBS column names for electricity (columns 7–15 in 85592NED).
_COL_DELIVERY = "VariabelLeveringstariefContractprijs_9"
_COL_ENERGY_TAX = "Energiebelasting_14"

# BTW dimension code for "exclusief BTW".
_BTW_EXCL = "A048944"

# Fixed VAT multiplier — to be made configurable if needed.
VAT_MULTIPLIER = 1.21

# Earliest month for historical import.
HISTORICAL_START_MONTH = "2024-09"

_CBS_SOURCE_LABEL = "CBS 85592NED"
_PAGE_SIZE = 100


def fetch_reference_prices(
    start_month: str,
    end_month: str,
    log_fn: Callable[[str, str], None],
) -> list[tuple[str, float, float, str, str]]:
    """Fetch monthly reference electricity prices from CBS.

    Retrieves the variable delivery tariff and energy tax per month and
    returns them as a list of tuples ready for
    ``DataStore.upsert_reference_prices()``.

    Prices from CBS are excl. BTW and are multiplied by VAT_MULTIPLIER
    before being returned.

    Args:
        start_month: First month to fetch, inclusive (``YYYY-MM``).
            Clamped to HISTORICAL_START_MONTH if earlier.
        end_month: Last month to fetch, inclusive (``YYYY-MM``).
        log_fn: Callable ``(message: str, level: str) -> None``.

    Returns:
        List of tuples:
        ``(month, delivery_price_eur_kwh, energy_tax_eur_kwh, source, fetched_at)``
        Prices are incl. BTW. Returns an empty list on failure.
    """
    if start_month < HISTORICAL_START_MONTH:
        log_fn(
            f"CBS fetcher: start_month {start_month} clamped to "
            f"{HISTORICAL_START_MONTH}.",
            "INFO",
        )
        start_month = HISTORICAL_START_MONTH

    try:
        raw_rows = _fetch_all_pages(start_month, end_month, log_fn)
    except Exception as exc:
        log_fn(f"CBS fetcher: unexpected error during fetch: {exc}", "ERROR")
        return []

    if not raw_rows:
        log_fn(
            f"CBS fetcher: no data returned for {start_month}–{end_month}.",
            "WARNING",
        )
        return []

    result = _parse_rows(raw_rows, log_fn)
    log_fn(
        f"CBS fetcher: fetched {len(result)} reference price row(s) "
        f"({start_month}–{end_month}).",
        "INFO",
    )
    return result


def _month_to_cbs(month: str) -> str:
    """Convert ``YYYY-MM`` to CBS period format ``YYYYMMnn``."""
    year, mon = month.split("-")
    return f"{year}MM{mon}"


def _cbs_to_month(cbs_period: str) -> str:
    """Convert CBS period ``YYYYMMnn`` to ``YYYY-MM``."""
    # Format is always "2024MM09" — year(4) + "MM" + month(2)
    year = cbs_period[:4]
    mon = cbs_period[6:8]
    return f"{year}-{mon}"


def _fetch_all_pages(
    start_month: str,
    end_month: str,
    log_fn: Callable[[str, str], None],
) -> list[dict]:
    """Fetch all pages from the CBS OData v3 endpoint."""
    cbs_start = _month_to_cbs(start_month)
    cbs_end = _month_to_cbs(end_month)

    filter_expr = (
        f"Btw eq '{_BTW_EXCL}' and "
        f"Perioden ge '{cbs_start}' and Perioden le '{cbs_end}'"
    )
    select_fields = f"Perioden,{_COL_DELIVERY},{_COL_ENERGY_TAX}"

    base_params = {
        "$filter": filter_expr,
        "$select": select_fields,
        "$top": str(_PAGE_SIZE),
    }

    all_rows: list[dict] = []
    skip = 0

    while True:
        params = dict(base_params)
        if skip:
            params["$skip"] = str(skip)

        url = _CBS_BASE_URL + "?" + urllib.parse.urlencode(params)
        log_fn(f"CBS fetcher: GET {url}", "DEBUG")

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                if response.status != 200:
                    log_fn(
                        f"CBS fetcher: HTTP {response.status} from CBS API.",
                        "ERROR",
                    )
                    return []
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            log_fn(f"CBS fetcher: HTTP error {exc.code}: {exc.reason}", "ERROR")
            return []
        except urllib.error.URLError as exc:
            log_fn(f"CBS fetcher: URL error: {exc.reason}", "ERROR")
            return []
        except json.JSONDecodeError as exc:
            log_fn(f"CBS fetcher: invalid JSON in response: {exc}", "ERROR")
            return []

        page_rows = body.get("value", [])
        all_rows.extend(page_rows)

        # OData v3: follow @odata.nextLink if present, otherwise stop.
        if "@odata.nextLink" in body:
            skip += _PAGE_SIZE
        else:
            break

    return all_rows


def _parse_rows(
    raw_rows: list[dict],
    log_fn: Callable[[str, str], None],
) -> list[tuple[str, float, float, str, str]]:
    """Convert raw CBS rows to reference price tuples (incl. BTW)."""
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    result: list[tuple[str, float, float, str, str]] = []

    for row in raw_rows:
        cbs_period = row.get("Perioden", "").strip()
        raw_delivery = row.get(_COL_DELIVERY, "").strip()
        raw_tax = row.get(_COL_ENERGY_TAX, "").strip()

        if not cbs_period:
            log_fn(f"CBS fetcher: row without Perioden, skipping: {row}", "WARNING")
            continue

        # CBS includes annual aggregate rows formatted as "YYYYMMnn" with month "00".
        # Skip these — only monthly rows are relevant.
        if cbs_period[6:8] == "00":
            continue

        if not raw_delivery or not raw_tax:
            log_fn(
                f"CBS fetcher: missing value(s) for {cbs_period} — "
                f"delivery={raw_delivery!r}, tax={raw_tax!r}. Skipping.",
                "WARNING",
            )
            continue

        try:
            delivery = float(raw_delivery) * VAT_MULTIPLIER
            tax = float(raw_tax) * VAT_MULTIPLIER
        except (TypeError, ValueError) as exc:
            log_fn(
                f"CBS fetcher: non-numeric value for {cbs_period}: {exc}. Skipping.",
                "WARNING",
            )
            continue

        month = _cbs_to_month(cbs_period)
        result.append(
            (month, round(delivery, 6), round(tax, 6), _CBS_SOURCE_LABEL, fetched_at)
        )

    return result
