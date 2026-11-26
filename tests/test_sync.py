from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from pulse_mirror.db import repo
from pulse_mirror.sources.garmin.errors import AuthRequiredError
from pulse_mirror.sources.garmin.service import remap_all
from pulse_mirror.sync import service as sync
from tests.conftest import FakeGarmin, days_between

TODAY = date(2025, 1, 15)


def test_sync_recent_ok(conn, throttle):
    api = FakeGarmin(days_between("2025-01-01", "2025-01-15"))
    result = sync.sync_recent(conn, lambda: api, throttle, TODAY, 3)
    assert result.status == "ok" and result.days_fetched == 3
    assert repo.last_sync_run(conn, "garmin", "ok") is not None
    dates = {r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_metrics")}
    assert dates == {"2025-01-13", "2025-01-14", "2025-01-15"}


def test_missing_login_is_recorded_not_raised(conn, throttle):
    def factory():
        raise AuthRequiredError("No Garmin tokens")

    result = sync.sync_recent(conn, factory, throttle, TODAY, 3, kind="scheduled")
    assert result.status == "auth_required"
    assert repo.last_sync_run(conn, "garmin")["status"] == "auth_required"
    assert repo.last_sync_run(conn, "garmin", "ok") is None


def test_manual_cooldown(conn, throttle):
    api = FakeGarmin(set())
    sync.sync_recent(conn, lambda: api, throttle, TODAY, 1, kind="manual")
    with pytest.raises(sync.SyncRefusedError, match="Cooldown"):
        sync.sync_recent(conn, lambda: api, throttle, TODAY, 1, kind="manual")
    later = datetime.now(UTC) + timedelta(minutes=11)
    sync.check_allowed(conn, "manual", now=later)  # no exception


def test_running_sync_blocks_but_stale_and_backfill_do_not(conn):
    run_id = repo.start_sync_run(conn, "garmin", "scheduled")
    with pytest.raises(sync.SyncRefusedError, match="already running"):
        sync.check_allowed(conn, "scheduled")
    sync.check_allowed(conn, "scheduled", now=datetime.now(UTC) + timedelta(hours=3))
    repo.finish_sync_run(conn, run_id, "ok", 0)
    repo.start_sync_run(conn, "garmin", "backfill")
    sync.check_allowed(conn, "scheduled")


def test_remap_rebuilds_from_raw(conn, throttle):
    sync.sync_recent(conn, lambda: FakeGarmin({"2025-01-15"}), throttle, TODAY, 1)
    before = conn.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0]
    conn.execute("DELETE FROM daily_metrics")
    conn.execute("DELETE FROM activities")
    assert remap_all(conn) == 1
    assert conn.execute("SELECT COUNT(*) FROM daily_metrics").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] >= 1
