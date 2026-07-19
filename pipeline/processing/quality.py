from pyspark.sql import DataFrame, SparkSession

from bronze_reader import bronze_file_path
from fs import path_exists


# ---------------------------
# Exceptions
# ---------------------------


class DataQualityError(Exception):
    """Raised when a silver data-quality assertion fails — fails the job loudly rather than
    letting a corrupt/incomplete run write silently-wrong silver output."""


# ---------------------------
# Public API
# ---------------------------


def assert_bronze_complete(
    spark: SparkSession, bronze_root: str, airline: str, origins: list[str], run_date: str
) -> None:
    """
    Fails loudly if any requested origin's bronze file is missing for run_date. Continuing anyway
    would silently corrupt both remaining outputs: the origin's route-days vanish from today's
    flights_latest_state partition (the dashboard reads that as "no flights from this origin"),
    and flight_price_history gains gaps now plus spurious is_new_flight rows when the origin
    returns — silently wrong rather than loudly missing.
    """
    missing = [o for o in origins if not path_exists(spark, bronze_file_path(bronze_root, airline, o, run_date))]
    if missing:
        raise DataQualityError(
            f"Bronze completeness check failed for run_date={run_date}: missing origin file(s) {missing}"
        )


def assert_flight_key_unique(df: DataFrame, context: str) -> None:
    """
    Fails loudly on a duplicate flight_key within a single snapshot. Silently deduping instead
    would hide a real scraper/schema bug (two rows claiming to be the same route-day fare — the
    feed's one-cheapest-per-route-day grain violated).
    """
    dupes = df.groupBy("flight_key").count().filter("count > 1")
    dupe_count = dupes.count()
    if dupe_count:
        sample = [row["flight_key"] for row in dupes.limit(5).collect()]
        raise DataQualityError(f"{context}: {dupe_count} duplicate flight_key value(s) found, e.g. {sample}")
