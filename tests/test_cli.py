import pytest
import argparse
from datetime import date, timedelta
from unittest.mock import patch

from cli import (
    parse_arguments,
    validate_budget,
    validate_destination,
    validate_place,
    validate_sort,
    validate_timerange,
)
from config import MAX_TIMERANGE_MONTHS
from conftest import FROZEN_NOW

TODAY = FROZEN_NOW.date()


# ---------------------------
# Tests for validate_place
# ---------------------------


def test_validate_place_iata():
    assert validate_place("EIN") == ("iata", ["EIN"])
    assert validate_place("cdg") == ("iata", ["CDG"])


def test_validate_place_city():
    assert validate_place("Eindhoven") == ("name", ["EIN"])
    assert validate_place("barcelona") == ("name", ["BCN"])


def test_validate_place_city_with_multiple_airports():
    kind, airports = validate_place("Paris")
    assert kind == "name"
    assert sorted(airports) == ["CDG", "ORY"]


def test_validate_place_country():
    kind, airports = validate_place("Netherlands")
    assert kind == "name"
    assert "EIN" in airports and "AMS" in airports


def test_validate_place_unique_substring():
    """A partial name resolves when it matches exactly one city/country (e.g. 'Kingdom' → United Kingdom)."""
    assert validate_place("Kingdom") == validate_place("United Kingdom")


def test_validate_place_ambiguous_substring():
    """A partial name matching several places must fail loudly, listing the candidates."""
    with pytest.raises(argparse.ArgumentTypeError, match="Ambiguous place: land.*Finland.*Ireland"):
        validate_place("land")


def test_validate_place_typo_suggests_closest():
    """A misspelled name is never silently guessed — it fails with a 'did you mean' hint."""
    with pytest.raises(argparse.ArgumentTypeError, match="Did you mean: United Kingdom"):
        validate_place("United Kindon")


def test_validate_place_unknown():
    for invalid in ["XYZ", "JFK"]:
        with pytest.raises(argparse.ArgumentTypeError, match=f"Invalid place: {invalid}"):
            validate_place(invalid)


# ---------------------------
# Tests for validate_destination
# ---------------------------


def test_validate_destination_none_means_any():
    assert validate_destination("none") == ("any", [])
    assert validate_destination("NONE") == ("any", [])


def test_validate_destination_place():
    assert validate_destination("BCN") == ("iata", ["BCN"])
    assert validate_destination("Barcelona") == ("name", ["BCN"])


# ---------------------------
# Tests for validate_timerange
# ---------------------------


def test_validate_timerange_range():
    assert validate_timerange("2026-07-01...2026-07-30") == (date(2026, 7, 1), date(2026, 7, 30))


def test_validate_timerange_single_date_means_that_day():
    assert validate_timerange("2026-07-05") == (date(2026, 7, 5), date(2026, 7, 5))


def test_validate_timerange_today_is_valid():
    assert validate_timerange(TODAY.isoformat()) == (TODAY, TODAY)


def test_validate_timerange_invalid_format():
    for invalid in [
        "20260701",
        "2026-07-01..2026-07-30",
        "2026-07-01...2026-07-15...2026-07-30",
        "2026-07-01...",
        "2026-13-01",
        "1m",
    ]:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid timerange format"):
            validate_timerange(invalid)


def test_validate_timerange_start_after_end():
    with pytest.raises(argparse.ArgumentTypeError, match="start date .* is after end date"):
        validate_timerange("2026-07-30...2026-07-01")


def test_validate_timerange_in_the_past():
    with pytest.raises(argparse.ArgumentTypeError, match="is in the past"):
        validate_timerange("2026-06-01...2026-06-20")


def test_validate_timerange_start_beyond_max_range():
    beyond = TODAY + timedelta(days=MAX_TIMERANGE_MONTHS * 30 + 1)
    with pytest.raises(argparse.ArgumentTypeError, match="beyond the maximum search range"):
        validate_timerange(f"{beyond.isoformat()}...{(beyond + timedelta(days=5)).isoformat()}")


# ---------------------------
# Tests for validate_budget
# ---------------------------


def test_validate_budget_valid():
    assert validate_budget("1") == 1
    assert validate_budget("100") == 100
    assert validate_budget("5000") == 5000


def test_validate_budget_invalid_format():
    for invalid, msg in [
        ("abc", "Invalid budget format: abc"),
        ("1.5", "Invalid budget format: 1.5"),
        ("", "Invalid budget format:"),
    ]:
        with pytest.raises(argparse.ArgumentTypeError, match=msg):
            validate_budget(invalid)


def test_validate_budget_zero():
    with pytest.raises(argparse.ArgumentTypeError, match="Must be a positive integer"):
        validate_budget("0")


def test_validate_budget_negative():
    with pytest.raises(argparse.ArgumentTypeError, match="Must be a positive integer"):
        validate_budget("-10")


# ---------------------------
# Tests for validate_sort
# ---------------------------


def test_validate_sort_valid():
    assert validate_sort("date") == "date"
    assert validate_sort("price") == "price"
    assert validate_sort("PRICE") == "price"


def test_validate_sort_invalid():
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid sort mode"):
        validate_sort("cheapest")


# ---------------------------
# Tests for parse_arguments
# ---------------------------


def test_parse_arguments_defaults():
    with patch("sys.argv", ["flight_search.py"]):
        args = parse_arguments()
        assert args.departure == ("iata", ["EIN"])
        assert args.destination == ("any", [])
        assert args.timerange == (TODAY, TODAY + timedelta(days=30))
        assert args.budget == 50
        assert args.sort == "date"


def test_parse_arguments_all_valid_iata():
    with patch("sys.argv", ["flight_search.py", "AMS", "BCN", "2026-07-01...2026-07-15", "150", "price"]):
        args = parse_arguments()
        assert args.departure == ("iata", ["AMS"])
        assert args.destination == ("iata", ["BCN"])
        assert args.timerange == (date(2026, 7, 1), date(2026, 7, 15))
        assert args.budget == 150
        assert args.sort == "price"


def test_parse_arguments_all_valid_names():
    with patch("sys.argv", ["flight_search.py", "Paris", "Spain", "2026-07-05", "80"]):
        args = parse_arguments()
        assert args.departure[0] == "name"
        assert sorted(args.departure[1]) == ["CDG", "ORY"]
        assert args.destination[0] == "name"
        assert "BCN" in args.destination[1]


def test_parse_arguments_partial_valid():
    with patch("sys.argv", ["flight_search.py", "LHR"]):
        args = parse_arguments()
        assert args.departure == ("iata", ["LHR"])
        assert args.destination == ("any", [])
        assert args.budget == 50


def test_parse_arguments_mixed_kinds_rejected():
    """IATA and city/country names must not be mixed between departure and destination."""
    with patch("sys.argv", ["flight_search.py", "EIN", "Barcelona"]):
        with pytest.raises(SystemExit):
            parse_arguments()

    with patch("sys.argv", ["flight_search.py", "Eindhoven", "BCN"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_none_destination_allowed_with_either_kind():
    with patch("sys.argv", ["flight_search.py", "Eindhoven", "none"]):
        args = parse_arguments()
        assert args.destination == ("any", [])

    with patch("sys.argv", ["flight_search.py", "EIN", "none"]):
        args = parse_arguments()
        assert args.destination == ("any", [])


def test_parse_arguments_invalid_departure():
    with patch("sys.argv", ["flight_search.py", "JFK"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_invalid_timerange():
    for invalid in ["2x", "1m", "2026-07-30...2026-07-01"]:
        with patch("sys.argv", ["flight_search.py", "EIN", "none", invalid]):
            with pytest.raises(SystemExit):
                parse_arguments()


def test_parse_arguments_invalid_budget():
    for invalid in ["abc", "-50", "0"]:
        with patch("sys.argv", ["flight_search.py", "EIN", "none", "2026-07-01", invalid]):
            with pytest.raises(SystemExit):
                parse_arguments()


def test_parse_arguments_invalid_sort():
    with patch("sys.argv", ["flight_search.py", "EIN", "none", "2026-07-01", "50", "cheapest"]):
        with pytest.raises(SystemExit):
            parse_arguments()
