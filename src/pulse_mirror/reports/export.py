"""CSV export of everything (Garmin metrics, activities, events, ratings)."""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile

DATASETS: dict[str, str] = {
    "metrics": """SELECT d.date, d.metric_key, m.name AS metric, m.unit, d.source,
                         d.value_num, d.value_text
                  FROM daily_metrics d JOIN metric_types m ON m.key = d.metric_key
                  ORDER BY d.date, d.metric_key, d.source""",
    "activities": """SELECT local_date, start_time, type, name, duration_s, distance_m,
                            elevation_gain_m, avg_hr, max_hr, avg_power, norm_power, calories,
                            training_load, aerobic_te, anaerobic_te, source, source_id
                     FROM activities ORDER BY start_time""",
    "events": """SELECT e.id, e.start_at, e.end_at, c.name AS category, e.title, e.note,
                        e.source, e.created_at, e.updated_at
                 FROM events e JOIN event_categories c ON c.id = e.category_id
                 WHERE e.deleted_at IS NULL ORDER BY e.start_at""",
    "ratings": """SELECT r.id, r.recorded_at, s.key AS scale, v.name AS scale_name,
                         v.version AS scale_version, v.min_value, v.max_value, r.value, r.note,
                         r.source, r.created_at, r.updated_at
                  FROM ratings r JOIN scale_versions v ON v.id = r.scale_version_id
                  JOIN scales s ON s.id = v.scale_id
                  WHERE r.deleted_at IS NULL ORDER BY r.recorded_at""",
}


def dataset_csv(conn: sqlite3.Connection, name: str) -> str:
    cur = conn.execute(DATASETS[name])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([c[0] for c in cur.description])
    writer.writerows(tuple(row) for row in cur)
    return out.getvalue()


def all_zip(conn: sqlite3.Connection) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in DATASETS:
            zf.writestr(f"pulse-mirror-{name}.csv", dataset_csv(conn, name))
    return buf.getvalue()
