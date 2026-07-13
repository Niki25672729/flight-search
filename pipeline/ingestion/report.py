import logging
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from cache import load_retry_queue, read_cache, write_run_report
from config import DATE_FORMAT, SCRAPE_BUFFER_DAYS, SCRAPE_ORIGINS


# ---------------------------
# Constants
# ---------------------------

REPORT_LOG_PREFIX = "FLIGHT_INGESTION_REPORT"
# Coarse placeholder threshold for the day-over-day PEAK/DROP flag, not real anomaly detection --
# that's deferred to the future dbt/gold layer, which can compare against full history (weekly comparison) instead of
# just yesterday. Retire this once that layer lands, rather than leaving it to drift alongside a
# stronger signal (see MONITORING.md).
REPORT_PEAK_DROP_THRESHOLD_PCT = 20.0
REPORT_STATUS_SEVERITY = {"failed": 0, "partial": 1, "success": 2}


# ---------------------------
# Data Model
# ---------------------------


@dataclass
class OriginSummary:
    """One origin's ingestion outcome for a single run_date, derived read-only from cache/retry-queue state."""

    origin: str
    status: str  # "success" | "partial" | "failed" -- "failed" means no cache data at all for run_date
    flights_count: int
    queries_succeeded: int
    queries_total: int
    prior_flights_count: int | None  # None when yesterday's cache doesn't exist (e.g. a newly added origin)
    pct_change: float | None
    flag: str | None  # "PEAK" | "DROP" | None


# ---------------------------
# Helpers
# ---------------------------


def _prior_date(run_date: str) -> str:
    """Returns the calendar day before run_date, in the same yyyymmdd format."""
    return (datetime.strptime(run_date, DATE_FORMAT) - timedelta(days=1)).strftime(DATE_FORMAT)


def _pct_change(today: int, prior: int | None) -> tuple[float | None, str | None]:
    """
    Computes day-over-day percent change and a PEAK/DROP flag at the >20% threshold. Returns
    (None, None) when there's no prior-day count to compare against.
    """
    if prior is None or prior == 0:
        return None, None
    pct = (today - prior) / prior * 100
    if pct > REPORT_PEAK_DROP_THRESHOLD_PCT:
        return round(pct, 2), "PEAK"
    if pct < -REPORT_PEAK_DROP_THRESHOLD_PCT:
        return round(pct, 2), "DROP"
    return round(pct, 2), None


def _format_origin_line(summary: OriginSummary) -> str:
    """Formats a single origin's report line."""
    comparison = "no prior day data"
    if summary.prior_flights_count is not None:
        comparison = (
            f"{summary.flights_count:,} vs {summary.prior_flights_count:,} yesterday ({summary.pct_change:+.1f}%)"
        )
        if summary.flag:
            comparison += f" {summary.flag}"

    return (
        f"{summary.status.upper():<7} {summary.origin}: {summary.flights_count:,} flights | "
        f"queries {summary.queries_succeeded}/{summary.queries_total} succeeded | {comparison}"
    )


def _aggregate_stats(summaries: list[OriginSummary]) -> dict[str, int]:
    """Computes run-wide totals shared by the log line (format_run_report) and the persisted
    JSON payload (build_report_payload), so the two never drift apart."""
    status_counts = Counter(s.status for s in summaries)
    return {
        "origins": len(summaries),
        "success": status_counts["success"],
        "partial": status_counts["partial"],
        "failed": status_counts["failed"],
        "flights": sum(s.flights_count for s in summaries),
        "queries_succeeded": sum(s.queries_succeeded for s in summaries),
        # Count of origins flagged PEAK/DROP, so a glance at status.json catches a volume swing
        # without scanning every origin line.
        "queries_flagged": sum(1 for s in summaries if s.flag),
        "queries_total": sum(s.queries_total for s in summaries),
    }


# ---------------------------
# Public API
# ---------------------------


def summarize_origin(origin: str, run_date: str) -> OriginSummary:
    """
    Builds one origin's summary for run_date, read-only against cache/retry-queue state already
    written by ingest_flights/retry_failed_ingests -- never scrapes or writes.
    """
    flights = read_cache(origin, "ryanair", run_date)
    flights_count = len(flights) if flights is not None else 0

    remaining_queue = load_retry_queue("ryanair", origin, run_date)

    if flights is None:
        status = "failed"
    elif remaining_queue:
        status = "partial"
    else:
        status = "success"

    # SCRAPE_BUFFER_DAYS - len(remaining_queue) assumes every date not left in the retry queue
    # succeeded -- true when the scrape ran to completion, but not when it crashed before ever
    # reaching the retry-queue bookkeeping for a given date (flights is None, status "failed").
    # In that case 0 is the honest count; anything else would claim full success on a line a
    # human is now meant to trust at a glance.
    queries_succeeded = 0 if status == "failed" else SCRAPE_BUFFER_DAYS - len(remaining_queue)

    prior_flights = read_cache(origin, "ryanair", _prior_date(run_date))
    prior_flights_count = len(prior_flights) if prior_flights is not None else None
    pct_change, flag = _pct_change(flights_count, prior_flights_count)

    return OriginSummary(
        origin=origin,
        status=status,
        flights_count=flights_count,
        queries_succeeded=queries_succeeded,
        queries_total=SCRAPE_BUFFER_DAYS,
        prior_flights_count=prior_flights_count,
        pct_change=pct_change,
        flag=flag,
    )


def build_run_summary(run_date: str) -> list[OriginSummary]:
    """
    Builds a summary for every configured origin, sorted worst-status-first (failed, then
    partial, then success) so problems are visible without scrolling past healthy origins.
    """
    summaries = [summarize_origin(origin, run_date) for origin in SCRAPE_ORIGINS]
    return sorted(summaries, key=lambda s: (REPORT_STATUS_SEVERITY[s.status], s.origin))


def format_run_report(summaries: list[OriginSummary], run_date: str) -> str:
    """
    Formats the full run report: one aggregate line across all origins, followed by one line per
    origin (worst-status-first). Every line is prefixed REPORT_LOG_PREFIX so it's greppable
    across Airflow's per-task logs.
    """
    stats = _aggregate_stats(summaries)
    aggregate_line = (
        f"{REPORT_LOG_PREFIX} run_date={run_date} origins={stats['origins']} "
        f"(success={stats['success']}, partial={stats['partial']}, failed={stats['failed']}) "
        f"flights={stats['flights']:,} queries={stats['queries_succeeded']}/{stats['queries_total']} succeeded"
    )
    origin_lines = [f"{REPORT_LOG_PREFIX} {_format_origin_line(s)}" for s in summaries]
    return "\n".join([aggregate_line, *origin_lines])


def build_report_payload(summaries: list[OriginSummary], run_date: str) -> dict[str, Any]:
    """
    Builds a plain-JSON-serializable payload carrying the same content as format_run_report, for
    persisting to GCS via cache.write_run_report (see generate_run_report). OriginSummary is
    already all str/int/float/None fields, so dataclasses.asdict needs no custom serializer
    (unlike Flight, which cache.py serializes separately).
    """
    return {"run_date": run_date, "aggregate": _aggregate_stats(summaries), "origins": [asdict(s) for s in summaries]}


# ---------------------------
# Scheduled Entry Point
# ---------------------------


def generate_run_report(run_date: str) -> None:
    """
    Scheduled entry point (see run.py and flight_pipeline_dag.py).
    Logs a summary of the day's ingestion run — aggregate + per-origin. Read-only, never
    fails, and doesn't handle DagRun-level failure alerting (that's callbacks.py's job).

    Logs at WARNING (not INFO) if any origin is "partial"/"failed", so partial data loss
    is visible in the log stream, not buried in routine INFO noise.

    Uses its own REPORT_LOG_PREFIX (not callbacks.py's PIPELINE_ALERT) since this signals a
    narrower issue than a full DagRun failure.

    Also persists the same content to GCS (cache.write_run_report) for later reference --
    best-effort, never raises, so this never affects generate_run_report's own "never fails"
    contract regardless of whether the write lands.
    """
    summaries = build_run_summary(run_date)
    report = format_run_report(summaries, run_date)
    if any(s.status != "success" for s in summaries):
        logging.warning(report)
    else:
        logging.info(report)

    write_run_report("ryanair", run_date, build_report_payload(summaries, run_date))
