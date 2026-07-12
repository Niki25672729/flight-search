import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from cache import _CasExhausted, load_retry_queue
from config import DATE_FORMAT
from scraper import (
    _strip_city_annotation,
    _extract_city_country_from_full,
    _classify_airport,
    _save_unknown_and_ambiguous_findings,
    _record_failed_query,
    scrape_ryanair,
    retry_failed_queries,
    confirm_recovered,
)
from conftest import FROZEN_NOW, make_ryanair_py_flight


# ---------------------------
# Constants
# ---------------------------

RUN_DATE = FROZEN_NOW.strftime(DATE_FORMAT)


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture(autouse=True)
def mock_unknown_airport_details(mocker):
    mocker.patch("scraper.unknown_airport_details", {})


@pytest.fixture(autouse=True)
def mock_ambiguous_airport_details(mocker):
    mocker.patch("scraper.ambiguous_airport_details", {})


@pytest.fixture(autouse=True)
def mock_ignored_airports(mocker):
    mocker.patch("scraper.IGNORED_AIRPORTS", {"XYZ"})


@pytest.fixture
def mock_unknown_airports_file(mocker, tmp_path):
    unknown_path = tmp_path / "unknown_airports.json"
    mocker.patch("scraper.UNKNOWN_AIRPORTS_PATH", str(unknown_path))
    return unknown_path


@pytest.fixture
def mock_ambiguous_airports_file(mocker, tmp_path):
    ambiguous_path = tmp_path / "ambiguous_airports.json"
    mocker.patch("scraper.AMBIGUOUS_AIRPORTS_PATH", str(ambiguous_path))
    return ambiguous_path


@pytest.fixture
def mock_ryanair_client(mocker):
    mock_instance = MagicMock()
    mocker.patch("scraper.Ryanair", return_value=mock_instance)
    return mock_instance


# ---------------------------
# Tests for _strip_city_annotation
# ---------------------------


def test_strip_city_annotation_removes_parenthetical_suffix():
    """Tests that a nearest-major-city annotation is stripped, leaving just the airport's own city."""
    assert _strip_city_annotation("Reus (Barcelona)") == "Reus"
    assert _strip_city_annotation("Beauvais (Paris)") == "Beauvais"


def test_strip_city_annotation_leaves_plain_city_unchanged():
    """Tests that a city with no annotation is returned as-is."""
    assert _strip_city_annotation("Barcelona") == "Barcelona"


def test_strip_city_annotation_handles_none():
    """Tests that None passes through unchanged rather than raising."""
    assert _strip_city_annotation(None) is None


# ---------------------------
# Tests for _extract_city_country_from_full
# ---------------------------


def test_extract_city_country_from_full_splits_correctly():
    """Tests that ryanair-py's 'Name, Country' format is split into (city, country)."""
    assert _extract_city_country_from_full("London Stansted, United Kingdom") == ("London Stansted", "United Kingdom")


def test_extract_city_country_from_full_strips_city_annotation():
    """Tests that a nearest-major-city annotation within destinationFull is stripped."""
    assert _extract_city_country_from_full("Reus (Barcelona), Spain") == ("Reus", "Spain")


def test_extract_city_country_from_full_handles_missing_or_malformed():
    """Tests that None/empty/malformed input returns (None, None) rather than raising."""
    assert _extract_city_country_from_full(None) == (None, None)
    assert _extract_city_country_from_full("") == (None, None)
    assert _extract_city_country_from_full("NoCommaHere") == (None, None)


# ---------------------------
# Tests for _classify_airport
# ---------------------------


def test_classify_airport_known_airport_agrees():
    """Tests that a known EU airport whose API data agrees with the static record is resolved, unlogged."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("BCN", "Barcelona", "Spain", ambiguous_found, unknown_found)

    assert result == ("Barcelona", "Spain")
    assert ambiguous_found == {}


def test_classify_airport_known_airport_disagrees_logs_ambiguous():
    """Tests that a known EU airport whose API data disagrees with the static record is logged as ambiguous."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("BCN", "Barna", "Spain", ambiguous_found, unknown_found)

    # API value wins for the resolved Flight data, but the mismatch is flagged
    assert result == ("Barna", "Spain")
    assert ambiguous_found == {"BCN": {"city": "Barna", "country": "Spain"}}


def test_classify_airport_falls_back_to_static_when_api_data_missing():
    """Tests that a known EU airport with no API city/country falls back to the static record."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("BCN", None, None, ambiguous_found, unknown_found)

    assert result == ("Barcelona", "Spain")


def test_classify_airport_ignored_airport_skipped():
    """Tests that an ignored airport (e.g. XYZ, in ignored_airports.json) is skipped without logging."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("XYZ", None, None, ambiguous_found, unknown_found)

    assert result is None
    assert unknown_found == {}


def test_classify_airport_unknown_airport_logged():
    """Tests that a code in neither eu_airports.json nor ignored_airports.json is logged as unknown."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("ZZZ", "Somewhere", "Nowhere", ambiguous_found, unknown_found)

    assert result is None
    assert unknown_found == {"ZZZ": {"city": "Somewhere", "country": "Nowhere"}}


def test_classify_airport_does_not_relog_known_unknown(mocker):
    """Tests that an airport already recorded in unknown_airport_details is not re-logged."""
    mocker.patch("scraper.unknown_airport_details", {"ZZZ": {"city": "Existing", "country": "Country"}})
    ambiguous_found, unknown_found = {}, {}

    result = _classify_airport("ZZZ", "Somewhere", "Nowhere", ambiguous_found, unknown_found)

    assert result is None
    assert unknown_found == {}


# ---------------------------
# Tests for _save_unknown_and_ambiguous_findings
# ---------------------------


def test_save_unknown_and_ambiguous_findings_writes_both_files(
    mock_unknown_airports_file, mock_ambiguous_airports_file
):
    """Tests that both findings dicts are written to their respective files when non-empty."""
    _save_unknown_and_ambiguous_findings(
        ambiguous_found={"BCN": {"city": "Barna", "country": "Spain"}},
        unknown_found={"ZZZ": {"city": "Somewhere", "country": "Nowhere"}},
    )

    assert json.loads(mock_ambiguous_airports_file.read_text()) == {"BCN": {"city": "Barna", "country": "Spain"}}
    assert json.loads(mock_unknown_airports_file.read_text()) == {"ZZZ": {"city": "Somewhere", "country": "Nowhere"}}


def test_save_unknown_and_ambiguous_findings_skips_empty(mock_unknown_airports_file, mock_ambiguous_airports_file):
    """Tests that no files are written when both findings dicts are empty."""
    _save_unknown_and_ambiguous_findings(ambiguous_found={}, unknown_found={})

    assert not mock_ambiguous_airports_file.exists()
    assert not mock_unknown_airports_file.exists()


# ---------------------------
# Tests for End-to-End Orchestration
# ---------------------------


def test_scrape_ryanair_returns_flights_for_known_destination(mocker, mock_ryanair_client):
    """Tests that a cheapest-fare result for a known destination is converted into a Flight."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [
        make_ryanair_py_flight(destination="BCN", destination_full="Barcelona, Spain", price=29.99)
    ]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert len(result) == 1
    assert result[0].destination_iata == "BCN"
    assert result[0].destination_city == "Barcelona"
    assert result[0].destination_country == "Spain"
    assert result[0].price_eur == 29.99
    assert result[0].airline == "Ryanair"
    assert result[0].arrival_time is None


def test_scrape_ryanair_strips_space_from_flight_number(mocker, mock_ryanair_client):
    """Tests that ryanair-py's 'FR 123' flight number format is normalised to 'FR123'."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [make_ryanair_py_flight(flight_number="FR 5682")]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert result[0].flight_number == "FR5682"


def test_scrape_ryanair_skips_ignored_destination(mocker, mock_ryanair_client, mock_unknown_airports_file):
    """Tests that a destination in ignored_airports.json produces no Flight."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [
        make_ryanair_py_flight(destination="XYZ", destination_full="Somewhere, Nowhereland")
    ]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert result == []
    # distinguishes "ignored" from "unknown" — an unknown destination would have written this file
    assert not mock_unknown_airports_file.exists()


def test_scrape_ryanair_logs_unknown_destination(mocker, mock_ryanair_client, mock_unknown_airports_file):
    """Tests that a genuinely unknown destination is logged to unknown_airports.json and produces no Flight."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [
        make_ryanair_py_flight(destination="ZZZ", destination_full="Somewhere, Nowhereland")
    ]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert result == []
    logged = json.loads(mock_unknown_airports_file.read_text())
    assert logged["ZZZ"] == {"city": "Somewhere", "country": "Nowhereland"}


def test_scrape_ryanair_queries_once_per_day_in_buffer(mocker, mock_ryanair_client):
    """Tests that get_cheapest_flights is called once per day in SCRAPE_BUFFER_DAYS, not once for the whole range."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 5)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = []

    scrape_ryanair("EIN", RUN_DATE)

    assert mock_ryanair_client.get_cheapest_flights.call_count == 5
    calls = mock_ryanair_client.get_cheapest_flights.call_args_list
    # each call queries a single day (date_from == date_to)
    for call in calls:
        args = call.args
        assert args[1] == args[2]


def test_scrape_ryanair_continues_after_a_failed_day(mocker, mock_ryanair_client):
    """Tests that a single day's query failure is logged and skipped, not fatal to the whole scrape."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 3)
    mocker.patch("scraper.time.sleep")
    good_flight = make_ryanair_py_flight(destination="BCN", destination_full="Barcelona, Spain")
    mock_ryanair_client.get_cheapest_flights.side_effect = [
        Exception("transient failure"),
        [good_flight],
        [good_flight],
    ]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert mock_ryanair_client.get_cheapest_flights.call_count == 3
    assert len(result) == 2  # the 2 successful days each produced 1 flight


def test_scrape_ryanair_classifies_each_destination_only_once(mocker, mock_ryanair_client):
    """Tests that a destination seen across multiple days is only classified once (not re-logged repeatedly)."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 3)
    mocker.patch("scraper.time.sleep")
    mock_classify = mocker.patch("scraper._classify_airport", return_value=("Barcelona", "Spain"))
    mock_ryanair_client.get_cheapest_flights.return_value = [make_ryanair_py_flight(destination="BCN")]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert len(result) == 3  # one flight per day, all for the same already-classified destination
    mock_classify.assert_called_once()


def test_scrape_ryanair_records_failed_day_for_retry(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """Tests that a day whose query fails is recorded in the retry queue."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.side_effect = Exception("transient failure")

    result = scrape_ryanair("EIN", RUN_DATE)

    assert result == []
    queue = load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT))
    assert len(queue) == 1
    assert queue[0]["origin_iata"] == "EIN"


def test_scrape_ryanair_survives_retry_queue_cas_exhaustion(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """
    Tests that a retry-queue write failing with _CasExhausted (e.g. a zombie
    ingestion container racing the same origin's blob) doesn't abort the scrape — it's
    best-effort bookkeeping, not the scrape result itself.
    """
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 3)
    mocker.patch("scraper.time.sleep")
    mocker.patch("scraper.update_retry_queue", side_effect=_CasExhausted("CAS exhausted"))
    good_flight = make_ryanair_py_flight(destination="BCN", destination_full="Barcelona, Spain")
    mock_ryanair_client.get_cheapest_flights.side_effect = [
        Exception("transient failure"),
        [good_flight],
        [good_flight],
    ]

    result = scrape_ryanair("EIN", RUN_DATE)

    assert mock_ryanair_client.get_cheapest_flights.call_count == 3
    assert len(result) == 2  # the 2 successful days each produced 1 flight


# ---------------------------
# Tests for _record_failed_query
# ---------------------------


def test_record_failed_query_appends_entry(tmp_retry_queue_path):
    """Tests that a failed query is appended to the retry queue with the fields needed to reissue it."""
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)

    queue = json.loads(tmp_retry_queue_path.read_text())
    assert queue == [{"origin_iata": "EIN", "query_date": "2026-08-18"}]


def test_record_failed_query_does_not_duplicate(tmp_retry_queue_path):
    """Tests that recording the same failed query twice doesn't create a duplicate entry."""
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)

    queue = json.loads(tmp_retry_queue_path.read_text())
    assert len(queue) == 1


# ---------------------------
# Tests for retry_failed_queries
# ---------------------------


def test_retry_failed_queries_empty_queue_returns_empty(tmp_retry_queue_path):
    """Tests that retrying an empty queue is a no-op."""
    result = retry_failed_queries(RUN_DATE, origin="EIN")

    assert result == ([], [])


def test_retry_failed_queries_skips_ryanair_client_when_queue_empty(mocker, tmp_retry_queue_path):
    """Tests that an empty queue never constructs a Ryanair client."""
    mock_ryanair_cls = mocker.patch("scraper.Ryanair")

    result = retry_failed_queries(RUN_DATE, origin="EIN")

    assert result == ([], [])
    mock_ryanair_cls.assert_not_called()


def test_retry_failed_queries_recovers_flights_and_entries(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """
    Tests that a successful retry returns the recovered flights/entries for the given origin,
    without writing anything back to the retry queue itself.
    """
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [make_ryanair_py_flight(destination="BCN")]

    recovered_flights, recovered_entries = retry_failed_queries(RUN_DATE, origin="EIN")

    assert len(recovered_flights) == 1
    assert recovered_flights[0].destination_iata == "BCN"
    assert recovered_entries == [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    mock_ryanair_client.get_cheapest_flights.assert_called_once_with("EIN", date(2026, 8, 18), date(2026, 8, 18))
    # the retry queue itself is untouched — that's the caller's job, via confirm_recovered
    assert load_retry_queue("ryanair", "EIN", RUN_DATE) == [{"origin_iata": "EIN", "query_date": "2026-08-18"}]


def test_retry_failed_queries_excludes_still_failing_entries(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """Tests that a query that still fails on retry is excluded from the returned recovered entries."""
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.side_effect = Exception("still down")

    recovered_flights, recovered_entries = retry_failed_queries(RUN_DATE, origin="EIN")

    assert recovered_flights == []
    assert recovered_entries == []
    # still present, since retry_failed_queries never writes to the queue
    queue = load_retry_queue("ryanair", "EIN", RUN_DATE)
    assert len(queue) == 1
    assert queue[0]["origin_iata"] == "EIN"


# ---------------------------
# Tests for confirm_recovered
# ---------------------------


def test_confirm_recovered_removes_only_specified_entries(tmp_retry_queue_path):
    """Tests that confirm_recovered removes exactly the given entries, leaving others untouched."""
    recovered_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    still_failing_entry = {"origin_iata": "EIN", "query_date": "2026-08-19"}
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)
    _record_failed_query("EIN", date(2026, 8, 19), "ryanair", RUN_DATE)

    confirm_recovered("ryanair", "EIN", RUN_DATE, [recovered_entry])

    assert load_retry_queue("ryanair", "EIN", RUN_DATE) == [still_failing_entry]


def test_confirm_recovered_leaves_concurrently_appended_entry_untouched(tmp_retry_queue_path):
    """
    Tests that an entry appended to the queue after retry_failed_queries took its snapshot
    (simulating a concurrent zombie-container append for the same origin) survives being
    confirmed away, since confirm_recovered diffs against the queue's current content, not a
    blind overwrite with a precomputed "still failing" list.
    """
    recovered_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)
    # simulates a concurrent write landing after retry_failed_queries's snapshot was taken
    concurrently_appended_entry = {"origin_iata": "EIN", "query_date": "2026-09-01"}
    _record_failed_query("EIN", date(2026, 9, 1), "ryanair", RUN_DATE)

    confirm_recovered("ryanair", "EIN", RUN_DATE, [recovered_entry])

    assert load_retry_queue("ryanair", "EIN", RUN_DATE) == [concurrently_appended_entry]


def test_confirm_recovered_noop_when_no_entries_recovered(tmp_retry_queue_path):
    """Tests that confirming an empty list of recovered entries leaves the queue unchanged."""
    _record_failed_query("EIN", date(2026, 8, 18), "ryanair", RUN_DATE)

    confirm_recovered("ryanair", "EIN", RUN_DATE, [])

    assert load_retry_queue("ryanair", "EIN", RUN_DATE) == [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
