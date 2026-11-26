"""Historical backfill: walk backwards one day at a time, slowly.

* Resumable: progress (``cursor_date``) is committed after every day.
* Idempotent: each day is written with replace/upsert semantics.
* Gentle: every request goes through the throttle; 429s cause long pauses and,
  if they persist, the backfill stops (status ``paused``) instead of pushing on.
* Stops by itself after ``empty_stop`` consecutive days without any data
  (i.e. before the first Garmin device), or at ``stop_before``.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from pulse_mirror.db import repo, transaction
from pulse_mirror.db.connection import utcnow
from pulse_mirror.sources.garmin import service
from pulse_mirror.sources.garmin.client import GarminApi
from pulse_mirror.sources.garmin.errors import (
    AuthRequiredError,
    GarminUnavailableError,
    RateLimitedError,
)
from pulse_mirror.sources.garmin.throttle import Throttle

log = logging.getLogger(__name__)

ACTIVITY_CHUNK_DAYS = 30
RATE_LIMIT_BACKOFF_S: Sequence[float] = (300, 900, 1800)
UNAVAILABLE_BACKOFF_S: Sequence[float] = (60, 300)


@dataclass
class BackfillResult:
    status: str  # done | paused | error
    days_fetched: int
    cursor_date: str | None
    message: str


def _load_state(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM backfill_state WHERE source = ?", (service.SOURCE,)
    ).fetchone()
    return row


def _save_state(
    conn: sqlite3.Connection, cursor: str | None, newest: str, streak: int, status: str
) -> None:
    conn.execute(
        """INSERT INTO backfill_state
               (source, cursor_date, newest_date, empty_streak, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (source) DO UPDATE SET cursor_date = excluded.cursor_date,
               newest_date = excluded.newest_date, empty_streak = excluded.empty_streak,
               status = excluded.status, updated_at = excluded.updated_at""",
        (service.SOURCE, cursor, newest, streak, status, utcnow()),
    )


def reset(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM backfill_state WHERE source = ?", (service.SOURCE,))


def run_backfill(
    conn: sqlite3.Connection,
    api: GarminApi,
    throttle: Throttle,
    today: date,
    *,
    stop_before: date | None = None,
    max_days: int | None = None,
    empty_stop: int = 60,
    progress: Callable[[str], None] = log.info,
) -> BackfillResult:
    state = _load_state(conn)
    if state is not None and state["status"] == "done":
        return BackfillResult("done", 0, state["cursor_date"], "Backfill already complete.")

    newest = date.fromisoformat(state["newest_date"]) if state else today - timedelta(days=1)
    cursor = date.fromisoformat(state["cursor_date"]) if state and state["cursor_date"] else None
    streak = int(state["empty_streak"]) if state else 0
    _save_state(conn, cursor.isoformat() if cursor else None, newest.isoformat(), streak, "running")

    run_id = repo.start_sync_run(conn, service.SOURCE, "backfill")
    fetched = 0
    rate_limit_hits = 0
    unavailable_hits = 0

    def finish(status: str, run_status: str, message: str) -> BackfillResult:
        cursor_s = cursor.isoformat() if cursor else None
        _save_state(conn, cursor_s, newest.isoformat(), streak, status)
        repo.finish_sync_run(
            conn, run_id, run_status, fetched, None if run_status == "ok" else message
        )
        progress(message)
        return BackfillResult(status, fetched, cursor_s, message)

    try:
        while True:
            day = (cursor - timedelta(days=1)) if cursor else newest
            if stop_before is not None and day < stop_before:
                return finish("done", "ok", f"Reached {stop_before}. Backfill complete.")
            if max_days is not None and fetched >= max_days:
                return finish(
                    "paused", "ok", f"Fetched {fetched} days (limit). Paused at {cursor}."
                )

            try:
                offset = (newest - day).days
                if offset % ACTIVITY_CHUNK_DAYS == 0:
                    chunk_start = day - timedelta(days=ACTIVITY_CHUNK_DAYS - 1)
                    n = service.sync_activities(
                        conn, api, throttle, chunk_start.isoformat(), day.isoformat()
                    )
                    progress(f"activities {chunk_start}..{day}: {n}")
                has_data = service.sync_day(conn, api, throttle, day.isoformat())
            except RateLimitedError:
                if rate_limit_hits >= len(RATE_LIMIT_BACKOFF_S):
                    return finish(
                        "paused",
                        "rate_limited",
                        f"Garmin keeps rate-limiting (429). Paused at {cursor}; resume later.",
                    )
                wait = RATE_LIMIT_BACKOFF_S[rate_limit_hits]
                rate_limit_hits += 1
                progress(f"429 from Garmin, pausing {wait:.0f}s")
                throttle.pause(wait)
                continue
            except GarminUnavailableError as e:
                if unavailable_hits >= len(UNAVAILABLE_BACKOFF_S):
                    return finish(
                        "paused", "error", f"Garmin unavailable: {e}. Paused at {cursor}."
                    )
                wait = UNAVAILABLE_BACKOFF_S[unavailable_hits]
                unavailable_hits += 1
                progress(f"Garmin unavailable ({e}), pausing {wait:.0f}s")
                throttle.pause(wait)
                continue
            except AuthRequiredError as e:
                return finish("paused", "auth_required", str(e))

            rate_limit_hits = unavailable_hits = 0
            fetched += 1
            streak = 0 if has_data else streak + 1
            cursor = day
            with transaction(conn):
                _save_state(conn, day.isoformat(), newest.isoformat(), streak, "running")
            progress(f"{day}: {'ok' if has_data else 'no data'} (empty streak {streak})")

            if streak >= empty_stop:
                return finish(
                    "done",
                    "ok",
                    f"{streak} days without data before {day + timedelta(days=streak)}: "
                    "reached the start of the history. Backfill complete.",
                )
    except KeyboardInterrupt:
        finish("paused", "aborted", f"Interrupted. Paused at {cursor}; run again to resume.")
        raise
