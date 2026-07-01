from dataclasses import dataclass
from datetime import datetime


@dataclass
class Flight:
    """Represents a single one-way flight."""

    destination_iata: str
    destination_city: str
    destination_country: str
    airline: str
    departure_time: datetime
    arrival_time: datetime | None  # Not available for all airlines
    price_eur: float