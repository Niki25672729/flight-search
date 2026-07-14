from rich.console import Console
from rich.table import Table

from models import Flight

_CONSOLE = Console()


def display_flights(flights: list[Flight], show_origin: bool = False) -> None:
    """
    Displays a list of Flight objects as a formatted table using the rich library.

    Args:
        flights: A list of Flight objects to display, in the order they should be shown
            (sorting is the caller's responsibility — see filter_flights' sort modes).
        show_origin: Prepend a Departure column with each flight's origin airport —
            used when a search spans multiple origin airports (city/country departure).
    """
    if not flights:
        _CONSOLE.print("[bold yellow]No flights found matching your criteria.[/bold yellow]")
        return

    # Check if any flight has an arrival_time
    show_arrival_time = any(flight.arrival_time is not None for flight in flights)

    table = Table(
        title="[bold green]Budget Flight Search Results[/bold green]",
        show_header=True,
        header_style="bold magenta",
        show_lines=True,
    )

    if show_origin:
        table.add_column("Departure", style="cyan", justify="left")
    table.add_column("Destination", style="cyan", justify="left")
    table.add_column("Airline", style="blue", justify="left")
    table.add_column("Departure Time", style="yellow", justify="left")
    if show_arrival_time:
        table.add_column("Arrival Time", style="yellow", justify="left")
    table.add_column("Price", style="green", justify="right")

    for flight in flights:
        destination = f"{flight.destination_iata}, {flight.destination_city}, {flight.destination_country}"
        departure_time_str = flight.departure_time.strftime("%Y-%m-%d %H:%M")
        price_str = f"€{flight.price_eur:.2f}"

        row_data = [flight.origin_iata] if show_origin else []
        row_data += [destination, flight.airline, departure_time_str]

        if show_arrival_time:
            arrival_time_str = flight.arrival_time.strftime("%Y-%m-%d %H:%M") if flight.arrival_time else "N/A"
            row_data.append(arrival_time_str)

        row_data.append(price_str)
        table.add_row(*row_data)

    _CONSOLE.print(table)
