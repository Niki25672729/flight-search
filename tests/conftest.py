from datetime import datetime

import pytest
from freezegun import freeze_time

from models import Flight


# ---------------------------
# Constants
# ---------------------------

FROZEN_NOW = datetime(2026, 6, 28, 17, 0, 0)

SAMPLE_FLIGHT_BCN = Flight(
    destination_iata="BCN",
    destination_city="Barcelona",
    destination_country="Spain",
    airline="Ryanair",
    departure_time=datetime(2026, 7, 1, 10, 0, 0),
    arrival_time=datetime(2026, 7, 1, 12, 0, 0),
    price_eur=50.0,
)

SAMPLE_FLIGHT_AMS = Flight(
    destination_iata="AMS",
    destination_city="Amsterdam",
    destination_country="Netherlands",
    airline="Transavia",
    departure_time=datetime(2026, 7, 5, 8, 0, 0),
    arrival_time=datetime(2026, 7, 5, 10, 0, 0),
    price_eur=75.0,
)


# ---------------------------
# Helpers
# ---------------------------

def make_dummy_flight(destination_iata: str = "DUM") -> Flight:
    """Creates a minimal dummy Flight for use in tests where flight content is irrelevant."""
    return Flight(
        destination_iata=destination_iata,
        destination_city="Unknown",
        destination_country="Unknown",
        airline="Unknown",
        departure_time=datetime(2026, 1, 1),
        arrival_time=None,
        price_eur=0.0,
    )


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture(autouse=True)
def frozen_time():
    """Freezes datetime.now() to FROZEN_NOW for all tests."""
    with freeze_time(FROZEN_NOW) as frozen:
        yield frozen
