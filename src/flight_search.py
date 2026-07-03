import logging

from cache import read_cache, write_cache
from cli import parse_arguments
from datetime import datetime, timedelta
from display import display_flights
from models import Flight
from scraper import scrape_ryanair

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def filter_flights(flights: list[Flight], timerange_days: int, budget: int) -> list[Flight]:
    """
    Filters flights by departure time and budget, sorted by price ascending.

    Args:
        flights: List of Flight objects to filter.
        timerange_days: Number of days from today to include.
        budget: Maximum price in euros.

    Returns:
        Filtered and sorted list of Flight objects.
    """
    now = datetime.now()
    cutoff = now + timedelta(days=timerange_days)

    return sorted(
        [
            flight
            for flight in flights
            if flight.price_eur <= budget and now <= flight.departure_time <= cutoff
        ],
        key=lambda f: (f.destination_country, f.destination_city, f.departure_time)
    )


def main() -> None:
    """Entry point — orchestrates CLI, cache, scraper and display."""
    args = parse_arguments()

    departure_airport: str = args.departure_airport
    timerange_days: int = args.timerange
    budget: int = args.budget

    logging.info(f"Searching flights from {departure_airport} within {timerange_days} days and €{budget} budget...")

    flights = read_cache(departure_airport)

    if flights is None:
        logging.info(f"No cache found for {departure_airport}, scraping...")
        flights = scrape_ryanair(departure_airport)
        write_cache(departure_airport, flights)

    filtered = filter_flights(flights, timerange_days, budget)
    logging.info(f"Found {len(filtered)} flights matching criteria.")

    display_flights(filtered)


if __name__ == "__main__":
    main()
