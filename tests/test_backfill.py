from __future__ import annotations

from datetime import date

from pulse_mirror.sources.garmin import backfill
from pulse_mirror.sources.garmin.errors import AuthRequiredError
from tests.conftest import FakeGarmin, days_between

TODAY = date(2025, 3, 1)  # backfill starts at 2025-02-28


def metric_days(conn):
    return {r[0] for r in conn.execute("SELECT DISTINCT date FROM daily_metrics")}


def state(conn):
    return conn.execute("SELECT * FROM backfill_state WHERE source='garmin'").fetchone()


def test_walks_back_until_history_starts(conn, throttle):
    data = days_between("2025-02-01", "2025-02-28")
    result = backfill.run_backfill(conn, FakeGarmin(data), throttle, TODAY, empty_stop=5)
    assert result.status == "done"
    assert metric_days(conn) == data
    assert state(conn)["status"] == "done"
    assert result.cursor_date == "2025-01-27"  # 5 empty days before 2025-02-01
    run = conn.execute("SELECT * FROM sync_runs WHERE kind='backfill'").fetchone()
    assert run["status"] == "ok" and run["days_fetched"] == 33


def test_activities_fetched_in_chunks(conn, throttle):
    api = FakeGarmin(days_between("2025-01-01", "2025-02-28"))
    backfill.run_backfill(conn, api, throttle, TODAY, empty_stop=3)
    chunks = [key for ep, key in api.calls if ep == "activities"]
    # the third chunk starts at 2024-12-30, one of the empty days before the history ends
    assert chunks == [
        "2025-01-30..2025-02-28",
        "2024-12-31..2025-01-29",
        "2024-12-01..2024-12-30",
    ]
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] > 0


def test_resumable_and_idempotent(conn, throttle):
    data = days_between("2025-02-10", "2025-02-28")
    first = backfill.run_backfill(conn, FakeGarmin(data), throttle, TODAY, max_days=7)
    assert first.status == "paused" and first.cursor_date == "2025-02-22"
    api = FakeGarmin(data)
    second = backfill.run_backfill(conn, api, throttle, TODAY, empty_stop=4)
    assert second.status == "done"
    # resumed from the cursor: never re-fetched the first 7 days
    assert min(d for ep, d in api.calls if ep == "user_summary") == "2025-02-06"
    assert max(d for ep, d in api.calls if ep == "user_summary") == "2025-02-21"
    assert metric_days(conn) == data
    # a finished backfill doesn't run again
    third = backfill.run_backfill(conn, FakeGarmin(data), throttle, TODAY)
    assert third.days_fetched == 0 and "already complete" in third.message


def test_stop_before(conn, throttle):
    data = days_between("2024-01-01", "2025-02-28")
    result = backfill.run_backfill(
        conn, FakeGarmin(data), throttle, TODAY, stop_before=date(2025, 2, 20)
    )
    assert result.status == "done"
    assert min(metric_days(conn)) == "2025-02-20"


def test_rate_limit_pauses_then_continues(conn):
    pauses: list[float] = []

    class RecordingThrottle:
        def wait(self) -> None: ...
        def pause(self, s: float) -> None:
            pauses.append(s)

    data = days_between("2025-02-20", "2025-02-28")
    api = FakeGarmin(data, rate_limit_on={"2025-02-25"})
    result = backfill.run_backfill(conn, api, RecordingThrottle(), TODAY, empty_stop=2)
    assert pauses == [backfill.RATE_LIMIT_BACKOFF_S[0]]
    assert result.status == "done"
    assert metric_days(conn) == data


def test_auth_error_pauses_with_clear_status(conn, throttle):
    class Expired(FakeGarmin):
        def daily_endpoint(self, endpoint, day):
            raise AuthRequiredError("login expired")

    result = backfill.run_backfill(conn, Expired(set()), throttle, TODAY)
    assert result.status == "paused"
    run = conn.execute("SELECT status, error_message FROM sync_runs").fetchone()
    assert run["status"] == "auth_required" and "login expired" in run["error_message"]
