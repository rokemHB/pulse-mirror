"""Synthetic but plausible data for the demo (source ``demo``). Deterministic per seed.

The model is deliberately simple but internally consistent, so the charts tell
stories: fitness builds over the season, illness and alcohol push resting HR up
and HRV down, a cycling trip shows up in training load, ratings follow HRV and
sleep. No real person's data is involved.
"""

from __future__ import annotations

import math
import random
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pulse_mirror.db import repo, transaction
from pulse_mirror.db.connection import utcnow
from pulse_mirror.model import Activity, MetricValue

SOURCE = "demo"


@dataclass(frozen=True)
class DemoEvent:
    category: str
    title: str
    start: date
    days: int | None  # None = a point in time (the evening of ``start``)
    note: str | None = None


def demo_events(end: date) -> list[DemoEvent]:
    """A handful of made-up events, placed relative to ``end``."""

    def ago(days: int) -> date:
        return end - timedelta(days=days)

    def summer(years_back: int) -> date:
        return date(end.year - years_back - (1 if end.month < 8 else 0), 7, 12)

    return [
        DemoEvent("trip", "Alpine cycling trip", summer(0), 9, "Seven passes in nine days."),
        DemoEvent("trip", "Gravel week in the hills", summer(1), 7),
        DemoEvent("illness", "Flu", ago(300), 6, "Fever for three days."),
        DemoEvent("illness", "Cold", ago(75), 4),
        DemoEvent("alcohol", "Wedding party", ago(130), None),
        DemoEvent("alcohol", "Team dinner, a few beers", ago(40), None),
        DemoEvent("alcohol", "Birthday drinks", ago(12), None),
        DemoEvent("poor_sleep", "Kids sick, little sleep", ago(55), None),
        DemoEvent("poor_sleep", "Late flight home", ago(200), None),
        DemoEvent("stress", "Deadline week", ago(160), 5),
        DemoEvent("injury", "Knee pain, no riding", ago(420), 10),
        DemoEvent("vaccination", "Booster shot", ago(230), None),
        DemoEvent("trip", "Winter holidays", ago(330), 8),
    ]


SCALES = [
    # key, name, description, min, max, step, min_label, max_label, color
    (
        "energy",
        "Energy",
        "How energetic do I feel?",
        1,
        10,
        1,
        "drained",
        "full of energy",
        "#f4a261",
    ),
    ("mood", "Mood", "General mood", 1, 5, 1, "terrible", "excellent", "#2a9d8f"),
    (
        "soreness",
        "Muscle soreness",
        "Legs after training",
        0,
        10,
        1,
        "none",
        "very sore",
        "#e76f51",
    ),
]


def _effects(events: list[DemoEvent], day: date) -> dict[str, float]:
    """How strongly each kind of event affects ``day`` (0..1)."""
    out = {
        "illness": 0.0,
        "alcohol": 0.0,
        "poor_sleep": 0.0,
        "stress": 0.0,
        "trip": 0.0,
        "injury": 0.0,
        "vaccination": 0.0,
    }
    for ev in events:
        if ev.days is None:
            # point events hit the following night/morning, fading over two days
            delta = (day - ev.start).days
            if 1 <= delta <= 2:
                out[ev.category] = max(out[ev.category], 1.0 if delta == 1 else 0.35)
        else:
            delta = (day - ev.start).days
            if 0 <= delta < ev.days:
                out[ev.category] = max(out[ev.category], 1.0)
            elif ev.category == "illness" and ev.days <= delta < ev.days + 5:
                out["illness"] = max(out["illness"], 0.5 * (1 - (delta - ev.days) / 5))
    return out


def generate(conn: sqlite3.Connection, end: date, days: int = 900, seed: int = 7) -> None:
    rng = random.Random(seed)
    events = demo_events(end)
    start = end - timedelta(days=days - 1)
    acute = chronic = 40.0
    vo2_run, vo2_bike = 47.0, 50.0
    hrv_hist: list[float] = []
    loads: dict[date, float] = {}

    with transaction(conn):
        versions = _ensure_scales(conn)
        for i in range(days):
            day = start + timedelta(days=i)
            fx = _effects(events, day)
            season = math.sin((day.timetuple().tm_yday - 100) / 365 * 2 * math.pi)  # summer peak
            fitness = 0.5 + 0.25 * season + 0.15 * (i / days) + 0.05 * math.sin(i / 23)

            # --- training ---------------------------------------------------------
            ride_p = 0.45 + 0.25 * season
            if day.weekday() >= 5:
                ride_p += 0.25
            if fx["trip"] and any(
                e.title.startswith(("Alpine", "Gravel"))
                for e in events
                if e.days and 0 <= (day - e.start).days < e.days
            ):
                ride_p = 1.0
            if fx["illness"] > 0.4 or fx["injury"]:
                ride_p = 0.0
            load = 0.0
            if rng.random() < ride_p:
                long_ride = day.weekday() >= 5 or ride_p == 1.0
                hours = rng.uniform(2.0, 4.0) if long_ride else rng.uniform(0.75, 1.75)
                power = 160 + 55 * fitness + rng.gauss(0, 10)
                load = hours * (38 + 30 * (power - 150) / 100) + rng.gauss(0, 8)
                load = max(20.0, load)
                speed = 23 + 7 * fitness + rng.gauss(0, 1.5)
                start_local = datetime(
                    day.year, day.month, day.day, 9 if long_ride else 17, rng.randint(0, 59)
                )
                repo.upsert_activity(
                    conn,
                    SOURCE,
                    Activity(
                        source_id=f"demo-{day.isoformat()}",
                        start_time=(start_local - timedelta(hours=1)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        local_date=day.isoformat(),
                        type="road_biking" if rng.random() < 0.7 else "gravel_cycling",
                        name="Long ride" if long_ride else "After-work ride",
                        duration_s=round(hours * 3600),
                        distance_m=round(hours * speed * 1000),
                        elevation_gain_m=round(
                            hours * rng.uniform(150, 900 if fx["trip"] else 450)
                        ),
                        avg_hr=round(128 + 12 * rng.random() - 5 * fitness),
                        max_hr=round(160 + 15 * rng.random()),
                        avg_power=round(power),
                        norm_power=round(power * rng.uniform(1.04, 1.12)),
                        calories=round(hours * power * 3.6),
                        training_load=round(load, 1),
                        aerobic_te=round(min(5.0, 2.0 + hours * 0.7 + rng.gauss(0, 0.3)), 1),
                        anaerobic_te=round(max(0.0, rng.gauss(0.8, 0.5)), 1),
                    ),
                )
            elif rng.random() < 0.15 and not fx["injury"]:
                load = rng.uniform(30, 60)  # an occasional run
                minutes = rng.uniform(25, 45)
                repo.upsert_activity(
                    conn,
                    SOURCE,
                    Activity(
                        source_id=f"demo-run-{day.isoformat()}",
                        start_time=f"{day.isoformat()}T06:30:00Z",
                        local_date=day.isoformat(),
                        type="running",
                        name="Easy run",
                        duration_s=round(minutes * 60),
                        distance_m=round(minutes * 170),
                        elevation_gain_m=round(rng.uniform(10, 80)),
                        avg_hr=round(140 + rng.gauss(0, 5)),
                        max_hr=round(158 + rng.gauss(0, 5)),
                        calories=round(minutes * 11),
                        training_load=round(load, 1),
                        aerobic_te=2.6,
                        anaerobic_te=0.4,
                    ),
                )
            loads[day] = load
            acute += (load * 7 - acute) / 7  # weekly-scale EWMAs like Garmin's
            chronic += (load * 7 - chronic) / 28
            acwr = acute / chronic if chronic else 1.0

            # --- recovery & physiology -------------------------------------------------
            yesterday_load = loads.get(day - timedelta(days=1), 0.0)
            sleep_h = 7.1 + (0.5 if day.weekday() >= 5 else 0) + rng.gauss(0, 0.45)
            sleep_h -= 2.2 * fx["poor_sleep"] + 1.0 * fx["alcohol"] + 0.6 * fx["stress"]
            sleep_h += 0.6 * fx["illness"]
            sleep_h = max(3.5, min(9.8, sleep_h))

            rhr = (
                54
                - 6 * fitness
                + 7 * fx["illness"]
                + 5 * fx["alcohol"]
                + 2 * fx["stress"]
                + 1.5 * fx["vaccination"]
                + 0.012 * yesterday_load
                + rng.gauss(0, 1.1)
            )
            hrv = (
                52
                + 14 * fitness
                - 18 * fx["illness"]
                - 14 * fx["alcohol"]
                - 7 * fx["stress"]
                - 6 * fx["poor_sleep"]
                - 6 * fx["vaccination"]
                - 0.025 * yesterday_load
                + 1.5 * (sleep_h - 7)
                + rng.gauss(0, 3.5)
            )
            hrv = max(18.0, hrv)
            hrv_hist.append(hrv)
            weekly = sum(hrv_hist[-7:]) / len(hrv_hist[-7:])
            base = hrv_hist[-60:]
            base_mean = sum(base) / len(base)
            base_lo, base_hi = base_mean * 0.88, base_mean * 1.12
            hrv_status = (
                "BALANCED"
                if base_lo <= weekly <= base_hi
                else "LOW"
                if weekly < base_mean * 0.8
                else "UNBALANCED"
            )

            deep = sleep_h * rng.uniform(0.15, 0.22) * (0.7 if fx["alcohol"] else 1)
            rem = sleep_h * rng.uniform(0.18, 0.25) * (0.75 if fx["alcohol"] else 1)
            awake = rng.uniform(0.1, 0.4) + 0.5 * fx["poor_sleep"]
            light = max(0.5, sleep_h - deep - rem)
            score = 88 - 10 * abs(7.8 - sleep_h) - 15 * fx["alcohol"] - 20 * fx["poor_sleep"]
            score += (hrv - base_mean) * 0.4 + rng.gauss(0, 3)
            score = max(25, min(98, score))

            bb_max = max(20, min(100, 40 + 6 * sleep_h + 0.5 * (hrv - base_mean) + rng.gauss(0, 4)))
            bb_min = max(5, min(bb_max - 15, bb_max - 45 - 0.25 * load + rng.gauss(0, 6)))
            stress = max(
                12,
                min(
                    70,
                    34
                    - 0.3 * (hrv - 50)
                    + 12 * fx["stress"]
                    + 10 * fx["illness"]
                    + rng.gauss(0, 4),
                ),
            )
            steps = max(1500, rng.gauss(8500, 2200) - 4000 * fx["illness"])

            if load > 0 and rng.random() < 0.35:  # VO2max only updates after some workouts
                target = 46 + 9 * fitness
                vo2_bike += (target + 3 - vo2_bike) * 0.15
                vo2_run += (target - vo2_run) * 0.08

            status_text = (
                "OVERREACHING"
                if acwr > 1.5
                else "PRODUCTIVE"
                if acwr > 1.1
                else "MAINTAINING"
                if acwr > 0.8
                else "RECOVERY"
                if acwr > 0.6
                else "DETRAINING"
            )
            d = day.isoformat()
            values = [
                MetricValue(d, "resting_hr", round(rhr)),
                MetricValue(d, "hrv_overnight_avg", round(hrv)),
                MetricValue(d, "hrv_overnight_5min_high", round(hrv * rng.uniform(1.25, 1.5))),
                MetricValue(d, "hrv_weekly_avg", round(weekly)),
                MetricValue(d, "hrv_baseline_low", round(base_lo)),
                MetricValue(d, "hrv_baseline_high", round(base_hi)),
                MetricValue(d, "hrv_status", value_text=hrv_status),
                MetricValue(d, "sleep_duration", round(sleep_h * 3600)),
                MetricValue(d, "sleep_deep", round(deep * 3600)),
                MetricValue(d, "sleep_light", round(light * 3600)),
                MetricValue(d, "sleep_rem", round(rem * 3600)),
                MetricValue(d, "sleep_awake", round(awake * 3600)),
                MetricValue(d, "sleep_score", round(score)),
                MetricValue(d, "body_battery_max", round(bb_max)),
                MetricValue(d, "body_battery_min", round(bb_min)),
                MetricValue(
                    d, "body_battery_charged", round(bb_max - bb_min + rng.uniform(-5, 10))
                ),
                MetricValue(d, "body_battery_drained", round(bb_max - bb_min + rng.uniform(0, 12))),
                MetricValue(d, "stress_avg", round(stress)),
                MetricValue(d, "stress_max", round(min(99, stress + rng.uniform(35, 60)))),
                MetricValue(d, "steps", round(steps)),
                MetricValue(d, "vo2max_running", round(vo2_run, 1)),
                MetricValue(d, "vo2max_cycling", round(vo2_bike, 1)),
                MetricValue(d, "training_load_acute", round(acute)),
                MetricValue(d, "training_load_chronic", round(chronic)),
                MetricValue(d, "training_acwr", round(acwr, 2)),
                MetricValue(d, "training_status", value_text=status_text),
            ]
            repo.replace_daily_metrics(conn, SOURCE, d, values)

            # --- ratings (most mornings) -----------------------------------------------
            if rng.random() < 0.72 and i > days - 420:
                _rate_morning(
                    conn, rng, versions, day, hrv - base_mean, sleep_h, yesterday_load, fx
                )

        _insert_events(conn, events, end)
        repo.finish_sync_run(conn, repo.start_sync_run(conn, "garmin", "scheduled"), "ok", 3)


def _ensure_scales(conn: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for order, (key, name, desc, lo, hi, step, lo_l, hi_l, color) in enumerate(SCALES):
        row = conn.execute("SELECT id FROM scales WHERE key = ?", (key,)).fetchone()
        if row is None:
            now = utcnow()
            scale_id = conn.execute(
                "INSERT INTO scales (key, sort_order, created_at) VALUES (?, ?, ?)",
                (key, order, now),
            ).lastrowid
            conn.execute(
                """INSERT INTO scale_versions (scale_id, version, name, description, min_value,
                       max_value, step, min_label, max_label, color, created_at)
                   VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (scale_id, name, desc, lo, hi, step, lo_l, hi_l, color, now),
            )
        version = conn.execute(
            """SELECT v.id FROM scale_versions v JOIN scales s ON s.id = v.scale_id
               WHERE s.key = ? ORDER BY v.version DESC LIMIT 1""",
            (key,),
        ).fetchone()
        out[key] = int(version[0])
    return out


def _rate_morning(
    conn: sqlite3.Connection,
    rng: random.Random,
    versions: dict[str, int],
    day: date,
    hrv_delta: float,
    sleep_h: float,
    load: float,
    fx: dict[str, float],
) -> None:
    def clamp(v: float, lo: int, hi: int) -> int:
        return max(lo, min(hi, round(v)))

    energy = clamp(
        6
        + 0.12 * hrv_delta
        + 0.8 * (sleep_h - 7)
        - 3 * fx["illness"]
        - 1.5 * fx["alcohol"]
        + rng.gauss(0, 1),
        1,
        10,
    )
    mood = clamp(
        3.5
        + 0.05 * hrv_delta
        - 1.2 * fx["stress"]
        - fx["illness"]
        + 0.8 * fx["trip"]
        + rng.gauss(0, 0.6),
        1,
        5,
    )
    soreness = clamp(load / 40 + rng.gauss(0, 1), 0, 10)
    recorded = datetime(day.year, day.month, day.day, 6, rng.randint(30, 59), tzinfo=UTC)
    now = utcnow()
    for key, value in (("energy", energy), ("mood", mood), ("soreness", soreness)):
        conn.execute(
            """INSERT INTO ratings (id, scale_version_id, value, note, recorded_at, source,
                                    created_at, updated_at)
               VALUES (?, ?, ?, NULL, ?, ?, ?, ?)""",
            (
                str(uuid.UUID(int=rng.getrandbits(128))),
                versions[key],
                value,
                recorded.strftime("%Y-%m-%dT%H:%M:%SZ"),
                SOURCE,
                now,
                now,
            ),
        )


def _insert_events(conn: sqlite3.Connection, events: list[DemoEvent], end: date) -> None:
    cats = {r["key"]: r["id"] for r in conn.execute("SELECT id, key FROM event_categories")}
    now = utcnow()
    for i, ev in enumerate(events):
        if ev.start > end:
            continue
        if ev.days is None:
            start_at = f"{ev.start.isoformat()}T20:00:00Z"
            end_at = None
        else:
            start_at = f"{ev.start.isoformat()}T06:00:00Z"
            end_at = f"{(ev.start + timedelta(days=ev.days - 1)).isoformat()}T20:00:00Z"
        conn.execute(
            """INSERT INTO events (id, category_id, title, note, start_at, end_at, source,
                                   created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.UUID(int=i + 1)),
                cats[ev.category],
                ev.title,
                ev.note,
                start_at,
                end_at,
                SOURCE,
                now,
                now,
            ),
        )
