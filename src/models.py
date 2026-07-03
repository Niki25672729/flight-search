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

    def __str__(self) -> str:
        departure = self.departure_time.strftime("%Y-%m-%d %H:%M")
        return (
            f"{self.destination_iata} ({self.destination_city}, {self.destination_country}) | "
            f"{self.airline} | {departure} | €{self.price_eur:.2f}"
        )
