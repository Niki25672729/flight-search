import os
from datetime import timedelta


# ---------------------------
# Cache
# ---------------------------

CACHE_DIR = "cache"
CACHE_TTL = timedelta(weeks=1)
DATE_FORMAT = "%Y%m%d"


# ---------------------------
# Scraping
# ---------------------------

SCRAPE_BUFFER_DAYS = 3 * 30 + 7  # ~3 months + 1 week

# Ryanair
COOKIE_TTL_SECONDS = 3600
COOKIE_CACHE_PATH = ".ryanair_cookies.json"
RYANAIR_SESSION_URL = "https://www.ryanair.com/ie/en"


# ---------------------------
# CLI
# ---------------------------

MAX_TIMERANGE_MONTHS = 3


# ---------------------------
# Airports
# ---------------------------

EU_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "eu_airports.json")
UNKNOWN_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "unknown_airports.json")
