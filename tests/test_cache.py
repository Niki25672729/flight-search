import json
import logging
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from cache import read_cache, write_cache, load_retry_queue, save_retry_queue, _serialize_datetime, _get_gcs_bucket
from config import DATE_FORMAT, FLIGHT_CACHE_FILENAME
from models import Flight
from conftest import (
    FROZEN_NOW,
    SAMPLE_FLIGHT_BCN,
    make_dummy_flight,
    CLOUD_CACHE_ROOT,
    LOCAL_FLIGHT_CACHE_SUBPATH,
    CLOUD_FLIGHT_CACHE_SUBPATH,
    CLOUD_RETRY_CACHE_SUBPATH,
)


# ---------------------------
# Helpers
# ---------------------------


def _make_mock_gcs_blob(flights: list[Flight], exists: bool = True) -> MagicMock:
    """Builds a mock GCS blob whose content is the NDJSON serialization of the given flights."""
    blob = MagicMock()
    blob.exists.return_value = exists
    blob.download_as_text.return_value = "\n".join(json.dumps(f, default=_serialize_datetime) for f in flights)
    return blob


def create_mock_cache_file(
    cache_dir: str, airport: str, timestamp: datetime, content: list[Flight], airline: str = "ryanair"
) -> str:
    """Creates a mock cache file with specific content and timestamp in the given cache directory."""
    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline=airline, yyyymm=timestamp.strftime("%Y%m"), dd=timestamp.strftime("%d")
    )
    full_dir = os.path.join(cache_dir, day_dir)
    os.makedirs(full_dir, exist_ok=True)
    filename = FLIGHT_CACHE_FILENAME.format(origin=airport, yyyymmdd=timestamp.strftime(DATE_FORMAT))
    filepath = os.path.join(full_dir, filename)
    with open(filepath, "w") as f:
        json.dump(content, f, default=_serialize_datetime)
    return filepath


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture
def tmp_cache_dir(tmp_path, mocker):
    test_dir = tmp_path / "test_cache"
    test_dir.mkdir()
    mocker.patch("cache.LOCAL_FLIGHT_CACHE_DIR", os.path.join(str(test_dir), LOCAL_FLIGHT_CACHE_SUBPATH))
    mocker.patch("cache.os.makedirs", wraps=os.makedirs)
    yield str(test_dir)


# --- GCS ---


@pytest.fixture
def mock_gcs_bucket(mocker):
    """Patches cache.GCS_BUCKET_NAME and cache.storage.Client to return a mock bucket."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mock_client = mocker.patch("cache.storage.Client")
    mock_bucket = MagicMock()
    mock_client.return_value.bucket.return_value = mock_bucket
    return mock_bucket


# ---------------------------
# Tests for read_cache
# ---------------------------


def test_read_cache_hit_fresh(tmp_cache_dir, caplog):
    """Tests successful reading of today's cache file."""
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    create_mock_cache_file(tmp_cache_dir, "EIN", now, [SAMPLE_FLIGHT_BCN])

    with caplog.at_level(logging.INFO):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    assert result[0].departure_time == SAMPLE_FLIGHT_BCN.departure_time
    assert result[0].arrival_time == SAMPLE_FLIGHT_BCN.arrival_time
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=now.strftime(DATE_FORMAT))
    assert f"Cache hit for ryanair-EIN: Loaded {expected_filename}" in caplog.text


def test_read_cache_miss_no_file(tmp_cache_dir, caplog):
    """Tests cache miss when no cache file exists for today."""
    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is None
    assert "Cache miss for ryanair-EIN: no cache file found for today." in caplog.text


def test_read_cache_ignores_previous_day_file_for_same_origin(tmp_cache_dir, caplog):
    """
    Tests that a previous day's file for the same origin isn't picked up as a cache hit.
    Each day now has its own directory, so history is kept naturally without any special handling.
    """
    yesterday = (FROZEN_NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_filepath = create_mock_cache_file(tmp_cache_dir, "EIN", yesterday, [make_dummy_flight("YESTERDAY")])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is None
    assert os.path.exists(yesterday_filepath)
    assert "Cache miss for ryanair-EIN: no cache file found for today." in caplog.text


def test_read_cache_ignores_other_origin_file_in_same_day_dir(tmp_cache_dir, caplog):
    """Tests that another origin's file in the same day directory doesn't affect this origin's read."""
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    ams_filepath = create_mock_cache_file(tmp_cache_dir, "AMS", now, [make_dummy_flight("AMS_FLIGHT")])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is None
    assert os.path.exists(ams_filepath)
    assert "Cache miss for ryanair-EIN: no cache file found for today." in caplog.text


def test_read_cache_corrupt_file(tmp_cache_dir, caplog):
    """Tests handling of a corrupt (invalid JSON) cache file, ensuring it's deleted."""
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    filepath = create_mock_cache_file(tmp_cache_dir, "EIN", now, [SAMPLE_FLIGHT_BCN])
    with open(filepath, "w") as f:
        f.write("{invalid json content")

    with caplog.at_level(logging.ERROR):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is None
    assert not os.path.exists(filepath)
    assert "corrupt" in caplog.text


# ---------------------------
# Tests for write_cache
# ---------------------------


def test_write_cache_creates_new_file_and_keeps_history(tmp_cache_dir, caplog):
    """
    Tests that write_cache creates today's file without touching previous days' files
    for the same origin, or other origins' files in today's directory.
    """
    yesterday = (FROZEN_NOW - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_filepath = create_mock_cache_file(tmp_cache_dir, "EIN", yesterday, [make_dummy_flight("YESTERDAY")])
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    ams_filepath = create_mock_cache_file(tmp_cache_dir, "AMS", now, [make_dummy_flight("AMS_FLIGHT")])

    with caplog.at_level(logging.INFO):
        write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_filepath = os.path.join(tmp_cache_dir, day_dir, expected_filename)
    assert os.path.exists(expected_filepath)

    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert len(loaded_data) == 1
    assert loaded_data[0]["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata
    assert loaded_data[0]["departure_time"] == SAMPLE_FLIGHT_BCN.departure_time.isoformat()

    assert os.path.exists(yesterday_filepath)
    assert os.path.exists(ams_filepath)
    assert f"Cache written to {expected_filepath}" in caplog.text


def test_write_cache_creates_directory_if_not_exists(mocker, tmp_path, caplog):
    """Tests that write_cache correctly creates the cache directory if it doesn't exist."""
    non_existent_cache_dir_path = str(tmp_path / "new_cache_dir")
    mocker.patch("cache.LOCAL_FLIGHT_CACHE_DIR", os.path.join(non_existent_cache_dir_path, LOCAL_FLIGHT_CACHE_SUBPATH))
    mock_makedirs = mocker.patch("cache.os.makedirs", wraps=os.makedirs)

    with caplog.at_level(logging.INFO):
        write_cache("FRA", "ryanair", [], FROZEN_NOW.strftime(DATE_FORMAT))

    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_day_dir = os.path.join(non_existent_cache_dir_path, day_dir)
    mock_makedirs.assert_any_call(expected_day_dir, exist_ok=True)
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="FRA", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_filepath = os.path.join(expected_day_dir, expected_filename)
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


def test_read_cache_gcs_hit(mock_gcs_bucket):
    """Tests that read_cache returns flights from today's GCS blob when it exists."""
    mock_gcs_bucket.blob.return_value = _make_mock_gcs_blob([make_dummy_flight("FRESH")])

    result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is not None
    assert len(result) == 1
    assert result[0].destination_iata == "FRESH"
    day_dir = CLOUD_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{day_dir}/{expected_filename}"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)


def test_read_cache_gcs_miss_returns_none(mock_gcs_bucket):
    """Tests that a genuine GCS miss (no blob for today) returns None without touching local cache."""
    mock_gcs_bucket.blob.return_value = _make_mock_gcs_blob([], exists=False)

    result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is None


def test_read_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that read_cache falls back to the local cache when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    create_mock_cache_file(tmp_cache_dir, "EIN", now, [SAMPLE_FLIGHT_BCN])

    with caplog.at_level(logging.WARNING):
        result = read_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result is not None
    assert result[0].destination_iata == SAMPLE_FLIGHT_BCN.destination_iata
    assert "GCS unreachable for ryanair-EIN" in caplog.text


def test_write_cache_uploads_ndjson_to_gcs(mock_gcs_bucket):
    """Tests that write_cache uploads flights as NDJSON to today's GCS blob."""
    write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

    day_dir = CLOUD_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{day_dir}/{expected_filename}"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)
    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.assert_called_once()
    uploaded_content = mock_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content.splitlines()[0])["destination_iata"] == "BCN"


def test_write_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that write_cache falls back to the local cache when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))

    with caplog.at_level(logging.WARNING):
        write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_filepath = os.path.join(tmp_cache_dir, day_dir, expected_filename)
    assert os.path.exists(expected_filepath)
    assert "GCS unreachable for ryanair-EIN" in caplog.text


# ---------------------------
# Tests for Retry Queue
# ---------------------------


def test_load_retry_queue_returns_empty_without_creating_file_if_missing(tmp_retry_queue_path):
    """Tests that loading a missing local retry queue returns [] without creating a file."""
    assert not tmp_retry_queue_path.exists()

    result = load_retry_queue("ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == []
    assert not tmp_retry_queue_path.exists()


def test_save_and_load_retry_queue_roundtrip(tmp_retry_queue_path):
    """Tests that saving and reloading the local retry queue preserves its content."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    save_retry_queue("ryanair", entries, FROZEN_NOW.strftime(DATE_FORMAT))

    assert load_retry_queue("ryanair", FROZEN_NOW.strftime(DATE_FORMAT)) == entries


def test_save_retry_queue_deletes_local_file_when_queue_empty(tmp_retry_queue_path):
    """Tests that saving an empty queue deletes the file rather than writing an empty list."""
    save_retry_queue("ryanair", [{"origin_iata": "EIN", "query_date": "2026-08-18"}], FROZEN_NOW.strftime(DATE_FORMAT))
    assert tmp_retry_queue_path.exists()

    save_retry_queue("ryanair", [], FROZEN_NOW.strftime(DATE_FORMAT))

    assert not tmp_retry_queue_path.exists()


def test_save_retry_queue_local_noop_when_already_absent(tmp_retry_queue_path):
    """Tests that saving an empty queue when no file exists yet doesn't raise or create one."""
    assert not tmp_retry_queue_path.exists()

    save_retry_queue("ryanair", [], FROZEN_NOW.strftime(DATE_FORMAT))

    assert not tmp_retry_queue_path.exists()


def test_load_retry_queue_gcs_hit(mock_gcs_bucket):
    """Tests that load_retry_queue reads the GCS retry queue blob when it exists."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_text.return_value = json.dumps(entries)
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == entries
    subpath = CLOUD_RETRY_CACHE_SUBPATH.format(airline="ryanair", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{subpath}"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)


def test_load_retry_queue_gcs_miss_returns_empty(mock_gcs_bucket):
    """Tests that load_retry_queue returns [] when the GCS blob doesn't exist, without touching local."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == []


def test_load_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that load_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    tmp_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_retry_queue_path.write_text(json.dumps(entries))

    with caplog.at_level(logging.WARNING):
        result = load_retry_queue("ryanair", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == entries
    assert "GCS unreachable for ryanair retry queue" in caplog.text


def test_save_retry_queue_uploads_to_gcs(mock_gcs_bucket):
    """Tests that save_retry_queue uploads the queue as JSON to the GCS retry queue blob."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    save_retry_queue("ryanair", entries, FROZEN_NOW.strftime(DATE_FORMAT))

    subpath = CLOUD_RETRY_CACHE_SUBPATH.format(airline="ryanair", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{subpath}"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)
    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.assert_called_once()
    uploaded_content = mock_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content) == entries


def test_save_retry_queue_deletes_gcs_blob_when_queue_empty(mock_gcs_bucket):
    """Tests that saving an empty queue deletes the GCS blob rather than uploading an empty list."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_gcs_bucket.blob.return_value = mock_blob

    save_retry_queue("ryanair", [], FROZEN_NOW.strftime(DATE_FORMAT))

    mock_blob.delete.assert_called_once()
    mock_blob.upload_from_string.assert_not_called()


def test_save_retry_queue_gcs_noop_when_already_absent(mock_gcs_bucket):
    """Tests that saving an empty queue when no blob exists yet doesn't attempt a delete."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_gcs_bucket.blob.return_value = mock_blob

    save_retry_queue("ryanair", [], FROZEN_NOW.strftime(DATE_FORMAT))

    mock_blob.delete.assert_not_called()
    mock_blob.upload_from_string.assert_not_called()


def test_save_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that save_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]

    with caplog.at_level(logging.WARNING):
        save_retry_queue("ryanair", entries, FROZEN_NOW.strftime(DATE_FORMAT))

    assert json.loads(tmp_retry_queue_path.read_text()) == entries
    assert "GCS unreachable for ryanair retry queue" in caplog.text
