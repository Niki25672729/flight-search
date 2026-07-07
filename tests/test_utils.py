from datetime import datetime, timezone

from utils import _utc_now


# ---------------------------
# Tests for _utc_now
# ---------------------------


def test_utc_now_calls_datetime_now_with_utc_timezone(mocker):
    """Tests that _utc_now() explicitly requests UTC time via datetime.now(timezone.utc), not local time."""
    aware_utc = datetime(2026, 6, 28, 17, 0, 0, tzinfo=timezone.utc)
    mock_datetime = mocker.patch("utils.datetime")
    mock_datetime.now.return_value = aware_utc

    result = _utc_now()

    mock_datetime.now.assert_called_once_with(timezone.utc)
    assert result == datetime(2026, 6, 28, 17, 0, 0)
    assert result.tzinfo is None
