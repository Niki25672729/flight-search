import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from models import Flight
from scraper import _get_ryanair_session, scrape_ryanair
from config import RYANAIR_SESSION_URL, SCRAPE_BUFFER_DAYS
from utils import EU_AIRPORT_DETAILS
from conftest import FROZEN_NOW


# ---------------------------
# Helpers
# ---------------------------

def _make_mock_ryanair_flight(
    destination: str,
    destination_full: str,
    departure_time: datetime,
    price: float,
) -> MagicMock:
    """Creates a mock ryanair-py Flight object with the fields used by scrape_ryanair."""
    mock_flight = MagicMock()
    mock_flight.destination = destination
    mock_flight.destinationFull = destination_full
    mock_flight.departureTime = departure_time
    mock_flight.price = price
    return mock_flight


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def mock_ryanair_session(mocker):
    """Mocks _get_ryanair_session to return a dummy requests.Session."""
    mock_session = MagicMock()
    mocker.patch("scraper._get_ryanair_session", return_value=mock_session)
    return mock_session


@pytest.fixture
def mock_ryanair_api(mocker, mock_ryanair_session):
    """
    Mocks the Ryanair API client construction and get_cheapest_flights.
    Returns the mock api instance for test customisation.
    """
    mock_api = MagicMock()
    mocker.patch("scraper.Ryanair.__new__", return_value=mock_api)
    return mock_api


@pytest.fixture
def mock_unknown_airports_file(mocker, tmp_path):
    """
    Patches unknown_airports.json path to a temp file to avoid polluting the real file.
    """
    unknown_path = tmp_path / "unknown_airports.json"
    mocker.patch("scraper.UNKNOWN_AIRPORTS_PATH", str(unknown_path))
    mocker.patch("scraper.unknown_airport_details", {})
    return unknown_path


# ---------------------------
# Tests for _get_ryanair_session
# ---------------------------

def test_get_ryanair_session_uses_cache_if_fresh(mocker, tmp_path):
    """
    Tests that a fresh cached cookie file is loaded without making a network request.
    """
    cookie_path = tmp_path / ".ryanair_cookies.json"
    cached_data = {
        "cookies": {"fr-correlation-id": "abc123"},
        "timestamp": datetime.now().timestamp() - 60,  # 60 seconds old
    }
    cookie_path.write_text(json.dumps(cached_data))
    mocker.patch("scraper.COOKIE_CACHE_PATH", str(cookie_path))

    mock_get = mocker.patch("requests.Session.get")
    session = _get_ryanair_session()

    mock_get.assert_not_called()
    assert session.cookies.get("fr-correlation-id") == "abc123"


def test_get_ryanair_session_fetches_new_if_expired(mocker, tmp_path):
    """
    Tests that expired cached cookies trigger a new network request.
    """
    cookie_path = tmp_path / ".ryanair_cookies.json"
    cached_data = {
        "cookies": {"fr-correlation-id": "old"},
        "timestamp": datetime.now().timestamp() - 7200,  # 2 hours old, TTL is 1 hour
    }
    cookie_path.write_text(json.dumps(cached_data))
    mocker.patch("scraper.COOKIE_CACHE_PATH", str(cookie_path))

    mock_get = mocker.patch("requests.Session.get")
    _get_ryanair_session()

    mock_get.assert_called_once_with(RYANAIR_SESSION_URL, timeout=660)


def test_get_ryanair_session_fetches_new_if_no_cache(mocker, tmp_path):
    """
    Tests that a missing cookie cache file triggers a new network request.
    """
    cookie_path = tmp_path / ".ryanair_cookies.json"
    mocker.patch("scraper.COOKIE_CACHE_PATH", str(cookie_path))

    mock_get = mocker.patch("requests.Session.get")
    _get_ryanair_session()

    mock_get.assert_called_once_with(RYANAIR_SESSION_URL, timeout=660)


def test_get_ryanair_session_saves_cookies_to_cache(mocker, tmp_path):
    """
    Tests that new cookies are saved to the cache file after fetching.
    """
    cookie_path = tmp_path / ".ryanair_cookies.json"
    mocker.patch("scraper.COOKIE_CACHE_PATH", str(cookie_path))
    mocker.patch("requests.Session.get")

    _get_ryanair_session()

    assert cookie_path.exists()
    cached = json.loads(cookie_path.read_text())
    assert "cookies" in cached
    assert "timestamp" in cached


# ---------------------------
# Tests for scrape_ryanair
# ---------------------------

def test_scrape_ryanair_returns_flights_for_known_airports(mock_ryanair_api, mock_unknown_airports_file):
    """
    Tests that flights to known EU airports are returned as Flight objects.
    """
    mock_ryanair_api.get_cheapest_flights.return_value = [
        _make_mock_ryanair_flight("STN", "London Stansted, United Kingdom", datetime(2026, 8, 25, 11, 15), 17.99),
        _make_mock_ryanair_flight("BCN", "Barcelona, Spain", datetime(2026, 9, 10, 14, 0), 29.99),
    ]

    result = scrape_ryanair("EIN")

    assert len(result) == 2
    assert result[0].destination_iata == "STN"
    assert result[0].destination_city == "London Stansted"
    assert result[0].destination_country == "United Kingdom"
    assert result[0].airline == "Ryanair"
    assert result[0].arrival_time is None
    assert result[0].price_eur == 17.99
    assert result[1].destination_iata == "BCN"


def test_scrape_ryanair_skips_unknown_airports(mock_ryanair_api, mock_unknown_airports_file):
    """
    Tests that flights to airports not in eu_airports.json are excluded from results
    but recorded in unknown_airports.json.
    """
    mock_ryanair_api.get_cheapest_flights.return_value = [
        _make_mock_ryanair_flight("STN", "London Stansted, United Kingdom", datetime(2026, 8, 25, 11, 15), 17.99),
        _make_mock_ryanair_flight("XYZ", "Unknown City, Unknown Country", datetime(2026, 8, 26, 10, 0), 99.99),
    ]

    result = scrape_ryanair("EIN")

    assert len(result) == 1
    assert result[0].destination_iata == "STN"

    # XYZ should be saved to unknown_airports.json
    assert mock_unknown_airports_file.exists()
    unknown = json.loads(mock_unknown_airports_file.read_text())
    assert "XYZ" in unknown
    assert unknown["XYZ"]["city"] == "Unknown City"


def test_scrape_ryanair_returns_empty_list_on_api_failure(mock_ryanair_api):
    """
    Tests that an empty list is returned gracefully when the Ryanair API raises an exception.
    """
    mock_ryanair_api.get_cheapest_flights.side_effect = Exception("API error")

    result = scrape_ryanair("EIN")

    assert result == []


def test_scrape_ryanair_handles_malformed_destination_full(mock_ryanair_api):
    """
    Tests that a flight with a malformed destinationFull string falls back to eu_airports.json values.
    """
    mock_ryanair_api.get_cheapest_flights.return_value = [
        _make_mock_ryanair_flight("STN", "NoCommaHere", datetime(2026, 8, 25, 11, 15), 17.99),
    ]

    result = scrape_ryanair("EIN")

    assert len(result) == 1
    assert result[0].destination_iata == "STN"
    # Falls back to eu_airports.json values
    assert result[0].destination_city == EU_AIRPORT_DETAILS["STN"]["city"]
    assert result[0].destination_country == EU_AIRPORT_DETAILS["STN"]["country"]


def test_scrape_ryanair_date_range_is_correct(mock_ryanair_api):
    """
    Tests that the date range passed to the API covers tomorrow to ~3 months + 1 week.
    """
    mock_ryanair_api.get_cheapest_flights.return_value = []

    scrape_ryanair("EIN")

    call_kwargs = mock_ryanair_api.get_cheapest_flights.call_args
    date_from = call_kwargs.kwargs.get("date_from") or call_kwargs.args[1]
    date_to = call_kwargs.kwargs.get("date_to") or call_kwargs.args[2]

    expected_start = (FROZEN_NOW + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    expected_end = expected_start + timedelta(days=SCRAPE_BUFFER_DAYS)

    assert date_from == expected_start
    assert date_to == expected_end


def test_scrape_ryanair_does_not_duplicate_unknown_airports(mock_ryanair_api, mock_unknown_airports_file, mocker):
    """
    Tests that an airport already in unknown_airport_details is not added again.
    """
    mocker.patch("scraper.unknown_airport_details", {"XYZ": {"city": "Existing", "country": "Country"}})
    mock_ryanair_api.get_cheapest_flights.return_value = [
        _make_mock_ryanair_flight("XYZ", "Existing, Country", datetime(2026, 8, 25, 11, 15), 99.99),
    ]

    result = scrape_ryanair("EIN")

    assert result == []
    # unknown_airports.json should not be written since no new unknowns
    assert not mock_unknown_airports_file.exists()
