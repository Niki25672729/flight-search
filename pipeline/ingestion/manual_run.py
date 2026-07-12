import logging
import sys

from config import DATE_FORMAT, SCRAPE_ORIGINS
from run import ingest_airport, retry_failed_ingests
from utils import _utc_now


def run_ingestion(run_date: str) -> None:
    """Manual/hotfix entry point: ensures fresh GCS cache for every SCRAPE_ORIGINS origin."""
    failed_origins = [origin for origin in SCRAPE_ORIGINS if not ingest_airport(origin, run_date)]
    logging.info(f"Ingestion run finished. {len(failed_origins)} origins failed: {failed_origins}")


def retry_ingestion(run_date: str) -> None:
    """Manual/hotfix entry point: retries every SCRAPE_ORIGINS origin's retry queue for run_date."""
    for origin in SCRAPE_ORIGINS:
        retry_failed_ingests(run_date, origin)


def main() -> None:
    """Entry point — runs the retry queue if invoked with 'retry', otherwise a full ingestion run."""
    run_date = _utc_now().strftime(DATE_FORMAT)
    if len(sys.argv) > 1 and sys.argv[1] == "retry":
        retry_ingestion(run_date)
    else:
        run_ingestion(run_date)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    main()
