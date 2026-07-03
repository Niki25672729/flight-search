import json
import logging
import os
from datetime import datetime, timedelta

import pytest
from freezegun import freeze_time

from cache import CACHE_DIR, FILENAME_REGEX, read_cache, write_cache, _serialize_datetime
from config import CACHE_TTL, DATE_FORMAT
from models import Flight
from conftest import FROZEN_NOW, SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS, make_dummy_flight


# ---------------------------
# Fixtures
# ---------------------------

@pytest.fixture(scope="function")
def tmp_cache_dir(tmp_path, mocker):
    """
    Creates a temporary directory for cache files and patches `cache.CACHE_DIR`
    to point to this temporary location. Ensures isolation between tests.
    Yields a tuple of (cache_dir_path, create_mock_cache_file helper).
    """
    test_dir = tmp_path / "test_cache"
    test_dir.mkdir()
    mocker.patch("cache.CACHE_DIR", str(test_dir))
    mocker.patch("cache.os.makedirs", wraps=os.makedirs)

    def create_mock_cache_file(airport: str, timestamp: datetime, content: list[Flight]) -> str:
        """
        Helper to create a mock cache file with specific content and timestamp in the
        temporary cache directory managed by the fixture.
        """
        filename = f"{airport}_{timestamp.strftime(DATE_FORMAT)}.json"
        filepath = os.path.join(str(test_dir), filename)
        with open(filepath, "w") as f:
            json.dump(content, f, default=_serialize_datetime)
        return filepath

    yield str(test_dir), create_mock_cache_file


# ---------------------------
# Tests for read_cache
# ---------------------------

def test_read_cache_hit_fresh(tmp_cache_dir, caplog):
    """Tests successful reading of a fresh cache file."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    assert result[0].departure_time == SAMPLE_FLIGHT_BCN.departure_time
    assert result[0].arrival_time == SAMPLE_FLIGHT_BCN.arrival_time
    assert f"Cache hit for EIN: Loaded EIN_{cache_file_date.strftime(DATE_FORMAT)}.json" in caplog.text


def test_read_cache_miss_no_file(mocker, tmp_path, caplog):
    """Tests cache miss when no cache file exists for the airport."""
    non_existent_dir = tmp_path / "test_cache"
    mocker.patch("cache.CACHE_DIR", str(non_existent_dir))

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN")

    assert result is None
    assert f"Cache directory '{non_existent_dir}' does not exist" in caplog.text


def test_read_cache_miss_stale_file(tmp_cache_dir, caplog):
    """
    Tests cache miss when the existing file is older than CACHE_TTL,
    and verifies the stale file is deleted.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    stale_cache_file_date = (FROZEN_NOW - CACHE_TTL - timedelta(days=1)).replace(hour=0)
    stale_filepath = create_mock_cache_file("EIN", stale_cache_file_date, [make_dummy_flight("STALE")])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN")

    assert result is None
    assert not os.path.exists(stale_filepath)
    assert f"Cleaned up stale cache file: EIN_{stale_cache_file_date.strftime(DATE_FORMAT)}.json" in caplog.text
    assert "Cache miss for EIN: No fresh cache found after checking and cleanup." in caplog.text


def test_read_cache_corrupt_file(tmp_cache_dir, caplog):
    """Tests handling of a corrupt (invalid JSON) cache file, ensuring it's deleted."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    filepath = create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])
    with open(filepath, "w") as f:
        f.write("{invalid json content")

    with caplog.at_level(logging.ERROR):
        result = read_cache("EIN")

    assert result is None
    assert not os.path.exists(filepath)
    assert "corrupt" in caplog.text


def test_read_cache_multiple_files_newest_wins(tmp_cache_dir, caplog):
    """
    Tests that with multiple fresh cache files, the newest is loaded
    and older ones are deleted.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    stale_date = (FROZEN_NOW - CACHE_TTL - timedelta(days=1)).replace(hour=0)
    older_fresh_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    newest_fresh_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=2)).replace(hour=0)

    stale_filepath = create_mock_cache_file("EIN", stale_date, [make_dummy_flight("STALE")])
    older_fresh_filepath = create_mock_cache_file("EIN", older_fresh_date, [make_dummy_flight("OLD_FRESH")])
    newest_fresh_filepath = create_mock_cache_file("EIN", newest_fresh_date, [make_dummy_flight("NEW_FRESH")])

    # Also create a malformed filename
    malformed_filename_filepath = os.path.join(tmp_cache_dir_path, "EIN_MALFORMED_TIMESTAMP.json")
    with open(malformed_filename_filepath, "w") as f:
        json.dump([{"destination_iata": "MALFORMED_FILE"}], f)

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "NEW_FRESH"
    assert not os.path.exists(stale_filepath)
    assert not os.path.exists(older_fresh_filepath)
    assert os.path.exists(newest_fresh_filepath)
    assert not os.path.exists(malformed_filename_filepath)
    assert f"Cleaned up stale cache file: EIN_{stale_date.strftime(DATE_FORMAT)}.json" in caplog.text
    assert f"Cleaned up older fresh cache file: EIN_{older_fresh_date.strftime(DATE_FORMAT)}.json" in caplog.text
    assert "Cleaned up malformed cache filename file: EIN_MALFORMED_TIMESTAMP.json" in caplog.text
    assert f"Cache hit for EIN: Loaded EIN_{newest_fresh_date.strftime(DATE_FORMAT)}.json" in caplog.text


def test_read_cache_multiple_files_all_stale(tmp_cache_dir, caplog):
    """Tests scenario where multiple files exist but all are stale."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    stale_file_date1 = (FROZEN_NOW - CACHE_TTL - timedelta(days=1)).replace(hour=0)
    stale_file_date2 = (FROZEN_NOW - CACHE_TTL - timedelta(days=2)).replace(hour=0)

    filepath1 = create_mock_cache_file("EIN", stale_file_date1, [make_dummy_flight("STALE1")])
    filepath2 = create_mock_cache_file("EIN", stale_file_date2, [make_dummy_flight("STALE2")])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN")

    assert result is None
    assert not os.path.exists(filepath1)
    assert not os.path.exists(filepath2)
    assert f"Cleaned up stale cache file: EIN_{stale_file_date1.strftime(DATE_FORMAT)}.json" in caplog.text
    assert f"Cleaned up stale cache file: EIN_{stale_file_date2.strftime(DATE_FORMAT)}.json" in caplog.text
    assert "Cache miss for EIN: No fresh cache found after checking and cleanup." in caplog.text


def test_read_cache_malformed_filename_is_deleted(tmp_cache_dir, caplog):
    """
    Tests that a cache file with a malformed filename timestamp is deleted,
    while a valid fresh file is still returned.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    fresh_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    good_filepath = create_mock_cache_file("EIN", fresh_file_date, [make_dummy_flight("GOOD")])

    malformed_filename_full = "EIN_BADTIMESTAMP.json"
    malformed_filepath = os.path.join(tmp_cache_dir_path, malformed_filename_full)
    with open(malformed_filepath, "w") as f:
        json.dump([{"destination_iata": "BAD_TIMESTAMP_FILE"}], f)

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "GOOD"
    assert os.path.exists(good_filepath)
    assert not os.path.exists(malformed_filepath)
    assert f"Cleaned up malformed cache filename file: {malformed_filename_full}" in caplog.text


# ---------------------------
# Tests for write_cache
# ---------------------------

def test_write_cache_creates_new_file_and_cleans_up_old(tmp_cache_dir, caplog):
    """
    Tests that write_cache creates a new file, deletes existing files for the same
    airport, but leaves files for other airports untouched.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    old_ein_file_date1 = (FROZEN_NOW - CACHE_TTL).replace(hour=0)
    old_ein_file_date2 = (FROZEN_NOW - CACHE_TTL + timedelta(days=1)).replace(hour=0)
    ams_file_date = (FROZEN_NOW - CACHE_TTL + timedelta(days=2)).replace(hour=0)

    old_ein_filepath1 = create_mock_cache_file("EIN", old_ein_file_date1, [make_dummy_flight("OLD1")])
    old_ein_filepath2 = create_mock_cache_file("EIN", old_ein_file_date2, [make_dummy_flight("OLD2")])
    ams_filepath = create_mock_cache_file("AMS", ams_file_date, [make_dummy_flight("AMS_FLIGHT")])

    with caplog.at_level(logging.INFO):
        write_cache("EIN", [SAMPLE_FLIGHT_BCN])

    expected_filename = f"EIN_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(tmp_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)

    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert len(loaded_data) == 1
    assert loaded_data[0]["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata
    assert loaded_data[0]["departure_time"] == SAMPLE_FLIGHT_BCN.departure_time.isoformat()

    assert not os.path.exists(old_ein_filepath1)
    assert not os.path.exists(old_ein_filepath2)
    assert os.path.exists(ams_filepath)
    assert f"Cleaned up old cache file: {os.path.basename(old_ein_filepath1)}" in caplog.text
    assert f"Cache written to {expected_filename}" in caplog.text


def test_write_cache_no_cleanup_needed(tmp_cache_dir, caplog):
    """Tests write_cache when there are no existing files for the airport."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir

    with caplog.at_level(logging.INFO):
        write_cache("AMS", [SAMPLE_FLIGHT_AMS])

    expected_filename = f"AMS_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(tmp_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)
    assert len(os.listdir(tmp_cache_dir_path)) == 1
    assert f"Cache written to {expected_filename}" in caplog.text


def test_write_cache_creates_directory_if_not_exists(mocker, tmp_path, caplog):
    """Tests that write_cache correctly creates the cache directory if it doesn't exist."""
    non_existent_cache_dir_path = str(tmp_path / "new_cache_dir")
    mocker.patch("cache.CACHE_DIR", non_existent_cache_dir_path)
    mock_makedirs = mocker.patch("cache.os.makedirs", wraps=os.makedirs)

    with caplog.at_level(logging.INFO):
        write_cache("FRA", [])

    mock_makedirs.assert_called_once_with(non_existent_cache_dir_path, exist_ok=True)
    expected_filename = f"FRA_{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(non_existent_cache_dir_path, expected_filename)
    assert os.path.exists(expected_filepath)
    with open(expected_filepath, "r") as f:
        assert json.load(f) == []
