import json
import logging
import os
from datetime import datetime, timedelta
from typing import Callable
from unittest.mock import MagicMock

import pytest
from google.api_core.exceptions import PreconditionFailed

from cache import (
    read_cache,
    write_cache,
    update_cache,
    load_retry_queue,
    update_retry_queue,
    _serialize_datetime,
    _get_gcs_bucket,
)
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


def _append_entry(entry: dict) -> Callable[[list[dict]], list[dict]]:
    """Returns a mutate function that appends `entry` to the queue, if not already present."""

    def _mutate(queue: list[dict]) -> list[dict]:
        return queue if entry in queue else [*queue, entry]

    return _mutate


def _replace_with(new_queue: list[dict]) -> Callable[[list[dict]], list[dict]]:
    """Returns a mutate function that ignores the current queue and returns `new_queue`."""
    return lambda _queue: new_queue


def _append_flight(flight: Flight) -> Callable[[list[Flight]], list[Flight]]:
    """Returns a mutate function that appends `flight` to the flight list."""
    return lambda flights: [*flights, flight]


# ---------------------------
# Helpers
# ---------------------------


def _make_mock_gcs_blob(flights: list[Flight], exists: bool = True) -> MagicMock:
    """Builds a mock GCS blob whose content is the NDJSON serialization of the given flights."""
    blob = MagicMock()
    blob.exists.return_value = exists
    blob.download_as_text.return_value = "\n".join(json.dumps(f, default=_serialize_datetime) for f in flights)
    return blob


def _make_mock_gcs_cache_blob(flights: list[Flight], generation: int) -> MagicMock:
    """Builds a mock GCS blob (as returned by bucket.get_blob) with NDJSON content/generation."""
    blob = MagicMock()
    blob.generation = generation
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
    result = write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

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
    assert result is True


def test_write_cache_without_precondition_omits_if_generation_match(mock_gcs_bucket):
    """Tests that the default (no if_generation_match) write is a plain, unconditional overwrite."""
    write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

    mock_blob = mock_gcs_bucket.blob.return_value
    assert "if_generation_match" not in mock_blob.upload_from_string.call_args.kwargs


def test_write_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that write_cache falls back to the local cache when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))

    with caplog.at_level(logging.WARNING):
        result = write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT))

    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_filepath = os.path.join(tmp_cache_dir, day_dir, expected_filename)
    assert os.path.exists(expected_filepath)
    assert "GCS unreachable for ryanair-EIN" in caplog.text
    assert result is True


# ---------------------------
# Tests for write_cache CAS (if_generation_match)
# ---------------------------


def test_write_cache_with_if_generation_match_passes_precondition_through(mock_gcs_bucket):
    """Tests that a given if_generation_match is forwarded to the GCS upload call."""
    result = write_cache("EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT), if_generation_match=0)

    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.assert_called_once()
    assert mock_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    assert result is True


def test_write_cache_drops_write_on_cas_conflict_without_local_fallback(mock_gcs_bucket, caplog):
    """
    Tests the exact scenario this round closes: a stale writer (e.g. a zombie ingestion
    container) whose if_generation_match=0 precondition no longer holds because a newer write
    (e.g. retry_failed_ingests' merged recovered flights) already created the blob. The write
    must be dropped (not silently clobber the newer data) and must NOT fall back to writing
    the local cache instead — a CAS conflict is not "GCS unreachable".
    """
    mock_blob = mock_gcs_bucket.blob.return_value
    mock_blob.upload_from_string.side_effect = PreconditionFailed("conflict")

    with caplog.at_level(logging.WARNING):
        result = write_cache(
            "EIN", "ryanair", [SAMPLE_FLIGHT_BCN], FROZEN_NOW.strftime(DATE_FORMAT), if_generation_match=0
        )

    assert result is False
    assert "dropped" in caplog.text
    assert "GCS unreachable" not in caplog.text


# ---------------------------
# Tests for update_cache (CAS mutate)
# ---------------------------


def test_update_cache_creates_new_blob_with_if_generation_match_zero(mock_gcs_bucket):
    """Tests that mutating a not-yet-existing blob uses if_generation_match=0 (must-not-exist)."""
    mock_gcs_bucket.get_blob.return_value = None
    new_blob = MagicMock()
    mock_gcs_bucket.blob.return_value = new_blob

    update_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT), _append_flight(SAMPLE_FLIGHT_BCN))

    day_dir = CLOUD_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{day_dir}/{expected_filename}"
    mock_gcs_bucket.get_blob.assert_called_once_with(expected_blob_name)
    new_blob.upload_from_string.assert_called_once()
    assert new_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    uploaded_content = new_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content.splitlines()[0])["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata


def test_update_cache_writes_existing_blob_with_its_real_generation(mock_gcs_bucket):
    """Tests that mutating an existing blob uses if_generation_match=<the blob's actual generation>."""
    existing_flight = make_dummy_flight("EXISTING")
    existing_blob = _make_mock_gcs_cache_blob([existing_flight], generation=42)
    mock_gcs_bucket.get_blob.return_value = existing_blob

    update_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT), _append_flight(SAMPLE_FLIGHT_BCN))

    existing_blob.upload_from_string.assert_called_once()
    assert existing_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 42
    uploaded_lines = existing_blob.upload_from_string.call_args[0][0].splitlines()
    assert json.loads(uploaded_lines[0])["destination_iata"] == "EXISTING"
    assert json.loads(uploaded_lines[1])["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata


def test_update_cache_cas_conflict_retries_against_freshly_read_state(mock_gcs_bucket):
    """
    Tests that a PreconditionFailed on the first write attempt causes a re-read and a retry —
    and that the mutate function is re-applied against the freshly-read state (simulating a
    concurrent writer, e.g. a zombie ingest_airport container, landing a write between the
    first read and the first write here), not blindly retried with the stale first-read
    snapshot.
    """
    concurrent_flight = make_dummy_flight("CONCURRENT")

    stale_blob = _make_mock_gcs_cache_blob([], generation=1)
    stale_blob.upload_from_string.side_effect = PreconditionFailed("conflict")
    fresh_blob = _make_mock_gcs_cache_blob([concurrent_flight], generation=2)

    mock_gcs_bucket.get_blob.side_effect = [stale_blob, fresh_blob]

    update_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT), _append_flight(SAMPLE_FLIGHT_BCN))

    assert mock_gcs_bucket.get_blob.call_count == 2
    stale_blob.upload_from_string.assert_called_once()
    fresh_blob.upload_from_string.assert_called_once()
    assert fresh_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 2
    uploaded_lines = fresh_blob.upload_from_string.call_args[0][0].splitlines()
    # both the concurrent writer's flight (from the fresh re-read) and ours must survive
    assert json.loads(uploaded_lines[0])["destination_iata"] == "CONCURRENT"
    assert json.loads(uploaded_lines[1])["destination_iata"] == SAMPLE_FLIGHT_BCN.destination_iata


def test_update_cache_raises_after_cas_attempts_exhausted(mock_gcs_bucket):
    """Tests that persistent CAS conflicts raise a clear error rather than retrying forever."""
    always_conflicting_blob = _make_mock_gcs_cache_blob([], generation=1)
    always_conflicting_blob.upload_from_string.side_effect = PreconditionFailed("conflict")
    mock_gcs_bucket.get_blob.return_value = always_conflicting_blob

    with pytest.raises(RuntimeError):
        update_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT), _append_flight(SAMPLE_FLIGHT_BCN))

    assert mock_gcs_bucket.get_blob.call_count == 5


def test_update_cache_falls_back_to_local_when_gcs_unreachable(mocker, tmp_cache_dir, caplog):
    """Tests that update_cache falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    now = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    create_mock_cache_file(tmp_cache_dir, "EIN", now, [make_dummy_flight("EXISTING")])

    with caplog.at_level(logging.WARNING):
        update_cache("EIN", "ryanair", FROZEN_NOW.strftime(DATE_FORMAT), _append_flight(SAMPLE_FLIGHT_BCN))

    day_dir = LOCAL_FLIGHT_CACHE_SUBPATH.format(
        airline="ryanair", yyyymm=FROZEN_NOW.strftime("%Y%m"), dd=FROZEN_NOW.strftime("%d")
    )
    expected_filename = FLIGHT_CACHE_FILENAME.format(origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT))
    expected_filepath = os.path.join(tmp_cache_dir, day_dir, expected_filename)
    with open(expected_filepath, "r") as f:
        loaded_data = json.load(f)
    assert [flight["destination_iata"] for flight in loaded_data] == ["EXISTING", SAMPLE_FLIGHT_BCN.destination_iata]
    assert "GCS unreachable for ryanair-EIN" in caplog.text


# ---------------------------
# Tests for Retry Queue
# ---------------------------


def test_load_retry_queue_returns_empty_without_creating_file_if_missing(tmp_retry_queue_path):
    """Tests that loading a missing local retry queue returns [] without creating a file."""
    assert not tmp_retry_queue_path.exists()

    result = load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == []
    assert not tmp_retry_queue_path.exists()


def test_update_and_load_retry_queue_roundtrip_local(tmp_retry_queue_path):
    """Tests that updating and reloading the local retry queue preserves its mutated content."""
    entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(entry))

    assert load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT)) == [entry]


def test_update_retry_queue_local_mutate_sees_current_state(tmp_retry_queue_path):
    """Tests that the mutate function is applied against the queue's actual current content."""
    first = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    second = {"origin_iata": "EIN", "query_date": "2026-08-19"}
    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(first))

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(second))

    assert load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT)) == [first, second]


def test_update_retry_queue_keyed_per_origin_dont_collide(tmp_retry_queue_path):
    """Tests that two origins' queues are stored independently and don't overwrite each other."""
    ein_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    stn_entry = {"origin_iata": "STN", "query_date": "2026-08-19"}

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(ein_entry))
    update_retry_queue("ryanair", "STN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(stn_entry))

    assert load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT)) == [ein_entry]
    assert load_retry_queue("ryanair", "STN", FROZEN_NOW.strftime(DATE_FORMAT)) == [stn_entry]


def test_update_retry_queue_deletes_local_file_when_result_empty(tmp_retry_queue_path):
    """Tests that a mutate result of [] deletes the file rather than writing an empty list."""
    entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(entry))
    assert tmp_retry_queue_path.exists()

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _replace_with([]))

    assert not tmp_retry_queue_path.exists()


def test_update_retry_queue_local_noop_when_already_absent(tmp_retry_queue_path):
    """Tests that a mutate result of [] when no file exists yet doesn't raise or create one."""
    assert not tmp_retry_queue_path.exists()

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _replace_with([]))

    assert not tmp_retry_queue_path.exists()


def test_load_retry_queue_gcs_hit(mock_gcs_bucket):
    """Tests that load_retry_queue reads the GCS retry queue blob when it exists."""
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    mock_blob = MagicMock()
    mock_blob.exists.return_value = True
    mock_blob.download_as_text.return_value = json.dumps(entries)
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == entries
    subpath = CLOUD_RETRY_CACHE_SUBPATH.format(
        airline="ryanair", origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT)
    )
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{subpath}"
    mock_gcs_bucket.blob.assert_called_once_with(expected_blob_name)


def test_load_retry_queue_gcs_miss_returns_empty(mock_gcs_bucket):
    """Tests that load_retry_queue returns [] when the GCS blob doesn't exist, without touching local."""
    mock_blob = MagicMock()
    mock_blob.exists.return_value = False
    mock_gcs_bucket.blob.return_value = mock_blob

    result = load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == []


def test_load_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that load_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entries = [{"origin_iata": "EIN", "query_date": "2026-08-18"}]
    tmp_retry_queue_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_retry_queue_path.write_text(json.dumps(entries))

    with caplog.at_level(logging.WARNING):
        result = load_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT))

    assert result == entries
    assert "GCS unreachable for ryanair-EIN retry queue" in caplog.text


def _make_mock_gcs_retry_blob(entries: list[dict], generation: int) -> MagicMock:
    """Builds a mock GCS blob (as returned by bucket.get_blob) with the given content/generation."""
    blob = MagicMock()
    blob.generation = generation
    blob.download_as_text.return_value = json.dumps(entries)
    return blob


def test_update_retry_queue_creates_new_blob_with_if_generation_match_zero(mock_gcs_bucket):
    """Tests that writing a not-yet-existing blob uses if_generation_match=0 (must-not-exist)."""
    entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    mock_gcs_bucket.get_blob.return_value = None
    new_blob = MagicMock()
    mock_gcs_bucket.blob.return_value = new_blob

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(entry))

    subpath = CLOUD_RETRY_CACHE_SUBPATH.format(
        airline="ryanair", origin="EIN", yyyymmdd=FROZEN_NOW.strftime(DATE_FORMAT)
    )
    expected_blob_name = f"{CLOUD_CACHE_ROOT}/{subpath}"
    mock_gcs_bucket.get_blob.assert_called_once_with(expected_blob_name)
    new_blob.upload_from_string.assert_called_once()
    assert new_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 0
    uploaded_content = new_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content) == [entry]


def test_update_retry_queue_writes_existing_blob_with_its_real_generation(mock_gcs_bucket):
    """Tests that writing an existing blob uses if_generation_match=<the blob's actual generation>."""
    existing_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    new_entry = {"origin_iata": "EIN", "query_date": "2026-08-19"}
    existing_blob = _make_mock_gcs_retry_blob([existing_entry], generation=42)
    mock_gcs_bucket.get_blob.return_value = existing_blob

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(new_entry))

    existing_blob.upload_from_string.assert_called_once()
    assert existing_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 42
    uploaded_content = existing_blob.upload_from_string.call_args[0][0]
    assert json.loads(uploaded_content) == [existing_entry, new_entry]


def test_update_retry_queue_deletes_existing_blob_when_result_empty(mock_gcs_bucket):
    """Tests that a mutate result of [] deletes the blob using its generation, not an upload."""
    existing_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}
    existing_blob = _make_mock_gcs_retry_blob([existing_entry], generation=7)
    mock_gcs_bucket.get_blob.return_value = existing_blob

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _replace_with([]))

    existing_blob.delete.assert_called_once_with(if_generation_match=7)
    existing_blob.upload_from_string.assert_not_called()


def test_update_retry_queue_gcs_noop_when_already_absent_and_result_empty(mock_gcs_bucket):
    """Tests that a mutate result of [] when no blob exists yet doesn't attempt a delete or upload."""
    mock_gcs_bucket.get_blob.return_value = None

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _replace_with([]))

    mock_gcs_bucket.blob.return_value.delete.assert_not_called()
    mock_gcs_bucket.blob.return_value.upload_from_string.assert_not_called()


def test_update_retry_queue_cas_conflict_retries_against_freshly_read_state(mock_gcs_bucket):
    """
    Tests that a PreconditionFailed on the first write attempt causes a re-read and a retry —
    and that the mutate function is re-applied against the freshly-read state (simulating a
    concurrent writer's append that landed between the first read and the first write), not
    blindly retried with the stale first-read snapshot.
    """
    concurrent_entry = {"origin_iata": "STN", "query_date": "2026-08-19"}
    our_entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}

    stale_blob = _make_mock_gcs_retry_blob([], generation=1)
    stale_blob.upload_from_string.side_effect = PreconditionFailed("conflict")
    fresh_blob = _make_mock_gcs_retry_blob([concurrent_entry], generation=2)

    mock_gcs_bucket.get_blob.side_effect = [stale_blob, fresh_blob]

    update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(our_entry))

    assert mock_gcs_bucket.get_blob.call_count == 2
    stale_blob.upload_from_string.assert_called_once()
    fresh_blob.upload_from_string.assert_called_once()
    assert fresh_blob.upload_from_string.call_args.kwargs["if_generation_match"] == 2
    uploaded_content = fresh_blob.upload_from_string.call_args[0][0]
    # both the concurrent writer's entry (from the fresh re-read) and ours must survive
    assert json.loads(uploaded_content) == [concurrent_entry, our_entry]


def test_update_retry_queue_raises_after_cas_attempts_exhausted(mock_gcs_bucket):
    """Tests that persistent CAS conflicts raise a clear error rather than retrying forever."""
    always_conflicting_blob = _make_mock_gcs_retry_blob([], generation=1)
    always_conflicting_blob.upload_from_string.side_effect = PreconditionFailed("conflict")
    mock_gcs_bucket.get_blob.return_value = always_conflicting_blob

    with pytest.raises(RuntimeError):
        update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry({"a": 1}))

    assert mock_gcs_bucket.get_blob.call_count == 5


def test_update_retry_queue_falls_back_to_local_when_gcs_unreachable(mocker, tmp_retry_queue_path, caplog):
    """Tests that update_retry_queue falls back to local when GCS itself is unreachable."""
    mocker.patch("cache.GCS_BUCKET_NAME", "test-bucket")
    mocker.patch("cache.storage.Client", side_effect=Exception("connection refused"))
    entry = {"origin_iata": "EIN", "query_date": "2026-08-18"}

    with caplog.at_level(logging.WARNING):
        update_retry_queue("ryanair", "EIN", FROZEN_NOW.strftime(DATE_FORMAT), _append_entry(entry))

    assert json.loads(tmp_retry_queue_path.read_text()) == [entry]
    assert "GCS unreachable for ryanair-EIN retry queue" in caplog.text
