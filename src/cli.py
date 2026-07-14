import argparse
from datetime import date, datetime, timedelta
from difflib import get_close_matches

from config import CLI_DATE_FORMAT, DEFAULT_TIMERANGE_DAYS, MAX_TIMERANGE_MONTHS, SORT_MODES, TIMERANGE_SEPARATOR
from utils import EU_AIRPORTS, NAME_TO_AIRPORTS, _utc_now


# ---------------------------
# Constants
# ---------------------------

ANY_DESTINATION = "none"


# ---------------------------
# Helpers
# ---------------------------


def _timerange_format_error(timerange: str) -> str:
    return (
        f"Invalid timerange format: {timerange}. "
        f"Expected yyyy-mm-dd{TIMERANGE_SEPARATOR}yyyy-mm-dd or a single yyyy-mm-dd."
    )


def _default_timerange() -> str:
    today = _utc_now().date()
    end = today + timedelta(days=DEFAULT_TIMERANGE_DAYS)
    return f"{today.isoformat()}{TIMERANGE_SEPARATOR}{end.isoformat()}"


def _resolve_name(place: str) -> list[str]:
    """Resolves a city/country name to airport codes: exact match, then unique substring, else suggestions."""
    query = place.lower()
    if query in NAME_TO_AIRPORTS:
        return NAME_TO_AIRPORTS[query]

    candidates = sorted(name for name in NAME_TO_AIRPORTS if query in name)
    if len(candidates) == 1:
        return NAME_TO_AIRPORTS[candidates[0]]
    if candidates:
        raise argparse.ArgumentTypeError(
            f"Ambiguous place: {place}. Matches: {', '.join(name.title() for name in candidates)}."
        )

    suggestions = get_close_matches(query, NAME_TO_AIRPORTS)
    hint = f" Did you mean: {', '.join(name.title() for name in suggestions)}?" if suggestions else ""
    raise argparse.ArgumentTypeError(
        f"Invalid place: {place}. Must be a European airport IATA code, city, or country.{hint}"
    )


# ---------------------------
# Validators
# ---------------------------


def validate_place(place: str) -> tuple[str, list[str]]:
    """Classifies a place as an IATA code or a city/country name, resolved to its airport codes."""
    if place.upper() in EU_AIRPORTS:
        return "iata", [place.upper()]
    return "name", _resolve_name(place)


def validate_destination(destination: str) -> tuple[str, list[str]]:
    """Like validate_place, but also accepts 'none' meaning any destination."""
    if destination.lower() == ANY_DESTINATION:
        return "any", []
    return validate_place(destination)


def validate_timerange(timerange: str) -> tuple[date, date]:
    """Validates a 'yyyy-mm-dd...yyyy-mm-dd' date range, or a single 'yyyy-mm-dd' meaning that one day."""
    parts = timerange.split(TIMERANGE_SEPARATOR)
    if len(parts) == 1:
        start_str = end_str = parts[0]
    elif len(parts) == 2:
        start_str, end_str = parts
    else:
        raise argparse.ArgumentTypeError(_timerange_format_error(timerange))

    try:
        start = datetime.strptime(start_str, CLI_DATE_FORMAT).date()
        end = datetime.strptime(end_str, CLI_DATE_FORMAT).date()
    except ValueError:
        raise argparse.ArgumentTypeError(_timerange_format_error(timerange))

    today = _utc_now().date()
    if start > end:
        raise argparse.ArgumentTypeError(f"Invalid timerange: start date {start} is after end date {end}.")
    if end < today:
        raise argparse.ArgumentTypeError(f"Invalid timerange: end date {end} is in the past.")
    if start > today + timedelta(days=MAX_TIMERANGE_MONTHS * 30):
        raise argparse.ArgumentTypeError(
            f"Invalid timerange: start date {start} is beyond the maximum search range "
            f"of {MAX_TIMERANGE_MONTHS} months."
        )

    return start, end


def validate_budget(budget_str: str) -> int:
    """
    Validates if the budget is a positive integer.
    """
    try:
        budget = int(budget_str)
        if budget <= 0:
            raise argparse.ArgumentTypeError(f"Invalid budget: {budget_str}. Must be a positive integer.")
        return budget
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid budget format: {budget_str}. Must be an integer.")


def validate_sort(sort: str) -> str:
    """Validates the sort mode."""
    normalised = sort.lower()
    if normalised not in SORT_MODES:
        raise argparse.ArgumentTypeError(f"Invalid sort mode: {sort}. Expected one of: {', '.join(SORT_MODES)}.")
    return normalised


# ---------------------------
# Public API
# ---------------------------


def parse_arguments() -> argparse.Namespace:
    """
    Parses CLI arguments for flight search.
    """
    parser = argparse.ArgumentParser(
        description="Search for budget flights from a European departure airport, city, or country "
        "within a given date range and budget."
    )

    parser.add_argument(
        "departure",
        nargs="?",  # Makes the argument optional
        default="EIN",
        type=validate_place,
        help="Departure airport IATA code, city, or country (EU only). Default: EIN",
    )
    parser.add_argument(
        "destination",
        nargs="?",  # Makes the argument optional
        default=ANY_DESTINATION,
        type=validate_destination,
        help="Destination airport IATA code, city, or country; 'none' means any destination. "
        "Must be the same kind as departure (IATA with IATA, name with name). Default: none",
    )
    parser.add_argument(
        "timerange",
        nargs="?",  # Makes the argument optional
        default=_default_timerange(),
        type=validate_timerange,
        help=f"Date range yyyy-mm-dd{TIMERANGE_SEPARATOR}yyyy-mm-dd, or a single yyyy-mm-dd for that one day. "
        f"Max: {MAX_TIMERANGE_MONTHS} months ahead. Default: today{TIMERANGE_SEPARATOR}today+{DEFAULT_TIMERANGE_DAYS}d",
    )
    parser.add_argument(
        "budget",
        nargs="?",  # Makes the argument optional
        default="50",
        type=validate_budget,
        help="Maximum budget in euros. Must be a positive integer. Default: 50",
    )
    parser.add_argument(
        "sort",
        nargs="?",  # Makes the argument optional
        default="date",
        type=validate_sort,
        help="Sort mode: 'date' (destination, then date) or 'price' (destination, then price, then date). "
        "Default: date",
    )

    args = parser.parse_args()

    departure_kind, _ = args.departure
    destination_kind, _ = args.destination
    if destination_kind != "any" and departure_kind != destination_kind:
        parser.error("Departure and destination must be the same kind: both IATA codes, or both city/country names.")

    return args


if __name__ == "__main__":
    args = parse_arguments()
    print(f"Departure: {args.departure}")
    print(f"Destination: {args.destination}")
    print(f"Time Range: {args.timerange}")
    print(f"Budget: {args.budget}")
    print(f"Sort: {args.sort}")
