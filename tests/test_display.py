import pytest

from conftest import SAMPLE_FLIGHT_AMS, SAMPLE_FLIGHT_BCN, make_dummy_flight
from display import display_flights


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture
def mock_console(mocker):
    """Mocks the module-level _CONSOLE in display.py to capture print calls."""
    return mocker.patch("display._CONSOLE")


# ---------------------------
# Tests for display_flights
# ---------------------------


def test_display_flights_empty_list(mock_console):
    """Tests that a friendly message is shown when no flights are provided."""
    display_flights([])

    mock_console.print.assert_called_once()
    call_args = mock_console.print.call_args[0][0]
    assert "No flights found" in call_args


def test_display_flights_single_flight(mock_console):
    """Tests that a single flight is displayed in a table."""
    display_flights([SAMPLE_FLIGHT_BCN])

    mock_console.print.assert_called_once()
    table = mock_console.print.call_args[0][0]
    assert table.row_count == 1


def test_display_flights_multiple_flights(mock_console):
    """Tests that multiple flights are all added to the table."""
    display_flights([SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS])

    mock_console.print.assert_called_once()
    table = mock_console.print.call_args[0][0]
    assert table.row_count == 2


def test_display_flights_hides_arrival_time_column_when_all_none(mock_console):
    """Tests that the Arrival Time column is hidden when all flights have arrival_time=None."""
    flights = [make_dummy_flight("BCN"), make_dummy_flight("AMS")]

    display_flights(flights)

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert "Arrival Time" not in column_names


def test_display_flights_shows_arrival_time_column_when_any_set(mock_console):
    """Tests that the Arrival Time column is shown when at least one flight has arrival_time set."""
    flights = [
        make_dummy_flight("BCN"),  # arrival_time=None
        SAMPLE_FLIGHT_AMS,  # arrival_time set
    ]

    display_flights(flights)

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert "Arrival Time" in column_names


def test_display_flights_shows_na_for_missing_arrival_time(mock_console):
    """Tests that N/A is shown for flights without arrival_time when column is visible."""
    flights = [
        make_dummy_flight("BCN"),  # arrival_time=None — should show N/A
        SAMPLE_FLIGHT_AMS,  # arrival_time set
    ]

    display_flights(flights)

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    destination_col_idx = column_names.index("Destination")
    arrival_col_idx = column_names.index("Arrival Time")

    # Locate the dummy flight's row by its destination cell rather than assuming input order.
    destination_cells = table.columns[destination_col_idx]._cells
    dummy_row_idx = destination_cells.index("BCN, Unknown, Unknown")

    arrival_cells = table.columns[arrival_col_idx]._cells
    assert arrival_cells[dummy_row_idx] == "N/A"


def test_display_flights_destination_format(mock_console):
    """Tests that destination is formatted as 'IATA, City, Country'."""
    display_flights([SAMPLE_FLIGHT_BCN])

    table = mock_console.print.call_args[0][0]
    destination_col = table.columns[0]
    assert destination_col._cells[0] == "BCN, Barcelona, Spain"


def test_display_flights_price_format(mock_console):
    """Tests that price is formatted as '€XX.XX'."""
    display_flights([SAMPLE_FLIGHT_BCN])

    table = mock_console.print.call_args[0][0]
    price_col = table.columns[-1]
    assert price_col._cells[0] == "€50.00"


def test_display_flights_departure_time_format(mock_console):
    """Tests that departure time is formatted as 'YYYY-MM-DD HH:MM'."""
    display_flights([SAMPLE_FLIGHT_BCN])

    table = mock_console.print.call_args[0][0]
    departure_col = table.columns[2]
    assert departure_col._cells[0] == "2026-07-01 10:00"


def test_display_flights_table_has_correct_columns_without_arrival(mock_console):
    """Tests column headers when arrival_time is not shown."""
    display_flights([make_dummy_flight()])

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert column_names == ["Destination", "Airline", "Departure Time", "Price"]


def test_display_flights_table_has_correct_columns_with_arrival(mock_console):
    """Tests column headers when arrival_time is shown."""
    display_flights([SAMPLE_FLIGHT_BCN])

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert column_names == ["Destination", "Airline", "Departure Time", "Arrival Time", "Price"]


def test_display_flights_show_origin_prepends_departure_column(mock_console):
    """Tests that show_origin adds a leading Departure column with each flight's origin airport."""
    display_flights([SAMPLE_FLIGHT_BCN], show_origin=True)

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert column_names[0] == "Departure"
    assert table.columns[0]._cells[0] == "EIN"


def test_display_flights_hides_departure_column_by_default(mock_console):
    """Tests that the Departure column only appears for multi-airport searches (show_origin=True)."""
    display_flights([SAMPLE_FLIGHT_BCN])

    table = mock_console.print.call_args[0][0]
    column_names = [col.header for col in table.columns]
    assert "Departure" not in column_names


def test_display_flights_preserves_caller_order(mock_console):
    """Tests that rows keep the caller's order — sorting is filter_flights' job (sort modes), not display's."""
    # Spain-before-Netherlands would be re-ordered if display still sorted internally.
    display_flights([SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS])

    table = mock_console.print.call_args[0][0]
    destination_cells = table.columns[0]._cells
    assert destination_cells[0] == "BCN, Barcelona, Spain"
    assert destination_cells[1] == "AMS, Amsterdam, Netherlands"
