import os


# ---------------------------
# Cache
# ---------------------------

DATE_FORMAT = "%Y%m%d"
FLIGHT_CACHE_FILENAME = "{origin}_{yyyymmdd}.json"
FLIGHT_STATUS_FILENAME = "status.json"

FLIGHT_CACHE_DIR = "flights/{airline}/{yyyymm}/{dd}"
RETRY_QUEUE_PATH = "flights/{airline}/retry/retry_{origin}_{yyyymmdd}.json"

# Local
LOCAL_CACHE_ROOT = "cache"
LOCAL_FLIGHT_CACHE_DIR = os.path.join(LOCAL_CACHE_ROOT, FLIGHT_CACHE_DIR)
LOCAL_RETRY_QUEUE_PATH = os.path.join(LOCAL_CACHE_ROOT, RETRY_QUEUE_PATH)

# Cloud
GCS_BUCKET_NAME = os.environ.get("FLIGHT_SEARCH_GCS_BUCKET", "")
CLOUD_CACHE_ROOT = "bronze"
CLOUD_FLIGHT_CACHE_DIR = f"{CLOUD_CACHE_ROOT}/{FLIGHT_CACHE_DIR}"
CLOUD_RETRY_QUEUE_PATH = f"{CLOUD_CACHE_ROOT}/{RETRY_QUEUE_PATH}"
CLOUD_REPORT_PATH = f"{CLOUD_FLIGHT_CACHE_DIR}/{FLIGHT_STATUS_FILENAME}"


# ---------------------------
# Silver (v2 Pipeline)
# ---------------------------

SILVER_LATEST_STATE_TABLE = "flights_latest_state"
SILVER_PRICE_HISTORY_TABLE = "flight_price_history"


# ---------------------------
# Scraping
# ---------------------------

SCRAPE_BUFFER_DAYS = 3 * 30 + 7  # ~3 months + 1 week
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
CLI_DATE_FORMAT = "%Y-%m-%d"
TIMERANGE_SEPARATOR = "..."
DEFAULT_TIMERANGE_DAYS = 30
SORT_MODES = ("date", "price")


# ---------------------------
# Airports
# ---------------------------

EU_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "eu_airports.json")
IGNORED_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "ignored_airports.json")
AMBIGUOUS_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "ambiguous_airports.json")
UNKNOWN_AIRPORTS_PATH = os.path.join(os.path.dirname(__file__), "unknown_airports.json")
