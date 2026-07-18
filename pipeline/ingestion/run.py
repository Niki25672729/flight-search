import logging
import sys
from datetime import date

from cache import _CasExhausted, _get_gcs_bucket, read_cache, update_cache, write_cache
from models import Flight
from report import generate_run_report
from scraper import confirm_recovered, retry_failed_queries, scrape_ryanair


# ---------------------------
# Helpers
# ---------------------------


def _dedupe_flights(flights: list[Flight]) -> list[Flight]:
    """Dedupes on the route-day key (origin, destination, departure DATE, airline) — silver's
    flight_key grain — keeping the latest scraped_at, so a retry that observed a different
    cheapest than the original scrape can't break the grain."""
    latest: dict[tuple[str, str, date, str | None], Flight] = {}
    for flight in flights:
        key = (flight.origin_iata, flight.destination_iata, flight.departure_time.date(), flight.airline)
        existing = latest.get(key)
        if existing is None or flight.scraped_at > existing.scraped_at:
            latest[key] = flight
    return list(latest.values())


# ---------------------------
# Ingestion
# ---------------------------


def ingest_airport(origin: str, run_date: str) -> bool:
    """
    Ensures fresh cache exists for a single origin: skips if already fresh,
    otherwise scrapes and writes. Returns True on success (including skip), False
    on failure.
    """
    if read_cache(origin, "ryanair", run_date) is not None:
        logging.info(f"Skip {origin}: fresh data already exists.")
        return True

    logging.info(f"Start scrape {origin}")
    try:
        flights = scrape_ryanair(origin, run_date)
    except Exception as e:
        logging.error(f"Unexpected error scraping {origin}: {e}")
        return False

    if not flights:
        logging.warning(f"{origin}: scrape returned 0 flights — not writing (likely a failure).")
        return False

    written = write_cache(origin, "ryanair", flights, run_date, if_generation_match=0)
    if written:
        logging.info(f"Done {origin}: {len(flights)} flights ingested.")
    else:
        logging.info(f"Skip {origin}: a newer cache write already landed since this scrape started.")
    return True


def retry_failed_ingests(run_date: str, origin: str) -> None:
    """
    Re-attempts origin's queued {query_date} entries and merges recovered flights into cache.
    Merge-then-confirm: the cache write must succeed before queue entries are confirmed
    removed, so a crash never leaves a queue "resolved" with no matching cache data.

    Args:
        run_date: Date (yyyymmdd) whose retry-queue blob to read.
        origin: The origin whose retry queue to process.
    """
    recovered_flights, recovered_entries = retry_failed_queries(run_date, origin)
    if not recovered_flights and not recovered_entries:
        return

    if recovered_flights:

        def _merge(existing: list[Flight]) -> list[Flight]:
            return _dedupe_flights(existing + recovered_flights)

        try:
            update_cache(origin, "ryanair", run_date, _merge)
        except _CasExhausted:
            logging.warning(f"{origin}: cache merge lost the CAS race repeatedly; leaving entries queued for next run.")
            return  # do NOT confirm — entries stay queued, the merge is idempotent so a re-attempt next run is safe
        logging.info(f"Merged {len(recovered_flights)} recovered flights into {origin}'s cache.")

    if recovered_entries:
        try:
            confirm_recovered("ryanair", origin, run_date, recovered_entries)
        except _CasExhausted:
            logging.warning(
                f"{origin}: retry queue confirmation lost the CAS race repeatedly; entries remain queued "
                "(the merge already landed, so no data loss — just a redundant retry next run)."
            )
            return
        logging.info(f"Confirmed {len(recovered_entries)} recovered entries removed from {origin}'s retry queue.")


# ---------------------------
# Health Check
# ---------------------------


def check_gcs_accessible() -> bool:
    """Checks GCS reachability via a real round-trip (client construction alone doesn't touch
    the network) — fails loudly before ingest/retry risks a silent, lost local-fallback write."""
    try:
        _get_gcs_bucket().exists()
        return True
    except Exception as e:
        logging.warning(f"GCS accessibility check failed: {e}")
        return False


# ---------------------------
# Scheduled Entry Point
# ---------------------------


def main() -> None:
    """
    Scheduled entry point (see flight_pipeline_dag.py). Dispatch is positional:

    - ``run.py check-gcs``: checks GCS accessibility and exits 0/1. Used by the DAG's upstream
      check_gcs_accessible task, so Airflow's own retries can absorb a transient GCS outage
      before any ingest/retry task risks a silent local-fallback write inside an ephemeral
      container.
    - ``run.py <origin> <run_date>``: ingests that single origin.
    - ``run.py retry <origin> <run_date>``: retries single's queue.
    - ``run.py report <run_date>``: logs a per-origin + aggregate summary of run_date's ingestion
      run (read-only, always exits 0 -- see report.generate_run_report).
    """
    origin_or_retry = sys.argv[1]

    if origin_or_retry == "check-gcs":
        sys.exit(0 if check_gcs_accessible() else 1)

    if origin_or_retry == "retry":
        origin, run_date = sys.argv[2], sys.argv[3]
        retry_failed_ingests(run_date, origin=origin)
        return

    if origin_or_retry == "report":
        run_date = sys.argv[2]
        generate_run_report(run_date)
        return

    run_date = sys.argv[2]
    success = ingest_airport(origin_or_retry, run_date)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
