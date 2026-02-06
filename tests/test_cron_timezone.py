from datetime import datetime, timezone

import pytest

from nanobot.cron.service import _compute_next_run
from nanobot.cron.types import CronSchedule


pytest.importorskip("croniter")


def test_cron_schedule_respects_timezone_field() -> None:
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("America/New_York")
    except Exception:
        pytest.skip("ZoneInfo database not available for America/New_York in this environment")

    # Feb 6, 2026 is standard time in New York (UTC-5).
    # If "now" is 13:30 UTC, then the next 09:00 America/New_York is 14:00 UTC same day.
    now_dt_utc = datetime(2026, 2, 6, 13, 30, tzinfo=timezone.utc)
    now_ms = int(now_dt_utc.timestamp() * 1000)

    schedule = CronSchedule(kind="cron", expr="0 9 * * *", tz="America/New_York")
    next_ms = _compute_next_run(schedule, now_ms)
    assert next_ms is not None

    expected_dt_utc = datetime(2026, 2, 6, 14, 0, tzinfo=timezone.utc)
    assert next_ms == int(expected_dt_utc.timestamp() * 1000)


def test_cron_schedule_uses_now_ms_not_wall_clock() -> None:
    # If now is 08:59 UTC and schedule is "0 9 * * *" in UTC, next is 09:00 UTC same day.
    now_dt_utc = datetime(2026, 2, 6, 8, 59, tzinfo=timezone.utc)
    now_ms = int(now_dt_utc.timestamp() * 1000)

    # No tz specified means we compute in UTC (base time is in UTC).
    schedule = CronSchedule(kind="cron", expr="0 9 * * *", tz=None)
    next_ms = _compute_next_run(schedule, now_ms)
    assert next_ms is not None

    expected_dt_utc = datetime(2026, 2, 6, 9, 0, tzinfo=timezone.utc)
    assert next_ms == int(expected_dt_utc.timestamp() * 1000)
