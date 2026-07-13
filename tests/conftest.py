import os
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from config import (
    CLOUD_CACHE_ROOT,
    CLOUD_FLIGHT_CACHE_DIR,
    CLOUD_RETRY_QUEUE_PATH,
    DATE_FORMAT,
    LOCAL_CACHE_ROOT,
    LOCAL_FLIGHT_CACHE_DIR,
    LOCAL_RETRY_QUEUE_PATH,
)
from models import Flight


# ---------------------------
# Constants
# ---------------------------

FROZEN_NOW = datetime(2026, 6, 28, 17, 0, 0)

LOCAL_FLIGHT_CACHE_SUBPATH = LOCAL_FLIGHT_CACHE_DIR.removeprefix(f"{LOCAL_CACHE_ROOT}{os.sep}")
LOCAL_RETRY_CACHE_SUBPATH = LOCAL_RETRY_QUEUE_PATH.removeprefix(f"{LOCAL_CACHE_ROOT}{os.sep}")
CLOUD_FLIGHT_CACHE_SUBPATH = CLOUD_FLIGHT_CACHE_DIR.removeprefix(f"{CLOUD_CACHE_ROOT}/")
CLOUD_RETRY_CACHE_SUBPATH = CLOUD_RETRY_QUEUE_PATH.removeprefix(f"{CLOUD_CACHE_ROOT}/")


SAMPLE_FLIGHT_BCN = Flight(
    origin_iata="EIN",
    destination_iata="BCN",
    destination_city="Barcelona",
    destination_country="Spain",
    airline="Ryanair",
    flight_number="FR1234",
    departure_time=datetime(2026, 7, 1, 10, 0, 0),
    arrival_time=datetime(2026, 7, 1, 12, 0, 0),
    price_eur=50.0,
)

SAMPLE_FLIGHT_AMS = Flight(
    origin_iata="EIN",
    destination_iata="AMS",
    destination_city="Amsterdam",
    destination_country="Netherlands",
    airline="Transavia",
    flight_number="FR567",
    departure_time=FROZEN_NOW + timedelta(days=60),
    arrival_time=FROZEN_NOW + timedelta(days=60, hours=2),
    price_eur=200.0,
)


# ---------------------------
# Helpers
# ---------------------------


def make_dummy_flight(destination_iata: str = "DUM") -> Flight:
    """Creates a minimal dummy Flight for use in tests where flight content is irrelevant."""
    return Flight(
        origin_iata="EIN",
        destination_iata=destination_iata,
        destination_city="Unknown",
        destination_country="Unknown",
        airline="Unknown",
        flight_number="FR123",
        departure_time=datetime(2026, 1, 1),
        arrival_time=None,
        price_eur=0.0,
    )


def make_ryanair_py_flight(
    origin: str = "EIN",
    destination: str = "BCN",
    destination_full: str = "Barcelona, Spain",
    departure_time: datetime | None = None,
    flight_number: str = "FR 123",
    price: float = 29.99,
    currency: str = "EUR",
):
    """Builds a ryanair-py Flight (as returned by Ryanair.get_cheapest_flights) for tests."""
    from ryanair.types import Flight as RyanairPyFlight

    return RyanairPyFlight(
        departureTime=departure_time or datetime(2026, 8, 25, 11, 15),
        flightNumber=flight_number,
        price=price,
        currency=currency,
        origin=origin,
        originFull="Eindhoven, Netherlands",
        destination=destination,
        destinationFull=destination_full,
    )


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture(autouse=True)
def frozen_time():
    """Freezes datetime.now() to FROZEN_NOW for all tests."""
    with freeze_time(FROZEN_NOW) as frozen:
        yield frozen


@pytest.fixture(autouse=True)
def default_gcs_bucket_name(mocker):
    """Defaults GCS_BUCKET_NAME to unset, isolating tests from the local environment."""
    mocker.patch("cache.GCS_BUCKET_NAME", "")


@pytest.fixture
def tmp_retry_queue_path(tmp_path, mocker):
    """Patches cache.LOCAL_RETRY_QUEUE_PATH to a temporary location. Returns today's resolved ryanair/EIN path."""
    mocker.patch("cache.LOCAL_RETRY_QUEUE_PATH", os.path.join(str(tmp_path), LOCAL_RETRY_CACHE_SUBPATH))
    return tmp_path / LOCAL_RETRY_CACHE_SUBPATH.format(
        airline="ryanair", origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT)
    )


@pytest.fixture
def mock_read_cache(mocker):
    """
    Mocks cache.read_cache as imported by flight_search.py, run.py, and report.py -- all three
    aliased to the same underlying cache.read_cache, so each import site is patched to the same
    Mock object (patching cache.read_cache itself wouldn't affect any of them, since mock.patch
    has to hit the point of use, not the definition). Defaults to None (cache miss): some
    report.py tests rely on this default directly, without setting it explicitly, for their
    "no cache data at all" scenarios.
    """
    mock = mocker.patch("flight_search.read_cache", return_value=None)
    mocker.patch("run.read_cache", new=mock)
    mocker.patch("report.read_cache", new=mock)
    return mock


@pytest.fixture
def mock_load_retry_queue(mocker):
    """Mocks cache.load_retry_queue as imported by report.py. Defaults to an empty queue."""
    return mocker.patch("report.load_retry_queue", return_value=[])


@pytest.fixture
def mock_scrape_origins(mocker):
    """Patches SCRAPE_ORIGINS down to two origins in manual_run.py and report.py, so tests don't
    loop over the real ~49."""
    mocker.patch("manual_run.SCRAPE_ORIGINS", ["EIN", "STN"])
    mocker.patch("report.SCRAPE_ORIGINS", ["EIN", "STN"])
