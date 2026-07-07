import json
from datetime import date
from unittest.mock import MagicMock

import pytest

from scraper import (
    _strip_city_annotation,
    _extract_city_country_from_full,
    _classify_route,
    _save_unknown_and_ambiguous_findings,
    _load_retry_queue,
    _save_retry_queue,
    _record_failed_query,
    scrape_ryanair,
    retry_failed_queries,
)
from conftest import make_ryanair_py_flight


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
def tmp_retry_queue_path(mocker, tmp_path):
    path = tmp_path / "retry.json"
    mocker.patch("scraper.RETRY_QUEUE_PATH", str(path))
    return path


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
# Tests for _classify_route
# ---------------------------


def test_classify_route_known_airport_agrees():
    """Tests that a known EU airport whose API data agrees with the static record is resolved, unlogged."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("BCN", "Barcelona", "Spain", ambiguous_found, unknown_found)

    assert result == ("Barcelona", "Spain")
    assert ambiguous_found == {}


def test_classify_route_known_airport_disagrees_logs_ambiguous():
    """Tests that a known EU airport whose API data disagrees with the static record is logged as ambiguous."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("BCN", "Barna", "Spain", ambiguous_found, unknown_found)

    # API value wins for the resolved Flight data, but the mismatch is flagged
    assert result == ("Barna", "Spain")
    assert ambiguous_found == {"BCN": {"city": "Barna", "country": "Spain"}}


def test_classify_route_falls_back_to_static_when_api_data_missing():
    """Tests that a known EU airport with no API city/country falls back to the static record."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("BCN", None, None, ambiguous_found, unknown_found)

    assert result == ("Barcelona", "Spain")


def test_classify_route_ignored_airport_skipped():
    """Tests that an ignored airport (e.g. XYZ, in ignored_airports.json) is skipped without logging."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("XYZ", None, None, ambiguous_found, unknown_found)

    assert result is None
    assert unknown_found == {}


def test_classify_route_unknown_airport_logged():
    """Tests that a code in neither eu_airports.json nor ignored_airports.json is logged as unknown."""
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("ZZZ", "Somewhere", "Nowhere", ambiguous_found, unknown_found)

    assert result is None
    assert unknown_found == {"ZZZ": {"city": "Somewhere", "country": "Nowhere"}}


def test_classify_route_does_not_relog_known_unknown(mocker):
    """Tests that an airport already recorded in unknown_airport_details is not re-logged."""
    mocker.patch("scraper.unknown_airport_details", {"ZZZ": {"city": "Existing", "country": "Country"}})
    ambiguous_found, unknown_found = {}, {}

    result = _classify_route("ZZZ", "Somewhere", "Nowhere", ambiguous_found, unknown_found)

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
# Tests for scrape_ryanair (end-to-end orchestration)
# ---------------------------


def test_scrape_ryanair_returns_flights_for_known_destination(mocker, mock_ryanair_client):
    """Tests that a cheapest-fare result for a known destination is converted into a Flight."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [
        make_ryanair_py_flight(destination="BCN", destination_full="Barcelona, Spain", price=29.99)
    ]

    result = scrape_ryanair("EIN")

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

    result = scrape_ryanair("EIN")

    assert result[0].flight_number == "FR5682"


def test_scrape_ryanair_skips_ignored_destination(mocker, mock_ryanair_client, mock_unknown_airports_file):
    """Tests that a destination in ignored_airports.json produces no Flight."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [
        make_ryanair_py_flight(destination="XYZ", destination_full="Somewhere, Nowhereland")
    ]

    result = scrape_ryanair("EIN")

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

    result = scrape_ryanair("EIN")

    assert result == []
    logged = json.loads(mock_unknown_airports_file.read_text())
    assert logged["ZZZ"] == {"city": "Somewhere", "country": "Nowhereland"}


def test_scrape_ryanair_queries_once_per_day_in_buffer(mocker, mock_ryanair_client):
    """Tests that get_cheapest_flights is called once per day in SCRAPE_BUFFER_DAYS, not once for the whole range."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 5)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = []

    scrape_ryanair("EIN")

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

    result = scrape_ryanair("EIN")

    assert mock_ryanair_client.get_cheapest_flights.call_count == 3
    assert len(result) == 2  # the 2 successful days each produced 1 flight


def test_scrape_ryanair_classifies_each_destination_only_once(mocker, mock_ryanair_client):
    """Tests that a destination seen across multiple days is only classified once (not re-logged repeatedly)."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 3)
    mocker.patch("scraper.time.sleep")
    mock_classify = mocker.patch("scraper._classify_route", return_value=("Barcelona", "Spain"))
    mock_ryanair_client.get_cheapest_flights.return_value = [make_ryanair_py_flight(destination="BCN")]

    result = scrape_ryanair("EIN")

    assert len(result) == 3  # one flight per day, all for the same already-classified destination
    mock_classify.assert_called_once()


def test_scrape_ryanair_records_failed_day_for_retry(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """Tests that a day whose query fails is recorded in the retry queue."""
    mocker.patch("scraper.SCRAPE_BUFFER_DAYS", 1)
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.side_effect = Exception("transient failure")

    result = scrape_ryanair("EIN")

    assert result == []
    queue = _load_retry_queue()
    assert len(queue) == 1
    assert queue[0]["origin_iata"] == "EIN"


# ---------------------------
# Tests for the retry queue (_load_retry_queue/_save_retry_queue/_record_failed_query)
# ---------------------------


def test_load_retry_queue_creates_empty_file_if_missing(tmp_retry_queue_path):
    """Tests that loading a missing retry queue creates an empty one and returns []."""
    assert not tmp_retry_queue_path.exists()

    result = _load_retry_queue()

    assert result == []
    assert tmp_retry_queue_path.exists()
    assert json.loads(tmp_retry_queue_path.read_text()) == []


def test_record_failed_query_appends_entry(tmp_retry_queue_path):
    """Tests that a failed query is appended to the retry queue with the fields needed to reissue it."""
    _record_failed_query("EIN", date(2026, 8, 18))

    queue = json.loads(tmp_retry_queue_path.read_text())
    assert queue == [{"origin_iata": "EIN", "query_date": "2026-08-18"}]


def test_record_failed_query_does_not_duplicate(tmp_retry_queue_path):
    """Tests that recording the same failed query twice doesn't create a duplicate entry."""
    _record_failed_query("EIN", date(2026, 8, 18))
    _record_failed_query("EIN", date(2026, 8, 18))

    queue = json.loads(tmp_retry_queue_path.read_text())
    assert len(queue) == 1


def test_save_and_load_retry_queue_roundtrip(tmp_retry_queue_path):
    """Tests that saving and reloading the retry queue preserves its content."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    _save_retry_queue(entries)

    assert _load_retry_queue() == entries


# ---------------------------
# Tests for retry_failed_queries
# ---------------------------


def test_retry_failed_queries_empty_queue_returns_empty(tmp_retry_queue_path):
    """Tests that retrying an empty queue is a no-op."""
    result = retry_failed_queries()

    assert result == []


def test_retry_failed_queries_recovers_and_clears_queue(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """Tests that a successful retry recovers flights and removes the entry from the queue."""
    _record_failed_query("EIN", date(2026, 8, 18))
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.return_value = [make_ryanair_py_flight(destination="BCN")]

    result = retry_failed_queries()

    assert len(result) == 1
    assert result[0].destination_iata == "BCN"
    assert _load_retry_queue() == []


def test_retry_failed_queries_keeps_still_failing_entries(mocker, mock_ryanair_client, tmp_retry_queue_path):
    """Tests that a query that still fails on retry is kept in the queue."""
    _record_failed_query("EIN", date(2026, 8, 18))
    mocker.patch("scraper.time.sleep")
    mock_ryanair_client.get_cheapest_flights.side_effect = Exception("still down")

    result = retry_failed_queries()

    assert result == []
    queue = _load_retry_queue()
    assert len(queue) == 1
    assert queue[0]["origin_iata"] == "EIN"
