import json
import logging
import os
import time
from datetime import datetime, timedelta

import requests
from ryanair import Ryanair
from ryanair.SessionManager import SessionManager

from models import Flight
from config import COOKIE_CACHE_PATH, COOKIE_TTL_SECONDS, UNKNOWN_AIRPORTS_PATH, SCRAPE_BUFFER_DAYS, RYANAIR_SESSION_URL
from utils import EU_AIRPORT_DETAILS, unknown_airport_details


def _get_ryanair_session() -> requests.Session:
    """Returns a requests.Session with valid Ryanair cookies, using cache if available."""
    session = requests.Session()

    if os.path.exists(COOKIE_CACHE_PATH):
        try:
            with open(COOKIE_CACHE_PATH, "r") as f:
                cached = json.load(f)
            age = time.time() - cached["timestamp"]
            if age < COOKIE_TTL_SECONDS:
                logging.info(f"Using cached Ryanair cookies (created {int(age)}s ago).")
                session.cookies.update(cached["cookies"])
                return session
            logging.info("Cached cookies expired, refreshing...")
        except Exception as e:
            logging.warning(f"Failed to load cached cookies: {e}")

    logging.info("Fetching new Ryanair session cookies...")
    start = time.time()
    session.get(RYANAIR_SESSION_URL, timeout=660)
    logging.info(f"Session cookies fetched in {time.time() - start:.1f}s.")

    try:
        with open(COOKIE_CACHE_PATH, "w") as f:
            json.dump({"cookies": dict(session.cookies), "timestamp": time.time()}, f)
        logging.info("Ryanair cookies cached to disk.")
    except Exception as e:
        logging.warning(f"Failed to cache cookies: {e}")

    return session


def scrape_ryanair(origin_airport: str) -> list[Flight]:
    """
    Scrapes one-way flights from Ryanair for the next 3 months + 1 week buffer.

    Args:
        origin_airport (str): The IATA code of the departure airport.

    Returns:
        List[Flight]: A list of Flight objects.
    """
    logging.info(f"Starting Ryanair scraping for {origin_airport}...")
    flights_data: list[Flight] = []
    unknown_airports: dict[str, dict[str, str]] = {}

    # Calculate date range: tomorrow + 3 months + 1 week buffer
    start_date = (datetime.now() + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=SCRAPE_BUFFER_DAYS)

    try:
        logging.info("Initialising Ryanair client...")
        # Bypass __init__ to avoid SessionManager making a slow HTTP request during initialisation
        api = Ryanair.__new__(Ryanair)
        api.currency = "EUR"
        api._num_queries = 0
        api.session_manager = SessionManager.__new__(SessionManager)
        api.session_manager.session = _get_ryanair_session()
        api.session = api.session_manager.session
        logging.info("Fetching flights from Ryanair API...")
        start = time.time()
        all_ryanair_flights = api.get_cheapest_flights(origin_airport, date_from=start_date, date_to=end_date)
        logging.info(f"Ryanair API returned {len(all_ryanair_flights)} flights in {time.time() - start:.1f}s.")

        for flight in all_ryanair_flights:
            airport_code = flight.destination
            parts = flight.destinationFull.rsplit(", ", 1)
            if len(parts) == 2:
                destination_city, destination_country = parts
            else:
                destination_city = "Unknown"
                destination_country = "Unknown"

            if airport_code in EU_AIRPORT_DETAILS:
                if destination_city == "Unknown" or destination_country == "Unknown":
                    destination_city = EU_AIRPORT_DETAILS[airport_code]["city"]
                    destination_country = EU_AIRPORT_DETAILS[airport_code]["country"]

                flights_data.append(
                    Flight(
                        destination_iata=airport_code,
                        destination_city=destination_city,
                        destination_country=destination_country,
                        airline="Ryanair",
                        departure_time=flight.departureTime,
                        arrival_time=None,  # No arrival_time field in ryanair-py's Flight object
                        price_eur=flight.price,
                    )
                )
            elif airport_code not in unknown_airport_details:
                unknown_airports[airport_code] = {"city": destination_city, "country": destination_country}

        logging.info(f"Successfully scraped {len(flights_data)} Ryanair flights from {origin_airport}.")

    except Exception as e:
        logging.error(f"Failed to scrape Ryanair flights from {origin_airport}: {e}")
        return []  # Return empty list on failure

    if unknown_airports:
        unknown_airport_details.update(unknown_airports)
        try:
            with open(UNKNOWN_AIRPORTS_PATH, "w") as f:
                json.dump(unknown_airport_details, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Failed to save unknown airports: {e}")

    return flights_data


if __name__ == "__main__":
    # Example usage (for testing purposes)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print("Scraping flights from EIN for Ryanair...")
    test_flights = scrape_ryanair("EIN")
    for flight in test_flights[:5]:  # Print first 5 results
        print(flight)
    print(f"\nTotal flights found: {len(test_flights)}")
