import pytest
import argparse
from unittest.mock import patch

from cli import (
    validate_departure_airport,
    validate_timerange,
    validate_budget,
    parse_arguments,
    EU_AIRPORTS,
    MAX_TIMERANGE_MONTHS
)


# ---------------------------
# validate_departure_airport
# ---------------------------

def test_validate_departure_airport_valid():
    assert validate_departure_airport("EIN") == "EIN"
    assert validate_departure_airport("LHR") == "LHR"
    assert validate_departure_airport("cdg") == "CDG"


def test_validate_departure_airport_invalid_iata():
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid departure airport: XYZ"):
        validate_departure_airport("XYZ")


def test_validate_departure_airport_non_european():
    # NOTE: avoid mutating global state if possible
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid departure airport: JFK"):
        validate_departure_airport("JFK")


# ---------------------------
# validate_timerange
# ---------------------------

def test_validate_timerange_valid_days():
    assert validate_timerange("1d") == 1
    assert validate_timerange("30d") == 30


def test_validate_timerange_valid_weeks():
    assert validate_timerange("1w") == 7
    assert validate_timerange("12w") == 84


def test_validate_timerange_valid_months():
    assert validate_timerange("1m") == 30
    assert validate_timerange(f"{MAX_TIMERANGE_MONTHS}m") == MAX_TIMERANGE_MONTHS * 30


def test_validate_timerange_invalid_format():
    for invalid in ["3", "3x", "d3", "1.5m"]:
        with pytest.raises(argparse.ArgumentTypeError, match="Invalid timerange format"):
            validate_timerange(invalid)


def test_validate_timerange_zero_value():
    for invalid in ["0d", "0w", "0m"]:
        with pytest.raises(argparse.ArgumentTypeError, match="Timerange value must be a positive number."):
            validate_timerange(invalid)


def test_validate_timerange_negative_value():
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid timerange format"):
        validate_timerange("-1d")


def test_validate_timerange_exceeds_max_months():
    with pytest.raises(argparse.ArgumentTypeError, match="Timerange exceeds maximum allowed duration of 3 months."):
        validate_timerange(f"{MAX_TIMERANGE_MONTHS * 30 + 1}d")

    with pytest.raises(argparse.ArgumentTypeError, match="Timerange exceeds maximum allowed duration of 3 months."):
        validate_timerange(f"{MAX_TIMERANGE_MONTHS * 4 + 1}w")

    with pytest.raises(argparse.ArgumentTypeError, match="Timerange exceeds maximum allowed duration of 3 months."):
        validate_timerange(f"{MAX_TIMERANGE_MONTHS + 1}m")


# ---------------------------
# validate_budget
# ---------------------------

def test_validate_budget_valid():
    assert validate_budget("1") == 1
    assert validate_budget("100") == 100
    assert validate_budget("5000") == 5000


def test_validate_budget_invalid_format():
    for invalid, msg in [
        ("abc", "Invalid budget format: abc"),
        ("1.5", "Invalid budget format: 1.5"),
        ("", "Invalid budget format:")
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
# parse_arguments
# ---------------------------

def test_parse_arguments_defaults():
    with patch("sys.argv", ["flight_search.py"]):
        args = parse_arguments()
        assert args.departure_airport == "EIN"
        assert args.timerange == 30
        assert args.budget == 50


def test_parse_arguments_all_valid():
    with patch("sys.argv", ["flight_search.py", "AMS", "2w", "150"]):
        args = parse_arguments()
        assert args.departure_airport == "AMS"
        assert args.timerange == 14
        assert args.budget == 150


def test_parse_arguments_partial_valid():
    with patch("sys.argv", ["flight_search.py", "LHR"]):
        args = parse_arguments()
        assert args.departure_airport == "LHR"
        assert args.timerange == 30
        assert args.budget == 50

    with patch("sys.argv", ["flight_search.py", "CDG", "3w"]):
        args = parse_arguments()
        assert args.departure_airport == "CDG"
        assert args.timerange == 21
        assert args.budget == 50


def test_parse_arguments_invalid_departure_airport():
    with patch("sys.argv", ["flight_search.py", "JFK"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_invalid_timerange_format():
    with patch("sys.argv", ["flight_search.py", "EIN", "2x"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_invalid_timerange_value():
    with patch("sys.argv", ["flight_search.py", "EIN", "0m"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_timerange_exceeds_max():
    with patch("sys.argv", ["flight_search.py", "EIN", f"{MAX_TIMERANGE_MONTHS + 1}m"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_invalid_budget_format():
    with patch("sys.argv", ["flight_search.py", "EIN", "1m", "abc"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_invalid_budget_value():
    with patch("sys.argv", ["flight_search.py", "EIN", "1m", "-50"]):
        with pytest.raises(SystemExit):
            parse_arguments()

    with patch("sys.argv", ["flight_search.py", "EIN", "1m", "0"]):
        with pytest.raises(SystemExit):
            parse_arguments()


def test_parse_arguments_mixed_invalid():
    with patch("sys.argv", ["flight_search.py", "LAX", "2m", "100"]):
        with pytest.raises(SystemExit):
            parse_arguments()

    with patch("sys.argv", ["flight_search.py", "AMS", "invalid_time", "100"]):
        with pytest.raises(SystemExit):
            parse_arguments()