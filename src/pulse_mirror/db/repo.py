"""Small, explicit SQL helpers. Callers own the transaction."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict
from typing import Any

from pulse_mirror.db.connection import utcnow
from pulse_mirror.model import Activity, MetricValue


def upsert_raw(
    conn: sqlite3.Connection, source: str, endpoint: str, key: str, payload: Any
) -> None:
    conn.execute(
        """
        INSERT INTO raw_responses (source, endpoint, key, fetched_at, payload_json, mapper_version)
        VALUES (?, ?, ?, ?, ?, NULL)
        ON CONFLICT (source, endpoint, key) DO UPDATE SET
            fetched_at = excluded.fetched_at,
            payload_json = excluded.payload_json,
            mapper_version = NULL
        """,
        (source, endpoint, key, utcnow(), None if payload is None else json.dumps(payload)),
    )


def raw_payloads_for_key(conn: sqlite3.Connection, source: str, key: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT endpoint, payload_json FROM raw_responses WHERE source = ? AND key = ?",
        (source, key),
    ).fetchall()
    return {
        r["endpoint"]: None if r["payload_json"] is None else json.loads(r["payload_json"])
        for r in rows
    }


def mark_mapped(conn: sqlite3.Connection, source: str, key: str, mapper_version: int) -> None:
    conn.execute(
        "UPDATE raw_responses SET mapper_version = ? WHERE source = ? AND key = ?",
        (mapper_version, source, key),
    )


def replace_daily_metrics(
    conn: sqlite3.Connection, source: str, date: str, values: Iterable[MetricValue]
) -> int:
    """Replace all of ``source``'s metrics for ``date`` (idempotent re-mapping)."""
    conn.execute("DELETE FROM daily_metrics WHERE source = ? AND date = ?", (source, date))
    now = utcnow()
    rows = [
        (v.date, v.metric_key, source, v.value_num, v.value_text, now)
        for v in values
        if v.value_num is not None or v.value_text is not None
    ]
    conn.executemany(
        """INSERT INTO daily_metrics (date, metric_key, source, value_num, value_text, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    return len(rows)


_ACTIVITY_COLUMNS = [f for f in Activity.__dataclass_fields__ if f != "source_id"]


def upsert_activity(conn: sqlite3.Connection, source: str, activity: Activity) -> None:
    data = asdict(activity)
    cols = ["source", "source_id", *_ACTIVITY_COLUMNS, "updated_at"]
    values = [source, data["source_id"], *(data[c] for c in _ACTIVITY_COLUMNS), utcnow()]
    updates = ", ".join(f"{c} = excluded.{c}" for c in [*_ACTIVITY_COLUMNS, "updated_at"])
    conn.execute(
        f"INSERT INTO activities ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
        f"ON CONFLICT (source, source_id) DO UPDATE SET {updates}",
        values,
    )


# --- sync runs -------------------------------------------------------------------


def start_sync_run(conn: sqlite3.Connection, source: str, kind: str) -> int:
    cur = conn.execute(
        "INSERT INTO sync_runs (source, kind, started_at, status) VALUES (?, ?, ?, 'running')",
        (source, kind, utcnow()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def finish_sync_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    days_fetched: int,
    error_message: str | None = None,
) -> None:
    conn.execute(
        """UPDATE sync_runs SET finished_at = ?, status = ?, days_fetched = ?, error_message = ?
           WHERE id = ?""",
        (utcnow(), status, days_fetched, error_message, run_id),
    )


def last_sync_run(
    conn: sqlite3.Connection, source: str, status: str | None = None
) -> sqlite3.Row | None:
    sql = "SELECT * FROM sync_runs WHERE source = ?"
    params: list[Any] = [source]
    if status:
        sql += " AND status = ?"
        params.append(status)
    row: sqlite3.Row | None = conn.execute(
        sql + " ORDER BY started_at DESC, id DESC LIMIT 1", params
    ).fetchone()
    return row
