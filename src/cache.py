import json
import logging
import os
import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

from google.cloud import storage

from models import Flight
from config import CACHE_TTL, DATE_FORMAT, GCS_BUCKET_NAME, LOCAL_FLIGHT_CACHE_DIR
from utils import _utc_now

# Regex to match local flight cache filenames: {YYYYMMDD}.json
FILENAME_REGEX = re.compile(r"^(\d{8})\.json$")


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


def _read_local_cache(departure_airport: str, airline: str) -> list[Flight] | None:
    """
    Reads cached flight data for a given departure airport/airline from local.
    Returns a list of Flight objects on cache hit, None on cache miss or expiry.
    Broken files (malformed filename/date, corrupt content) are deleted.
    """
    now = _utc_now()
    cache_path = LOCAL_FLIGHT_CACHE_DIR.format(airline=airline, origin=departure_airport, yyyymm=now.strftime("%Y%m"))
    if not os.path.exists(cache_path):
        logging.warning(f"Cache miss for {airline}/{departure_airport}: no cache directory found.")
        return None

    freshest_valid_filepath: str | None = None
    freshest_valid_file_date: datetime | None = None

    for filename in os.listdir(cache_path):
        filepath = os.path.join(cache_path, filename)
        match = FILENAME_REGEX.match(filename)

        if match:  # Filename matches the expected pattern {YYYYMMDD}.json
            date_str = match.group(1)
            try:
                # Attempt to parse the date from the filename
                file_date = datetime.strptime(date_str, DATE_FORMAT)
            except ValueError:
                # Date part of the filename is malformed — broken, delete it
                logging.warning(
                    f"Warning: Malformed date '{date_str}' in cache filename for {airline}/{departure_airport}: "
                    f"{filename}."
                )
                _delete_file(filepath, "malformed date cache file")
                continue

            # Check if the file is fresh (within CACHE_TTL)
            if (now - file_date) < CACHE_TTL:
                if freshest_valid_file_date is None or file_date > freshest_valid_file_date:
                    freshest_valid_filepath = filepath
                    freshest_valid_file_date = file_date
        else:  # Filename does not match FILENAME_REGEX — malformed
            logging.warning(
                f"Warning: Malformed filename (not {DATE_FORMAT} format) for {airline}/{departure_airport}: {filename}."
            )
            _delete_file(filepath, "malformed cache filename file")

    # After iterating through all files, attempt to load the freshest valid one found
    if freshest_valid_filepath:
        try:
            with open(freshest_valid_filepath, "r") as f:
                data = json.load(f)
            logging.info(
                f"Cache hit for {airline}/{departure_airport}: Loaded {os.path.basename(freshest_valid_filepath)}"
            )
            return _deserialize_flight_data(data)
        except Exception as e:
            # Catch JSONDecodeError specifically, and other read errors
            error_type = "corrupt" if isinstance(e, json.JSONDecodeError) else "problematic"
            logging.error(f"Error: Cache file {os.path.basename(freshest_valid_filepath)} is {error_type}. {e}.")
            _delete_file(freshest_valid_filepath, f"{error_type} cache file")
            return None
    else:
        logging.warning(f"Cache miss for {airline}/{departure_airport}: No fresh cache found.")
        return None


def _write_local_cache(departure_airport: str, airline: str, flights: list[Flight]):
    """
    Writes flight data to a new dated cache file for the departure airport/airline to local.
    Malformed filenames in this month's directory are cleaned up.
    """
    now = _utc_now()
    cache_path = LOCAL_FLIGHT_CACHE_DIR.format(airline=airline, origin=departure_airport, yyyymm=now.strftime("%Y%m"))
    os.makedirs(cache_path, exist_ok=True)

    # Clean up malformed cache filenames in this month's directory
    for filename in os.listdir(cache_path):
        if not FILENAME_REGEX.match(filename) and filename.endswith(".json"):
            _delete_file(
                os.path.join(cache_path, filename), f"malformed cache filename file for {airline}/{departure_airport}"
            )

    # Create new cache file
    current_date_str = now.strftime(DATE_FORMAT)
    new_filename = f"{current_date_str}.json"
    new_filepath = os.path.join(cache_path, new_filename)

    try:
        with open(new_filepath, "w") as f:
            json.dump(flights, f, default=_serialize_datetime, indent=4)
        logging.info(f"Cache written to {airline}/{departure_airport}/{now.strftime('%Y%m')}/{new_filename}")
    except Exception as e:
        logging.error(f"Error writing cache to {new_filename}: {e}")


def _get_gcs_bucket() -> storage.Bucket:
    """
    PLACEHOLDER: Returns the GCS bucket client.
    """
    if not GCS_BUCKET_NAME:
        raise RuntimeError("GCS_BUCKET_NAME is not configured")
    return storage.Client().bucket(GCS_BUCKET_NAME)


def _gcs_flight_blob_name(departure_airport: str, airline: str, date: datetime) -> str:
    return f"bronze/flights/{airline}/{departure_airport}/{date.strftime(DATE_FORMAT)}.json"


def _read_gcs_cache(departure_airport: str, airline: str) -> list[Flight] | None:
    """
    PLACEHOLDER: Checks GCS for the freshest flight blob (within CACHE_TTL) for this airport.
    """
    bucket = _get_gcs_bucket()
    prefix = f"bronze/flights/{airline}/{departure_airport}/"
    now = _utc_now()

    freshest_blob = None
    freshest_date: datetime | None = None
    for blob in bucket.list_blobs(prefix=prefix):
        filename = blob.name.rsplit("/", 1)[-1].removesuffix(".json")
        try:
            blob_date = datetime.strptime(filename, DATE_FORMAT)
        except ValueError:
            continue
        if (now - blob_date) < CACHE_TTL and (freshest_date is None or blob_date > freshest_date):
            freshest_blob, freshest_date = blob, blob_date

    if freshest_blob is None:
        logging.info(f"GCS cache miss for {airline}/{departure_airport}: no fresh blob under {prefix}.")
        return None

    data = [json.loads(line) for line in freshest_blob.download_as_text().splitlines() if line.strip()]
    logging.info(f"GCS cache hit for {airline}/{departure_airport}: loaded {freshest_blob.name}")
    return _deserialize_flight_data(data)


def _write_gcs_cache(departure_airport: str, airline: str, flights: list[Flight]) -> None:
    """
    PLACEHOLDER: Writes flights as NDJSON (one record per line) to today's GCS blob for this
    airport.
    """
    bucket = _get_gcs_bucket()
    blob_name = _gcs_flight_blob_name(departure_airport, airline, _utc_now())
    lines = "\n".join(json.dumps(flight, default=_serialize_datetime) for flight in flights)
    bucket.blob(blob_name).upload_from_string(lines, content_type="application/x-ndjson")
    logging.info(f"GCS cache written to {blob_name}")


def read_cache(departure_airport: str, airline: str) -> list[Flight] | None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        return _read_gcs_cache(departure_airport, airline)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}/{departure_airport} ({e}); falling back to local cache.")
        return _read_local_cache(departure_airport, airline)


def write_cache(departure_airport: str, airline: str, flights: list[Flight]) -> None:
    """
    Checks GCS first; falls back to the local file cache if GCS itself is unreachable
    """
    try:
        _write_gcs_cache(departure_airport, airline, flights)
    except Exception as e:
        logging.warning(f"GCS unreachable for {airline}/{departure_airport} ({e}); falling back to local cache.")
        _write_local_cache(departure_airport, airline, flights)
