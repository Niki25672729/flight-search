import dataclasses
import json
import os
import types
import typing
from datetime import datetime

import pytest
from pyspark.sql import SparkSession
from pyspark.sql.types import DoubleType, IntegerType, StringType

from bronze_reader import BRONZE_SCHEMA, bronze_file_path, load_bronze_snapshot
from config import FLIGHT_CACHE_DIR, FLIGHT_CACHE_FILENAME
from models import Flight
from quality import DataQualityError, assert_bronze_complete, assert_flight_key_unique
from silver import (
    compute_price_history,
    configure_partition_overwrite,
    latest_state_path,
    price_history_path,
    process_day,
    read_prior_latest_state,
    write_partitioned,
)


# ---------------------------
# Constants
# ---------------------------

AIRLINE = "ryanair"
ORIGINS = ["AGP", "BCN"]

# datetime maps to StringType deliberately: bronze JSON carries ISO strings, parsed to
# TimestampType only after the read (bronze_reader._parse_timestamp).
_EXPECTED_SPARK_TYPE = {str: StringType(), float: DoubleType(), int: IntegerType(), datetime: StringType()}


# ---------------------------
# Helpers
# ---------------------------


def _base_type(annotation: type) -> type:
    """Strips `| None` from an Optional annotation, returning the single concrete type."""
    if isinstance(annotation, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        assert len(args) == 1, f"expected a single non-None type, got {annotation}"
        return args[0]
    return annotation


def _bronze_row(**overrides) -> dict:
    """Minimal bronze-JSON-shaped row (asdict(Flight) field set), overridable per test."""
    row = {
        "origin_iata": "AGP",
        "destination_iata": "EMA",
        "destination_city": "East Midlands",
        "destination_country": "United Kingdom",
        "airline": "Ryanair",
        "flight_number": "FR1",
        "departure_time": "2026-07-10T07:10:00",
        "arrival_time": None,
        "price_eur": 10.0,
        "currency": "EUR",
        "seats_left": None,
        "scraped_at": "2026-07-07T10:00:00",
    }
    row.update(overrides)
    return row


def _write_bronze_day(bronze_root: str, yyyymmdd: str, rows: list[dict], origin: str = "AGP") -> None:
    """Writes one origin/day bronze file under `bronze_root`, mirroring the real local cache
    layout. Goes through the same JSON-string read path as production data, which also sidesteps
    freezegun's autouse FakeDatetime clashing with PySpark's strict local schema verification."""
    day_dir = os.path.join(bronze_root, FLIGHT_CACHE_DIR.format(airline=AIRLINE, yyyymm=yyyymmdd[:6], dd=yyyymmdd[6:8]))
    os.makedirs(day_dir, exist_ok=True)
    with open(os.path.join(day_dir, FLIGHT_CACHE_FILENAME.format(origin=origin, yyyymmdd=yyyymmdd)), "w") as f:
        json.dump(rows, f)


def _row_set(df, scrape_date_literal: str) -> set:
    """Collects one scrape_date partition as a set of tuples, for order-independent row comparison."""
    rows = df.filter(f"scrape_date = to_date('{scrape_date_literal}')").collect()
    return {tuple(row.asDict().items()) for row in rows}


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture(scope="session")
def spark():
    """One local Spark session for the whole processing test run (session-scoped — bootstrap is
    slow, ~seconds; per-test isolation instead comes from each test's own bronze/output roots)."""
    session = (
        SparkSession.builder.master("local[1]")
        .appName("flight-silver-tests")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
        .config("spark.sql.session.timeZone", "UTC")  # deterministic regardless of the host's local TZ/DST
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()


@pytest.fixture
def bronze_root(tmp_path) -> str:
    """Empty bronze root under tmp_path — each test seeds exactly the days/rows it asserts about,
    through the real JSON read path (no real cache data, no mocking of the loader)."""
    return str(tmp_path / "bronze")


@pytest.fixture
def seeded_bronze_root(bronze_root) -> str:
    """
    Two-day, two-origin synthetic bronze exercising every diff scenario the suite asserts:
    a stable price (FR1, no history rows), a reprice (FR2 27.99 -> 25.00), a displacement
    (the 2026-08-04 route-day's cheapest flips FR3@18:25 68.05 -> FR9@10:50 68.99 — same
    flight_key), and a route-day entering the window (FR5, is_new_flight). Day 07-07 has
    4 rows (3 AGP + 1 BCN), day 07-08 has 5 (4 AGP + 1 BCN).
    """
    agp_day1 = [
        _bronze_row(flight_number="FR1", departure_time="2026-07-10T07:10:00", price_eur=18.99),
        _bronze_row(flight_number="FR2", departure_time="2026-07-11T05:45:00", price_eur=27.99),
        _bronze_row(flight_number="FR3", departure_time="2026-08-04T18:25:00", price_eur=68.05),
    ]
    agp_day2 = [
        _bronze_row(flight_number="FR1", departure_time="2026-07-10T07:10:00", price_eur=18.99),
        _bronze_row(flight_number="FR2", departure_time="2026-07-11T05:45:00", price_eur=25.00),
        _bronze_row(flight_number="FR9", departure_time="2026-08-04T10:50:00", price_eur=68.99),
        _bronze_row(flight_number="FR5", departure_time="2026-07-28T18:25:00", price_eur=53.97),
    ]
    bcn_row = _bronze_row(
        origin_iata="BCN", destination_iata="DUB", destination_city="Dublin",
        destination_country="Ireland", flight_number="FR4", departure_time="2026-07-12T09:00:00", price_eur=30.00,
    )
    _write_bronze_day(bronze_root, "20260707", agp_day1)
    _write_bronze_day(bronze_root, "20260707", [bcn_row], origin="BCN")
    _write_bronze_day(bronze_root, "20260708", agp_day2)
    _write_bronze_day(bronze_root, "20260708", [bcn_row], origin="BCN")
    return bronze_root


@pytest.fixture
def output_root(tmp_path) -> str:
    """Fresh silver output root per test — keeps tests isolated despite sharing one Spark session."""
    return str(tmp_path / "silver")


# ---------------------------
# Schema Parity
# ---------------------------


def test_bronze_schema_field_names_match_flight_dataclass():
    """BRONZE_SCHEMA claims (comment) to match asdict(Flight) field-for-field, but it is
    hand-written: a field added to Flight without updating it would be silently dropped by the
    explicit-schema JSON read (Spark ignores unknown keys, even under FAILFAST) and vanish from
    silver with no error. This makes that drift fail in the same PR that touches Flight."""
    assert [f.name for f in BRONZE_SCHEMA.fields] == [f.name for f in dataclasses.fields(Flight)]


def test_bronze_schema_types_match_flight_field_types():
    """A wrongly-typed schema field wouldn't fail loudly either — FAILFAST catches a type
    mismatch per record, but only against whatever the schema declares; this pins the declared
    types to the dataclass's own annotations via the documented mapping."""
    hints = typing.get_type_hints(Flight)
    for schema_field in BRONZE_SCHEMA.fields:
        expected = _EXPECTED_SPARK_TYPE[_base_type(hints[schema_field.name])]
        assert schema_field.dataType == expected, f"{schema_field.name}: {schema_field.dataType} != {expected}"


def test_optional_flight_fields_are_nullable_in_bronze_schema():
    """A Flight field that can legitimately be None (e.g. flight_number, arrival_time) must never
    be declared non-nullable in the schema — real bronze rows would then depend on Spark's
    (non-)enforcement of nullability instead of a correct declaration. The reverse is not
    asserted: the schema deliberately requires only the four business-critical columns."""
    hints = typing.get_type_hints(Flight)
    nullable_in_schema = {f.name: f.nullable for f in BRONZE_SCHEMA.fields}
    for name, annotation in hints.items():
        is_optional = isinstance(annotation, types.UnionType) and type(None) in typing.get_args(annotation)
        if is_optional:
            assert nullable_in_schema[name], f"{name} is Optional on Flight but non-nullable in BRONZE_SCHEMA"


# ---------------------------
# Bronze Reader
# ---------------------------


def test_bronze_file_path_matches_local_cache_layout(seeded_bronze_root):
    """Path building must stay derived from src/config.py's own template, not a duplicated one."""
    path = bronze_file_path(seeded_bronze_root, AIRLINE, "AGP", "20260707")
    assert path == os.path.join(seeded_bronze_root, "flights", "ryanair", "202607", "07", "AGP_20260707.json")
    assert os.path.exists(path)


def test_load_bronze_snapshot_reads_explicit_schema_and_parses_timestamps(spark, seeded_bronze_root):
    df = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ORIGINS, "20260707")
    assert df.count() == 4  # 3 AGP + 1 BCN, per the seeded world
    assert "flight_key" in df.columns
    dtypes = dict(df.dtypes)
    assert dtypes["departure_time"] == "timestamp"
    assert dtypes["scraped_at"] == "timestamp"
    assert dtypes["price_eur"] == "double"


def test_flight_key_is_a_stable_hash_not_pythons_randomized_hash(spark, seeded_bronze_root):
    """
    flight_key must be reproducible across separate reads/processes — Python's built-in hash()
    is per-process-randomized (PYTHONHASHSEED) and would silently break retry-idempotency, since
    a retry in a fresh process would compute different keys for the same flight.
    """
    df1 = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ["AGP"], "20260707")
    df2 = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ["AGP"], "20260707")
    keys1 = {row.flight_key for row in df1.select("flight_key").collect()}
    keys2 = {row.flight_key for row in df2.select("flight_key").collect()}
    assert keys1 == keys2
    assert len(keys1) == df1.count()  # already unique within a single snapshot


def test_displaced_flight_keeps_the_same_flight_key(spark, seeded_bronze_root):
    """The 2026-08-04 route-day is FR3@18:25 on day 1 and FR9@10:50 on day 2 — same
    (origin, destination, departure DATE, airline), so the SAME flight_key: departure_time and
    flight_number are attributes, not identity (ARCHITECTURE_DASHBOARD.md's route-day key)."""
    df1 = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ["AGP"], "20260707")
    df2 = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ["AGP"], "20260708")
    key1 = df1.filter("flight_number = 'FR3'").collect()[0].flight_key
    key2 = df2.filter("flight_number = 'FR9'").collect()[0].flight_key
    assert key1 == key2


# ---------------------------
# Quality
# ---------------------------


def test_assert_bronze_complete_passes_when_all_origins_present(spark, seeded_bronze_root):
    assert_bronze_complete(spark, seeded_bronze_root, AIRLINE, ORIGINS, "20260707")  # must not raise


def test_assert_bronze_complete_raises_on_missing_origin(spark, seeded_bronze_root):
    """A missing bronze file must fail loudly — silently continuing would drop the origin's
    route-days from today's flights_latest_state partition and punch gaps into
    flight_price_history, silently wrong rather than loudly missing."""
    with pytest.raises(DataQualityError, match="STN"):
        assert_bronze_complete(spark, seeded_bronze_root, AIRLINE, [*ORIGINS, "STN"], "20260707")


def test_assert_flight_key_unique_passes_on_unique_keys(spark, bronze_root):
    # Different departure DAYS — distinct route-days, distinct keys under the route-day flight_key.
    _write_bronze_day(
        bronze_root,
        "20260707",
        [_bronze_row(departure_time="2026-07-10T07:10:00"), _bronze_row(departure_time="2026-07-11T07:10:00")],
    )
    df = load_bronze_snapshot(spark, bronze_root, AIRLINE, ["AGP"], "20260707")
    assert_flight_key_unique(df, context="test")  # must not raise


def test_assert_flight_key_unique_raises_on_duplicate(spark, bronze_root):
    """Two fares on the SAME route-day are one flight_key by design (route-day grain) — a snapshot
    containing both violates the feed's one-cheapest-per-route-day grain and must fail loudly
    (2026-07-12: an original scrape and a retry pass left 232 such pairs in bronze)."""
    _write_bronze_day(
        bronze_root,
        "20260707",
        [
            _bronze_row(flight_number="FR1", departure_time="2026-07-10T07:10:00", price_eur=10.0),
            _bronze_row(flight_number="FR2", departure_time="2026-07-10T19:40:00", price_eur=11.0),
        ],
    )
    df = load_bronze_snapshot(spark, bronze_root, AIRLINE, ["AGP"], "20260707")
    with pytest.raises(DataQualityError, match="duplicate flight_key"):
        assert_flight_key_unique(df, context="test")


# ---------------------------
# Diff Invariants
# ---------------------------


def test_diff_of_identical_days_produces_no_rows(spark, bronze_root, output_root):
    """diff(X, X) must be empty: identical consecutive days mean zero price events — a nonzero
    result here would be fabricated history (the failure mode of keying on the physical flight)."""
    rows = [_bronze_row(), _bronze_row(flight_number="FR2", departure_time="2026-07-11T05:45:00")]
    _write_bronze_day(bronze_root, "20260707", rows)
    _write_bronze_day(bronze_root, "20260708", rows)
    for run_date in ("20260707", "20260708"):
        process_day(spark, bronze_root, output_root, AIRLINE, ["AGP"], run_date)

    day2 = spark.read.parquet(price_history_path(output_root)).filter("scrape_date = to_date('2026-07-08')")
    assert day2.count() == 0


def test_diff_rows_are_exactly_the_new_and_changed_route_days(spark, seeded_bronze_root, output_root):
    """Day 2 of the seeded world must produce exactly 3 events — the reprice (FR2), the
    displacement-as-price-change (FR9 with prior from FR3), and the entering route-day (FR5) —
    and nothing for the stable FR1/FR4. Every row is new XOR changed, with prior_price_eur
    null exactly on the new one."""
    for run_date in ("20260707", "20260708"):
        process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, run_date)

    rows = (
        spark.read.parquet(price_history_path(output_root))
        .filter("scrape_date = to_date('2026-07-08')")
        .collect()
    )
    by_flight = {row.flight_number: row for row in rows}
    assert set(by_flight) == {"FR2", "FR9", "FR5"}

    assert by_flight["FR2"].price_eur == 25.00
    assert by_flight["FR2"].prior_price_eur == 27.99
    assert by_flight["FR2"].is_new_flight is False

    # Displacement: same route-day as FR3, so it's a change with FR3's price as prior.
    assert by_flight["FR9"].price_eur == 68.99
    assert by_flight["FR9"].prior_price_eur == 68.05
    assert by_flight["FR9"].is_new_flight is False

    assert by_flight["FR5"].price_eur == 53.97
    assert by_flight["FR5"].prior_price_eur is None
    assert by_flight["FR5"].is_new_flight is True


def test_bootstrap_day_is_all_new(spark, seeded_bronze_root, output_root):
    """With no prior partition, every route-day is is_new_flight=True with a null prior — the
    first-ever run (and the far edge of the window every day after) must read as 'new', never
    as a fabricated price change."""
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, "20260707")

    rows = spark.read.parquet(price_history_path(output_root)).collect()
    assert len(rows) == 4
    assert all(row.is_new_flight for row in rows)
    assert all(row.prior_price_eur is None for row in rows)


# ---------------------------
# Idempotency
# ---------------------------


def test_rerunning_same_run_date_produces_no_duplicate_rows(spark, seeded_bronze_root, output_root):
    """Literal acceptance criterion: run the job twice for the same run_date, assert no
    duplicate/doubled rows in the diff output (partition-overwrite, not append)."""
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, "20260707")
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, "20260708")

    before = spark.read.parquet(price_history_path(output_root)).count()
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, "20260708")  # re-run, same run_date
    after = spark.read.parquet(price_history_path(output_root)).count()
    assert before == after


def test_retry_after_partial_failure_reproduces_identical_diffs(spark, seeded_bronze_root, output_root):
    """
    Simulates the retry an Airflow task-level retry can actually produce: the job dies after the
    price-history diff is written but before flights_latest_state is. A retry (a fresh, full
    process_day call) must recompute byte-for-byte identical diff rows, because "prior" resolves
    strictly before run_date (read_prior_latest_state) — never to the day's own partial state.
    """
    run_date = "20260708"
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, "20260707")  # bootstrap baseline
    configure_partition_overwrite(spark)

    # --- "Crashed" run: diff written, write_latest_state deliberately skipped ---
    today = load_bronze_snapshot(spark, seeded_bronze_root, AIRLINE, ORIGINS, run_date).cache()
    prior = read_prior_latest_state(spark, output_root, run_date).cache()
    write_partitioned(compute_price_history(today, prior, run_date), price_history_path(output_root))

    assert spark.read.parquet(latest_state_path(output_root)).count() == 4  # still day-1's baseline, untouched

    crashed_price_history = _row_set(spark.read.parquet(price_history_path(output_root)), "2026-07-08")
    assert crashed_price_history  # sanity: the "crashed" run actually produced diff rows to compare against

    # --- Retry: a full, real process_day call for the same run_date ---
    process_day(spark, seeded_bronze_root, output_root, AIRLINE, ORIGINS, run_date)

    retried_price_history = _row_set(spark.read.parquet(price_history_path(output_root)), "2026-07-08")

    assert crashed_price_history == retried_price_history

    latest_state = spark.read.parquet(latest_state_path(output_root))
    assert latest_state.filter("scrape_date = to_date('2026-07-08')").count() == 5  # today's new partition
    assert latest_state.filter("scrape_date = to_date('2026-07-07')").count() == 4  # prior partition untouched
