import logging
from datetime import date, datetime, time

from cache import read_cache, write_cache
from cli import parse_arguments
from config import DATE_FORMAT
from display import display_flights
from models import Flight
from scraper import scrape_ryanair
from utils import _utc_now

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def filter_flights(
    flights: list[Flight],
    start: date,
    end: date,
    budget: int,
    destination_airports: set[str] | None = None,
    sort: str = "date",
) -> list[Flight]:
    """
    Filters flights by date range, budget, and destination, sorted per the sort mode.

    Args:
        flights: List of Flight objects to filter.
        start: First departure date to include.
        end: Last departure date to include (inclusive).
        budget: Maximum price in euros.
        destination_airports: Destination IATA codes to keep; None means any destination.
        sort: 'date' sorts by destination then departure time; 'price' by destination, price, then time.

    Returns:
        Filtered and sorted list of Flight objects.
    """
    # Clamp the lower bound to now so today's already-departed flights are excluded
    range_start = max(datetime.combine(start, time.min), _utc_now())
    range_end = datetime.combine(end, time.max)

    matched = [
        flight
        for flight in flights
        if flight.price_eur <= budget
        and range_start <= flight.departure_time <= range_end
        and (destination_airports is None or flight.destination_iata in destination_airports)
    ]

    if sort == "price":
        return sorted(matched, key=lambda f: (f.destination_country, f.destination_city, f.price_eur, f.departure_time))
    return sorted(matched, key=lambda f: (f.destination_country, f.destination_city, f.departure_time))


def main() -> None:
    """Entry point — orchestrates CLI, cache, scraper and display."""
    args = parse_arguments()

    _, origin_airports = args.departure
    destination_kind, destination_codes = args.destination
    start, end = args.timerange
    budget: int = args.budget
    sort: str = args.sort
    destination_airports = None if destination_kind == "any" else set(destination_codes)

    destination_label = "anywhere" if destination_airports is None else "/".join(sorted(destination_airports))
    logging.info(
        f"Searching flights from {'/'.join(origin_airports)} to {destination_label} "
        f"between {start} and {end} within €{budget} budget..."
    )

    today = _utc_now().strftime(DATE_FORMAT)
    flights: list[Flight] = []
    for origin in origin_airports:
        origin_flights = read_cache(origin, "ryanair", today)
        if origin_flights is None:
            logging.info(f"No cache found for {origin}, scraping...")
            origin_flights = scrape_ryanair(origin, today)
            write_cache(origin, "ryanair", origin_flights, today)
        flights.extend(origin_flights)

    filtered = filter_flights(flights, start, end, budget, destination_airports, sort)
    logging.info(f"Found {len(filtered)} flights matching criteria.")

    display_flights(filtered, show_origin=len(origin_airports) > 1)


if __name__ == "__main__":
    main()
