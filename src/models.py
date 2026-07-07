from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Flight:
    """Represents a single one-way flight, as observed at scrape time."""

    origin_iata: str
    destination_iata: str
    destination_city: str
    destination_country: str
    airline: str
    flight_number: str | None  # e.g. "FR1926"; not always available
    departure_time: datetime
    arrival_time: datetime | None  # Not available for all airlines
    price_eur: float

    # --- Added for daily scraping + historical/price-trend analysis ---
    currency: str = "EUR"  # kept for completeness even though prices are normalised to EUR
    seats_left: int | None = None  # airline's reported seats/fares left; None = not reported by source
    scraped_at: datetime = field(default_factory=datetime.now)  # when THIS record was captured

    def __str__(self) -> str:
        departure = self.departure_time.strftime("%Y-%m-%d %H:%M")
        return (
            f"{self.destination_iata} ({self.destination_city}, {self.destination_country}) | "
            f"{self.airline} | {departure} | €{self.price_eur:.2f}"
        )
