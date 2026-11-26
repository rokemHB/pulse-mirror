"""Fetch -> store raw -> map -> store model. Used by sync and backfill."""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from pulse_mirror.db import repo, transaction
from pulse_mirror.sources.garmin import mapping
from pulse_mirror.sources.garmin.client import DAILY_ENDPOINTS, GarminApi
from pulse_mirror.sources.garmin.throttle import Throttle

log = logging.getLogger(__name__)

SOURCE = "garmin"


def activities_key(start: str, end: str) -> str:
    return f"{start}..{end}"


def sync_day(conn: sqlite3.Connection, api: GarminApi, throttle: Throttle, day: str) -> bool:
    """Fetch all daily endpoints for ``day`` and store them. Returns ``day_has_data``.

    All network calls happen first; the DB write is one transaction, so a crash
    mid-day leaves the day either complete or untouched (idempotent re-runs).
    """
    payloads = {}
    for endpoint in DAILY_ENDPOINTS:
        throttle.wait()
        payloads[endpoint] = api.daily_endpoint(endpoint, day)

    with transaction(conn):
        for endpoint, payload in payloads.items():
            repo.upsert_raw(conn, SOURCE, endpoint, day, payload)
        repo.replace_daily_metrics(conn, SOURCE, day, mapping.map_day(day, payloads))
        repo.mark_mapped(conn, SOURCE, day, mapping.MAPPER_VERSION)
    return mapping.day_has_data(day, payloads)


def sync_activities(
    conn: sqlite3.Connection, api: GarminApi, throttle: Throttle, start: str, end: str
) -> int:
    throttle.wait()
    raw = api.activities(start, end)
    activities = [a for a in (mapping.map_activity(r) for r in raw) if a is not None]
    with transaction(conn):
        repo.upsert_raw(conn, SOURCE, "activities", activities_key(start, end), raw)
        for activity in activities:
            repo.upsert_activity(conn, SOURCE, activity)
    return len(activities)


def remap_all(conn: sqlite3.Connection) -> int:
    """Re-run the mapping over all stored raw responses (no network). Returns days mapped."""
    days = [
        r["key"]
        for r in conn.execute(
            "SELECT DISTINCT key FROM raw_responses WHERE source = ? AND endpoint != 'activities'",
            (SOURCE,),
        )
    ]
    for day in days:
        payloads = repo.raw_payloads_for_key(conn, SOURCE, day)
        with transaction(conn):
            repo.replace_daily_metrics(conn, SOURCE, day, mapping.map_day(day, payloads))
            repo.mark_mapped(conn, SOURCE, day, mapping.MAPPER_VERSION)
    for row in conn.execute(
        "SELECT key, payload_json FROM raw_responses WHERE source = ? AND endpoint = 'activities'",
        (SOURCE,),
    ).fetchall():
        payloads = repo.raw_payloads_for_key(conn, SOURCE, row["key"])
        with transaction(conn):
            for raw in payloads.get("activities") or []:
                activity = mapping.map_activity(raw)
                if activity is not None:
                    repo.upsert_activity(conn, SOURCE, activity)
            repo.mark_mapped(conn, SOURCE, row["key"], mapping.MAPPER_VERSION)
    return len(days)


def recent_days(today: date, n: int) -> list[str]:
    """``n`` days ending today, oldest first."""
    return [(today - timedelta(days=i)).isoformat() for i in reversed(range(n))]
