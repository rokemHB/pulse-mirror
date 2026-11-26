"""Numbers shared by the morning email and the weekly PDF (one source of truth)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class MetricLine:
    key: str
    name: str
    unit: str | None
    value: float | None
    date: str | None
    avg7: float | None
    avg28: float | None

    def fmt(self, v: float | None = None) -> str:
        v = self.value if v is None else v
        if v is None:
            return "–"
        if self.unit == "s":
            minutes = round(v / 60)
            return f"{minutes // 60}h {minutes % 60:02d}m"
        digits = 1 if self.key.startswith("vo2max") else 0
        text = f"{v:.{digits}f}"
        return f"{text} {self.unit}" if self.unit else text


SUMMARY_KEYS = [
    "sleep_duration", "sleep_score", "hrv_overnight_avg", "resting_hr",
    "body_battery_max", "stress_avg", "training_load_acute", "vo2max_cycling",
]  # fmt: skip


def _series(conn: sqlite3.Connection, key: str, start: date, end: date) -> list[tuple[str, float]]:
    rows = conn.execute(
        """SELECT date, value_num FROM daily_metrics
           WHERE metric_key = ? AND value_num IS NOT NULL AND date BETWEEN ? AND ?
           ORDER BY date, CASE source WHEN 'garmin' THEN 0 ELSE 1 END""",
        (key, start.isoformat(), end.isoformat()),
    ).fetchall()
    seen: dict[str, float] = {}
    for d, v in rows:
        seen.setdefault(d, v)
    return sorted(seen.items())


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def metric_line(conn: sqlite3.Connection, key: str, day: date) -> MetricLine:
    meta = conn.execute("SELECT name, unit FROM metric_types WHERE key = ?", (key,)).fetchone()
    points = _series(conn, key, day - timedelta(days=30), day)
    if not points:
        return MetricLine(key, meta["name"], meta["unit"], None, None, None, None)
    last_day, value = points[-1]
    last = date.fromisoformat(last_day)
    before = [(date.fromisoformat(d), v) for d, v in points[:-1]]

    def window(n: int) -> list[float]:
        return [v for d, v in before if d >= last - timedelta(days=n)]

    return MetricLine(
        key, meta["name"], meta["unit"], value, last_day, _avg(window(7)), _avg(window(28))
    )


def morning_lines(conn: sqlite3.Connection, day: date) -> list[MetricLine]:
    return [metric_line(conn, k, day) for k in SUMMARY_KEYS]


def text_value(conn: sqlite3.Connection, key: str, day: date) -> str | None:
    row = conn.execute(
        "SELECT value_text FROM daily_metrics WHERE metric_key = ? AND date = ? LIMIT 1",
        (key, day.isoformat()),
    ).fetchone()
    return row[0] if row else None


def activities_between(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM activities WHERE local_date BETWEEN ? AND ? ORDER BY start_time",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def events_between(conn: sqlite3.Connection, start: date, end: date) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT e.*, c.name AS category, c.color FROM events e
           JOIN event_categories c ON c.id = e.category_id
           WHERE e.deleted_at IS NULL AND e.start_at < ? AND COALESCE(e.end_at, e.start_at) >= ?
           ORDER BY e.start_at""",
        ((end + timedelta(days=1)).isoformat(), start.isoformat()),
    ).fetchall()


def ratings_avg_between(
    conn: sqlite3.Connection, start: date, end: date
) -> list[tuple[str, float, float, int]]:
    """Per scale: (name, average normalized to 0..1, average raw, count)."""
    rows = conn.execute(
        """SELECT s.id, MAX(v.name) AS name,
                  AVG((r.value - v.min_value) / (v.max_value - v.min_value)),
                  AVG(r.value), COUNT(*)
           FROM ratings r JOIN scale_versions v ON v.id = r.scale_version_id
           JOIN scales s ON s.id = v.scale_id
           WHERE r.deleted_at IS NULL AND r.recorded_at >= ? AND r.recorded_at < ?
           GROUP BY s.id ORDER BY s.sort_order""",
        (start.isoformat(), (end + timedelta(days=1)).isoformat()),
    ).fetchall()
    return [(r[1], r[2], r[3], r[4]) for r in rows]


def series_between(
    conn: sqlite3.Connection, key: str, start: date, end: date
) -> list[tuple[str, float]]:
    return _series(conn, key, start, end)
