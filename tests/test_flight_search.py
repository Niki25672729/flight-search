from datetime import datetime, timedelta

import pytest

from conftest import FROZEN_NOW, SAMPLE_FLIGHT_AMS, SAMPLE_FLIGHT_BCN, make_dummy_flight
from flight_search import filter_flights, main
from models import Flight


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture
def mock_read_cache(mocker):
    """Mocks cache.read_cache."""
    return mocker.patch("flight_search.read_cache")


@pytest.fixture
def mock_write_cache(mocker):
    """Mocks cache.write_cache."""
    return mocker.patch("flight_search.write_cache")


@pytest.fixture
def mock_scrape_ryanair(mocker):
    """Mocks scraper.scrape_ryanair."""
    return mocker.patch("flight_search.scrape_ryanair")


@pytest.fixture
def mock_display_flights(mocker):
    """Mocks display.display_flights."""
    return mocker.patch("flight_search.display_flights")


@pytest.fixture
def mock_parse_arguments(mocker):
    """Mocks cli.parse_arguments to return controlled args."""
    mock_args = mocker.MagicMock()
    mock_args.departure_airport = "EIN"
    mock_args.timerange = 30
    mock_args.budget = 50
    return mocker.patch("flight_search.parse_arguments", return_value=mock_args)


# ---------------------------
# Tests for filter_flights
# ---------------------------

def test_filter_flights_within_budget_and_timerange():
    """Tests that flights within budget and timerange are returned."""
    flights = [
        Flight(
            destination_iata="BCN",
            destination_city="Barcelona",
            destination_country="Spain",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=5),
            arrival_time=None,
            price_eur=40.0,
        ),
    ]

    result = filter_flights(flights, timerange_days=30, budget=50)

    assert len(result) == 1
    assert result[0].destination_iata == "BCN"


def test_filter_flights_excludes_over_budget():
    """Tests that flights exceeding the budget are excluded."""
    flights = [
        Flight(
            destination_iata="BCN",
            destination_city="Barcelona",
            destination_country="Spain",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=5),
            arrival_time=None,
            price_eur=100.0,
        ),
    ]

    result = filter_flights(flights, timerange_days=30, budget=50)

    assert result == []


def test_filter_flights_excludes_outside_timerange():
    """Tests that flights outside the timerange are excluded."""
    flights = [
        Flight(
            destination_iata="BCN",
            destination_city="Barcelona",
            destination_country="Spain",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=60),
            arrival_time=None,
            price_eur=40.0,
        ),
    ]

    result = filter_flights(flights, timerange_days=30, budget=50)

    assert result == []


def test_filter_flights_sorted_by_price():
    """Tests that results are sorted by price ascending."""
    flights = [
        Flight(
            destination_iata="AMS",
            destination_city="Amsterdam",
            destination_country="Netherlands",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=5),
            arrival_time=None,
            price_eur=75.0,
        ),
        Flight(
            destination_iata="BCN",
            destination_city="Barcelona",
            destination_country="Spain",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=3),
            arrival_time=None,
            price_eur=30.0,
        ),
    ]

    result = filter_flights(flights, timerange_days=30, budget=100)

    assert result[0].destination_iata == "BCN"
    assert result[1].destination_iata == "AMS"


def test_filter_flights_includes_exact_budget():
    """Tests that a flight exactly at the budget limit is included."""
    flights = [
        Flight(
            destination_iata="BCN",
            destination_city="Barcelona",
            destination_country="Spain",
            airline="Ryanair",
            departure_time=FROZEN_NOW + timedelta(days=5),
            arrival_time=None,
            price_eur=50.0,
        ),
    ]

    result = filter_flights(flights, timerange_days=30, budget=50)

    assert len(result) == 1


def test_filter_flights_empty_list():
    """Tests that an empty list returns an empty list."""
    result = filter_flights([], timerange_days=30, budget=50)

    assert result == []


# ---------------------------
# Tests for main
# ---------------------------

def test_main_uses_cache_when_available(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that scraper is not called when cache hit occurs."""
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN]

    main()

    mock_read_cache.assert_called_once_with("EIN")
    mock_scrape_ryanair.assert_not_called()
    mock_write_cache.assert_not_called()
    mock_display_flights.assert_called_once()


def test_main_scrapes_and_caches_on_cache_miss(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that scraper is called and results are cached on cache miss."""
    mock_read_cache.return_value = None
    mock_scrape_ryanair.return_value = [SAMPLE_FLIGHT_BCN]

    main()

    mock_scrape_ryanair.assert_called_once_with("EIN")
    mock_write_cache.assert_called_once_with("EIN", [SAMPLE_FLIGHT_BCN])
    mock_display_flights.assert_called_once()


def test_main_filters_by_budget(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that only flights within budget are passed to display."""
    cheap_flight = Flight(
        destination_iata="BCN",
        destination_city="Barcelona",
        destination_country="Spain",
        airline="Ryanair",
        departure_time=FROZEN_NOW + timedelta(days=5),
        arrival_time=None,
        price_eur=40.0,
    )
    expensive_flight = Flight(
        destination_iata="AMS",
        destination_city="Amsterdam",
        destination_country="Netherlands",
        airline="Ryanair",
        departure_time=FROZEN_NOW + timedelta(days=5),
        arrival_time=None,
        price_eur=200.0,
    )
    mock_read_cache.return_value = [cheap_flight, expensive_flight]

    main()

    displayed = mock_display_flights.call_args[0][0]
    assert len(displayed) == 1
    assert displayed[0].destination_iata == "BCN"


def test_main_filters_by_timerange(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that only flights within the timerange are passed to display."""
    near_flight = Flight(
        destination_iata="BCN",
        destination_city="Barcelona",
        destination_country="Spain",
        airline="Ryanair",
        departure_time=FROZEN_NOW + timedelta(days=5),
        arrival_time=None,
        price_eur=40.0,
    )
    far_flight = Flight(
        destination_iata="AMS",
        destination_city="Amsterdam",
        destination_country="Netherlands",
        airline="Ryanair",
        departure_time=FROZEN_NOW + timedelta(days=60),
        arrival_time=None,
        price_eur=40.0,
    )
    mock_read_cache.return_value = [near_flight, far_flight]

    main()

    displayed = mock_display_flights.call_args[0][0]
    assert len(displayed) == 1
    assert displayed[0].destination_iata == "BCN"


def test_main_displays_empty_when_no_flights_match(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that display is called with empty list when no flights match criteria."""
    mock_read_cache.return_value = []

    main()

    mock_display_flights.assert_called_once_with([])
