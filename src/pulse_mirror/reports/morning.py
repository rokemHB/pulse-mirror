"""The short morning summary sent after the scheduled sync."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from pulse_mirror.reports import summary
from pulse_mirror.sync.service import SyncResult


def _delta(line: summary.MetricLine) -> str:
    if line.value is None or line.avg28 is None:
        return ""
    diff = line.value - line.avg28
    if abs(diff) < (60 if line.unit == "s" else 0.5):
        return " (= 28d ⌀)"
    sign = "+" if diff > 0 else "−"
    return f" ({sign}{line.fmt(abs(diff)).replace(' ' + (line.unit or ''), '').strip()} vs 28d ⌀)"


def compose(conn: sqlite3.Connection, day: date, result: SyncResult | None) -> tuple[str, str]:
    if result is not None and result.status != "ok":
        hint = (
            "Log in again on the NAS:\n  docker exec -it pulse-mirror pulse-mirror login garmin\n"
            if result.status == "auth_required"
            else "The dashboard shows details; the next scheduled sync will retry.\n"
        )
        return (
            f"pulse-mirror: Garmin sync failed ({result.status})",
            f"This morning's sync failed:\n\n  {result.message}\n\n{hint}",
        )

    lines = {line.key: line for line in summary.morning_lines(conn, day)}
    fresh = {
        k: v for k, v in lines.items() if v.date and v.date >= (day - timedelta(days=1)).isoformat()
    }
    hrv_status = summary.text_value(conn, "hrv_status", day)
    status = summary.text_value(conn, "training_status", day)

    def part(key: str) -> str | None:
        line = fresh.get(key)
        return f"{line.name}: {line.fmt()}{_delta(line)}" if line else None

    body = [p for p in (part(k) for k in summary.SUMMARY_KEYS) if p]
    if hrv_status:
        body.append(f"HRV status: {hrv_status.lower()}")
    if status:
        body.append(f"Training status: {status.lower()}")
    yesterday = day - timedelta(days=1)
    acts = summary.activities_between(conn, yesterday, yesterday)
    if acts:
        mins = round(sum(a["duration_s"] or 0 for a in acts) / 60)
        km = sum(a["distance_m"] or 0 for a in acts) / 1000
        body.append(f"Yesterday: {len(acts)} activit{'y' if len(acts) == 1 else 'ies'}, "
                    f"{mins // 60}h {mins % 60:02d}m, {km:.0f} km")  # fmt: skip
    if not body:
        body = ["No new data from Garmin yet (has the watch synced with the phone?)."]

    head = []
    for key, label in (("sleep_duration", "sleep"), ("hrv_overnight_avg", "HRV"),
                       ("resting_hr", "RHR")):  # fmt: skip
        if key in fresh:
            head.append(f"{label} {fresh[key].fmt()}")
    subject = f"pulse-mirror {day:%a %d %b}" + (": " + " · ".join(head) if head else "")
    return subject, "\n".join(body) + "\n"
