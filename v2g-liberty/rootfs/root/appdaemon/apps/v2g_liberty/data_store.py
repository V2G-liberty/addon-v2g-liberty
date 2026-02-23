"""Module for local SQLite data storage."""

import sqlite3
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass

from .log_wrapper import get_class_method_logger
from .v2g_globals import get_local_now

CURRENT_SCHEMA_VERSION = 1

PRICE_RATING_BINS = [0, 0.15, 0.35, 0.65, 0.85, 1.0]
PRICE_RATING_LABELS = ["very_low", "low", "average", "high", "very_high"]

VALID_GRANULARITIES = ("quarter", "hour", "day", "week", "month", "year")

# Priority for app_state tiebreaking (lower number = higher priority).
_APP_STATE_PRIORITY = {
    "error": 1,
    "not_connected": 2,
    "max_boost": 3,
    "charge": 4,
    "discharge": 5,
    "pause": 6,
    "automatic": 7,
    "unknown": 8,
}


def calculate_price_ratings(prices_df: pd.DataFrame) -> pd.Series:
    """Calculate price ratings based on percentile position of consumption price.

    Uses pandas rank(pct=True) + pd.cut() to assign each price a rating
    based on its percentile position within the provided price window.

    Boundaries: [0, 0.15, 0.35, 0.65, 0.85, 1.0]
    Labels: very_low / low / average / high / very_high

    Args:
        prices_df: DataFrame with at least a 'consumption_price_kwh' column.

    Returns:
        Series[str] with price rating labels, same index as input.
    """
    if prices_df.empty:
        return pd.Series(dtype="object")

    pct_ranks = prices_df["consumption_price_kwh"].rank(pct=True)
    ratings = pd.cut(
        pct_ranks,
        bins=PRICE_RATING_BINS,
        labels=PRICE_RATING_LABELS,
        include_lowest=True,
    )
    return ratings.astype(str)


def _period_key(timestamp_str: str, granularity: str) -> str:
    """Compute the period bucket key for a given timestamp and granularity.

    Args:
        timestamp_str: ISO 8601 timestamp string (e.g. "2026-02-21T12:05:00+01:00").
        granularity: One of "quarter", "hour", "day", "week", "month", "year".

    Returns:
        Period key string. Format depends on granularity:
        - quarter/hour: ISO 8601 timestamp (e.g. "2026-02-21T12:00:00+01:00")
        - day: date string (e.g. "2026-02-21")
        - week: ISO week string (e.g. "2026-W08")
        - month: year-month string (e.g. "2026-02")
        - year: year string (e.g. "2026")
    """
    dt = datetime.fromisoformat(timestamp_str)
    if granularity == "quarter":
        quarter_minute = (dt.minute // 15) * 15
        return dt.replace(minute=quarter_minute, second=0, microsecond=0).isoformat()
    if granularity == "hour":
        return dt.replace(minute=0, second=0, microsecond=0).isoformat()
    if granularity == "day":
        return dt.strftime("%Y-%m-%d")
    if granularity == "week":
        iso_year, iso_week, _ = dt.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if granularity == "month":
        return dt.strftime("%Y-%m")
    if granularity == "year":
        return dt.strftime("%Y")
    raise ValueError(f"Unknown granularity: {granularity}")


def _dominant_app_state(app_states: list[str]) -> str:
    """Find the dominant app_state using longest contiguous run logic.

    Identifies the longest contiguous run of the same state within the list.
    When runs tie in length, the state with higher priority wins
    (lower number in _APP_STATE_PRIORITY).

    Appends '+' suffix when multiple distinct states are present.

    Args:
        app_states: Ordered list of app_state values from consecutive intervals.

    Returns:
        Dominant state string, optionally with '+' suffix.
    """
    if not app_states:
        return "unknown"

    distinct_states = set(app_states)

    # Find all contiguous runs
    runs: list[tuple[str, int]] = []
    current_state = app_states[0]
    current_length = 1

    for state in app_states[1:]:
        if state == current_state:
            current_length += 1
        else:
            runs.append((current_state, current_length))
            current_state = state
            current_length = 1
    runs.append((current_state, current_length))

    # Find the longest run; tiebreaker: higher priority (lower number)
    best = max(
        runs,
        key=lambda r: (r[1], -_APP_STATE_PRIORITY.get(r[0], 99)),
    )
    dominant = best[0]

    if len(distinct_states) > 1:
        return dominant + "+"
    return dominant


def _dominant_price_rating(ratings: list) -> str | None:
    """Find the most frequent price_rating from a list.

    Args:
        ratings: List of price_rating values (may contain None).

    Returns:
        Most frequent non-None rating, or None if all are None.
    """
    valid = [r for r in ratings if r is not None]
    if not valid:
        return None
    counter = Counter(valid)
    return counter.most_common(1)[0][0]


class DataStore:
    """Local SQLite database for interval, price, and reservation data.

    Stores charging interval data, energy prices, and calendar reservations
    in a local SQLite database. Data is used for user-facing statistics and
    daily batch export to FlexMeasures.

    Database location: /data/v2g_liberty_data.db
    """

    DB_PATH = "/data/v2g_liberty_data.db"

    def __init__(self, hass: Hass):
        self.__log = get_class_method_logger(hass.log)
        self.__connection: sqlite3.Connection | None = None
        self.__log("DataStore created.")

    async def initialise(self):
        """Open database, set PRAGMAs, create tables, and check schema version."""
        self.__log("Initialising DataStore.")
        self.__connection = sqlite3.connect(self.DB_PATH)
        self.__connection.row_factory = sqlite3.Row
        self.__set_pragmas()
        self.__create_tables()
        self.__check_schema_version()
        self.__log("DataStore initialised successfully.")

    def __set_pragmas(self):
        """Configure SQLite for flash-friendly operation."""
        cursor = self.__connection.cursor()
        cursor.execute("PRAGMA journal_mode = WAL")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.execute("PRAGMA temp_store = MEMORY")
        cursor.close()

    def __create_tables(self):
        """Create all tables if they don't exist."""
        cursor = self.__connection.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL,
                applied_at TEXT NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS interval_log (
                timestamp TEXT PRIMARY KEY,
                power_kw REAL NOT NULL,
                energy_kwh REAL NOT NULL,
                app_state TEXT NOT NULL,
                soc_pct REAL,
                availability_pct REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_log (
                timestamp TEXT PRIMARY KEY,
                consumption_price_kwh REAL NOT NULL,
                production_price_kwh REAL NOT NULL,
                price_rating TEXT
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS reservation_log (
                timestamp TEXT NOT NULL,
                start_timestamp TEXT NOT NULL,
                end_timestamp TEXT NOT NULL,
                target_soc_pct REAL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS emission_log (
                timestamp TEXT PRIMARY KEY,
                emission_intensity_kg_mwh REAL NOT NULL
            )
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fm_send_status (
                last_sent_up_to TEXT NOT NULL
            )
        """)

        self.__connection.commit()
        cursor.close()
        self.__log("All tables created/verified.")

    def __check_schema_version(self):
        """Check schema version and run migrations if needed."""
        cursor = self.__connection.cursor()
        cursor.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        )
        row = cursor.fetchone()

        if row is None:
            # First start: insert initial version
            now = get_local_now().isoformat()
            cursor.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (CURRENT_SCHEMA_VERSION, now),
            )
            self.__connection.commit()
            self.__log(
                f"Database created with schema version {CURRENT_SCHEMA_VERSION}."
            )
        else:
            current_version = row["version"]
            if current_version < CURRENT_SCHEMA_VERSION:
                self.__run_migrations(current_version)
            elif current_version > CURRENT_SCHEMA_VERSION:
                self.__log(
                    f"Database schema version {current_version} is newer than "
                    f"expected {CURRENT_SCHEMA_VERSION}. This may cause issues.",
                    level="WARNING",
                )
            else:
                self.__log(f"Database schema version {current_version} is up to date.")

        cursor.close()

    def __run_migrations(self, from_version: int):
        """Run sequential migrations from from_version to CURRENT_SCHEMA_VERSION."""
        self.__log(
            f"Migrating database from version {from_version} "
            f"to {CURRENT_SCHEMA_VERSION}."
        )

        now = get_local_now().isoformat()
        cursor = self.__connection.cursor()
        cursor.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (CURRENT_SCHEMA_VERSION, now),
        )
        self.__connection.commit()
        cursor.close()
        self.__log(f"Database migrated to version {CURRENT_SCHEMA_VERSION}.")

    @property
    def connection(self) -> sqlite3.Connection | None:
        """Provide read access to the database connection for advanced queries."""
        return self.__connection

    def insert_interval(
        self,
        timestamp: str,
        power_kw: float,
        energy_kwh: float,
        app_state: str,
        soc_pct: float | None,
        availability_pct: float,
    ) -> None:
        """Insert a single interval row into interval_log."""
        cursor = self.__connection.cursor()
        cursor.execute(
            "INSERT INTO interval_log "
            "(timestamp, power_kw, energy_kwh, app_state, soc_pct, "
            "availability_pct) VALUES (?, ?, ?, ?, ?, ?)",
            (
                timestamp,
                power_kw,
                energy_kwh,
                app_state,
                soc_pct,
                availability_pct,
            ),
        )
        self.__connection.commit()
        cursor.close()

    def upsert_prices(
        self, rows: list[tuple], recalculate_ratings: bool = True
    ) -> None:
        """Insert or replace price rows in price_log.

        Each row is a tuple:
        (timestamp, consumption_price_kwh, production_price_kwh, price_rating).
        Uses INSERT OR REPLACE for UPSERT behaviour
        (forecast → definitive price overwrites).

        When recalculate_ratings is True (default), recalculates price_rating
        for the affected 24h window (6h back, 18h forward).
        """
        cursor = self.__connection.cursor()
        cursor.executemany(
            "INSERT OR REPLACE INTO price_log "
            "(timestamp, consumption_price_kwh, production_price_kwh, "
            "price_rating) VALUES (?, ?, ?, ?)",
            rows,
        )
        self.__connection.commit()
        cursor.close()

        if recalculate_ratings and rows:
            self.__recalculate_ratings_for_window(rows)

        self.__log(f"Upserted {len(rows)} price row(s).")

    def __recalculate_ratings_for_window(self, rows: list[tuple]) -> None:
        """Recalculate price_rating for the 24h window around upserted rows.

        Window: 6h before earliest upserted timestamp to 18h after latest.
        """
        timestamps = [datetime.fromisoformat(row[0]) for row in rows]
        window_start = (min(timestamps) - timedelta(hours=6)).isoformat()
        window_end = (max(timestamps) + timedelta(hours=18)).isoformat()

        prices_df = self.get_prices_in_window(window_start, window_end)
        if prices_df.empty:
            return

        ratings = calculate_price_ratings(prices_df)

        cursor = self.__connection.cursor()
        updates = list(zip(ratings, prices_df["timestamp"]))
        cursor.executemany(
            "UPDATE price_log SET price_rating = ? WHERE timestamp = ?",
            updates,
        )
        self.__connection.commit()
        cursor.close()

    def upsert_emissions(self, rows: list[tuple]) -> None:
        """Insert or replace emission rows in emission_log.

        Each row is a tuple: (timestamp, emission_intensity_kg_mwh).
        Uses INSERT OR REPLACE for UPSERT behaviour.
        """
        cursor = self.__connection.cursor()
        cursor.executemany(
            "INSERT OR REPLACE INTO emission_log "
            "(timestamp, emission_intensity_kg_mwh) VALUES (?, ?)",
            rows,
        )
        self.__connection.commit()
        cursor.close()
        self.__log(f"Upserted {len(rows)} emission row(s).")

    def insert_reservation(
        self,
        timestamp: str,
        start_timestamp: str,
        end_timestamp: str,
        target_soc_pct: float | None = None,
    ) -> None:
        """Insert a reservation snapshot into reservation_log."""
        cursor = self.__connection.cursor()
        cursor.execute(
            "INSERT INTO reservation_log "
            "(timestamp, start_timestamp, end_timestamp, target_soc_pct) "
            "VALUES (?, ?, ?, ?)",
            (timestamp, start_timestamp, end_timestamp, target_soc_pct),
        )
        self.__connection.commit()
        cursor.close()

    def get_price_at(self, timestamp: str) -> tuple[float, float, str | None] | None:
        """Look up the price and rating for a specific timestamp.

        Returns (consumption_price_kwh, production_price_kwh, price_rating)
        or None if no price exists for the given timestamp.
        """
        cursor = self.__connection.cursor()
        cursor.execute(
            "SELECT consumption_price_kwh, production_price_kwh, price_rating "
            "FROM price_log WHERE timestamp = ?",
            (timestamp,),
        )
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        return (
            row["consumption_price_kwh"],
            row["production_price_kwh"],
            row["price_rating"],
        )

    def get_prices_in_window(self, start: str, end: str):
        """Retrieve prices within a time window as a pandas DataFrame.

        Returns DataFrame with columns: timestamp, consumption_price_kwh,
        production_price_kwh, price_rating. Used for price_rating
        (re)calculation over a 24-hour window.
        """
        cursor = self.__connection.cursor()
        cursor.execute(
            "SELECT timestamp, consumption_price_kwh, production_price_kwh, "
            "price_rating FROM price_log "
            "WHERE timestamp >= ? AND timestamp <= ? "
            "ORDER BY timestamp",
            (start, end),
        )
        rows = cursor.fetchall()
        cursor.close()

        if not rows:
            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "consumption_price_kwh",
                    "production_price_kwh",
                    "price_rating",
                ]
            )

        return pd.DataFrame(
            [dict(row) for row in rows],
        )

    def get_fm_last_sent(self) -> str | None:
        """Get the timestamp up to which data has been sent to FlexMeasures.

        Returns the ISO 8601 timestamp, or None if no data has been sent yet.
        """
        cursor = self.__connection.cursor()
        cursor.execute("SELECT last_sent_up_to FROM fm_send_status LIMIT 1")
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        return row["last_sent_up_to"]

    def set_fm_last_sent(self, timestamp: str) -> None:
        """Update the timestamp up to which data has been sent to FlexMeasures.

        Inserts a new row if none exists, otherwise updates the existing row.
        """
        cursor = self.__connection.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM fm_send_status")
        count = cursor.fetchone()["cnt"]
        if count == 0:
            cursor.execute(
                "INSERT INTO fm_send_status (last_sent_up_to) VALUES (?)",
                (timestamp,),
            )
        else:
            cursor.execute(
                "UPDATE fm_send_status SET last_sent_up_to = ?",
                (timestamp,),
            )
        self.__connection.commit()
        cursor.close()

    def get_intervals_since(self, since: str) -> list[dict]:
        """Retrieve interval_log rows after the given timestamp.

        Returns a list of dicts with keys: timestamp, power_kw,
        soc_pct, availability_pct. Ordered by timestamp ascending.
        """
        cursor = self.__connection.cursor()
        cursor.execute(
            "SELECT timestamp, power_kw, soc_pct, availability_pct "
            "FROM interval_log WHERE timestamp > ? ORDER BY timestamp",
            (since,),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]

    def get_aggregated_data(self, start: str, end: str, granularity: str) -> list[dict]:
        """Get aggregated data for a time range at the specified granularity.

        Fetches raw interval data with joined prices and emissions, then
        aggregates per period bucket. The return format depends on granularity:

        - quarter (15-min): period_start, app_state, consumption_price,
          production_price, price_rating, soc_pct, energy_wh,
          charge_cost, discharge_revenue
        - hour: period_start, app_state, avg_price, price_rating,
          charge_wh, charge_cost, discharge_wh, discharge_revenue, soc_pct
        - day/week/month/year: period_start, availability_pct, charge_kwh,
          charge_cost, discharge_kwh, discharge_revenue, net_kwh, net_cost,
          co2_kg

        Args:
            start: ISO 8601 timestamp, inclusive lower bound.
            end: ISO 8601 timestamp, exclusive upper bound.
            granularity: One of "quarter", "hour", "day", "week",
                "month", "year".

        Returns:
            List of dicts, one per period, ordered by period_start.
        """
        if granularity not in VALID_GRANULARITIES:
            raise ValueError(
                f"Invalid granularity '{granularity}'. "
                f"Must be one of {VALID_GRANULARITIES}."
            )

        raw = self.__fetch_intervals_with_joins(start, end)
        if not raw:
            return []

        # Group intervals by period bucket
        buckets: dict[str, list[dict]] = {}
        for row in raw:
            key = _period_key(row["timestamp"], granularity)
            buckets.setdefault(key, []).append(row)

        # Aggregate each bucket
        results = []
        for period_start in sorted(buckets):
            intervals = buckets[period_start]
            if granularity == "quarter":
                results.append(self.__aggregate_quarter(period_start, intervals))
            elif granularity == "hour":
                results.append(self.__aggregate_hour(period_start, intervals))
            else:
                results.append(self.__aggregate_period(period_start, intervals))

        return results

    def __fetch_intervals_with_joins(self, start: str, end: str) -> list[dict]:
        """Fetch intervals with joined price and emission data."""
        cursor = self.__connection.cursor()
        cursor.execute(
            "SELECT i.timestamp, i.power_kw, i.energy_kwh, i.app_state, "
            "i.soc_pct, i.availability_pct, "
            "p.consumption_price_kwh, p.production_price_kwh, p.price_rating, "
            "e.emission_intensity_kg_mwh "
            "FROM interval_log i "
            "LEFT JOIN price_log p ON i.timestamp = p.timestamp "
            "LEFT JOIN emission_log e ON i.timestamp = e.timestamp "
            "WHERE i.timestamp >= ? AND i.timestamp < ? "
            "ORDER BY i.timestamp",
            (start, end),
        )
        rows = cursor.fetchall()
        cursor.close()
        return [dict(row) for row in rows]

    @staticmethod
    def __aggregate_quarter(period_start: str, intervals: list[dict]) -> dict:
        """Aggregate intervals for a 15-min quarter period."""
        app_states = [i["app_state"] for i in intervals]

        # Net energy
        energy_kwh = sum(i["energy_kwh"] for i in intervals)
        energy_wh = round(energy_kwh * 1000)

        # Average prices (non-null only)
        cons_prices = [
            i["consumption_price_kwh"]
            for i in intervals
            if i["consumption_price_kwh"] is not None
        ]
        prod_prices = [
            i["production_price_kwh"]
            for i in intervals
            if i["production_price_kwh"] is not None
        ]
        consumption_price = sum(cons_prices) / len(cons_prices) if cons_prices else None
        production_price = sum(prod_prices) / len(prod_prices) if prod_prices else None

        # Price rating — most frequent
        ratings = [i["price_rating"] for i in intervals]

        # SoC — last non-null value in the period
        soc_values = [i["soc_pct"] for i in intervals if i["soc_pct"] is not None]
        soc_pct = soc_values[-1] if soc_values else None

        # Financial: charge cost and discharge revenue
        charge_cost = sum(
            i["energy_kwh"] * i["consumption_price_kwh"]
            for i in intervals
            if i["energy_kwh"] > 0 and i["consumption_price_kwh"] is not None
        )
        discharge_revenue = sum(
            abs(i["energy_kwh"]) * i["production_price_kwh"]
            for i in intervals
            if i["energy_kwh"] < 0 and i["production_price_kwh"] is not None
        )

        return {
            "period_start": period_start,
            "app_state": _dominant_app_state(app_states),
            "consumption_price": (
                round(consumption_price, 5) if consumption_price is not None else None
            ),
            "production_price": (
                round(production_price, 5) if production_price is not None else None
            ),
            "price_rating": _dominant_price_rating(ratings),
            "soc_pct": soc_pct,
            "energy_wh": energy_wh,
            "charge_cost": round(charge_cost, 4),
            "discharge_revenue": round(discharge_revenue, 4),
        }

    @staticmethod
    def __aggregate_hour(period_start: str, intervals: list[dict]) -> dict:
        """Aggregate intervals for a 1-hour period."""
        app_states = [i["app_state"] for i in intervals]

        # Charge/discharge split
        charge_kwh = sum(i["energy_kwh"] for i in intervals if i["energy_kwh"] > 0)
        discharge_kwh = sum(
            abs(i["energy_kwh"]) for i in intervals if i["energy_kwh"] < 0
        )

        charge_cost = sum(
            i["energy_kwh"] * i["consumption_price_kwh"]
            for i in intervals
            if i["energy_kwh"] > 0 and i["consumption_price_kwh"] is not None
        )
        discharge_revenue = sum(
            abs(i["energy_kwh"]) * i["production_price_kwh"]
            for i in intervals
            if i["energy_kwh"] < 0 and i["production_price_kwh"] is not None
        )

        # Weighted average price
        total_kwh = charge_kwh + discharge_kwh
        if total_kwh > 0:
            avg_price = (charge_cost + discharge_revenue) / total_kwh
        else:
            # No energy flow — simple average of available consumption prices
            cons = [
                i["consumption_price_kwh"]
                for i in intervals
                if i["consumption_price_kwh"] is not None
            ]
            avg_price = sum(cons) / len(cons) if cons else None

        # Price rating — most frequent
        ratings = [i["price_rating"] for i in intervals]

        # SoC — last non-null value
        soc_values = [i["soc_pct"] for i in intervals if i["soc_pct"] is not None]
        soc_pct = soc_values[-1] if soc_values else None

        return {
            "period_start": period_start,
            "app_state": _dominant_app_state(app_states),
            "avg_price": (round(avg_price, 5) if avg_price is not None else None),
            "price_rating": _dominant_price_rating(ratings),
            "charge_wh": round(charge_kwh * 1000),
            "charge_cost": round(charge_cost, 4),
            "discharge_wh": round(discharge_kwh * 1000),
            "discharge_revenue": round(discharge_revenue, 4),
            "soc_pct": soc_pct,
        }

    @staticmethod
    def __aggregate_period(period_start: str, intervals: list[dict]) -> dict:
        """Aggregate intervals for day/week/month/year periods."""
        # Availability — average across all intervals
        availability_values = [i["availability_pct"] for i in intervals]
        avg_availability = sum(availability_values) / len(availability_values)

        # Charge/discharge split
        charge_kwh = sum(i["energy_kwh"] for i in intervals if i["energy_kwh"] > 0)
        discharge_kwh = sum(
            abs(i["energy_kwh"]) for i in intervals if i["energy_kwh"] < 0
        )

        charge_cost = sum(
            i["energy_kwh"] * i["consumption_price_kwh"]
            for i in intervals
            if i["energy_kwh"] > 0 and i["consumption_price_kwh"] is not None
        )
        discharge_revenue = sum(
            abs(i["energy_kwh"]) * i["production_price_kwh"]
            for i in intervals
            if i["energy_kwh"] < 0 and i["production_price_kwh"] is not None
        )

        net_kwh = charge_kwh - discharge_kwh
        net_cost = charge_cost - discharge_revenue

        # CO2: energy_kwh × emission_intensity_kg_mwh / 1000 → kg
        co2_kg = sum(
            i["energy_kwh"] * i["emission_intensity_kg_mwh"] / 1000
            for i in intervals
            if i["emission_intensity_kg_mwh"] is not None
        )

        return {
            "period_start": period_start,
            "availability_pct": round(avg_availability, 1),
            "charge_kwh": round(charge_kwh, 2),
            "charge_cost": round(charge_cost, 4),
            "discharge_kwh": round(discharge_kwh, 2),
            "discharge_revenue": round(discharge_revenue, 4),
            "net_kwh": round(net_kwh, 2),
            "net_cost": round(net_cost, 4),
            "co2_kg": round(co2_kg, 1),
        }

    def close(self):
        """Close the database connection."""
        if self.__connection:
            self.__connection.close()
            self.__connection = None
            self.__log("Database connection closed.")
