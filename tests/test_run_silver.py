import logging

import pytest

import run_silver


RUN_DATE = "20260707"


# ---------------------------
# Fixtures
# ---------------------------


@pytest.fixture
def mock_build_spark_session(mocker):
    """Avoids booting a real SparkSession."""
    return mocker.patch("run_silver.build_spark_session")


@pytest.fixture
def mock_run_date_already_processed(mocker):
    return mocker.patch("run_silver.run_date_already_processed")


@pytest.fixture
def mock_process_day(mocker):
    return mocker.patch("run_silver.process_day", return_value={"run_date": RUN_DATE})


def _argv(**overrides) -> list[str]:
    """Local roots by default -- avoids main()'s empty-bucket SystemExit guard."""
    args = {"--run-date": RUN_DATE, "--bronze-root": "/tmp/bronze", "--output-root": "/tmp/silver", **overrides}
    argv = []
    for key, value in args.items():
        argv += [key, value]
    return argv


# ---------------------------
# main() allow_overwrite gating
# ---------------------------


def test_main_skips_process_day_when_already_processed_and_not_allowed(
    mock_build_spark_session, mock_run_date_already_processed, mock_process_day, caplog
):
    """An already-processed run_date with allow_overwrite unset must never call process_day."""
    mock_run_date_already_processed.return_value = True

    with caplog.at_level(logging.WARNING):
        run_silver.main(_argv())

    mock_process_day.assert_not_called()
    assert f"Skipping run_date={RUN_DATE}" in caplog.text


def test_main_calls_process_day_when_allow_overwrite_is_true(
    mock_build_spark_session, mock_run_date_already_processed, mock_process_day
):
    """allow_overwrite=true is the opt-in that lets an already-processed run_date be recomputed."""
    mock_run_date_already_processed.return_value = True

    run_silver.main(_argv(**{"--allow-overwrite": "true"}))

    mock_process_day.assert_called_once()


def test_main_calls_process_day_when_run_date_not_already_processed(
    mock_build_spark_session, mock_run_date_already_processed, mock_process_day
):
    """The normal daily path: nothing exists yet for run_date, so it always proceeds regardless
    of allow_overwrite."""
    mock_run_date_already_processed.return_value = False

    run_silver.main(_argv())

    mock_process_day.assert_called_once()


def test_main_stops_spark_session_even_when_skipped(
    mock_build_spark_session, mock_run_date_already_processed, mock_process_day
):
    """The early-return skip path still goes through the try/finally -- spark.stop() must not be
    skipped along with process_day."""
    mock_run_date_already_processed.return_value = True

    run_silver.main(_argv())

    mock_build_spark_session.return_value.stop.assert_called_once()
