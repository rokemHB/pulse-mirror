"""Regular sync (daily schedule or refresh button): lock, cooldown, run log."""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pulse_mirror.db import repo
from pulse_mirror.sources.garmin import service as garmin
from pulse_mirror.sources.garmin.client import GarminApi
from pulse_mirror.sources.garmin.errors import (
    AuthRequiredError,
    GarminError,
    RateLimitedError,
)
from pulse_mirror.sources.garmin.throttle import Throttle

log = logging.getLogger(__name__)

MANUAL_COOLDOWN = timedelta(minutes=10)
STALE_RUN = timedelta(hours=2)  # a "running" row older than this is a crashed run
ACTIVITY_LOOKBACK_DAYS = 7

_lock = threading.Lock()


class SyncRefusedError(Exception):
    """A sync is already running or the cooldown hasn't passed yet."""


@dataclass
class SyncResult:
    status: str
    days_fetched: int
    activities: int
    message: str


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def check_allowed(conn: sqlite3.Connection, kind: str, now: datetime | None = None) -> None:
    """Raise :class:`SyncRefusedError` if another sync runs or (manual) cooldown is active.

    The backfill is not considered: it is throttled on its own and may run for hours.
    """
    now = now or datetime.now(UTC)
    row = conn.execute(
        """SELECT started_at FROM sync_runs WHERE source = ? AND kind != 'backfill'
           AND status = 'running' ORDER BY started_at DESC LIMIT 1""",
        (garmin.SOURCE,),
    ).fetchone()
    if row and now - _parse(row["started_at"]) < STALE_RUN:
        raise SyncRefusedError("A sync is already running.")
    if kind == "manual":
        last = conn.execute(
            """SELECT started_at FROM sync_runs WHERE source = ? AND kind != 'backfill'
               ORDER BY started_at DESC LIMIT 1""",
            (garmin.SOURCE,),
        ).fetchone()
        if last and now - _parse(last["started_at"]) < MANUAL_COOLDOWN:
            wait = MANUAL_COOLDOWN - (now - _parse(last["started_at"]))
            raise SyncRefusedError(
                f"Cooldown: try again in {int(wait.total_seconds() // 60) + 1} min."
            )


def sync_recent(
    conn: sqlite3.Connection,
    api_factory: Callable[[], GarminApi],
    throttle: Throttle,
    today: date,
    days: int,
    kind: str = "cli",
) -> SyncResult:
    """Fetch the last ``days`` days plus recent activities. ``api_factory()`` builds the client.

    The client is created inside the run, so a missing/expired login is recorded
    as ``auth_required`` in the run log and shows up on the dashboard.
    """
    if not _lock.acquire(blocking=False):
        raise SyncRefusedError("A sync is already running.")
    try:
        check_allowed(conn, kind)
        run_id = repo.start_sync_run(conn, garmin.SOURCE, kind)
        fetched = 0
        n_activities = 0
        try:
            api = api_factory()
            for day in garmin.recent_days(today, days):
                garmin.sync_day(conn, api, throttle, day)
                fetched += 1
            start = (today - timedelta(days=ACTIVITY_LOOKBACK_DAYS)).isoformat()
            n_activities = garmin.sync_activities(conn, api, throttle, start, today.isoformat())
        except AuthRequiredError as e:
            repo.finish_sync_run(conn, run_id, "auth_required", fetched, str(e))
            return SyncResult("auth_required", fetched, n_activities, str(e))
        except RateLimitedError as e:
            msg = f"Garmin rate limit (429): {e}. Try again later."
            repo.finish_sync_run(conn, run_id, "rate_limited", fetched, msg)
            return SyncResult("rate_limited", fetched, n_activities, msg)
        except GarminError as e:
            repo.finish_sync_run(conn, run_id, "error", fetched, str(e))
            return SyncResult("error", fetched, n_activities, str(e))
        except Exception as e:
            log.exception("Unexpected sync failure")
            repo.finish_sync_run(conn, run_id, "error", fetched, f"Unexpected error: {e!r}")
            raise
        repo.finish_sync_run(conn, run_id, "ok", fetched)
        msg = f"Synced {fetched} days and {n_activities} activities."
        return SyncResult("ok", fetched, n_activities, msg)
    finally:
        _lock.release()
