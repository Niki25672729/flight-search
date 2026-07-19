import argparse
import logging
import os

from pyspark.sql import SparkSession

from config import CLOUD_CACHE_ROOT, GCS_BUCKET_NAME, SCRAPE_ORIGINS
from fs import path_exists
from silver import latest_state_path, price_history_path, process_day


# ---------------------------
# Helpers
# ---------------------------


# The plain `spark.jars.packages` coordinate resolves gcs-connector's *unshaded* jar, which
# doesn't bundle its own Guava/Protobuf and collides with the versions Spark's own Hadoop client
# already carries (NoSuchMethodError on Preconditions.checkArgument). The `-shaded` jar relocates
# its dependencies internally to avoid exactly that; it's not cleanly resolvable via Ivy
# coordinates, so it's referenced as a local jar (spark.jars) instead, downloaded once and cached.
_GCS_CONNECTOR_JAR = os.environ.get(
    "GCS_CONNECTOR_JAR_PATH", os.path.expanduser("~/.cache/spark-jars/gcs-connector-hadoop3-2.2.28-shaded.jar")
)


_DRIVER_MEMORY = os.environ.get("SPARK_DRIVER_MEMORY", "4g")

# "silver" is the real path; local dev overrides to the fixture (e.g. silver_manual_test) via env
# or --output-root. Mirrors terraform's silver_gcs_prefix variable (see terraform.tfvars.example).
_SILVER_GCS_PREFIX = os.environ.get("SILVER_GCS_PREFIX", "silver")


def build_spark_session(app_name: str = "flight-silver", needs_gcs: bool = False) -> SparkSession:
    """
    Local Spark only — no external cluster. UTC session timezone keeps timestamp read/write
    deterministic regardless of the host's local TZ/DST.
    Driver memory is bumped from Spark's 1g default — local[*] runs driver and executors in one
    JVM, and a real (non-empty) prior-state join plus a GCS multipart write OOM'd at the default
    once flights_latest_state held real data (first hit on a second real day's run, not day one's
    trivial empty-prior case). Overridable via SPARK_DRIVER_MEMORY for a bigger/smaller host.

    needs_gcs pulls in the native Hadoop GCS connector (not gcsfuse, not a second GCS client
    library) only when bronze/output actually point at gs://, so purely local runs stay fully
    offline. Auth is Application Default Credentials — matches this project's existing
    impersonation-not-a-downloaded-key decision rather than a new credential file.
    """
    builder = (
        SparkSession.builder.master("local[*]")
        .appName(app_name)
        .config("spark.sql.session.timeZone", "UTC")
        .config("spark.driver.memory", _DRIVER_MEMORY)
    )
    if needs_gcs:
        builder = (
            builder.config("spark.jars", _GCS_CONNECTOR_JAR)
            .config("spark.hadoop.fs.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem")
            .config("spark.hadoop.fs.AbstractFileSystem.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS")
            .config("spark.hadoop.google.cloud.auth.type", "APPLICATION_DEFAULT")
        )
    return builder.getOrCreate()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI args — roots default to the real GCS layers (bucket from FLIGHT_SEARCH_GCS_BUCKET,
    silver prefix from SILVER_GCS_PREFIX); both stay overridable so fixtures/local paths work."""
    parser = argparse.ArgumentParser(description="Bronze -> silver flight processing job.")
    parser.add_argument("--run-date", required=True, help="yyyymmdd")
    parser.add_argument(
        "--origins",
        default=",".join(SCRAPE_ORIGINS),
        help="comma-separated origin IATA codes; defaults to config.SCRAPE_ORIGINS",
    )
    parser.add_argument(
        "--bronze-root",
        default=f"gs://{GCS_BUCKET_NAME}/{CLOUD_CACHE_ROOT}",
        help="root dir containing flights/{airline}/... bronze; defaults to the real GCS bronze layer",
    )
    parser.add_argument(
        "--output-root",
        default=f"gs://{GCS_BUCKET_NAME}/{_SILVER_GCS_PREFIX}",
        help="root dir to write silver parquet outputs; defaults to the real GCS silver layer",
    )
    parser.add_argument("--airline", default="ryanair")
    parser.add_argument(
        "--refuse-if-exists",
        action="store_true",
        help="Abort before writing if any output path already has data — for one-off/manual runs "
        "where overwriting must never happen. Off by default: the job's normal partition-overwrite "
        "retry-safety design needs overwrite to work for scheduled runs.",
    )
    return parser.parse_args(argv)


# ---------------------------
# Scheduled Entry Point
# ---------------------------


def main(argv: list[str] | None = None) -> None:
    """Scheduled entry point (Airflow DAG wiring lands in a separate change, see pipeline/ingestion/run.py)."""
    args = parse_args(argv)
    if any(root.startswith("gs:///") for root in (args.bronze_root, args.output_root)):
        raise SystemExit(
            "FLIGHT_SEARCH_GCS_BUCKET is not set — set it, or pass --bronze-root/--output-root explicitly."
        )
    origins = [o.strip() for o in args.origins.split(",") if o.strip()]
    needs_gcs = args.bronze_root.startswith("gs://") or args.output_root.startswith("gs://")
    spark = build_spark_session(needs_gcs=needs_gcs)
    try:
        if args.refuse_if_exists:
            existing = [
                p
                for p in (latest_state_path(args.output_root), price_history_path(args.output_root))
                if path_exists(spark, p)
            ]
            if existing:
                raise RuntimeError(f"--refuse-if-exists: output already present, aborting without writing: {existing}")
        summary = process_day(spark, args.bronze_root, args.output_root, args.airline, origins, args.run_date)
        logging.info(f"Silver run complete for {args.run_date}: {summary}")
    finally:
        spark.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
