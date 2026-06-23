import argparse
import re
import json
from datetime import datetime, timedelta

# Load European airports from JSON file
try:
    with open("src/eu_airports.json", "r") as f:
        EU_AIRPORTS = set(json.load(f))
except FileNotFoundError:
    print("Error: eu_airports.json not found. Please ensure it exists in the src/ directory.")
    EU_AIRPORTS = set() # Fallback to empty set to avoid further errors

MAX_TIMERANGE_MONTHS = 3

def validate_departure_airport(airport: str) -> str:
    """
    Validates if the departure airport is a known European IATA code.
    """
    if airport.upper() not in EU_AIRPORTS:
        raise argparse.ArgumentTypeError(
            f"Invalid departure airport: {airport}. Must be a valid European IATA code."
        )
    return airport.upper()

def validate_timerange(timerange: str) -> int:
    """
    Validates the time range format (e.g., '3d', '2w', '1m') and ensures it's within 3 months.
    """
    match = re.fullmatch(r"(\d+)([dwm])", timerange)
    if not match:
        raise argparse.ArgumentTypeError(
            f"Invalid timerange format: {timerange}. Expected format: <number>[d|w|m] (e.g., 3d, 2w, 1m)."
        )

    value = int(match.group(1))
    unit = match.group(2)

    if value <= 0:
        raise argparse.ArgumentTypeError("Timerange value must be a positive number.")

    # Convert to days for comparison
    if unit == 'd':
        total_days = value
    elif unit == 'w':
        total_days = value * 7
    elif unit == 'm':
        # Approximate 1 month as 30 days for maximum range check
        total_days = value * 30

    if total_days > MAX_TIMERANGE_MONTHS * 30: # 3 months * 30 days/month
        raise argparse.ArgumentTypeError(
            f"Timerange exceeds maximum allowed duration of {MAX_TIMERANGE_MONTHS} months."
        )

    return total_days

def validate_budget(budget_str: str) -> int:
    """
    Validates if the budget is a positive integer.
    """
    try:
        budget = int(budget_str)
        if budget <= 0:
            raise argparse.ArgumentTypeError(
                f"Invalid budget: {budget_str}. Must be a positive integer."
            )
        return budget
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid budget format: {budget_str}. Must be an integer."
        )

def parse_arguments() -> argparse.Namespace:
    """
    Parses CLI arguments for flight search.
    """
    parser = argparse.ArgumentParser(
        description="Search for budget flights from a European departure airport within a given time range and budget."
    )

    parser.add_argument(
        "departure_airport",
        nargs="?", # Makes the argument optional
        default="EIN",
        type=validate_departure_airport,
        help="Departure airport IATA code (EU only). Default: EIN"
    )
    parser.add_argument(
        "timerange",
        nargs="?", # Makes the argument optional
        default="1m",
        type=validate_timerange,
        help="Time range for the search (e.g., 3d, 2w, 1m). Max: 3 months. Default: 1m"
    )
    parser.add_argument(
        "budget",
        nargs="?", # Makes the argument optional
        default="50",
        type=validate_budget,
        help="Maximum budget in euros. Must be a positive integer. Default: 50"
    )

    return parser.parse_args()

if __name__ == "__main__":
    args = parse_arguments()
    print(f"Departure Airport: {args.departure_airport}")
    print(f"Time Range: {args.timerange}")
    print(f"Budget: {args.budget}")
