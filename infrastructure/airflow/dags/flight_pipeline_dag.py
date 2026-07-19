import os
from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.docker.operators.docker import DockerOperator
from docker.types import Mount

from callbacks import log_dagrun_failure_alert
from config import SCRAPE_ORIGINS


# ---------------------------
# Constants
# ---------------------------

INGESTION_IMAGE = "flight-search-ingestion:dev"
PROCESSING_IMAGE = "flight-search-processing:dev"
GCS_CREDENTIALS_TARGET = "/app/gcloud/application_default_credentials.json"
TASK_EXECUTION_TIMEOUT = timedelta(minutes=30)
CHECK_GCS_EXECUTION_TIMEOUT = timedelta(minutes=2)
REPORT_EXECUTION_TIMEOUT = timedelta(minutes=5)
COMMON_ENVIRONMENT = {
    "FLIGHT_SEARCH_GCS_BUCKET": os.environ["FLIGHT_SEARCH_GCS_BUCKET"],
    "GOOGLE_CLOUD_PROJECT": os.environ["GOOGLE_CLOUD_PROJECT"],
    "GOOGLE_APPLICATION_CREDENTIALS": GCS_CREDENTIALS_TARGET,
}
COMMON_MOUNTS = [
    Mount(source=os.environ["HOST_GCLOUD_ADC_PATH"], target=GCS_CREDENTIALS_TARGET, type="bind", read_only=True)
]


# ---------------------------
# DAG
# ---------------------------

with DAG(
    dag_id="flight_pipeline",
    description="Ingest Ryanair flights to GCS bronze (one mapped task per origin, via run.ingest_airport), then process/transform (processing and transform tasks land once pipeline/processing and pipeline/transform exist).",
    schedule="@daily",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    default_args={
        "retries": 3,
        "retry_delay": timedelta(minutes=10),
        "retry_exponential_backoff": True,
        "max_retry_delay": timedelta(minutes=30),
    },
    tags=["flight-search", "v2-pipeline"],
    on_failure_callback=log_dagrun_failure_alert,
) as dag:
    # Ingestion would fallback to local storage if GCS is not accessible and loses data, so we need to check if it is.
    check_gcs_accessible = DockerOperator(
        task_id="check_gcs_accessible",
        image=INGESTION_IMAGE,
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mount_tmp_dir=False,
        auto_remove="force",
        execution_timeout=CHECK_GCS_EXECUTION_TIMEOUT,
        retries=5,
        retry_delay=timedelta(minutes=1),
        command=["check-gcs"],
        environment=COMMON_ENVIRONMENT,
        mounts=COMMON_MOUNTS,
    )

    # run_date is `{{ data_interval_end | ds_nodash }}`, not data_interval_start: for a @daily
    # schedule, a run's logical date is the interval's start (the day *before* it executes), so
    # data_interval_end gives the day the run actually executes on instead.
    ingest_flights = DockerOperator.partial(
        task_id="ingest_flights",
        image=INGESTION_IMAGE,
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mount_tmp_dir=False,
        auto_remove="force",
        execution_timeout=TASK_EXECUTION_TIMEOUT,
        # Caps concurrent Ryanair API hits
        max_active_tis_per_dagrun=5,
        environment=COMMON_ENVIRONMENT,
        mounts=COMMON_MOUNTS,
    ).expand(command=[[origin, "{{ data_interval_end | ds_nodash }}"] for origin in SCRAPE_ORIGINS])

    retry_failed_ingests = DockerOperator.partial(
        task_id="retry_failed_ingests",
        image=INGESTION_IMAGE,
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mount_tmp_dir=False,
        auto_remove="force",
        execution_timeout=TASK_EXECUTION_TIMEOUT,
        # Same rationale as ingest_flights — caps concurrent Ryanair API hits during retries.
        max_active_tis_per_dagrun=5,
        trigger_rule="all_done",
        environment=COMMON_ENVIRONMENT,
        mounts=COMMON_MOUNTS,
    ).expand(command=[["retry", origin, "{{ data_interval_end | ds_nodash }}"] for origin in SCRAPE_ORIGINS])

    # Purely informational -- reads already-written cache/retry-queue
    # state for every origin and logs a per-origin + aggregate summary; never scrapes or writes, and
    # always exits 0 regardless of origin outcomes, so it can never itself fail the DagRun (that would
    # reopen exactly the all_done isolation retry_failed_ingests was set up to give each origin).
    generate_run_report = DockerOperator(
        task_id="generate_run_report",
        image=INGESTION_IMAGE,
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mount_tmp_dir=False,
        auto_remove="force",
        execution_timeout=REPORT_EXECUTION_TIMEOUT,
        trigger_rule="all_done",
        command=["report", "{{ data_interval_end | ds_nodash }}"],
        environment=COMMON_ENVIRONMENT,
        mounts=COMMON_MOUNTS,
    )

    # Bronze -> silver. Runs strictly after retry_failed_ingests so the day's bronze is as complete as it will get; run_silver's own
    # assert_bronze_complete still fails this task loudly if any origin is missing. Bronze/output
    # roots default to the real GCS layers inside the image (FLIGHT_SEARCH_GCS_BUCKET +
    # SILVER_GCS_PREFIX), so the command only carries the run date.
    process_bronze_to_silver = DockerOperator(
        task_id="process_bronze_to_silver",
        image=PROCESSING_IMAGE,
        docker_url="unix://var/run/docker.sock",
        network_mode="bridge",
        mount_tmp_dir=False,
        auto_remove="force",
        execution_timeout=TASK_EXECUTION_TIMEOUT,
        # Deliberately all_success (unlike its all_done siblings): a failed ingest chain must not
        # build silver from incomplete bronze — assert_bronze_complete is the second line of
        # defense, not the first.
        trigger_rule="all_success",
        command=["--run-date", "{{ data_interval_end | ds_nodash }}"],
        environment={**COMMON_ENVIRONMENT, "SILVER_GCS_PREFIX": os.environ.get("SILVER_GCS_PREFIX", "silver")},
        mounts=COMMON_MOUNTS,
    )

    check_gcs_accessible >> ingest_flights >> retry_failed_ingests >> [generate_run_report, process_bronze_to_silver]

    # transform (silver -> gold) joins after process_bronze_to_silver once its DAG wiring lands —
    # it must depend on this task, not just share the schedule (see ARCHITECTURE_DASHBOARD.md's
    # open question on non-atomic partition overwrites).
