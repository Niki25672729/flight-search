import logging
from dataclasses import replace

import pytest

from cache import _CasExhausted
from config import DATE_FORMAT
from conftest import FROZEN_NOW, make_dummy_flight
from run import check_gcs_accessible, ingest_airport, retry_failed_ingests, main as run_main
from manual_run import run_ingestion, retry_ingestion, main as manual_run_main


RUN_DATE = FROZEN_NOW.strftime(DATE_FORMAT)


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture
def mock_read_cache(mocker):
    """Mocks cache.read_cache."""
    return mocker.patch("run.read_cache")


@pytest.fixture
def mock_write_cache(mocker):
    """Mocks cache.write_cache. Defaults to True (write landed), matching the common case."""
    return mocker.patch("run.write_cache", return_value=True)


@pytest.fixture
def mock_update_cache(mocker):
    """Mocks cache.update_cache. Defaults to succeeding (no CAS exhaustion)."""
    return mocker.patch("run.update_cache")


@pytest.fixture
def mock_scrape_ryanair(mocker):
    """Mocks scraper.scrape_ryanair."""
    return mocker.patch("run.scrape_ryanair")


@pytest.fixture
def mock_retry_failed_queries(mocker):
    """Mocks scraper.retry_failed_queries."""
    return mocker.patch("run.retry_failed_queries")


@pytest.fixture
def mock_confirm_recovered(mocker):
    """Mocks scraper.confirm_recovered."""
    return mocker.patch("run.confirm_recovered")


@pytest.fixture
def mock_ingest_airport(mocker):
    """Mocks run.ingest_airport."""
    return mocker.patch("manual_run.ingest_airport")


@pytest.fixture
def mock_retry_failed_ingests(mocker):
    """Mocks run.retry_failed_ingests."""
    return mocker.patch("manual_run.retry_failed_ingests")


@pytest.fixture
def mock_scrape_origins(mocker):
    """Patches config.SCRAPE_ORIGINS down to two origins, so tests don't loop over the real ~49."""
    mocker.patch("manual_run.SCRAPE_ORIGINS", ["EIN", "STN"])


# ---------------------------
# Cloud (run.py)
# ---------------------------


# --- ingest_airport ---


def test_ingest_airport_skips_when_fresh_cache_exists(mock_read_cache, mock_write_cache, mock_scrape_ryanair, caplog):
    """Tests that an origin with a fresh GCS blob is skipped, not scraped."""
    mock_read_cache.return_value = [make_dummy_flight()]

    with caplog.at_level(logging.INFO):
        result = ingest_airport("EIN", RUN_DATE)

    assert result is True
    mock_scrape_ryanair.assert_not_called()
    mock_write_cache.assert_not_called()
    assert "Skip EIN: fresh data already exists." in caplog.text


def test_ingest_airport_scrapes_and_writes_on_cache_miss(
    mock_read_cache, mock_write_cache, mock_scrape_ryanair, caplog
):
    """
    Tests that a cache miss triggers a scrape and a write, and that the write is CAS-protected
    with if_generation_match=0 — this scrape started from a cache miss, so the write should
    only land if the blob still doesn't exist by the time it's issued (see cache.write_cache).
    """
    mock_read_cache.return_value = None
    flights = [make_dummy_flight()]
    mock_scrape_ryanair.return_value = flights

    with caplog.at_level(logging.INFO):
        result = ingest_airport("EIN", RUN_DATE)

    assert result is True
    mock_scrape_ryanair.assert_called_once_with("EIN", RUN_DATE)
    mock_write_cache.assert_called_once_with("EIN", "ryanair", flights, RUN_DATE, if_generation_match=0)
    assert "Done EIN: 1 flights ingested." in caplog.text


def test_ingest_airport_still_succeeds_when_write_dropped_by_cas_conflict(
    mock_read_cache, mock_write_cache, mock_scrape_ryanair, caplog
):
    """
    Tests that ingest_airport still returns True when its write is dropped because a newer
    write already landed (cache.write_cache returning False) — fresh data now exists for this
    origin either way, so this isn't a failure, just not this container's data that won.
    """
    mock_read_cache.return_value = None
    mock_scrape_ryanair.return_value = [make_dummy_flight()]
    mock_write_cache.return_value = False

    with caplog.at_level(logging.INFO):
        result = ingest_airport("EIN", RUN_DATE)

    assert result is True
    assert "a newer cache write already landed" in caplog.text


def test_ingest_airport_returns_false_on_scrape_exception(
    mock_read_cache, mock_write_cache, mock_scrape_ryanair, caplog
):
    """Tests that a scrape exception is logged and returns False, without raising."""
    mock_read_cache.return_value = None
    mock_scrape_ryanair.side_effect = Exception("boom")

    with caplog.at_level(logging.INFO):
        result = ingest_airport("EIN", RUN_DATE)

    assert result is False
    mock_write_cache.assert_not_called()
    assert "Unexpected error scraping EIN: boom" in caplog.text


def test_ingest_airport_returns_false_on_empty_flights(mock_read_cache, mock_write_cache, mock_scrape_ryanair, caplog):
    """Tests that an empty scrape result is treated as a failure and not written to cache."""
    mock_read_cache.return_value = None
    mock_scrape_ryanair.return_value = []

    with caplog.at_level(logging.INFO):
        result = ingest_airport("EIN", RUN_DATE)

    assert result is False
    mock_write_cache.assert_not_called()
    assert "EIN: scrape returned 0 flights — not writing (likely a failure)." in caplog.text


# --- retry_failed_ingests ---


def test_retry_failed_ingests_does_nothing_when_no_recovered(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered
):
    """Tests that no cache updates or confirmations happen when nothing was recovered."""
    mock_retry_failed_queries.return_value = ([], [])

    retry_failed_ingests(RUN_DATE, "EIN")

    mock_update_cache.assert_not_called()
    mock_confirm_recovered.assert_not_called()


def test_retry_failed_ingests_writes_cache_before_confirming_recovered(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered, caplog
):
    """Tests merge-then-confirm ordering: the cache update must complete before the queue
    entries are confirmed removed — the actual durability fix this round exists for.
    Asserts on what the mutate callable passed to update_cache produces when invoked against a
    simulated existing cache, since update_cache (not retry_failed_ingests) owns the actual
    read-mutate-write now."""
    existing_flight = make_dummy_flight("EXISTING")
    recovered_flight = make_dummy_flight("BCN")
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([recovered_flight], [entry])

    call_order = []
    mock_update_cache.side_effect = lambda *a, **k: call_order.append("update_cache")
    mock_confirm_recovered.side_effect = lambda *a, **k: call_order.append("confirm_recovered")

    with caplog.at_level(logging.INFO):
        retry_failed_ingests(RUN_DATE, "EIN")

    assert call_order == ["update_cache", "confirm_recovered"]
    mock_update_cache.assert_called_once()
    args = mock_update_cache.call_args[0]
    assert args[:3] == ("EIN", "ryanair", RUN_DATE)
    assert args[3]([existing_flight]) == [existing_flight, recovered_flight]
    mock_confirm_recovered.assert_called_once_with("ryanair", "EIN", RUN_DATE, [entry])
    assert "Merged 1 recovered flights into EIN's cache." in caplog.text
    assert "Confirmed 1 recovered entries removed from EIN's retry queue." in caplog.text


def test_retry_failed_ingests_handles_no_existing_cache(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered
):
    """Tests that recovered flights are merged in even when there's no existing cache for that origin."""
    recovered_flight = make_dummy_flight("BCN")
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([recovered_flight], [entry])

    retry_failed_ingests(RUN_DATE, "EIN")

    mutate = mock_update_cache.call_args[0][3]
    assert mutate([]) == [recovered_flight]
    mock_confirm_recovered.assert_called_once_with("ryanair", "EIN", RUN_DATE, [entry])


def test_retry_failed_ingests_confirms_entries_with_zero_recovered_flights(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered
):
    """Tests that entries which were attempted but produced no flights still get confirmed away,
    without triggering a (pointless) cache update."""
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([], [entry])

    retry_failed_ingests(RUN_DATE, "EIN")

    mock_update_cache.assert_not_called()
    mock_confirm_recovered.assert_called_once_with("ryanair", "EIN", RUN_DATE, [entry])


def test_retry_failed_ingests_dedupes_merged_flights_by_natural_key(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered
):
    """Tests that a flight already present in the existing cache (same origin/destination/
    departure_time/flight_number) collapses with its recovered counterpart, keeping the row
    with the latest scraped_at — this is what makes the merge step safe to re-run."""
    older = make_dummy_flight("BCN")
    newer = replace(older, scraped_at=older.scraped_at.replace(year=older.scraped_at.year + 1))
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([newer], [entry])

    retry_failed_ingests(RUN_DATE, "EIN")

    mutate = mock_update_cache.call_args[0][3]
    assert mutate([older]) == [newer]


def test_retry_failed_ingests_skips_confirm_when_cache_cas_exhausted(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered, caplog
):
    """Tests issue #2 / bug A: update_cache raising _CasExhausted skips confirm_recovered —
    entries stay queued, safe since the merge never durably landed and is idempotent to retry
    next run."""
    recovered_flight = make_dummy_flight("BCN")
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([recovered_flight], [entry])
    mock_update_cache.side_effect = _CasExhausted("boom")

    with caplog.at_level(logging.WARNING):
        retry_failed_ingests(RUN_DATE, "EIN")

    mock_confirm_recovered.assert_not_called()
    assert "EIN: cache merge lost the CAS race repeatedly" in caplog.text


def test_retry_failed_ingests_swallows_retry_queue_cas_exhaustion_from_confirm_recovered(
    mock_retry_failed_queries, mock_update_cache, mock_confirm_recovered, caplog
):
    """Tests bug B: confirm_recovered raising _CasExhausted is caught and logged — the
    merge already landed, so there's no data loss, just a queue entry left for a redundant
    retry next run."""
    recovered_flight = make_dummy_flight("BCN")
    entry = {"origin_iata": "EIN", "query_date": "2026-07-01"}
    mock_retry_failed_queries.return_value = ([recovered_flight], [entry])
    mock_confirm_recovered.side_effect = _CasExhausted("boom")

    with caplog.at_level(logging.WARNING):
        retry_failed_ingests(RUN_DATE, "EIN")

    mock_update_cache.assert_called_once()
    mock_confirm_recovered.assert_called_once()
    assert "EIN: retry queue confirmation lost the CAS race repeatedly" in caplog.text


# --- run.main (scheduled entry point) ---


def test_run_main_ingests_single_origin_and_exits_zero_on_success(mocker):
    """Tests that running with an origin + run_date arg calls ingest_airport for that origin/date
    and exits 0 on success."""
    mocker.patch("sys.argv", ["run.py", "EIN", RUN_DATE])
    mock_ingest = mocker.patch("run.ingest_airport", return_value=True)

    with pytest.raises(SystemExit) as exc_info:
        run_main()

    mock_ingest.assert_called_once_with("EIN", RUN_DATE)
    assert exc_info.value.code == 0


def test_run_main_exits_one_on_failure(mocker):
    """Tests that a failed ingest_airport call exits with code 1."""
    mocker.patch("sys.argv", ["run.py", "EIN", RUN_DATE])
    mocker.patch("run.ingest_airport", return_value=False)

    with pytest.raises(SystemExit) as exc_info:
        run_main()

    assert exc_info.value.code == 1


def test_run_main_dispatches_to_retry_with_origin(mocker):
    """Tests that running with 'retry', an origin, and a run_date calls retry_failed_ingests
    scoped to just that origin — the only retry shape run.py supports now; the loop-all-origins
    shape moved to manual_run.py's retry_ingestion()."""
    mocker.patch("sys.argv", ["run.py", "retry", "EIN", RUN_DATE])
    mock_retry = mocker.patch("run.retry_failed_ingests")
    mock_ingest = mocker.patch("run.ingest_airport")

    run_main()

    mock_retry.assert_called_once_with(RUN_DATE, origin="EIN")
    mock_ingest.assert_not_called()


# --- check_gcs_accessible ---


def test_check_gcs_accessible_true_when_reachable(mocker):
    """Tests that a successful round-trip (bucket.exists() doesn't raise) reports accessible."""
    mock_bucket = mocker.patch("run._get_gcs_bucket").return_value
    mock_bucket.exists.return_value = True

    assert check_gcs_accessible() is True


def test_check_gcs_accessible_false_when_unreachable(mocker, caplog):
    """Tests that GCS being unreachable is reported as inaccessible, not raised."""
    mocker.patch("run._get_gcs_bucket", side_effect=Exception("connection refused"))

    with caplog.at_level(logging.WARNING):
        result = check_gcs_accessible()

    assert result is False
    assert "GCS accessibility check failed" in caplog.text


def test_check_gcs_accessible_false_when_bucket_call_raises(mocker):
    """Tests that an error during the round-trip itself (not just client construction) is also caught."""
    mock_bucket = mocker.patch("run._get_gcs_bucket").return_value
    mock_bucket.exists.side_effect = Exception("permission denied")

    assert check_gcs_accessible() is False


def test_run_main_dispatches_to_check_gcs(mocker):
    """Tests that running with 'check-gcs' calls check_gcs_accessible and exits 0/1 on its result,
    without touching ingest_airport or retry_failed_ingests."""
    mocker.patch("sys.argv", ["run.py", "check-gcs"])
    mock_check = mocker.patch("run.check_gcs_accessible", return_value=True)
    mock_ingest = mocker.patch("run.ingest_airport")
    mock_retry = mocker.patch("run.retry_failed_ingests")

    with pytest.raises(SystemExit) as exc_info:
        run_main()

    mock_check.assert_called_once()
    mock_ingest.assert_not_called()
    mock_retry.assert_not_called()
    assert exc_info.value.code == 0


def test_run_main_exits_one_when_gcs_check_fails(mocker):
    """Tests that check-gcs reporting inaccessible exits 1."""
    mocker.patch("sys.argv", ["run.py", "check-gcs"])
    mocker.patch("run.check_gcs_accessible", return_value=False)

    with pytest.raises(SystemExit) as exc_info:
        run_main()

    assert exc_info.value.code == 1


# ---------------------------
# Local (manual_run.py)
# ---------------------------


# --- run_ingestion ---


def test_run_ingestion_calls_ingest_airport_for_each_origin(mock_scrape_origins, mock_ingest_airport):
    """Tests that ingest_airport is called once per configured origin, with the given run_date."""
    mock_ingest_airport.return_value = True

    run_ingestion(RUN_DATE)

    mock_ingest_airport.assert_any_call("EIN", RUN_DATE)
    mock_ingest_airport.assert_any_call("STN", RUN_DATE)
    assert mock_ingest_airport.call_count == 2


def test_run_ingestion_reports_no_failures_when_all_succeed(mock_scrape_origins, mock_ingest_airport, caplog):
    """Tests that the finish log reports 0 failures when every origin succeeds."""
    mock_ingest_airport.return_value = True

    with caplog.at_level(logging.INFO):
        run_ingestion(RUN_DATE)

    assert "Ingestion run finished. 0 origins failed: []" in caplog.text


def test_run_ingestion_collects_failed_origins(mock_scrape_origins, mock_ingest_airport, caplog):
    """Tests that origins where ingest_airport returns False are collected as failures."""
    mock_ingest_airport.side_effect = [False, True]

    with caplog.at_level(logging.INFO):
        run_ingestion(RUN_DATE)

    assert "Ingestion run finished. 1 origins failed: ['EIN']" in caplog.text


# --- retry_ingestion ---


def test_retry_ingestion_calls_retry_failed_ingests_for_each_origin(mock_scrape_origins, mock_retry_failed_ingests):
    """Tests that retry_ingestion calls retry_failed_ingests once per configured origin, with the
    given run_date — the loop-all-origins retry shape moved here from run.py."""
    retry_ingestion(RUN_DATE)

    mock_retry_failed_ingests.assert_any_call(RUN_DATE, "EIN")
    mock_retry_failed_ingests.assert_any_call(RUN_DATE, "STN")
    assert mock_retry_failed_ingests.call_count == 2


# --- main ---


def test_main_dispatches_to_retry_when_argv_is_retry(
    mocker, mock_scrape_origins, mock_retry_failed_ingests, mock_ingest_airport
):
    """Tests that running with 'retry' as the first argument calls retry_failed_ingests (per
    origin) with the computed run_date, not run_ingestion."""
    mocker.patch("sys.argv", ["manual_run.py", "retry"])

    manual_run_main()

    mock_retry_failed_ingests.assert_any_call(RUN_DATE, "EIN")
    mock_retry_failed_ingests.assert_any_call(RUN_DATE, "STN")
    mock_ingest_airport.assert_not_called()


def test_main_runs_ingestion_by_default(mocker, mock_scrape_origins, mock_retry_failed_ingests, mock_ingest_airport):
    """Tests that running with no arguments runs the full ingestion loop, not the retry queue."""
    mocker.patch("sys.argv", ["manual_run.py"])
    mock_ingest_airport.return_value = True

    manual_run_main()

    mock_retry_failed_ingests.assert_not_called()
    assert mock_ingest_airport.called
