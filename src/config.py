import os
from datetime import timedelta


# ---------------------------
# Cache
# ---------------------------

CACHE_TTL = timedelta(days=1)  # scraping runs daily now, so cache is only fresh for the current day
DATE_FORMAT = "%Y%m%d"

LOCAL_CACHE_ROOT = "cache"
LOCAL_FLIGHT_CACHE_DIR = os.path.join(LOCAL_CACHE_ROOT, "flights", "{airline}", "{origin}", "{yyyymm}")

GCS_BUCKET_NAME = os.environ.get("FLIGHT_SEARCH_GCS_BUCKET", "")


# ---------------------------
# Scraping
# ---------------------------

SCRAPE_BUFFER_DAYS = 3 * 30 + 7  # ~3 months + 1 week
RETRY_QUEUE_PATH = os.path.join(LOCAL_CACHE_ROOT, "retry.json")
SCRAPE_ORIGINS = [
    # Top 5 (data-backed, 2026 Cirium Diio)
    "STN",
    "DUB",
    "BGY",
    "CRL",
    "BCN",
    # Spain/Portugal — core leisure routes
    "AGP",
    "PMI",
    "FAO",
    "ALC",
    "VLC",
    "SVQ",
    "OPO",
    "LIS",
    # Italy (Ryanair's largest market share)
    "BLQ",
    "FCO",
    "CIA",
    "NAP",
    "CTA",
    "PMO",
    "BRI",
    "BDS",
    "VCE",
    "TSF",
    "PSA",
    # Poland / Central-Eastern Europe
    "KRK",
    "WMI",
    "WAW",
    "WRO",
    "GDN",
    "KTW",
    "POZ",
    "BUD",
    "OTP",
    "SOF",
    # Greece
    "ATH",
    "SKG",
    # Austria
    "VIE",
    # France
    "MRS",
    "NTE",
    "BOD",
    "TLS",
    # UK (Ryanair's secondary-airport strongholds)
    "MAN",
    "EDI",
    "GLA",
    "BRS",
    "LPL",
    "LBA",
    "EMA",
    "BHX",
    # Netherlands
    "EIN",
]

# Ryanair
RYANAIR_CURRENCY = "EUR"
SCRAPE_REQUEST_DELAY_SECONDS = 2


# ---------------------------
# CLI
# ---------------------------

MAX_TIMERANGE_MONTHS = 3


# ---------------------------
# Airports
# ---------------------------

EU_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "eu_airports.json")
IGNORED_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "ignored_airports.json")
AMBIGUOUS_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "ambiguous_airports.json")
UNKNOWN_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "unknown_airports.json")
