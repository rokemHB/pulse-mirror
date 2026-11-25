from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from pulse_mirror.config import Settings
from pulse_mirror.db import connect, migrate
from pulse_mirror.sync.scheduler import DailySync, missed_today, next_run, parse_hhmm
from tests.conftest import FakeGarmin

TZ = ZoneInfo("Europe/Berlin")
AT = time(8, 0)


def test_parse_and_next_run():
    assert parse_hhmm("08:30") == time(8, 30)
    before = datetime(2025, 3, 1, 7, 0, tzinfo=TZ)
    after = datetime(2025, 3, 1, 9, 0, tzinfo=TZ)
    assert next_run(before, AT) == datetime(2025, 3, 1, 8, 0, tzinfo=TZ)
    assert next_run(after, AT) == datetime(2025, 3, 2, 8, 0, tzinfo=TZ)


def test_next_run_across_dst_keeps_local_time():
    # 2025-03-30 is the spring-forward day in Berlin
    sat = datetime(2025, 3, 29, 9, 0, tzinfo=TZ)
    nxt = next_run(sat, AT)
    assert (nxt.hour, nxt.minute, nxt.day) == (8, 0, 30)


def test_missed_today():
    now = datetime(2025, 3, 1, 12, 0, tzinfo=TZ)
    assert missed_today(now, AT, None)
    assert missed_today(now, AT, datetime(2025, 2, 28, 8, 0, tzinfo=TZ))
    assert not missed_today(now, AT, datetime(2025, 3, 1, 8, 0, 5, tzinfo=TZ))
    assert not missed_today(datetime(2025, 3, 1, 7, 0, tzinfo=TZ), AT, None)


def test_run_once_records_scheduled_run_and_calls_hooks(tmp_path, monkeypatch):
    monkeypatch.setenv("PULSE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PULSE_GARMIN_DELAY_S", "0")
    settings = Settings.from_env()
    migrate(connect(settings.db_path))
    seen = []
    job = DailySync(settings, lambda: FakeGarmin(set()), after_sync=[seen.append])
    result = job.run_once()
    assert result is not None and result.status == "ok" and seen == [result]
    assert job._last_scheduled_start() is not None
