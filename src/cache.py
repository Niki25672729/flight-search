import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from models import Flight
from config import (
    FLIGHT_CACHE_FILENAME,
    GCS_BUCKET_NAME,
    CLOUD_FLIGHT_CACHE_DIR,
    CLOUD_REPORT_PATH,
    CLOUD_RETRY_QUEUE_PATH,
    LOCAL_FLIGHT_CACHE_DIR,
    LOCAL_RETRY_QUEUE_PATH,
)


# ---------------------------
# Constants
# ---------------------------

_CAS_MAX_ATTEMPTS = 5


# ---------------------------
# Exceptions
# ---------------------------


class _CasExhausted(RuntimeError):
    """Raised when a GCS CAS write (flight cache or retry queue) keeps losing the generation-match race."""


# ---------------------------
# Helpers
# ---------------------------


def _serialize_datetime(obj: object) -> str | dict[str, Any]:
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Flight):
        return asdict(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _deserialize_flight_data(data: list[dict]) -> list[Flight]:
    """Deserializes datetime strings back to datetime objects and converts dicts to Flight objects."""
    deserialized_flights: list[Flight] = []
    for flight_dict in data:
        for field_name in ("departure_time", "arrival_time", "scraped_at"):
            value = flight_dict.get(field_name)
            if isinstance(value, str):
                flight_dict[field_name] = datetime.fromisoformat(value).replace(tzinfo=None)
        deserialized_flights.append(Flight(**flight_dict))
    return deserialized_flights


def _delete_file(filepath: str, description: str):
    """
    Attempts to delete a file and prints a message about the outcome.
    """
    try:
        os.remove(filepath)
        logging.warning(f"Cleaned up {description}: {os.path.basename(filepath)}")
    except OSError as e:
        logging.error(f"Error deleting {description} file {os.path.basename(filepath)}: {e}")


# ---------------------------
# Cache
# ---------------------------


# --- Local ---


def _read_local_cache(departure_airport: str, airline: str, date: str) -> list[Flight] | None:
    """
    Reads today's cached flight data for a given departure airport/airline from local.
    Returns a list of Flight objects on cache hit, None on cache miss. A corrupt file is deleted.
    """
    day_dir = LOCAL_FLIGHT_CACHE_DIR.format(airline=airline, yyyymm=date[:6], dd=date[6:8])
    filename = FLIGHT_CACHE_FILENAME.format(origin=departure_airport, yyyymmdd=date)
    filepath = os.path.join(day_dir, filename)

    if not os.path.exists(filepath):
        logging.warning(f"Cache miss for {airline}-{departure_airport}: no cache file found for today.")
        return None

    try:
        with open(filepath, "r") as f:
            data = json.load(f)
        logging.info(f"Cache hit for {airline}-{departure_airport}: Loaded {filename}")
        return _deserialize_flight_data(data)
    except Exception as e:
        # Catch JSONDecodeError specifically, and other read errors
        error_type = "corrupt" if isinstance(e, json.JSONDecodeError) else "problematic"
        logging.error(f"Error: Cache file {filename} is {error_type}. {e}.")
        _delete_file(filepath, f"{error_type} cache file")
        return None


def _write_local_cache(departure_airport: str, airline: str, flights: list[Flight], date: str):
    """Writes flight data to today's cache file for the departure airport/airline to local."""
    day_dir = LOCAL_FLIGHT_CACHE_DIR.format(airline=airline, yyyymm=date[:6], dd=date[6:8])
    os.makedirs(day_dir, exist_ok=True)
    filename = FLIGHT_CACHE_FILENAME.format(origin=departure_airport, yyyymmdd=date)
    filepath = os.path.join(day_dir, filename)

    try:
        with open(filepath, "w") as f:
            json.dump(flights, f, default=_serialize_datetime, indent=4)
        logging.info(f"Cache written to {filepath}")
    except Exception as e:
        logging.error(f"Error writing cache to {filename}: {e}")


# --- Cloud ---


def _get_gcs_bucket() -> storage.Bucket:
    """
    Returns the GCS bucket client.
    """
    if not GCS_BUCKET_NAME:
        raise RuntimeError("GCS_BUCKET_NAME is not configured")
    return storage.Client().bucket(GCS_BUCKET_NAME)


def _gcs_flight_blob_name(departure_airport: str, airline: str, date: str) -> str:
    day_dir = CLOUD_FLIGHT_CACHE_DIR.format(airline=airline, yyyymm=date[:6], dd=date[6:8])
    filename = FLIGHT_CACHE_FILENAME.format(origin=departure_airport, yyyymmdd=date)
    return f"{day_dir}/{filename}"


def _read_gcs_cache(departure_airport: str, airline: str, date: str) -> list[Flight] | None:
    """
    Checks GCS for today's flight blob for this airport.
    """
    bucket = _get_gcs_bucket()
    blob_name = _gcs_flight_blob_name(departure_airport, airline, date)
    blob = bucket.blob(blob_name)

    if not blob.exists():
        logging.info(f"GCS cache miss for {airline}-{departure_airport}: no blob at {blob_name}.")
        return None

    data = [json.loads(line) for line in blob.download_as_text().splitlines() if line.strip()]
    logging.info(f"GCS cache hit for {airline}-{departure_airport}: loaded {blob_name}")
    return _deserialize_flight_data(data)


def _write_gcs_cache(
    departure_airport: str, airline: str, flights: list[Flight], date: str, if_generation_match: int | None = None
) -> bool:
    """
    Writes flights as NDJSON (one record per line) to today's GCS blob for this airport.

    Returns True if the write landed, False if it was dropped because of a generation
    conflict (i.e. someone else's newer write already exists and must not be clobbered).
    """
    bucket = _get_gcs_bucket()
    blob_name = _gcs_flight_blob_name(departure_airport, airline, date)
    lines = "\n".join(json.dumps(flight, default=_serialize_datetime) for flight in flights)
    blob = bucket.blob(blob_name)

    try:
        if if_generation_match is None:
            blob.upload_from_string(lines, content_type="application/x-ndjson")
        else:
            blob.upload_from_string(lines, content_type="application/x-ndjson", if_generation_match=if_generation_match)
    except PreconditionFailed:
        logging.warning(
            f"GCS cache write to {blob_name} dropped: blob changed since this writer last knew its state "
            f"(expected generation {if_generation_match}) — a newer write already landed, not clobbering it."
        )
        return False

    logging.info(f"GCS cache written to {blob_name}")
    return True


def _update_gcs_cache(
    departure_airport: str, airline: str, date: str, mutate: Callable[[list[Flight]], list[Flight]]
) -> None:
    """
    Atomically read-mutate-writes today's GCS flight-cache blob for this airport/airline using
    GCS's generation-match precondition (optimistic concurrency, i.e. compare-and-swap).
    """
    bucket = _get_gcs_bucket()
    blob_name = _gcs_flight_blob_name(departure_airport, airline, date)

    for attempt in range(1, _CAS_MAX_ATTEMPTS + 1):
        blob = bucket.get_blob(blob_name)
        if blob is not None:
            data = [json.loads(line) for line in blob.download_as_text().splitlines() if line.strip()]
            current = _deserialize_flight_data(data)
        else:
            current = []
        new_flights = mutate(current)

        lines = "\n".join(json.dumps(flight, default=_serialize_datetime) for flight in new_flights)
        target = blob if blob is not None else bucket.blob(blob_name)
        if_generation_match = blob.generation if blob is not None else 0

        try:
            target.upload_from_string(
                lines, content_type="application/x-ndjson", if_generation_match=if_generation_match
            )
            logging.info(f"GCS cache written to {blob_name}")
            return
        except PreconditionFailed:
            logging.warning(
                f"CAS conflict writing GCS cache {blob_name} "
                f"(attempt {attempt}/{_CAS_MAX_ATTEMPTS}); re-reading and retrying."
            )
            continue

    raise _CasExhausted(f"Failed to update GCS cache {blob_name} after {_CAS_MAX_ATTEMPTS} CAS attempts.")


# ---------------------------
# Retry Queue
# ---------------------------


# --- Local ---


def _load_local_retry_queue(airline: str, origin: str, date: str) -> list[dict]:
    """Loads today's local retry queue for this airline/origin. Returns [] if the file doesn't exist."""
    path = LOCAL_RETRY_QUEUE_PATH.format(airline=airline, origin=origin, yyyymmdd=date)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load local retry queue for {airline}-{origin}: {e}")
        return []


def _update_local_retry_queue(airline: str, origin: str, date: str, mutate: Callable[[list[dict]], list[dict]]) -> None:
    """
    Reads today's local retry queue, applies `mutate`, and writes the result back (deleting the file when the result is empty).
    """
    path = LOCAL_RETRY_QUEUE_PATH.format(airline=airline, origin=origin, yyyymmdd=date)
    current_queue = _load_local_retry_queue(airline, origin, date)
    new_queue = mutate(current_queue)
    if not new_queue:
        if os.path.exists(path):
            _delete_file(path, "empty retry queue")
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(new_queue, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"Failed to save local retry queue for {airline}-{origin}: {e}")


# --- Cloud ---


def _load_gcs_retry_queue(airline: str, origin: str, date: str) -> list[dict]:
    """Loads today's GCS retry queue blob for this airline/origin. Returns [] if the blob doesn't exist."""
    bucket = _get_gcs_bucket()
    blob = bucket.blob(CLOUD_RETRY_QUEUE_PATH.format(airline=airline, origin=origin, yyyymmdd=date))
    if not blob.exists():
        return []
    return json.loads(blob.download_as_text())


def _update_gcs_retry_queue(airline: str, origin: str, date: str, mutate: Callable[[list[dict]], list[dict]]) -> None:
    """
    Atomically read-mutate-writes today's GCS retry queue blob for this airline/origin using
    GCS's generation-match precondition (optimistic concurrency, i.e. compare-and-swap).
    """
    bucket = _get_gcs_bucket()
    blob_name = CLOUD_RETRY_QUEUE_PATH.format(airline=airline, origin=origin, yyyymmdd=date)

    for attempt in range(1, _CAS_MAX_ATTEMPTS + 1):
        blob = bucket.get_blob(blob_name)
        current_queue = json.loads(blob.download_as_text()) if blob is not None else []
        new_queue = mutate(current_queue)

        try:
            if not new_queue:
                if blob is not None:
                    blob.delete(if_generation_match=blob.generation)
                    logging.info(f"Deleted empty GCS retry queue blob {blob_name}")
                return
            target = blob if blob is not None else bucket.blob(blob_name)
            if_generation_match = blob.generation if blob is not None else 0
            target.upload_from_string(
                json.dumps(new_queue, indent=2, ensure_ascii=False),
                content_type="application/json",
                if_generation_match=if_generation_match,
            )
            logging.info(f"GCS retry queue written to {blob_name}")
            return
        except PreconditionFailed:
            logging.warning(
                f"CAS conflict writing GCS retry queue {blob_name} "
                f"(attempt {attempt}/{_CAS_MAX_ATTEMPTS}); re-reading and retrying."
            )
            continue

    raise _CasExhausted(f"Failed to update GCS retry queue {blob_name} after {_CAS_MAX_ATTEMPTS} CAS attempts.")


# ---------------------------
# Public API
# ---------------------------


# --- Cache ---


def read_cache(departure_airport: str, airline: str, date: str) -> list[Flight] | None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _read_gcs_cache(departure_airport, airline, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{departure_airport} ({e}); falling back to local cache.")
        return _read_local_cache(departure_airport, airline, date)


def write_cache(
    departure_airport: str, airline: str, flights: list[Flight], date: str, if_generation_match: int | None = None
) -> bool:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _write_gcs_cache(departure_airport, airline, flights, date, if_generation_match)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{departure_airport} ({e}); falling back to local cache.")
        _write_local_cache(departure_airport, airline, flights, date)
        return True


def update_cache(
    departure_airport: str, airline: str, date: str, mutate: Callable[[list[Flight]], list[Flight]]
) -> None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        _update_gcs_cache(departure_airport, airline, date, mutate)
    except _CasExhausted:
        raise
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{departure_airport} ({e}); falling back to local cache.")
        current = _read_local_cache(departure_airport, airline, date) or []
        _write_local_cache(departure_airport, airline, mutate(current), date)


# --- Retry Queue ---


def load_retry_queue(airline: str, origin: str, date: str) -> list[dict]:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _load_gcs_retry_queue(airline, origin, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{origin} retry queue ({e}); falling back to local cache.")
        return _load_local_retry_queue(airline, origin, date)


def update_retry_queue(airline: str, origin: str, date: str, mutate: Callable[[list[dict]], list[dict]]) -> None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        _update_gcs_retry_queue(airline, origin, date, mutate)
    except _CasExhausted:
        raise
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{origin} retry queue ({e}); falling back to local cache.")
        _update_local_retry_queue(airline, origin, date, mutate)


# --- Run Report ---


def write_run_report(airline: str, date: str, report: dict) -> bool:
    """
    Best-effort write of generate_run_report's output (pipeline/ingestion/report.py) to GCS, for
    later reference. No local fallback -- unlike read_cache/write_cache, the ingestion container
    has no host volume mount (see MONITORING.md's ambiguous/unknown-airport-discovery gap for the
    same problem with a different artifact), so a local write here would just land somewhere
    nobody ever reads. Never raises: catches any exception, logs a warning, and returns False, so
    generate_run_report's "never fails the task" contract holds regardless of whether this lands.
    """
    try:
        bucket = _get_gcs_bucket()
        blob_name = CLOUD_REPORT_PATH.format(airline=airline, yyyymm=date[:6], dd=date[6:8])
        bucket.blob(blob_name).upload_from_string(
            json.dumps(report, indent=2, ensure_ascii=False), content_type="application/json"
        )
        logging.info(f"Run report written to {blob_name}")
        return True
    except Exception as e:
        logging.warning(f"Failed to write run report for {airline} {date}: {e}")
        return False
