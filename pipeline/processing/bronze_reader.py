import os
from functools import reduce

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, IntegerType, StringType, StructField, StructType, TimestampType

from config import FLIGHT_CACHE_DIR, FLIGHT_CACHE_FILENAME
from fs import peek_first_non_space_char


# ---------------------------
# Constants
# ---------------------------

# Explicit bronze schema (not inferSchema) — matches asdict(Flight) field-for-field, so a
# malformed/missing field fails loudly (see FAILFAST below) instead of silently typing wrong.
BRONZE_SCHEMA = StructType(
    [
        StructField("origin_iata", StringType(), nullable=False),
        StructField("destination_iata", StringType(), nullable=False),
        StructField("destination_city", StringType(), nullable=True),
        StructField("destination_country", StringType(), nullable=True),
        StructField("airline", StringType(), nullable=True),
        StructField("flight_number", StringType(), nullable=True),
        StructField("departure_time", StringType(), nullable=False),
        StructField("arrival_time", StringType(), nullable=True),
        StructField("price_eur", DoubleType(), nullable=False),
        StructField("currency", StringType(), nullable=True),
        StructField("seats_left", IntegerType(), nullable=True),
        StructField("scraped_at", StringType(), nullable=True),
    ]
)

# BRONZE_SCHEMA with the three datetime columns parsed to TimestampType — what a DataFrame
# returned by read_bronze_file/load_bronze_snapshot actually looks like at runtime, plus flight_key.
_DATETIME_COLUMNS = ("departure_time", "arrival_time", "scraped_at")
PARSED_BRONZE_SCHEMA = StructType(
    [
        StructField(f.name, TimestampType() if f.name in _DATETIME_COLUMNS else f.dataType, f.nullable)
        for f in BRONZE_SCHEMA.fields
    ]
    + [StructField("flight_key", StringType(), nullable=False)]
)

# ---------------------------
# Helpers
# ---------------------------


def bronze_file_path(bronze_root: str, airline: str, origin: str, run_date: str) -> str:
    """Resolves one origin/day's bronze file path under `bronze_root`, reusing src/config's own
    layout constants so this never drifts from the scraper's."""
    day_dir = FLIGHT_CACHE_DIR.format(airline=airline, yyyymm=run_date[:6], dd=run_date[6:8])
    filename = FLIGHT_CACHE_FILENAME.format(origin=origin, yyyymmdd=run_date)
    return os.path.join(bronze_root, day_dir, filename)


def _is_json_array(spark: SparkSession, path: str) -> bool:
    """Peeks the first non-whitespace byte to tell a JSON-array file (local cache format) from
    NDJSON (real GCS bronze format)."""
    return peek_first_non_space_char(spark, path) == "["


def _parse_timestamp(column: str):
    """Parses ISO datetime strings (with or without the microseconds scraped_at carries) to TimestampType."""
    return F.coalesce(
        F.to_timestamp(F.col(column), "yyyy-MM-dd'T'HH:mm:ss.SSSSSS"),
        F.to_timestamp(F.col(column), "yyyy-MM-dd'T'HH:mm:ss"),
    )


def flight_key_column():
    """flight_key = hash(origin, destination, departure_date, airline) — identity of one route-day's
    cheapest fare, the only grain the source can see (get_cheapest_flights returns one fare per
    route-day). departure_time and flight_number are attributes of whichever physical flight is
    currently cheapest — they may change under a stable key, like price. A stable sha2, not
    Python's hash(), which is per-process-randomized and would break retry-idempotency."""
    return F.sha2(
        F.concat_ws(
            "|",
            F.col("origin_iata"),
            F.col("destination_iata"),
            F.date_format(F.col("departure_time"), "yyyy-MM-dd"),
            F.coalesce(F.col("airline"), F.lit("")),
        ),
        256,
    )


# ---------------------------
# Public API
# ---------------------------


def read_bronze_file(spark: SparkSession, path: str) -> DataFrame:
    """
    Reads a single bronze file against the explicit schema. FAILFAST surfaces a type mismatch or
    missing required field (schema drift) as a job failure instead of Spark's default PERMISSIVE
    mode silently nulling the bad field out.
    """
    multiline = "true" if _is_json_array(spark, path) else "false"
    df = spark.read.schema(BRONZE_SCHEMA).option("multiLine", multiline).option("mode", "FAILFAST").json(path)
    for column in _DATETIME_COLUMNS:
        df = df.withColumn(column, _parse_timestamp(column))
    return df


def load_bronze_snapshot(
    spark: SparkSession, bronze_root: str, airline: str, origins: list[str], run_date: str
) -> DataFrame:
    """
    Reads and unions one day's bronze files across `origins`, adding flight_key. Assumes every
    origin's file already exists — callers must run quality.assert_bronze_complete first, since a
    silently-skipped missing origin here would make that origin's real flights look "removed".
    """
    frames = [read_bronze_file(spark, bronze_file_path(bronze_root, airline, origin, run_date)) for origin in origins]
    unioned = reduce(DataFrame.unionByName, frames)
    return unioned.withColumn("flight_key", flight_key_column())
