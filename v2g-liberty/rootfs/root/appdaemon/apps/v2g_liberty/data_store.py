"""Module for local SQLite data storage."""

import sqlite3
from datetime import datetime, timedelta

import pandas as pd
from appdaemon.plugins.hass.hassapi import Hass

from .log_wrapper import get_class_method_logger
from .v2g_globals import get_local_now

CURRENT_SCHEMA_VERSION = 1

PRICE_RATING_BINS = [0, 0.15, 0.35, 0.65, 0.85, 1.0]
PRICE_RATING_LABELS = ["very_low", "low", "average", "high", "very_high"]


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
                availability_pct REAL NOT NULL,
                consumption_price_kwh REAL,
                production_price_kwh REAL,
                price_rating TEXT
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

        self.__connection.commit()
        cursor.close()

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
        consumption_price_kwh: float | None = None,
        production_price_kwh: float | None = None,
        price_rating: str | None = None,
    ) -> None:
        """Insert a single interval row into interval_log."""
        cursor = self.__connection.cursor()
        cursor.execute(
            "INSERT INTO interval_log "
            "(timestamp, power_kw, energy_kwh, app_state, soc_pct, "
            "availability_pct, consumption_price_kwh, production_price_kwh, "
            "price_rating) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                timestamp,
                power_kw,
                energy_kwh,
                app_state,
                soc_pct,
                availability_pct,
                consumption_price_kwh,
                production_price_kwh,
                price_rating,
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

    def close(self):
        """Close the database connection."""
        if self.__connection:
            self.__connection.close()
            self.__connection = None
            self.__log("Database connection closed.")
