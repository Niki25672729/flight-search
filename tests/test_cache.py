import json
import logging
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from cache import read_cache, write_cache, load_retry_queue, save_retry_queue, _serialize_datetime, _get_gcs_bucket
from config import DATE_FORMAT, CLOUD_FLIGHT_CACHE_DIR
from models import Flight
from conftest import FROZEN_NOW, SAMPLE_FLIGHT_BCN, SAMPLE_FLIGHT_AMS, make_dummy_flight


# ---------------------------
# Helpers
# ---------------------------


def _make_mock_gcs_blob(airport: str, date: datetime, flights: list[Flight]) -> MagicMock:
    """Builds a mock GCS blob whose content is the NDJSON serialization of the given flights."""
    blob = MagicMock()
    blob.name = f"bronze/flights/ryanair/{airport}/{date.strftime(DATE_FORMAT)}.json"
    blob.download_as_text.return_value = "\n".join(json.dumps(f, default=_serialize_datetime) for f in flights)
    return blob


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture(scope="function")
def tmp_cache_dir(tmp_path, mocker):
    """
    Creates a temporary directory for cache files and patches
    `cache.LOCAL_FLIGHT_CACHE_DIR` to point to this temporary location. Ensures
    isolation between tests. Yields a tuple of (cache_dir_path, create_mock_cache_file
    helper).
    """
    test_dir = tmp_path / "test_cache"
    test_dir.mkdir()
    mocker.patch(
        "cache.LOCAL_FLIGHT_CACHE_DIR", os.path.join(str(test_dir), "flights", "{airline}", "{origin}", "{yyyymm}")
    )
    mocker.patch("cache.os.makedirs", wraps=os.makedirs)

    def create_mock_cache_file(
        airport: str, timestamp: datetime, content: list[Flight], airline: str = "ryanair"
    ) -> str:
        """
        Helper to create a mock cache file with specific content and timestamp in the
        temporary cache directory managed by the fixture.
        """
        month_dir = os.path.join(str(test_dir), "flights", airline, airport, timestamp.strftime("%Y%m"))
        os.makedirs(month_dir, exist_ok=True)
        filepath = os.path.join(month_dir, f"{timestamp.strftime(DATE_FORMAT)}.json")
        with open(filepath, "w") as f:
            json.dump(content, f, default=_serialize_datetime)
        return filepath

    yield str(test_dir), create_mock_cache_file


# --- GCS ---


@pytest.fixture
def mock_gcs_bucket(mocker):
    """Patches cache.GCS_BUCKET_NAME and cache.storage.Client to return a mock bucket."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mock_client = mocker.patch("cache.storage.Client")
    mock_bucket = MagicMock()
    mock_client.return_value.bucket.return_value = mock_bucket
    return mock_bucket


@pytest.fixture
def tmp_retry_queue_path(tmp_path, mocker):
    """Patches cache.LOCAL_RETRY_QUEUE_PATH to a temporary location. Returns the resolved ryanair path."""
    mocker.patch("cache.LOCAL_RETRY_QUEUE_PATH", str(tmp_path / "{airline}_retry.json"))
    return tmp_path / "ryanair_retry.json"


# ---------------------------
# Tests for read_cache
# ---------------------------


def test_read_cache_hit_fresh(tmp_cache_dir, caplog):
    """Tests successful reading of a fresh cache file."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN", "ryanair")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    assert result[0].departure_time == SAMPLE_FLIGHT_BCN.departure_time
    assert result[0].arrival_time == SAMPLE_FLIGHT_BCN.arrival_time
    assert f"Cache hit for ryanair/EIN: Loaded {cache_file_date.strftime(DATE_FORMAT)}.json" in caplog.text


def test_read_cache_miss_no_file(mocker, tmp_path, caplog):
    """Tests cache miss when no cache file exists for the airport."""
    non_existent_dir = tmp_path / "test_cache"
    mocker.patch(
        "cache.LOCAL_FLIGHT_CACHE_DIR",
        os.path.join(str(non_existent_dir), "flights", "{airline}", "{origin}", "{yyyymm}"),
    )

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair")

    assert result is None
    assert "Cache miss for ryanair/EIN: no cache directory found." in caplog.text


def test_read_cache_miss_stale_file(tmp_cache_dir, caplog):
    """
    Tests cache miss when the existing file is older than CACHE_TTL,
    and verifies the stale file is kept (not deleted) as valid history.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    stale_cache_file_date = (FROZEN_NOW - timedelta(days=2)).replace(hour=0)
    stale_filepath = create_mock_cache_file("EIN", stale_cache_file_date, [make_dummy_flight("STALE")])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair")

    assert result is None
    assert os.path.exists(stale_filepath)
    assert "Cache miss for ryanair/EIN: No fresh cache found." in caplog.text


def test_read_cache_corrupt_file(tmp_cache_dir, caplog):
    """Tests handling of a corrupt (invalid JSON) cache file, ensuring it's deleted."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    filepath = create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])
    with open(filepath, "w") as f:
        f.write("{invalid json content")

    with caplog.at_level(logging.ERROR):
        result = read_cache("EIN", "ryanair")

    assert result is None
    assert not os.path.exists(filepath)
    assert "corrupt" in caplog.text


def test_read_cache_returns_fresh_file_and_keeps_stale_history(tmp_cache_dir, caplog):
    """
    Tests that the one fresh (today's) file is returned while older stale files are
    kept as history; only the malformed filename is deleted. With a 1-day TTL, at
    most one calendar day can be "fresh" at a time, so there's no "newest of several
    fresh files" scenario anymore — just fresh-vs-stale.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    older_stale_date = (FROZEN_NOW - timedelta(days=3)).replace(hour=0)
    stale_date = (FROZEN_NOW - timedelta(days=2)).replace(hour=0)
    fresh_date = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)

    older_stale_filepath = create_mock_cache_file("EIN", older_stale_date, [make_dummy_flight("OLDER_STALE")])
    stale_filepath = create_mock_cache_file("EIN", stale_date, [make_dummy_flight("STALE")])
    fresh_filepath = create_mock_cache_file("EIN", fresh_date, [make_dummy_flight("FRESH")])

    # Also create a malformed filename, in the same month directory as the other files
    month_dir = os.path.join(tmp_cache_dir_path, "flights", "ryanair", "EIN", fresh_date.strftime("%Y%m"))
    malformed_filename_filepath = os.path.join(month_dir, "MALFORMED_TIMESTAMP.json")
    with open(malformed_filename_filepath, "w") as f:
        json.dump([{"destination_iata": "MALFORMED_FILE"}], f)

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN", "ryanair")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "FRESH"
    assert os.path.exists(older_stale_filepath)
    assert os.path.exists(stale_filepath)
    assert os.path.exists(fresh_filepath)
    assert not os.path.exists(malformed_filename_filepath)
    assert "Cleaned up malformed cache filename file: MALFORMED_TIMESTAMP.json" in caplog.text
    assert f"Cache hit for ryanair/EIN: Loaded {fresh_date.strftime(DATE_FORMAT)}.json" in caplog.text


# ---------------------------
# Tests for write_cache
# ---------------------------


def test_write_cache_creates_new_file_and_keeps_old(tmp_cache_dir, caplog):
    """
    Tests that write_cache creates a new file and keeps existing valid files for
    the same airport (history) as well as files for other airports.
    """
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    old_ein_file_date1 = (FROZEN_NOW - timedelta(days=2)).replace(hour=0)
    old_ein_file_date2 = (FROZEN_NOW - timedelta(days=1)).replace(hour=0)
    ams_file_date = (FROZEN_NOW - timedelta(days=3)).replace(hour=0)

    old_ein_filepath1 = create_mock_cache_file("EIN", old_ein_file_date1, [make_dummy_flight("OLD1")])
    old_ein_filepath2 = create_mock_cache_file("EIN", old_ein_file_date2, [make_dummy_flight("OLD2")])
    ams_filepath = create_mock_cache_file("AMS", ams_file_date, [make_dummy_flight("AMS_FLIGHT")])

    with caplog.at_level(logging.INFO):
        write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN])

    expected_filename = f"{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(
        tmp_cache_dir_path, "flights", "ryanair", "EIN", FROZEN_NOW.strftime("%Y%m"), expected_filename
    )
    assert os.path.exists(expected_filepath)

    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert len(loaded_data) == 1
    assert loaded_data[0]["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata
    assert loaded_data[0]["departure_time"] == SAMPLE_FLIGHT_BCN.departure_time.isoformat()

    assert os.path.exists(old_ein_filepath1)
    assert os.path.exists(old_ein_filepath2)
    assert os.path.exists(ams_filepath)
    assert f"Cache written to ryanair/EIN/{FROZEN_NOW.strftime('%Y%m')}/{expected_filename}" in caplog.text


def test_write_cache_cleans_up_malformed_filename(tmp_cache_dir, caplog):
    """Tests that write_cache deletes only malformed filenames for this airport, keeping valid ones."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    valid_file_date = (FROZEN_NOW - timedelta(days=1)).replace(hour=0)
    valid_filepath = create_mock_cache_file("EIN", valid_file_date, [make_dummy_flight("OLD")])

    month_dir = os.path.join(tmp_cache_dir_path, "flights", "ryanair", "EIN", FROZEN_NOW.strftime("%Y%m"))
    malformed_filepath = os.path.join(month_dir, "BADTIMESTAMP.json")
    with open(malformed_filepath, "w") as f:
        json.dump([{"destination_iata": "BAD"}], f)

    with caplog.at_level(logging.INFO):
        write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN])

    assert os.path.exists(valid_filepath)
    assert not os.path.exists(malformed_filepath)
    assert "Cleaned up malformed cache filename file for ryanair/EIN: BADTIMESTAMP.json" in caplog.text


def test_write_cache_no_cleanup_needed(tmp_cache_dir, caplog):
    """Tests write_cache when there are no existing files for the airport."""
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir

    with caplog.at_level(logging.INFO):
        write_cache("AMS", "ryanair", [SAMPLE_FLIGHT_AMS])

    expected_filename = f"{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    month_dir = os.path.join(tmp_cache_dir_path, "flights", "ryanair", "AMS", FROZEN_NOW.strftime("%Y%m"))
    expected_filepath = os.path.join(month_dir, expected_filename)
    assert os.path.exists(expected_filepath)
    assert len(os.listdir(month_dir)) == 1
    assert f"Cache written to ryanair/AMS/{FROZEN_NOW.strftime('%Y%m')}/{expected_filename}" in caplog.text


def test_write_cache_creates_directory_if_not_exists(mocker, tmp_path, caplog):
    """Tests that write_cache correctly creates the cache directory if it doesn't exist."""
    non_existent_cache_dir_path = str(tmp_path / "new_cache_dir")
    mocker.patch(
        "cache.LOCAL_FLIGHT_CACHE_DIR",
        os.path.join(non_existent_cache_dir_path, "flights", "{airline}", "{origin}", "{yyyymm}"),
    )
    mock_makedirs = mocker.patch("cache.os.makedirs", wraps=os.makedirs)

    with caplog.at_level(logging.INFO):
        write_cache("FRA", "ryanair", [])

    expected_month_dir = os.path.join(
        non_existent_cache_dir_path, "flights", "ryanair", "FRA", FROZEN_NOW.strftime("%Y%m")
    )
    # os.makedirs recurses into itself to create missing parent directories, so it's
    # called multiple times here (once per missing path segment) — just confirm the
    # top-level call for our target directory happened among them.
    mock_makedirs.assert_any_call(expected_month_dir, exist_ok=True)
    expected_filename = f"{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(expected_month_dir, expected_filename)
    assert os.path.exists(expected_filepath)
    with open(expected_filepath, "r") as f:
        assert json.load(f) == []


# ---------------------------
# Tests for GCS-First Caching
# ---------------------------


def test_get_gcs_bucket_raises_when_not_configured(mocker):
    """Tests that an empty GCS_BUCKET_NAME raises immediately, without any network attempt."""
    mocker.patch("cache.GCS_BUCKET_NAME", "")

    with pytest.raises(RuntimeError):
        _get_gcs_bucket()


def test_read_cache_gcs_hit_returns_fresh_over_stale(mock_gcs_bucket):
    """
    Tests that read_cache picks the fresh (today's) GCS blob over stale ones. With a
    1-day TTL, at most one calendar day can be "fresh" at a time.
    """
    older_stale_date = (FROZEN_NOW - timedelta(days=3)).replace(hour=0)
    stale_date = (FROZEN_NOW - timedelta(days=2)).replace(hour=0)
    fresh_date = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)

    older_stale_blob = _make_mock_gcs_blob("EIN", older_stale_date, [make_dummy_flight("OLDER_STALE")])
    stale_blob = _make_mock_gcs_blob("EIN", stale_date, [make_dummy_flight("STALE")])
    fresh_blob = _make_mock_gcs_blob("EIN", fresh_date, [make_dummy_flight("FRESH")])
    mock_gcs_bucket.list_blobs.return_value = [older_stale_blob, stale_blob, fresh_blob]

    result = read_cache("EIN", "ryanair")

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "FRESH"
    mock_gcs_bucket.list_blobs.assert_called_once_with(prefix="bronze/flights/ryanair/EIN/")


def test_read_cache_gcs_miss_returns_none(mock_gcs_bucket):
    """Tests that a genuine GCS miss (no fresh blob) returns None without touching local cache."""
    stale_date = (FROZEN_NOW - timedelta(days=2)).replace(hour=0)
    mock_gcs_bucket.list_blobs.return_value = [_make_mock_gcs_blob("EIN", stale_date, [make_dummy_flight("STALE")])]

    result = read_cache("EIN", "ryanair")

    assert result is None


def test_read_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that read_cache falls back to the local cache when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir
    cache_file_date = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    create_mock_cache_file("EIN", cache_file_date, [SAMPLE_FLIGHT_BCN])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair")

    assert result is not None
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    assert "GCS unreachable for ryanair/EIN" in caplog.text


def test_write_cache_uploads_ndjson_to_gcs(mock_gcs_bucket):
    """Tests that write_cache uploads flights as NDJSON to today's GCS blob."""
    write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN])

    month_dir = CLOUD_FLIGHT_CACHE_DIR.format(airline="ryanair", origin="EIN", yyyymm=FROZEN_NOW.strftime("%Y%m"))
    expected_blob_name = f"{month_dir}/{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)
    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.assert_called_once()
    uploaded_content = mock_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content.splitlines()[0])["destination_iata"] == "BCN"


def test_write_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that write_cache falls back to the local cache when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    tmp_cache_dir_path, create_mock_cache_file = tmp_cache_dir

    with caplog.at_level(logging.WARNING):
        write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN])

    expected_filename = f"{FROZEN_NOW.strftime(DATE_FORMAT)}.json"
    expected_filepath = os.path.join(
        tmp_cache_dir_path, "flights", "ryanair", "EIN", FROZEN_NOW.strftime("%Y%m"), expected_filename
    )
    assert os.path.exists(expected_filepath)
    assert "GCS unreachable for ryanair/EIN" in caplog.text


# ---------------------------
# Tests for Retry Queue
# ---------------------------


def test_load_retry_queue_creates_empty_file_if_missing(tmp_retry_queue_path):
    """Tests that loading a missing local retry queue creates an empty one and returns []."""
    assert not tmp_retry_queue_path.exists()

    result = load_retry_queue("ryanair")

    assert result == []
    assert tmp_retry_queue_path.exists()
    assert json.loads(tmp_retry_queue_path.read_text()) == []


def test_save_and_load_retry_queue_roundtrip(tmp_retry_queue_path):
    """Tests that saving and reloading the local retry queue preserves its content."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    save_retry_queue("ryanair", entries)

    assert load_retry_queue("ryanair") == entries


def test_load_retry_queue_gcs_hit(mock_gcs_bucket):
    """Tests that load_retry_queue reads the GCS retry queue blob when it exists."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_text.return_value = json.dumps(entries)
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair")

    assert result == entries
    mock_gcs_bucket.blob.assert_called_once_with("bronze/flights/ryanair/retry.json")


def test_load_retry_queue_gcs_miss_returns_empty(mock_gcs_bucket):
    """Tests that load_retry_queue returns [] when the GCS blob doesn't exist, without touching local."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair")

    assert result == []


def test_load_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that load_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    tmp_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_retry_queue_path.write_text(json.dumps(entries))

    with caplog.at_level(logging.WARNING):
        result = load_retry_queue("ryanair")

    assert result == entries
    assert "GCS unreachable for ryanair retry queue" in caplog.text


def test_save_retry_queue_uploads_to_gcs(mock_gcs_bucket):
    """Tests that save_retry_queue uploads the queue as JSON to the GCS retry queue blob."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    save_retry_queue("ryanair", entries)

    mock_gcs_bucket.blob.assert_called_once_with("bronze/flights/ryanair/retry.json")
    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.assert_called_once()
    uploaded_content = mock_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content) == entries


def test_save_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that save_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    with caplog.at_level(logging.WARNING):
        save_retry_queue("ryanair", entries)

    assert json.loads(tmp_retry_queue_path.read_text()) == entries
    assert "GCS unreachable for ryanair retry queue" in caplog.text
