from __future__ import annotations

import json
from datetime import date

from pulse_mirror.demo.generator import generate
from pulse_mirror.demo.pages import build_pages

END = date(2025, 6, 30)


def test_generator_is_deterministic_and_plausible(conn, tmp_path):
    generate(conn, END, days=120)
    rhr = [
        r[0]
        for r in conn.execute(
            "SELECT value_num FROM daily_metrics WHERE metric_key='resting_hr' ORDER BY date"
        )
    ]
    assert len(rhr) == 120 and min(rhr) >= 40 and max(rhr) <= 75
    assert conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0] > 30
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] >= 3
    assert conn.execute("SELECT COUNT(DISTINCT scale_version_id) FROM ratings").fetchone()[0] == 3
    sources = {r[0] for r in conn.execute("SELECT DISTINCT source FROM daily_metrics")}
    assert sources == {"demo"}

    from pulse_mirror.db import connect, migrate

    other = connect(tmp_path / "again.db")
    migrate(other)
    generate(other, END, days=120)
    q = "SELECT date, metric_key, value_num, value_text FROM daily_metrics ORDER BY 1, 2"
    assert [tuple(r) for r in conn.execute(q)] == [tuple(r) for r in other.execute(q)]


def test_build_pages(tmp_path):
    out = build_pages(tmp_path / "site", END, days=60)
    assert (out / "index.html").exists() and (out / "vendor" / "echarts.min.js").exists()
    assert "static" in (out / "config.js").read_text()
    data = {p.stem: json.loads(p.read_text()) for p in (out / "demo-data").glob("*.json")}
    assert set(data) == {
        "status",
        "metrics",
        "daily",
        "activities",
        "event-categories",
        "events",
        "scales",
        "ratings",
    }
    assert data["status"]["demo"] is True
    keys = {s["key"] for s in data["daily"]}
    assert {"resting_hr", "hrv_overnight_avg", "sleep_duration"} <= keys
    # no real-person data can sneak in: everything is from the demo source
    assert {s["source"] for s in data["daily"]} == {"demo"}
