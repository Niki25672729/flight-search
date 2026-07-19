import importlib
import json
import logging
from dataclasses import asdict, dataclass
from datetime import timedelta

import pytest

from callbacks import log_dagrun_failure_alert
from config import DATE_FORMAT, SCRAPE_BUFFER_DAYS, SCRAPE_ORIGINS
from conftest import FROZEN_NOW, make_dummy_flight
from report import (
    REPORT_LOG_PREFIX,
    build_report_payload,
    build_run_summary,
    format_run_report,
    generate_run_report,
    summarize_origin,
)


RUN_DATE = FROZEN_NOW.strftime(DATE_FORMAT)
PRIOR_DATE = (FROZEN_NOW - timedelta(days=1)).strftime(DATE_FORMAT)


# ---------------------------
# Fixtures
# ---------------------------


@dataclass
class _FakeTaskInstance:
    """Stand-in for Airflow's TaskInstance -- only the attributes log_dagrun_failure_alert reads."""

    task_id: str
    map_index: int = -1  # -1 is Airflow's own sentinel for "not a mapped task instance"


class _FakeDagRun:
    """Stand-in for Airflow's DagRun -- avoids needing the airflow package installed for this test."""

    def __init__(self, dag_id: str, run_id: str, failed_task_instances: list[_FakeTaskInstance]):
        self.dag_id = dag_id
        self.run_id = run_id
        self._failed_task_instances = failed_task_instances

    def get_task_instances(self, state: str) -> list[_FakeTaskInstance]:
        assert state == "failed"
        return self._failed_task_instances


@pytest.fixture
def dag(monkeypatch):
    """
    Imports flight_pipeline_dag fresh, with the env vars it reads at module import time
    (FLIGHT_SEARCH_GCS_BUCKET, GOOGLE_CLOUD_PROJECT, HOST_GCLOUD_ADC_PATH) stubbed -- the real
    values only exist inside the Airflow container. Reloads on every test rather than relying on
    sys.modules's cache, so each test builds the DAG independently.

    Purely structural: never calls DockerOperator.execute(), so nothing here touches Docker.
    """
    monkeypatch.setenv("FLIGHT_SEARCH_GCS_BUCKET", "test-bucket")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("HOST_GCLOUD_ADC_PATH", "/tmp/fake_adc.json")

    import flight_pipeline_dag

    importlib.reload(flight_pipeline_dag)
    return flight_pipeline_dag.dag


@pytest.fixture
def mock_write_run_report(mocker):
    """Mocks cache.write_run_report as seen from report.py."""
    return mocker.patch("report.write_run_report", return_value=True)


# ---------------------------
# DAG Structure
# ---------------------------


def test_dag_loads_with_expected_schedule_and_tags(dag):
    """Tests the DAG imports cleanly (no import-time errors) with its intended schedule/tags."""
    assert dag.dag_id == "flight_pipeline"
    assert dag.timetable.summary == "0 0 * * *"  # @daily
    assert dag.catchup is False
    assert dag.tags == ["flight-search", "v2-pipeline"]


def test_default_args_retry_policy(dag):
    """Tests the DAG-wide retry policy every task inherits unless it overrides it (e.g.
    check_gcs_accessible's own retries=5)."""
    assert dag.default_args["retries"] == 3
    assert dag.default_args["retry_delay"].total_seconds() == 600
    assert dag.default_args["retry_exponential_backoff"] is True
    assert dag.default_args["max_retry_delay"].total_seconds() == 1800


def test_task_dependency_chain(dag):
    """Tests the full task chain: check_gcs_accessible -> ingest_flights -> retry_failed_ingests
    -> {generate_run_report, process_bronze_to_silver} -- reporting and processing both wait for
    the retry pass (the day's bronze is as complete as it will get), then run independently."""
    assert dag.task_dict["check_gcs_accessible"].downstream_task_ids == {"ingest_flights"}
    assert dag.task_dict["ingest_flights"].downstream_task_ids == {"retry_failed_ingests"}
    assert dag.task_dict["retry_failed_ingests"].downstream_task_ids == {
        "generate_run_report",
        "process_bronze_to_silver",
    }
    assert dag.task_dict["generate_run_report"].downstream_task_ids == set()
    assert dag.task_dict["process_bronze_to_silver"].downstream_task_ids == set()


def test_process_bronze_to_silver_command_and_gating(dag):
    """Tests the silver task carries only the run date (roots default to the real GCS layers
    inside the image) and uses all_success — a failed ingest chain must NOT silently produce a
    silver partition from incomplete bronze (assert_bronze_complete is the second line of
    defense, not the first)."""
    task = dag.task_dict["process_bronze_to_silver"]
    assert task.command == ["--run-date", "{{ data_interval_end | ds_nodash }}"]
    assert task.trigger_rule == "all_success"
    assert task.environment["SILVER_GCS_PREFIX"] == "silver"


def test_on_failure_callback_is_wired(dag):
    """Tests the DAG's on_failure_callback is callbacks.log_dagrun_failure_alert -- the only
    signal outside the Airflow UI for a failed DagRun (see MONITORING.md Alerting)."""
    assert dag.on_failure_callback is log_dagrun_failure_alert


def test_check_gcs_accessible_overrides_default_retry_policy(dag):
    """Tests check_gcs_accessible's own retries=5/1-minute delay -- a tighter policy than the DAG
    default, since a GCS outage here otherwise silently falls back to local storage and loses data."""
    task = dag.task_dict["check_gcs_accessible"]
    assert task.retries == 5
    assert task.retry_delay.total_seconds() == 60
    assert task.execution_timeout.total_seconds() == 120
    assert task.command == ["check-gcs"]


def test_retry_failed_ingests_and_generate_run_report_use_trigger_rule_all_done(dag):
    """Tests the two tasks that must still run after a partial upstream failure use
    trigger_rule=all_done -- the isolation MONITORING.md documents so one origin's total ingest
    failure doesn't block retries/reporting for every other origin."""
    assert dag.task_dict["retry_failed_ingests"].trigger_rule == "all_done"
    assert dag.task_dict["generate_run_report"].trigger_rule == "all_done"
    # Contrast: tasks upstream of the failure point still use Airflow's default.
    assert dag.task_dict["check_gcs_accessible"].trigger_rule == "all_success"
    assert dag.task_dict["ingest_flights"].trigger_rule == "all_success"


def test_ingest_flights_expands_one_task_per_origin(dag):
    """Tests ingest_flights maps one task per SCRAPE_ORIGINS entry, command=[origin, run_date]."""
    mapped_commands = dag.task_dict["ingest_flights"].expand_input.value["command"]
    assert [command[0] for command in mapped_commands] == SCRAPE_ORIGINS
    assert all(command[1] == "{{ data_interval_end | ds_nodash }}" for command in mapped_commands)


def test_retry_failed_ingests_expands_one_task_per_origin_with_retry_command(dag):
    """Tests retry_failed_ingests maps one task per SCRAPE_ORIGINS entry, command=["retry", origin, run_date]."""
    mapped_commands = dag.task_dict["retry_failed_ingests"].expand_input.value["command"]
    assert [command[0] for command in mapped_commands] == ["retry"] * len(SCRAPE_ORIGINS)
    assert [command[1] for command in mapped_commands] == SCRAPE_ORIGINS


def test_generate_run_report_command_and_timeout(dag):
    """Tests generate_run_report's command/timeout -- purely informational, never fails the DagRun
    itself (see MONITORING.md Monitoring)."""
    task = dag.task_dict["generate_run_report"]
    assert task.command == ["report", "{{ data_interval_end | ds_nodash }}"]
    assert task.execution_timeout.total_seconds() == 300


# ---------------------------
# DAG Callbacks
# ---------------------------


def test_log_dagrun_failure_alert_names_unmapped_failed_task(caplog):
    """Tests a non-mapped task failure (e.g. check_gcs_accessible) is named without a map index."""
    dag_run = _FakeDagRun("flight_pipeline", "scheduled__2026-07-12", [_FakeTaskInstance("check_gcs_accessible")])

    with caplog.at_level(logging.ERROR):
        log_dagrun_failure_alert({"dag_run": dag_run})

    assert "PIPELINE_ALERT" in caplog.text
    assert "dag_id=flight_pipeline" in caplog.text
    assert "run_id=scheduled__2026-07-12" in caplog.text
    assert "check_gcs_accessible" in caplog.text
    assert "check_gcs_accessible[" not in caplog.text


def test_log_dagrun_failure_alert_names_mapped_failed_task_with_index(caplog):
    """Tests a mapped task failure (e.g. one origin's ingest_flights) is named with its map index,
    so the alert points at which origin failed without opening any task log."""
    dag_run = _FakeDagRun(
        "flight_pipeline", "scheduled__2026-07-12", [_FakeTaskInstance("ingest_flights", map_index=3)]
    )

    with caplog.at_level(logging.ERROR):
        log_dagrun_failure_alert({"dag_run": dag_run})

    assert "ingest_flights[3]" in caplog.text


def test_log_dagrun_failure_alert_lists_every_failed_task(caplog):
    """Tests that multiple simultaneous failures are all named in one alert line."""
    dag_run = _FakeDagRun(
        "flight_pipeline",
        "scheduled__2026-07-12",
        [_FakeTaskInstance("ingest_flights", map_index=1), _FakeTaskInstance("ingest_flights", map_index=4)],
    )

    with caplog.at_level(logging.ERROR):
        log_dagrun_failure_alert({"dag_run": dag_run})

    assert "ingest_flights[1]" in caplog.text
    assert "ingest_flights[4]" in caplog.text


def test_log_dagrun_failure_alert_handles_no_failed_tasks(caplog):
    """Tests the edge case of a DagRun failure with no individual failed task instances found
    (e.g. a DAG-level timeout) -- still logs a well-formed alert instead of raising."""
    dag_run = _FakeDagRun("flight_pipeline", "scheduled__2026-07-12", [])

    with caplog.at_level(logging.ERROR):
        log_dagrun_failure_alert({"dag_run": dag_run})

    assert "PIPELINE_ALERT" in caplog.text
    assert "failed_tasks=[]" in caplog.text


# ---------------------------
# Report
# ---------------------------


# --- summarize_origin ---


def test_summarize_origin_success_when_cache_present_and_no_failed_queries(mock_read_cache, mock_load_retry_queue):
    """Tests the healthy case: today's cache exists and nothing is left in the retry queue."""
    today_flights = [make_dummy_flight() for _ in range(5)]
    mock_read_cache.side_effect = lambda origin, airline, date: today_flights if date == RUN_DATE else None

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.status == "success"
    assert summary.flights_count == 5
    assert summary.queries_succeeded == SCRAPE_BUFFER_DAYS
    assert summary.queries_total == SCRAPE_BUFFER_DAYS


def test_summarize_origin_partial_when_cache_present_but_queries_still_failing(mock_read_cache, mock_load_retry_queue):
    """Tests that a non-empty retry queue (after retry_failed_ingests already ran) marks the
    origin partial, even though some data landed."""
    mock_read_cache.side_effect = lambda origin, airline, date: [make_dummy_flight()] if date == RUN_DATE else None
    mock_load_retry_queue.return_value = [
        {"origin_iata": "EIN", "query_date": "2026-07-01"},
        {"origin_iata": "EIN", "query_date": "2026-07-02"},
    ]

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.status == "partial"
    assert summary.queries_succeeded == SCRAPE_BUFFER_DAYS - 2


def test_summarize_origin_failed_when_no_cache_data_at_all(mock_read_cache, mock_load_retry_queue):
    """
    Tests the silent-zero-data scenario MONITORING.md flags: ingest_airport/retry_failed_ingests
    both failed for this origin, so no cache blob exists at all for run_date. Relies on
    mock_read_cache's default (None, i.e. cache miss) from conftest.py.
    """
    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.status == "failed"
    assert summary.flights_count == 0


def test_summarize_origin_reports_zero_queries_succeeded_when_failed_even_with_empty_retry_queue(
    mock_read_cache, mock_load_retry_queue
):
    """
    Regression test (solution-architect review): a scrape that crashes before ever reaching the
    retry-queue bookkeeping leaves the queue empty even though nothing succeeded -- flights is
    None (status "failed") but the naive `SCRAPE_BUFFER_DAYS - len(remaining_queue)` arithmetic
    would otherwise claim every query succeeded. queries_succeeded must be 0, not
    SCRAPE_BUFFER_DAYS, whenever status is "failed".
    """
    mock_read_cache.return_value = None
    mock_load_retry_queue.return_value = []  # empty queue despite the total failure

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.status == "failed"
    assert summary.queries_succeeded == 0
    assert summary.queries_total == SCRAPE_BUFFER_DAYS


def test_summarize_origin_flags_peak_on_large_increase(mock_read_cache, mock_load_retry_queue):
    """Tests that a >20% day-over-day increase is flagged PEAK."""
    mock_read_cache.side_effect = lambda origin, airline, date: (
        [make_dummy_flight() for _ in range(200)]
        if date == RUN_DATE
        else [make_dummy_flight() for _ in range(100)]
        if date == PRIOR_DATE
        else None
    )

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.prior_flights_count == 100
    assert summary.pct_change == pytest.approx(100.0)
    assert summary.flag == "PEAK"


def test_summarize_origin_flags_drop_on_large_decrease(mock_read_cache, mock_load_retry_queue):
    """Tests that a >20% day-over-day decrease is flagged DROP."""
    mock_read_cache.side_effect = lambda origin, airline, date: (
        [make_dummy_flight() for _ in range(50)]
        if date == RUN_DATE
        else [make_dummy_flight() for _ in range(100)]
        if date == PRIOR_DATE
        else None
    )

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.pct_change == pytest.approx(-50.0)
    assert summary.flag == "DROP"


def test_summarize_origin_rounds_pct_change_to_two_decimals(mock_read_cache, mock_load_retry_queue):
    """Tests that pct_change is rounded to 2 decimals -- status.json should carry a readable
    number, not a long float repeating fraction."""
    mock_read_cache.side_effect = lambda origin, airline, date: (
        [make_dummy_flight() for _ in range(10)]
        if date == RUN_DATE
        else [make_dummy_flight() for _ in range(7)]
        if date == PRIOR_DATE
        else None
    )

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.pct_change == pytest.approx(42.86)


def test_summarize_origin_no_flag_within_threshold(mock_read_cache, mock_load_retry_queue):
    """Tests that a small day-over-day change (<=20%) isn't flagged."""
    mock_read_cache.side_effect = lambda origin, airline, date: (
        [make_dummy_flight() for _ in range(105)]
        if date == RUN_DATE
        else [make_dummy_flight() for _ in range(100)]
        if date == PRIOR_DATE
        else None
    )

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.flag is None


def test_summarize_origin_no_comparison_when_no_prior_day_data(mock_read_cache, mock_load_retry_queue):
    """Tests that a missing prior-day cache (e.g. a newly added origin) doesn't crash and just
    omits the comparison, rather than being misreported as a 100% drop."""
    mock_read_cache.side_effect = lambda origin, airline, date: [make_dummy_flight()] if date == RUN_DATE else None

    summary = summarize_origin("EIN", RUN_DATE)

    assert summary.prior_flights_count is None
    assert summary.pct_change is None
    assert summary.flag is None


# --- build_run_summary / format_run_report ---


def test_build_run_summary_sorts_failed_before_partial_before_success(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue
):
    """Tests worst-status-first ordering, so problems surface without scrolling past healthy origins."""

    def _read_cache(origin, airline, date):
        if date != RUN_DATE:
            return None
        if origin == "EIN":
            return None  # failed
        return [make_dummy_flight()]  # STN: success

    mock_read_cache.side_effect = _read_cache

    summaries = build_run_summary(RUN_DATE)

    assert [s.origin for s in summaries] == ["EIN", "STN"]
    assert [s.status for s in summaries] == ["failed", "success"]


def test_build_report_payload_aggregate_counts_flagged_origins(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue
):
    """Tests that the aggregate's queries_flagged counts origins with a PEAK/DROP flag, so a
    glance at status.json catches a volume swing without scanning every origin line."""

    def _read_cache(origin, airline, date):
        if origin == "EIN":
            return [make_dummy_flight() for _ in range(200)] if date == RUN_DATE else [make_dummy_flight()]  # PEAK
        return [make_dummy_flight()] if date == RUN_DATE else None  # STN: no prior data, not flagged

    mock_read_cache.side_effect = _read_cache

    summaries = build_run_summary(RUN_DATE)
    payload = build_report_payload(summaries, RUN_DATE)

    assert payload["aggregate"]["queries_flagged"] == 1


def test_format_run_report_includes_aggregate_and_per_origin_lines(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue
):
    """Tests that the formatted report has one aggregate REPORT_LOG_PREFIX line plus one per
    origin, with the aggregate reflecting totals across all origins."""

    def _read_cache(origin, airline, date):
        if date != RUN_DATE:
            return None
        return [make_dummy_flight() for _ in range(3)] if origin == "EIN" else [make_dummy_flight() for _ in range(7)]

    mock_read_cache.side_effect = _read_cache

    summaries = build_run_summary(RUN_DATE)
    report = format_run_report(summaries, RUN_DATE)
    lines = report.splitlines()

    assert lines[0].startswith(f"{REPORT_LOG_PREFIX} run_date={RUN_DATE}")
    assert "flights=10" in lines[0]
    assert len(lines) == 3  # 1 aggregate + 2 origins
    assert all(line.startswith(REPORT_LOG_PREFIX) for line in lines)


def test_format_run_report_aggregate_does_not_inflate_queries_succeeded_for_failed_origin(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue
):
    """
    Regression test (solution-architect review): a failed origin contributes 0 to the aggregate
    queries-succeeded total, not SCRAPE_BUFFER_DAYS -- otherwise a fully-failed origin would
    silently inflate the run-wide "queries succeeded" number in the one line meant to summarize
    the whole run.
    """

    def _read_cache(origin, airline, date):
        if date != RUN_DATE:
            return None
        return None if origin == "EIN" else [make_dummy_flight()]  # EIN: failed, STN: success

    mock_read_cache.side_effect = _read_cache

    summaries = build_run_summary(RUN_DATE)
    report = format_run_report(summaries, RUN_DATE)
    aggregate_line = report.splitlines()[0]

    assert f"queries={SCRAPE_BUFFER_DAYS}/{2 * SCRAPE_BUFFER_DAYS} succeeded" in aggregate_line


# --- build_report_payload ---


def test_build_report_payload_is_plain_json_serializable_and_matches_aggregate(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue
):
    """
    Tests that build_report_payload's shape is {run_date, aggregate, origins}, that "origins" is
    just dataclasses.asdict of each OriginSummary (str/int/float/None fields only, no custom
    serializer needed), and that its aggregate numbers match format_run_report's aggregate line
    (both are computed from the same _aggregate_stats helper, so they can't drift apart).
    """

    def _read_cache(origin, airline, date):
        if date != RUN_DATE:
            return None
        return None if origin == "EIN" else [make_dummy_flight()]  # EIN: failed, STN: success

    mock_read_cache.side_effect = _read_cache

    summaries = build_run_summary(RUN_DATE)
    payload = build_report_payload(summaries, RUN_DATE)

    assert payload["run_date"] == RUN_DATE
    assert payload["origins"] == [asdict(s) for s in summaries]
    assert payload["aggregate"] == {
        "origins": 2,
        "success": 1,
        "partial": 0,
        "failed": 1,
        "flights": 1,
        "queries_succeeded": SCRAPE_BUFFER_DAYS,
        "queries_flagged": 0,
        "queries_total": 2 * SCRAPE_BUFFER_DAYS,
    }
    # every value must be a plain JSON-serializable type -- no datetime, no custom objects
    json.dumps(payload)  # raises TypeError if anything isn't plain-JSON-serializable


# --- generate_run_report ---


def test_generate_run_report_never_raises_even_when_every_origin_has_zero_data(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue, mock_write_run_report, caplog
):
    """
    Tests that generate_run_report logs the report and completes normally even when every origin
    has zero data -- it must never fail the task regardless of origin outcomes (see
    flight_pipeline_dag.py's generate_run_report task, which relies on this always exiting 0).
    Relies on mock_read_cache's default (None) from conftest.py.
    """
    with caplog.at_level(logging.WARNING):
        generate_run_report(RUN_DATE)

    assert REPORT_LOG_PREFIX in caplog.text
    assert "failed=2" in caplog.text  # both mocked origins have no cache data by default


def test_generate_run_report_logs_at_info_when_every_origin_succeeds(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue, mock_write_run_report, caplog
):
    """Tests that an all-healthy run logs at INFO, not WARNING."""
    mock_read_cache.side_effect = lambda origin, airline, date: [make_dummy_flight()] if date == RUN_DATE else None

    with caplog.at_level(logging.INFO):
        generate_run_report(RUN_DATE)

    info_records = [r for r in caplog.records if REPORT_LOG_PREFIX in r.message]
    assert len(info_records) == 1
    assert info_records[0].levelno == logging.INFO


@pytest.mark.parametrize(
    "failing_origin_cache",
    [
        pytest.param({"EIN": None, "STN": [make_dummy_flight()]}, id="one-origin-failed"),
        pytest.param({"EIN": [make_dummy_flight()], "STN": [make_dummy_flight()]}, id="one-origin-partial"),
    ],
)
def test_generate_run_report_logs_at_warning_when_any_origin_is_not_success(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue, mock_write_run_report, caplog, failing_origin_cache
):
    """
    Regression test (solution-architect review): a partial or failed origin must escalate the
    report to WARNING -- otherwise a run that silently lost data for one origin (the exact cost
    of the all_done trade-off, per MONITORING.md) logs identically to a fully healthy run.
    """
    mock_read_cache.side_effect = lambda origin, airline, date: (
        failing_origin_cache[origin] if date == RUN_DATE else None
    )
    if failing_origin_cache["EIN"] is not None:
        # "one-origin-partial" case: cache exists but a query is still failing after retry.
        mock_load_retry_queue.side_effect = lambda airline, origin, date: (
            [{"origin_iata": "EIN", "query_date": "2026-07-01"}] if origin == "EIN" else []
        )

    with caplog.at_level(logging.WARNING):
        generate_run_report(RUN_DATE)

    warning_records = [r for r in caplog.records if REPORT_LOG_PREFIX in r.message]
    assert len(warning_records) == 1
    assert warning_records[0].levelno == logging.WARNING


def test_generate_run_report_persists_payload_to_gcs(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue, mock_write_run_report
):
    """Tests that generate_run_report persists build_report_payload's output via
    cache.write_run_report, scoped to the "ryanair" airline (matching the hardcoded literal used
    everywhere else in report.py/run.py)."""
    mock_read_cache.side_effect = lambda origin, airline, date: [make_dummy_flight()] if date == RUN_DATE else None

    generate_run_report(RUN_DATE)

    mock_write_run_report.assert_called_once()
    args = mock_write_run_report.call_args[0]
    assert args[0] == "ryanair"
    assert args[1] == RUN_DATE
    assert args[2]["run_date"] == RUN_DATE


def test_generate_run_report_never_raises_when_write_run_report_fails(
    mock_scrape_origins, mock_read_cache, mock_load_retry_queue, mock_write_run_report
):
    """
    Tests that generate_run_report doesn't raise or behave differently when the GCS persistence
    write fails (write_run_report returning False) -- logging already happened, and the "never
    fails the task" contract must hold regardless of whether the persisted artifact lands.
    """
    mock_write_run_report.return_value = False

    generate_run_report(RUN_DATE)  # must not raise

    mock_write_run_report.assert_called_once()
