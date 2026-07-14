import pytest
from dataclasses import replace
from datetime import timedelta

from config import DATE_FORMAT
from conftest import FROZEN_NOW, SAMPLE_FLIGHT_AMS, SAMPLE_FLIGHT_BCN
from flight_search import filter_flights, main

TODAY = FROZEN_NOW.date()
END_30 = TODAY + timedelta(days=30)
END_90 = TODAY + timedelta(days=90)


# ---------------------------
# Fixtures
# ---------------------------


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
    mock_args.departure = ("iata", ["EIN"])
    mock_args.destination = ("any", [])
    mock_args.timerange = (TODAY, END_30)
    mock_args.budget = 50
    mock_args.sort = "date"
    return mocker.patch("flight_search.parse_arguments", return_value=mock_args)


# ---------------------------
# Tests for filter_flights
# ---------------------------


def test_filter_flights_within_budget_and_timerange():
    """Tests that flights within budget and timerange are returned."""
    flights = [SAMPLE_FLIGHT_BCN]

    result = filter_flights(flights, start=TODAY, end=END_30, budget=60)

    assert len(result) == 1
    assert result[0].destination_iata == "BCN"


def test_filter_flights_excludes_over_budget():
    """Tests that flights exceeding the budget are excluded."""
    flights = [SAMPLE_FLIGHT_BCN]

    result = filter_flights(flights, start=TODAY, end=END_30, budget=30)

    assert result == []


def test_filter_flights_excludes_outside_timerange():
    """Tests that flights outside the date range are excluded."""
    flights = [SAMPLE_FLIGHT_AMS]  # departs FROZEN_NOW + 60 days

    result = filter_flights(flights, start=TODAY, end=END_30, budget=250)

    assert result == []


def test_filter_flights_excludes_already_departed_today():
    """Tests that a start date of today doesn't resurface flights that already departed earlier today."""
    departed = replace(SAMPLE_FLIGHT_BCN, departure_time=FROZEN_NOW - timedelta(hours=2))

    result = filter_flights([departed], start=TODAY, end=END_30, budget=250)

    assert result == []


def test_filter_flights_single_day_range_is_inclusive():
    """Tests that start == end covers that whole day (the single-date CLI shorthand)."""
    result = filter_flights(
        [SAMPLE_FLIGHT_BCN],
        start=SAMPLE_FLIGHT_BCN.departure_time.date(),
        end=SAMPLE_FLIGHT_BCN.departure_time.date(),
        budget=60,
    )

    assert len(result) == 1


def test_filter_flights_by_destination():
    """Tests that only flights to the requested destination airports are kept."""
    flights = [SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS]

    result = filter_flights(flights, start=TODAY, end=END_90, budget=250, destination_airports={"AMS"})

    assert len(result) == 1
    assert result[0].destination_iata == "AMS"


def test_filter_flights_sorted_by_country_city_time():
    """Tests that results are sorted by (country, city, departure_time) ascending, per ARCHITECTURE.md."""
    flights = [SAMPLE_FLIGHT_AMS, SAMPLE_FLIGHT_BCN]

    result = filter_flights(flights, start=TODAY, end=END_90, budget=250)

    # "Netherlands" sorts before "Spain" alphabetically, regardless of price.
    assert result[0].destination_iata == "AMS"
    assert result[1].destination_iata == "BCN"


def test_filter_flights_price_sort_orders_cheapest_first_within_destination():
    """Tests that sort='price' ranks a cheaper later flight above an earlier pricier one."""
    pricier_earlier = SAMPLE_FLIGHT_BCN  # €50 on 2026-07-01
    cheaper_later = replace(SAMPLE_FLIGHT_BCN, price_eur=20.0, departure_time=FROZEN_NOW + timedelta(days=12))

    result = filter_flights([pricier_earlier, cheaper_later], start=TODAY, end=END_30, budget=60, sort="price")

    assert [f.price_eur for f in result] == [20.0, 50.0]


def test_filter_flights_price_sort_still_groups_by_destination():
    """Tests that sort='price' keeps destination grouping — price only reorders within a destination."""
    cheap_spain = replace(SAMPLE_FLIGHT_BCN, price_eur=10.0)

    result = filter_flights([cheap_spain, SAMPLE_FLIGHT_AMS], start=TODAY, end=END_90, budget=250, sort="price")

    # Netherlands still sorts before Spain even though the Spain flight is cheaper.
    assert result[0].destination_iata == "AMS"
    assert result[1].destination_iata == "BCN"


def test_filter_flights_includes_exact_budget():
    """Tests that a flight exactly at the budget limit is included."""
    flights = [SAMPLE_FLIGHT_BCN]

    result = filter_flights(flights, start=TODAY, end=END_30, budget=50)

    assert len(result) == 1


def test_filter_flights_empty_list():
    """Tests that an empty list returns an empty list."""
    result = filter_flights([], start=TODAY, end=END_30, budget=50)

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

    mock_read_cache.assert_called_once_with("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))
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

    mock_scrape_ryanair.assert_called_once_with("EIN", FROZEN_NOW.strftime(DATE_FORMAT))
    mock_write_cache.assert_called_once_with("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))
    mock_display_flights.assert_called_once()


def test_main_searches_each_airport_of_a_multi_airport_place(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that a city/country departure searches every one of its airports and labels rows with the origin."""
    mock_parse_arguments.return_value.departure = ("name", ["CDG", "ORY"])
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN]

    main()

    assert mock_read_cache.call_count == 2
    origins = [call.args[0] for call in mock_read_cache.call_args_list]
    assert origins == ["CDG", "ORY"]
    assert mock_display_flights.call_args.kwargs == {"show_origin": True}


def test_main_single_airport_search_hides_origin_column(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that a single-airport search doesn't show the redundant Departure column."""
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN]

    main()

    assert mock_display_flights.call_args.kwargs == {"show_origin": False}


def test_main_filters_by_destination(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that only flights to the requested destination are passed to display."""
    mock_parse_arguments.return_value.destination = ("iata", ["AMS"])
    mock_parse_arguments.return_value.timerange = (TODAY, END_90)
    mock_parse_arguments.return_value.budget = 250
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS]

    main()

    displayed = mock_display_flights.call_args[0][0]
    assert len(displayed) == 1
    assert displayed[0].destination_iata == "AMS"


def test_main_filters_by_budget(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that only flights within budget are passed to display."""
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS]

    main()

    displayed = mock_display_flights.call_args[0][0]
    assert len(displayed) == 1
    assert displayed[0].destination_iata == "BCN"


def test_main_filters_by_timerange(
    mock_parse_arguments, mock_read_cache, mock_write_cache, mock_scrape_ryanair, mock_display_flights
):
    """Tests that only flights within the date range are passed to display."""
    mock_read_cache.return_value = [SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS]

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

    mock_display_flights.assert_called_once_with([], show_origin=False)
