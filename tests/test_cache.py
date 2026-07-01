import json
import os
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

# Import functions and constants from the cache module
from cache import CACHE_DIR, CACHE_TTL, DATE_FORMAT, read_cache, write_cache
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
    """Creates a minimal dummy Flight for cache file content — fields other than destination_iata are irrelevant."""
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

@pytest.fixture(scope="function")
def tmp_cache_dir(tmp_path, mocker):
    """
    Creates a temporary directory for cache files and patches `cache.CACHE_DIR`
    to point to this temporary location. Ensures isolation between tests.
    It also yields a helper function to create mock cache files within this temp directory.
    """
    test_dir = tmp_path / "test_cache"
    test_dir.mkdir()
    mocker.patch("cache.CACHE_DIR", str(test_dir))
    mocker.patch("cache.os.makedirs", wraps=os.makedirs) # Patch os.makedirs for tracking/mocking if needed

    def create_mock_cache_file(
        airport: str, timestamp: datetime, content: list[Flight]
    ) -> str:
        """
        Helper to create a mock cache file with specific content and timestamp in the
        temporary cache directory managed by the fixture. Handles datetime serialization
        for consistency with `write_cache`.
        """
        serializable_content = []
        for flight in content:
            serializable_flight = {
                "destination_iata": flight.destination_iata,
                "destination_city": flight.destination_city,
                "destination_country": flight.destination_country,
                "airline": flight.airline,
                "departure_time": flight.departure_time.isoformat() if flight.departure_time else None,
                "arrival_time": flight.arrival_time.isoformat() if flight.arrival_time else None,
                "price_eur": flight.price_eur,
            }
            serializable_content.append(serializable_flight)

        filename = f"{airport}_{timestamp.strftime(DATE_FORMAT)}.json"
        filepath = os.path.join(str(test_dir), filename) # Use str(test_dir) for path join
        with open(filepath, "w") as f:
            json.dump(serializable_content, f)
        return filepath

    # Yield both the path and the helper function
    yield str(test_dir), create_mock_cache_file


@pytest.fixture(autouse=True)
def frozen_time():
    """
    Freezes datetime.now() to a specific point for consistent testing.
    All tests in this module will implicitly use this frozen time.
    """
    # Using FROZEN_NOW as the fixed 'now' for all tests
    with freeze_time(FROZEN_NOW) as frozen:
        yield frozen


@pytest.fixture(autouse=True)
def mock_print(mocker):
    """
    Patches builtins.print to capture calls and prevent output during tests.
    """
    return mocker.patch("builtins.print")


# ---------------------------
# Tests for read_cache
# ---------------------------

def test_read_cache_hit_fresh(tmp_cache_dir, mock_print):
    """
    Tests successful reading of a fresh cache file.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)

    create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])

    result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    # Ensure datetime objects are correctly deserialized
    assert result[0].departure_time == SAMPLE_FLIGHT_BCN.departure_time
    assert result[0].arrival_time == SAMPLE_FLIGHT_BCN.arrival_time
    # Check that print was called with "Cache hit"
    mock_print.assert_called_with(f"Cache hit for EIN: Loaded EIN_{cache_file_date.strftime(DATE_FORMAT)}.json")


def test_read_cache_miss_no_file(mocker, tmp_path, mock_print):
    """
    Tests cache miss when no cache file exists for the airport.
    """
    non_existent_dir = tmp_path / "test_cache"
    mocker.patch("cache.CACHE_DIR", str(non_existent_dir))
    result = read_cache("EIN")

    assert result is None
    mock_print.assert_called_with(f"Cache miss for EIN: Cache directory '{non_existent_dir}' does not exist.") # Initial check for dir before files


def test_read_cache_miss_stale_file(tmp_cache_dir, mock_print):
    """
    Tests cache miss when the existing file is older than CACHE_TTL,
    and verifies the stale file is deleted.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    # Cache timestamp older than CACHE_TTL.
    stale_date = (FROZEN_NOW - CACHE_TTL - timedelta(days=1)).replace(hour=0)

    # Create a stale cache file
    stale_filepath = create_mock_cache_file("EIN", stale_date, [make_dummy_flight("STALE")])

    result = read_cache("EIN")

    assert result is None
    # Verify the stale file was deleted
    assert not os.path.exists(stale_filepath)
    # Verify cleanup message was printed
    mock_print.assert_any_call(f"Cleaned up stale cache file: EIN_{stale_date.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call("Cache miss for EIN: No fresh cache found after checking and cleanup.")


def test_read_cache_corrupt_file(tmp_cache_dir, mock_print):
    """
    Tests handling of a corrupt (invalid JSON) cache file, ensuring it's deleted.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)

    # Create a dummy valid file first using the helper, then overwrite with corrupt content
    filepath = create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])
    filename = os.path.basename(filepath)
    with open(filepath, "w") as f:
        f.write("{invalid json content")  # Write corrupt JSON

    result = read_cache("EIN")

    assert result is None
    # Verify the corrupt file was deleted
    assert not os.path.exists(filepath)
    # Verify error message and cleanup message were printed
    # This print is before deletion, and its message isn't from _delete_file
    mock_print.assert_any_call(f"Error: Cache file {filename} is corrupt. Expecting property name enclosed in double quotes: line 1 column 2 (char 1).")
    mock_print.assert_any_call(f"Cleaned up corrupt cache file: {filename}")


def test_read_cache_multiple_files_newest_fresh(tmp_cache_dir, mock_print):
    """
    Tests scenario with multiple cache files: one stale, one with malformed filename,
    and one fresh. Ensures the newest fresh file is returned and others are cleaned up.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir

    # Dates for files, ensuring they are distinct days for YYYYMMDD format
    stale_date = (FROZEN_NOW - CACHE_TTL - timedelta(days=2)).replace(hour=0)
    older_fresh_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    newest_fresh_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=2)).replace(hour=0)

    # Create an older stale file
    stale_filepath = create_mock_cache_file("EIN", stale_date, [make_dummy_flight("STALE")])
    # Create an older fresh file
    older_fresh_filepath = create_mock_cache_file("EIN", older_fresh_date, [make_dummy_flight("OLD_FRESH")])
    # Create the newest fresh file
    newest_fresh_filepath = create_mock_cache_file("EIN", newest_fresh_date, [make_dummy_flight("NEW_FRESH")])
    # Create a malformed file (filename doesn't match DATE_FORMAT regex)
    malformed_filename_filepath = os.path.join(
        tmp_cache_dir_path, "EIN_MALFORMED_TIMESTAMP.json"
    )
    with open(malformed_filename_filepath, "w") as f:
        json.dump([{"destination_iata": "MALFORMED_FILE"}], f)

    result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "NEW_FRESH"

    # Verify stale and older fresh files are deleted
    assert not os.path.exists(stale_filepath)
    # Older fresh files are also deleted by the cleanup logic after a newer fresh one is found
    assert not os.path.exists(older_fresh_filepath)  # This is deleted by _delete_file
    assert os.path.exists(newest_fresh_filepath)  # This one should remain
    assert not os.path.exists(malformed_filename_filepath)  # Malformed file should also be deleted as it starts with EIN_ and ends with .json
    mock_print.assert_any_call(f"Cleaned up stale cache file: EIN_{stale_date.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call(f"Cleaned up older fresh cache file: EIN_{older_fresh_date.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call(f"Cleaned up malformed cache filename file: EIN_MALFORMED_TIMESTAMP.json")
    mock_print.assert_any_call(f"Cache hit for EIN: Loaded EIN_{newest_fresh_date.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call(f"Warning: Malformed filename (not {DATE_FORMAT} format) for EIN: EIN_MALFORMED_TIMESTAMP.json.")


def test_read_cache_multiple_files_all_stale(tmp_cache_dir, mock_print):
    """
    Tests scenario where multiple files exist but all are stale.
    Ensures all are deleted and None is returned.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    stale_file_date1 = (FROZEN_NOW - CACHE_TTL - timedelta(days=1)).replace(hour=0)
    stale_file_date2 = (FROZEN_NOW - CACHE_TTL - timedelta(days=2)).replace(hour=0)

    filepath1 = create_mock_cache_file("EIN", stale_file_date1, [make_dummy_flight("STALE1")])
    filepath2 = create_mock_cache_file("EIN", stale_file_date2, [make_dummy_flight("STALE2")])

    result = read_cache("EIN")

    assert result is None
    assert not os.path.exists(filepath1)
    assert not os.path.exists(filepath2)
    mock_print.assert_any_call(f"Cleaned up stale cache file: EIN_{stale_file_date1.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call(f"Cleaned up stale cache file: EIN_{stale_file_date2.strftime(DATE_FORMAT)}.json")
    mock_print.assert_any_call("Cache miss for EIN: No fresh cache found after checking and cleanup.")


def test_read_cache_file_with_malformed_filename_timestamp_is_skipped_and_deleted(
    tmp_cache_dir, mock_print
):
    """
    Tests that a cache file with a filename whose timestamp portion is malformed
    (i.e., not parsable by DATE_FORMAT) is skipped and deleted, allowing valid files to be read.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    fresh_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)

    # Create one good fresh file
    good_filepath = create_mock_cache_file("EIN", fresh_file_date, [make_dummy_flight("GOOD")])

    # Create a file with a malformed timestamp in its filename
    malformed_filename_full = "EIN_BADTIMESTAMP.json"  # Simplified for YYYYMMDD context
    malformed_filepath = os.path.join(tmp_cache_dir_path, malformed_filename_full)
    with open(malformed_filepath, "w") as f:
        json.dump([{"destination_iata": "BAD_TIMESTAMP_FILE"}], f)

    result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "GOOD"
    assert os.path.exists(good_filepath)
    assert not os.path.exists(malformed_filepath)  # Malformed filename file should be deleted

    # Verify warning and cleanup messages for the malformed filename
    mock_print.assert_any_call(f"Warning: Malformed filename (not {DATE_FORMAT} format) for EIN: {malformed_filename_full}.")
    # The error message for deleting a non-existent file is expected because `os.remove` might be called multiple times
    # (e.g., once by cleanup for malformed files and then again if the test fixture tries to clean up).
    mock_print.assert_any_call(f"Cleaned up malformed cache filename file: {malformed_filename_full}") # The cleanup message


# ---------------------------
# Tests for write_cache
# ---------------------------

def test_write_cache_creates_new_file_and_cleans_up_old_for_same_airport(tmp_cache_dir, mock_print):
    """
    Tests that write_cache creates a new file, and deletes existing files
    for the same airport, but leaves files for other airports untouched.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    old_ein_file_date1 = (FROZEN_NOW - CACHE_TTL).replace(hour=0)
    old_ein_file_date2 = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    ams_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=2)).replace(hour=0)

    # Create some old cache files for EIN (different days) and one for another airport (AMS)
    old_ein_filepath1 = create_mock_cache_file("EIN", old_ein_file_date1, [make_dummy_flight("OLD1")])
    old_ein_filepath2 = create_mock_cache_file("EIN", old_ein_file_date2, [make_dummy_flight("OLD2")])
    ams_filepath = create_mock_cache_file("AMS", ams_file_date, [SAMPLE_FLIGHT_AMS])

    flights_to_write = [SAMPLE_FLIGHT_BCN]

    write_cache("EIN", flights_to_write)

    # Check that the new file was written
    expected_filename = f"EIN_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(tmp_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)

    # Check content of the new file
    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert len(loaded_data) == 1
    assert loaded_data[0]["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata # Added check for specific flight data
    assert loaded_data[0]["departure_time"] == SAMPLE_FLIGHT_BCN.departure_time.isoformat()
    # Check that older files for EIN were removed
    assert not os.path.exists(old_ein_filepath1)
    assert not os.path.exists(old_ein_filepath2)
    mock_print.assert_any_call(f"Cleaned up old cache file: {os.path.basename(old_ein_filepath1)}")

    # Ensure file for other airport (AMS) was NOT removed
    assert os.path.exists(ams_filepath)
    mock_print.assert_any_call(f"Cache written to {expected_filename}")


def test_write_cache_no_cleanup_needed(tmp_cache_dir, mock_print):
    """
    Tests write_cache when there are no existing files for the airport,
    ensuring only a new file is created.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir

    flights_to_write = [SAMPLE_FLIGHT_AMS]

    write_cache("AMS", flights_to_write)

    # Only the new file should exist
    expected_filename = f"AMS_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(tmp_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)
    assert (
        len(os.listdir(tmp_cache_dir_path)) == 1
    )  # Only one file in the directory (the new one)
    mock_print.assert_any_call(f"Cache written to {expected_filename}")


def test_write_cache_creates_directory_if_not_exists(mocker, tmp_path):
    """
    Tests that write_cache correctly creates the cache directory if it doesn't exist.
    """
    # Point CACHE_DIR to a path within tmp_path that does not exist yet
    non_existent_cache_dir_path = str(tmp_path / "new_cache_dir")
    mocker.patch("cache.CACHE_DIR", non_existent_cache_dir_path)

    # Mock os.makedirs within the cache module
    mock_makedirs = mocker.patch(
        "cache.os.makedirs", wraps=os.makedirs
    )  # Use wraps to call real os.makedirs if needed, but still spy on it

    flights = []
    write_cache("FRA", flights)

    # Assert os.makedirs was called for the new directory
    mock_makedirs.assert_called_once_with(non_existent_cache_dir_path, exist_ok=True)

    # Check that the file was actually written to the newly created directory
    expected_filename = f"FRA_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(non_existent_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)

    # Check content
    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert loaded_data == []