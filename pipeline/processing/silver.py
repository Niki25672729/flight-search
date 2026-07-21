import os
from datetime import datetime, timedelta

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

from bronze_reader import PARSED_BRONZE_SCHEMA, bronze_file_path, load_bronze_snapshot
from config import DATE_FORMAT, SILVER_LATEST_STATE_TABLE, SILVER_PRICE_HISTORY_TABLE
from fs import path_exists
from quality import DataQualityError, assert_bronze_complete, assert_flight_key_unique


# ---------------------------
# Constants
# ---------------------------

_PARTITION_OVERWRITE_MODE = "dynamic"  # spark.sql.sources.partitionOverwriteMode — see ARCHITECTURE_DASHBOARD.md
_BOOTSTRAP_LOOKBACK_DAYS = 5  # run_date-2..run_date-6, checked for an origin missing run_date-1 bronze


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


def _prior_run_date(run_date: str) -> str:
    return (_to_date(run_date) - timedelta(days=1)).strftime(DATE_FORMAT)


def _lookback_dates(run_date: str) -> list[str]:
    """run_date-2 through run_date-(1+_BOOTSTRAP_LOOKBACK_DAYS)."""
    base = _to_date(run_date)
    return [(base - timedelta(days=offset)).strftime(DATE_FORMAT) for offset in range(2, 2 + _BOOTSTRAP_LOOKBACK_DAYS)]


def run_date_already_processed(spark: SparkSession, output_root: str, run_date: str) -> bool:
    """True if run_date's flights_latest_state partition exists."""
    partition_path = os.path.join(latest_state_path(output_root), f"scrape_date={_to_date(run_date).isoformat()}")
    return path_exists(spark, partition_path)


def read_prior_state_from_bronze(
    spark: SparkSession, bronze_root: str, airline: str, origins: list[str], run_date: str
) -> DataFrame:
    """Reads run_date - 1's bronze as "prior" for the diff — see ARCHITECTURE_DASHBOARD.md's
    Idempotency & Write Ordering for the per-origin lookback/gap logic below."""
    prior_date = _prior_run_date(run_date)
    missing = [
        origin
        for origin in origins
        if not path_exists(spark, bronze_file_path(bronze_root, airline, origin, prior_date))
    ]
    if not missing:
        prior = load_bronze_snapshot(spark, bronze_root, airline, origins, prior_date)
        assert_flight_key_unique(prior, context=f"prior bronze snapshot (prior_date={prior_date})")
        return prior

    lookback = _lookback_dates(run_date)
    gapped = [
        origin
        for origin in missing
        if any(path_exists(spark, bronze_file_path(bronze_root, airline, origin, d)) for d in lookback)
    ]
    if gapped:
        raise DataQualityError(
            f"Bronze gap for prior_date={prior_date}: origin(s) {gapped} have history in the past "
            f"{1 + _BOOTSTRAP_LOOKBACK_DAYS} days but not at prior_date — not a new origin, refusing to skip it"
        )

    present_origins = [o for o in origins if o not in missing]
    if not present_origins:
        return spark.createDataFrame([], PARSED_BRONZE_SCHEMA)
    prior = load_bronze_snapshot(spark, bronze_root, airline, present_origins, prior_date)
    assert_flight_key_unique(prior, context=f"prior bronze snapshot (prior_date={prior_date})")
    return prior


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
    df.coalesce(1).write.mode("overwrite").partitionBy("scrape_date").parquet(path)


# ---------------------------
# Orchestration
# ---------------------------


def process_day(
    spark: SparkSession, bronze_root: str, output_root: str, airline: str, origins: list[str], run_date: str
) -> dict:
    """Runs one day's bronze -> silver job: prior day's bronze -> price-history diff -> today's
    partition. See ARCHITECTURE_DASHBOARD.md's Idempotency & Write Ordering and Refresh &
    Backfill."""
    assert_bronze_complete(spark, bronze_root, airline, origins, run_date)
    configure_partition_overwrite(spark)

    today = load_bronze_snapshot(spark, bronze_root, airline, origins, run_date).cache()
    assert_flight_key_unique(today, context=f"today's bronze snapshot (run_date={run_date})")

    prior = read_prior_state_from_bronze(spark, bronze_root, airline, origins, run_date).cache()

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
