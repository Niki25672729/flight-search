import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

# Configuration
CACHE_DIR = "cache"
CACHE_TTL = timedelta(weeks=1)
DATE_FORMAT = "%Y%m%d"
# Regex to match cache filenames: {airport}_{YYYYMMDD}.json
FILENAME_REGEX = re.compile(r"^([A-Z]{3})_(\d{8})\.json$")


def _serialize_datetime(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _deserialize_flight_data(data: List[Dict]) -> List[Dict]:
    """Deserializes datetime strings back to datetime objects in flight data."""
    for flight in data:
        if "departure_time" in flight and isinstance(flight["departure_time"], str):
            flight["departure_time"] = datetime.fromisoformat(flight["departure_time"])
        if "arrival_time" in flight and isinstance(flight["arrival_time"], str):
            flight["arrival_time"] = datetime.fromisoformat(flight["arrival_time"])
    return data


def _delete_file(filepath: str, description: str):
    """
    Attempts to delete a file and prints a message about the outcome.
    """
    try:
        os.remove(filepath)
        print(f"Cleaned up {description}: {os.path.basename(filepath)}")
    except OSError as e:
        print(f"Error deleting {description} file {os.path.basename(filepath)}: {e}")


def read_cache(departure_airport: str) -> Optional[List[Dict]]:
    """
    Reads cached flight data for a given departure airport.
    Returns a list of flight dicts on cache hit, None on cache miss or expiry.
    Deletes stale, corrupt, or malformed cache files.
    """
    cache_path = os.path.join(os.getcwd(), CACHE_DIR)
    if not os.path.exists(cache_path):
        print(f"Cache miss for {departure_airport}: Cache directory '{CACHE_DIR}' does not exist.")
        return None

    now = datetime.now()

    freshest_valid_filepath: Optional[str] = None
    freshest_valid_file_date: Optional[datetime] = None

    # Iterate through all files in the cache directory
    for filename in os.listdir(cache_path):
        filepath = os.path.join(cache_path, filename)
        match = FILENAME_REGEX.match(filename)

        if match:  # Filename matches the expected pattern {airport}_{YYYYMMDD}.json
            file_iata_code, date_str = match.groups()

            if file_iata_code == departure_airport: # File is for the current departure airport
                try:
                    # Attempt to parse the date from the filename
                    file_date = datetime.strptime(date_str, DATE_FORMAT)

                    # Check if the file is fresh (within CACHE_TTL)
                    if (now - file_date) < CACHE_TTL:
                        # If this file is newer than the current freshest_valid_file_date,
                        # or if no freshest_valid_file_date has been found yet
                        if freshest_valid_file_date is None or file_date > freshest_valid_file_date:
                            # If we previously found a fresh file, it is now superseded; delete it.
                            if freshest_valid_filepath and os.path.exists(freshest_valid_filepath):
                                _delete_file(freshest_valid_filepath, "older fresh cache file")

                            # Update to consider this file as the new freshest valid one
                            freshest_valid_filepath = filepath
                            freshest_valid_file_date = file_date
                        else:
                            # This fresh file is older than the current freshest_valid_file_date; delete it.
                            _delete_file(filepath, "older fresh cache file")
                    else:
                        # File is stale, delete it
                        _delete_file(filepath, "stale cache file")

                except ValueError:
                    # Date part of the filename is malformed
                    print(f"Warning: Malformed date '{date_str}' in cache filename for {departure_airport}: {filename}.")
                    # Attempt to delete the malformed file
                    _delete_file(filepath, "malformed date cache file")
            # Else (file_iata_code != departure_airport), it's for another airport; ignore it.
        else: # Filename does not match FILENAME_REGEX
            if filename.startswith(f"{departure_airport}_") and filename.endswith(".json"): # Seems to be for this airport but malformed
                print(f"Warning: Malformed filename (not {DATE_FORMAT} format) for {departure_airport}: {filename}.")
                _delete_file(filepath, "malformed cache filename file")

    # After iterating through all files, attempt to load the freshest valid one found
    if freshest_valid_filepath:
        try:
            with open(freshest_valid_filepath, "r") as f:
                data = json.load(f)
            print(f"Cache hit for {departure_airport}: Loaded {os.path.basename(freshest_valid_filepath)}")
            return _deserialize_flight_data(data)
        except Exception as e:
            # Catch JSONDecodeError specifically, and other read errors
            error_type = "corrupt" if isinstance(e, json.JSONDecodeError) else "problematic"
            print(f"Error: Cache file {os.path.basename(freshest_valid_filepath)} is {error_type}. {e}.")
            _delete_file(freshest_valid_filepath, f"{error_type} cache file")
            return None
    else:
        print(f"Cache miss for {departure_airport}: No fresh cache found after checking and cleanup.")
        return None


def write_cache(departure_airport: str, flights: List[Dict]):
    """
    Writes flight data to a new cache file and removes all previous cache files
    for the same departure airport.
    """
    cache_path = os.path.join(os.getcwd(), CACHE_DIR)
    os.makedirs(cache_path, exist_ok=True)

    # Clean up existing cache files for this airport
    for filename in os.listdir(cache_path):
        match = FILENAME_REGEX.match(filename)
        if match:
            iata_code, _ = match.groups()
            if iata_code == departure_airport:
                _delete_file(os.path.join(cache_path, filename), "old cache file")
        elif filename.startswith(f"{departure_airport}_") and filename.endswith(".json"):
            # Also clean up any malformed files that match the airport prefix but not FILENAME_REGEX
            _delete_file(os.path.join(cache_path, filename), f"malformed cache filename file for {departure_airport}")

    # Create new cache file
    current_date_str = datetime.now().strftime(DATE_FORMAT)
    new_filename = f"{departure_airport}_{current_date_str}.json"
    new_filepath = os.path.join(cache_path, new_filename)

    try:
        with open(new_filepath, "w") as f:
            json.dump(flights, f, default=_serialize_datetime, indent=4)
        print(f"Cache written to {new_filename}")
    except Exception as e:
        print(f"Error writing cache to {new_filename}: {e}")
