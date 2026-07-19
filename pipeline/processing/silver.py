import os
from datetime import datetime

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from bronze_reader import PARSED_BRONZE_SCHEMA, load_bronze_snapshot
from config import DATE_FORMAT, SILVER_LATEST_STATE_TABLE, SILVER_PRICE_HISTORY_TABLE
from fs import list_partition_values
from quality import assert_bronze_complete, assert_flight_key_unique


# ---------------------------
# Constants
# ---------------------------

_PARTITION_OVERWRITE_MODE = "dynamic"  # spark.sql.sources.partitionOverwriteMode — see ARCHITECTURE_DASHBOARD.md


# ---------------------------
# Paths
# ---------------------------


def latest_state_path(output_root: str) -> str:
    return os.path.join(output_root, SILVER_LATEST_STATE_TABLE)


def price_history_path(output_root: str) -> str:
    return os.path.join(output_root, SILVER_PRICE_HISTORY_TABLE)


# ---------------------------
# Helpers
# ---------------------------


def configure_partition_overwrite(spark: SparkSession) -> None:
    """Sets dynamic partition-overwrite mode so a partitioned write only replaces the scrape_date
    partition it targets, not the whole table — required for both outputs to be retry-safe."""
    spark.conf.set("spark.sql.sources.partitionOverwriteMode", _PARTITION_OVERWRITE_MODE)


def _to_date(run_date: str):
    return datetime.strptime(run_date, DATE_FORMAT).date()


def read_prior_latest_state(spark: SparkSession, output_root: str, run_date: str) -> DataFrame:
    """
    Reads the most recent flights_latest_state partition strictly before run_date, or an empty
    (correctly-typed) frame if none exists (first run, or run_date is the earliest processed).

    flights_latest_state is partitioned by scrape_date rather than a single overwritten table,
    so re-running an already-completed run_date is safe: this always excludes run_date's own
    partition from "prior", so a rerun never diffs today against itself — unlike a flat
    "whatever's currently there" read would.
    """
    path = latest_state_path(output_root)
    run_date_iso = _to_date(run_date).isoformat()
    prior_dates = [d for d in list_partition_values(spark, path, "scrape_date") if d < run_date_iso]
    if not prior_dates:
        return spark.createDataFrame([], PARSED_BRONZE_SCHEMA)
    return spark.read.parquet(path).filter(F.col("scrape_date") == max(prior_dates)).drop("scrape_date")


# ---------------------------
# Diff Output
# ---------------------------


def compute_price_history(today: DataFrame, prior: DataFrame, run_date: str) -> DataFrame:
    """1 row / flight_key, only when new (vs. prior state) or price-changed."""
    run_date_dt = _to_date(run_date)
    joined = today.alias("t").join(prior.alias("p"), on="flight_key", how="left")
    is_new = F.col("p.flight_key").isNull()
    changed = ~is_new & (F.col("t.price_eur") != F.col("p.price_eur"))
    return joined.filter(is_new | changed).select(
        F.lit(run_date_dt).alias("scrape_date"),
        F.col("t.flight_key").alias("flight_key"),
        F.col("t.origin_iata").alias("origin_iata"),
        F.col("t.destination_iata").alias("destination_iata"),
        F.col("t.airline").alias("airline"),
        F.col("t.flight_number").alias("flight_number"),
        F.col("t.departure_time").alias("departure_time"),
        F.col("t.price_eur").alias("price_eur"),
        F.col("p.price_eur").alias("prior_price_eur"),
        is_new.alias("is_new_flight"),
    )


# ---------------------------
# Writers
# ---------------------------


def write_partitioned(df: DataFrame, path: str) -> None:
    """Partition-overwrite by scrape_date — replaces only that partition, so a retry re-running
    the same run_date replaces that day's rows instead of accumulating duplicates via append."""
    df.write.mode("overwrite").partitionBy("scrape_date").parquet(path)


# ---------------------------
# Orchestration
# ---------------------------


def process_day(
    spark: SparkSession, bronze_root: str, output_root: str, airline: str, origins: list[str], run_date: str
) -> dict:
    """Runs one day's bronze -> silver job: prior partition -> price-history diff -> today's
    partition. Rerun-safe via read_prior_latest_state's strictly-before rule; the write order is
    only a reader-consistency nicety (ARCHITECTURE_DASHBOARD.md's "Idempotency & Write Ordering")."""
    assert_bronze_complete(spark, bronze_root, airline, origins, run_date)
    configure_partition_overwrite(spark)

    today = load_bronze_snapshot(spark, bronze_root, airline, origins, run_date).cache()
    assert_flight_key_unique(today, context=f"today's bronze snapshot (run_date={run_date})")

    prior = read_prior_latest_state(spark, output_root, run_date).cache()

    price_history = compute_price_history(today, prior, run_date)

    # Diff first —  a concurrent reader never sees a snapshot partition without its price-history partition.
    write_partitioned(price_history, price_history_path(output_root))

    today_count = today.select("flight_key").distinct().count()
    prior_count = prior.select("flight_key").distinct().count()
    new_count = price_history.filter("is_new_flight").count()

    # Today's partition last (see the write-order note above).
    tagged = today.withColumn("scrape_date", F.lit(_to_date(run_date)))
    write_partitioned(tagged, latest_state_path(output_root))

    return {"run_date": run_date, "today_count": today_count, "prior_count": prior_count, "new_count": new_count}
