import json
import logging

from config import EU_AIRPORTS_PATH, UNKNOWN_AIRPORTS_PATH


# ---------------------------
# Helpers
# ---------------------------


def _load_eu_airports() -> dict:
    try:
        with open(EU_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error("eu_airports.json not found.")
        return {}


def _load_unknown_airports() -> dict:
    try:
        with open(UNKNOWN_AIRPORTS_PATH, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


# ---------------------------
# Constants
# ---------------------------

EU_AIRPORT_DETAILS = _load_eu_airports()
EU_AIRPORTS = set(EU_AIRPORT_DETAILS.keys())


# ---------------------------
# Mutable state
# ---------------------------

# Mutable — updated at runtime when new airports are discovered during scraping
unknown_airport_details = _load_unknown_airports()
