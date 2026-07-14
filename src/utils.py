import json
import logging
from datetime import datetime, timezone

from config import EU_AIRPORTS_PATH, IGNORED_AIRPORTS_PATH, AMBIGUOUS_AIRPORTS_PATH, UNKNOWN_AIRPORTS_PATH


# ---------------------------
# Helpers
# ---------------------------


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load_eu_airports() -> dict:
    try:
        with open(EU_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error("eu_airports.json not found.")
        return {}


def _load_ignored_airports() -> dict:
    try:
        with open(IGNORED_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error("ignored_airports.json not found.")
        return {}


def _load_ambiguous_airports() -> dict:
    try:
        with open(AMBIGUOUS_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _load_unknown_airports() -> dict:
    try:
        with open(UNKNOWN_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _build_name_to_airports(airport_details: dict) -> dict[str, list[str]]:
    """Builds a lowercased city/country name → IATA codes reverse lookup (a name can map to several airports)."""
    name_map: dict[str, list[str]] = {}
    for iata, details in airport_details.items():
        # A name can be both a city and a country (e.g. Luxembourg), so dedupe per name
        for key in ("city", "country"):
            airports = name_map.setdefault(details[key].lower(), [])
            if iata not in airports:
                airports.append(iata)
    return name_map


# ---------------------------
# Constants
# ---------------------------

EU_AIRPORT_DETAILS = _load_eu_airports()
EU_AIRPORTS = set(EU_AIRPORT_DETAILS.keys())
NAME_TO_AIRPORTS = _build_name_to_airports(EU_AIRPORT_DETAILS)
IGNORED_AIRPORT_DETAILS = _load_ignored_airports()
IGNORED_AIRPORTS = set(IGNORED_AIRPORT_DETAILS.keys())


# ---------------------------
# Mutable state
# ---------------------------

# Mutable — updated at runtime when airports' city or country are different from the one in EU_AIRPORT_DETAILS
ambiguous_airport_details = _load_ambiguous_airports()
# Mutable — updated at runtime when new airports are discovered during scraping
unknown_airport_details = _load_unknown_airports()
