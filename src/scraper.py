import json
import logging
import time
from datetime import date, datetime, timedelta

from ryanair import Ryanair

from cache import _CasExhausted, load_retry_queue, update_retry_queue
from models import Flight
from config import (
    AMBIGUOUS_AIRPORTS_PATH,
    UNKNOWN_AIRPORTS_PATH,
    DATE_FORMAT,
    SCRAPE_BUFFER_DAYS,
    SCRAPE_ORIGINS,
    RYANAIR_CURRENCY,
    SCRAPE_REQUEST_DELAY_SECONDS,
)
from utils import EU_AIRPORT_DETAILS, IGNORED_AIRPORTS, ambiguous_airport_details, unknown_airport_details, _utc_now


# ---------------------------
# Helpers
# ---------------------------


# --- City/country parsing ---


def _strip_city_annotation(city: str | None) -> str | None:
    """
    "Reus (Barcelona)" => "Reus"
    """
    if not city:
        return city
    return city.split(" (")[0].strip()


def _extract_city_country_from_full(destination_full: str | None) -> tuple[str | None, str | None]:
    """
    ("London Stansted, United Kingdom") => ("London Stansted", "United Kingdom")
    """
    if not destination_full or ", " not in destination_full:
        return None, None
    city, country = destination_full.rsplit(", ", 1)
    return _strip_city_annotation(city), country


# --- Airport classification ---


def _classify_airport(
    code: str,
    api_city: str | None,
    api_country: str | None,
    ambiguous_found: dict[str, dict[str, str]],
    unknown_found: dict[str, dict[str, str]],
) -> tuple[str, str] | None:
    """
    Classifies a single destination code and returns the (city, country) to use for building Flight
    objects, or None if this destination should not produce any Flight this run.

    Rule:
    - In EU_AIRPORTS => (city, country)
        - If city or country info is not the same as in EU_AIRPORTS => mark in AMBIGUOUS_AIRPORTS
    - In IGNORED_AIRPORTS => None
    - Others => None
        - Update UNKNOWN_AIRPORTS
    """
    if code in EU_AIRPORT_DETAILS:
        static_city = EU_AIRPORT_DETAILS[code]["city"]
        static_country = EU_AIRPORT_DETAILS[code]["country"]

        disagrees = (api_city and api_city != static_city) or (api_country and api_country != static_country)
        if disagrees and code not in ambiguous_airport_details and code not in ambiguous_found:
            ambiguous_found[code] = {"city": api_city or static_city, "country": api_country or static_country}

        return (api_city or static_city, api_country or static_country)

    if code in IGNORED_AIRPORTS:
        return None

    if code not in unknown_airport_details and code not in unknown_found:
        unknown_found[code] = {"city": api_city or "Unknown", "country": api_country or "Unknown"}
    return None


def _save_unknown_and_ambiguous_findings(
    ambiguous_found: dict[str, dict[str, str]], unknown_found: dict[str, dict[str, str]]
) -> None:
    """Persists any new ambiguous/unknown airport findings accumulated during a scrape run. Best-effort."""
    if unknown_found:
        unknown_airport_details.update(unknown_found)
        try:
            with open(UNKNOWN_AIRPORTS_PATH, "w") as f:
                json.dump(unknown_airport_details, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Failed to save unknown airports: {e}")

    if ambiguous_found:
        ambiguous_airport_details.update(ambiguous_found)
        try:
            with open(AMBIGUOUS_AIRPORTS_PATH, "w") as f:
                json.dump(ambiguous_airport_details, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Failed to save ambiguous airports: {e}")


# --- Retry ---


def _record_failed_query(origin_iata: str, query_date: date, airline: str, run_date: str) -> None:
    """
    Appends a failed daily query to this origin's retry queue, keyed under run_date. Skips if
    already queued. Uses cache.update_retry_queue (CAS) rather than a plain load+save, so a
    concurrent writer to the same origin's blob can't silently lose this append.
    """
    entry = {"origin_iata": origin_iata, "query_date": query_date.strftime("%Y-%m-%d")}
    added = False

    def _append_if_missing(queue: list[dict]) -> list[dict]:
        nonlocal added
        if entry in queue:
            return queue
        added = True
        return [*queue, entry]

    try:
        update_retry_queue(airline, origin_iata, run_date, _append_if_missing)
    except _CasExhausted as e:
        logging.warning(f"{origin_iata}: failed to record {entry['query_date']} in retry queue (best-effort): {e}")
        return

    if added:
        logging.info(f"Recorded failed query {origin_iata} {entry['query_date']} for retry.")


def _retry_origin_queue(
    origin: str,
    queue: list[dict],
    client: Ryanair,
    resolved: dict[str, dict[str, str]],
    skipped: set[str],
    ambiguous_found: dict[str, dict[str, str]],
    unknown_found: dict[str, dict[str, str]],
) -> tuple[list[Flight], list[dict]]:
    """
    Retries every entry in a single origin's retry queue; returns the recovered flights and entries.
    Does not write back to the retry queue itself.
    """
    logging.info(f"Retrying {len(queue)} failed queries for {origin}...")
    recovered_flights: list[Flight] = []
    recovered_entries: list[dict] = []

    for i, entry in enumerate(queue, 1):
        query_date = datetime.strptime(entry["query_date"], "%Y-%m-%d").date()

        logging.info(f"[{i}/{len(queue)}] Retrying {origin} {entry['query_date']}...")
        try:
            raw_flights = client.get_cheapest_flights(origin, query_date, query_date)
        except Exception as e:
            logging.warning(f"  -> still failing: {origin} {entry['query_date']}: {e}")
            time.sleep(SCRAPE_REQUEST_DELAY_SECONDS)
            continue

        recovered_here = 0
        for flight in raw_flights:
            code = flight.destination
            if code in skipped:
                continue
            if code not in resolved:
                api_city, api_country = _extract_city_country_from_full(flight.destinationFull)
                classified = _classify_airport(code, api_city, api_country, ambiguous_found, unknown_found)
                if classified is None:
                    skipped.add(code)
                    continue
                resolved[code] = {"city": classified[0], "country": classified[1]}

            info = resolved[code]
            recovered_flights.append(
                Flight(
                    origin_iata=flight.origin,
                    destination_iata=code,
                    destination_city=info["city"],
                    destination_country=info["country"],
                    airline="Ryanair",
                    flight_number=(flight.flightNumber or "").replace(" ", "") or None,
                    departure_time=flight.departureTime,
                    arrival_time=None,
                    price_eur=flight.price,
                    currency=flight.currency,
                    scraped_at=_utc_now(),
                )
            )
            recovered_here += 1

        recovered_entries.append(entry)
        logging.info(f"  -> recovered {origin} {entry['query_date']} ({recovered_here} flights).")
        time.sleep(SCRAPE_REQUEST_DELAY_SECONDS)

    logging.info(
        f"Retry complete for {origin}: {len(recovered_flights)} flights recovered from "
        f"{len(recovered_entries)}/{len(queue)} queries."
    )
    return recovered_flights, recovered_entries


# ---------------------------
# Public API
# ---------------------------


def scrape_ryanair(origin_airport: str, run_date: str) -> list[Flight]:
    """
    Scrapes the cheapest one-way fare per destination, per day, from origin_airport
    for the next SCRAPE_BUFFER_DAYS.

    Args:
        origin_airport (str): The IATA code of the departure airport.
        run_date (str): The date (yyyymmdd) used as the retry-queue key

    Returns:
        List[Flight]: A list of Flight objects.
    """
    logging.info(f"Starting Ryanair scraping for {origin_airport}...")
    client = Ryanair(currency=RYANAIR_CURRENCY)

    start_date = (_utc_now() + timedelta(days=1)).date()
    flights_data: list[Flight] = []
    resolved: dict[str, dict[str, str]] = {}
    skipped: set[str] = set()
    ambiguous_found: dict[str, dict[str, str]] = {}
    unknown_found: dict[str, dict[str, str]] = {}

    for day_offset in range(SCRAPE_BUFFER_DAYS):
        query_date = start_date + timedelta(days=day_offset)
        try:
            raw_flights = client.get_cheapest_flights(origin_airport, query_date, query_date)
        except Exception as e:
            logging.warning(f"{origin_airport}: failed to fetch cheapest flights for {query_date}: {e}")
            _record_failed_query(origin_airport, query_date, "ryanair", run_date)
            time.sleep(SCRAPE_REQUEST_DELAY_SECONDS)
            continue

        for flight in raw_flights:
            airport_code = flight.destination
            if airport_code in skipped:
                continue
            if airport_code not in resolved:
                api_city, api_country = _extract_city_country_from_full(flight.destinationFull)
                classified = _classify_airport(airport_code, api_city, api_country, ambiguous_found, unknown_found)
                if classified is None:
                    skipped.add(airport_code)
                    continue
                resolved[airport_code] = {"city": classified[0], "country": classified[1]}

            info = resolved[airport_code]
            flights_data.append(
                Flight(
                    origin_iata=flight.origin,
                    destination_iata=airport_code,
                    destination_city=info["city"],
                    destination_country=info["country"],
                    airline="Ryanair",
                    flight_number=(flight.flightNumber or "").replace(" ", "") or None,
                    departure_time=flight.departureTime,
                    arrival_time=None,  # No arrival_time field in ryanair-py's Flight object
                    price_eur=flight.price,
                    currency=flight.currency,
                    scraped_at=_utc_now(),
                )
            )

        time.sleep(SCRAPE_REQUEST_DELAY_SECONDS)

    _save_unknown_and_ambiguous_findings(ambiguous_found, unknown_found)
    logging.info(f"Successfully scraped {len(flights_data)} Ryanair flights from {origin_airport}.")

    return flights_data


def retry_failed_queries(run_date: str, origin: str | None = None) -> dict[str, tuple[list[Flight], list[dict]]]:
    """
    Re-attempts every failed query in the retry queue for `origin`, or every origin in
    SCRAPE_ORIGINS if omitted. Does not write to the retry queue itself. Returns a dict of
    {origin: (recovered_flights, recovered_entries)} for origins whose queue was non-empty.
    """
    origins_to_retry = [origin] if origin is not None else SCRAPE_ORIGINS

    # Load every origin's queue before touching the network, so a fully-empty run (the
    # common case) never even constructs a Ryanair client/session.
    queues = {o: load_retry_queue("ryanair", o, run_date) for o in origins_to_retry}
    queues = {o: q for o, q in queues.items() if q}
    if not queues:
        logging.info("Retry queue is empty — nothing to retry.")
        return {}

    client = Ryanair(currency=RYANAIR_CURRENCY)
    results: dict[str, tuple[list[Flight], list[dict]]] = {}
    resolved: dict[str, dict[str, str]] = {}
    skipped: set[str] = set()
    ambiguous_found: dict[str, dict[str, str]] = {}
    unknown_found: dict[str, dict[str, str]] = {}

    for single_origin, queue in queues.items():
        results[single_origin] = _retry_origin_queue(
            single_origin, queue, client, resolved, skipped, ambiguous_found, unknown_found
        )

    _save_unknown_and_ambiguous_findings(ambiguous_found, unknown_found)
    return results


def confirm_recovered(airline: str, origin: str, run_date: str, recovered_entries: list[dict]) -> None:
    """
    Removes the given recovered entries from this origin's retry queue via a set difference
    against current content (not a blind overwrite), so a concurrently-appended new entry
    survives. Callers must only call this after the flights have been durably cached.
    """
    recovered_keys = {(entry["origin_iata"], entry["query_date"]) for entry in recovered_entries}

    def _remove_recovered(queue: list[dict]) -> list[dict]:
        return [entry for entry in queue if (entry["origin_iata"], entry["query_date"]) not in recovered_keys]

    update_retry_queue(airline, origin, run_date, _remove_recovered)


if __name__ == "__main__":
    # Example usage (for testing purposes)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    print("Scraping flights from EIN for Ryanair...")
    test_flights = scrape_ryanair("EIN", _utc_now().strftime(DATE_FORMAT))
    for flight in test_flights[:5]:  # Print first 5 results
        print(flight)
    print(f"\nTotal flights found: {len(test_flights)}")
