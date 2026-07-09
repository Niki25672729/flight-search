import json
import logging
import os
from dataclasses import asdict
from datetime import datetime
from typing import Any

from google.cloud import storage

from models import Flight
from config import (
    FLIGHT_CACHE_FILENAME,
    GCS_BUCKET_NAME,
    CLOUD_FLIGHT_CACHE_DIR,
    CLOUD_RETRY_QUEUE_PATH,
    LOCAL_FLIGHT_CACHE_DIR,
    LOCAL_RETRY_QUEUE_PATH,
)


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


def _write_gcs_cache(departure_airport: str, airline: str, flights: list[Flight], date: str) -> None:
    """
    PLACEHOLDER: Writes flights as NDJSON (one record per line) to today's GCS blob for this
    airport.
    """
    bucket = _get_gcs_bucket()
    blob_name = _gcs_flight_blob_name(departure_airport, airline, date)
    lines = "\n".join(json.dumps(flight, default=_serialize_datetime) for flight in flights)
    bucket.blob(blob_name).upload_from_string(lines, content_type="application/x-ndjson")
    logging.info(f"GCS cache written to {blob_name}")


# ---------------------------
# Retry Queue
# ---------------------------


# --- Local ---


def _load_local_retry_queue(airline: str, date: str) -> list[dict]:
    """Loads today's local retry queue for this airline. Returns [] if the file doesn't exist."""
    path = LOCAL_RETRY_QUEUE_PATH.format(airline=airline, yyyymmdd=date)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.warning(f"Failed to load local retry queue for {airline}: {e}")
        return []


def _save_local_retry_queue(airline: str, queue: list[dict], date: str) -> None:
    """Writes today's local retry queue for this airline. Deletes the file (if present) when the queue is empty."""
    path = LOCAL_RETRY_QUEUE_PATH.format(airline=airline, yyyymmdd=date)
    if not queue:
        if os.path.exists(path):
            _delete_file(path, "empty retry queue")
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"Failed to save local retry queue for {airline}: {e}")


# --- Cloud ---


def _load_gcs_retry_queue(airline: str, date: str) -> list[dict]:
    """Loads today's GCS retry queue blob for this airline. Returns [] if the blob doesn't exist."""
    bucket = _get_gcs_bucket()
    blob = bucket.blob(CLOUD_RETRY_QUEUE_PATH.format(airline=airline, yyyymmdd=date))
    if not blob.exists():
        return []
    return json.loads(blob.download_as_text())


def _save_gcs_retry_queue(airline: str, queue: list[dict], date: str) -> None:
    """Writes today's GCS retry queue blob for this airline. Deletes the blob (if present) when the queue is empty."""
    bucket = _get_gcs_bucket()
    blob_name = CLOUD_RETRY_QUEUE_PATH.format(airline=airline, yyyymmdd=date)
    blob = bucket.blob(blob_name)
    if not queue:
        if blob.exists():
            blob.delete()
            logging.info(f"Deleted empty GCS retry queue blob {blob_name}")
        return
    blob.upload_from_string(json.dumps(queue, indent=2, ensure_ascii=False), content_type="application/json")
    logging.info(f"GCS retry queue written to {blob_name}")


# ---------------------------
# Public API
# ---------------------------


def read_cache(departure_airport: str, airline: str, date: str) -> list[Flight] | None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _read_gcs_cache(departure_airport, airline, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{departure_airport} ({e}); falling back to local cache.")
        return _read_local_cache(departure_airport, airline, date)


def write_cache(departure_airport: str, airline: str, flights: list[Flight], date: str) -> None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        _write_gcs_cache(departure_airport, airline, flights, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}-{departure_airport} ({e}); falling back to local cache.")
        _write_local_cache(departure_airport, airline, flights, date)


def load_retry_queue(airline: str, date: str) -> list[dict]:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _load_gcs_retry_queue(airline, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline} retry queue ({e}); falling back to local cache.")
        return _load_local_retry_queue(airline, date)


def save_retry_queue(airline: str, queue: list[dict], date: str) -> None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        _save_gcs_retry_queue(airline, queue, date)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline} retry queue ({e}); falling back to local cache.")
        _save_local_retry_queue(airline, queue, date)
