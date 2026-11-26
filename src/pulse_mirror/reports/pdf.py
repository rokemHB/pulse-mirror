"""Weekly PDF report (reportlab): the week vs. your baselines, 8-week trends, notes."""

from __future__ import annotations

import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from pulse_mirror import __version__
from pulse_mirror.reports import summary

# Same fixed metric colors as the dashboard (light-mode steps).
COLORS = {
    "resting_hr": "#ec4647", "hrv_overnight_avg": "#257de0", "sleep_duration": "#6a4ce2",
    "training_load_acute": "#f47230",
}  # fmt: skip
TREND_KEYS = [
    ("resting_hr", "Resting HR (bpm)", 1.0),
    ("hrv_overnight_avg", "HRV overnight (ms)", 1.0),
    ("sleep_duration", "Sleep (h)", 1 / 3600),
    ("training_load_acute", "Acute training load", 1.0),
]


def iso_week_bounds(year: int, week: int) -> tuple[date, date]:
    monday = date.fromisocalendar(year, week, 1)
    return monday, monday + timedelta(days=6)


def report_filename(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"pulse-mirror-{year}-W{week:02d}.pdf"


def _avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _trend_chart(
    conn: sqlite3.Connection, key: str, title: str, scale: float, start: date, end: date,
    week_start: date, events: list[sqlite3.Row],
) -> Drawing:  # fmt: skip
    width, height = 85 * mm, 42 * mm
    d = Drawing(width, height)
    d.add(String(0, height - 9, title, fontName="Helvetica-Bold", fontSize=8))
    points = [
        ((date.fromisoformat(day) - start).days, v * scale)
        for day, v in summary.series_between(conn, key, start, end)
    ]
    if len(points) < 2:
        d.add(String(0, height / 2, "not enough data", fontSize=8, fillColor=colors.grey))
        return d
    plot = LinePlot()
    plot.x, plot.y, plot.width, plot.height = 22, 12, width - 28, height - 26
    plot.data = [points]
    plot.lines[0].strokeColor = colors.HexColor(COLORS[key])
    plot.lines[0].strokeWidth = 1.5
    span = (end - start).days
    plot.xValueAxis.valueMin, plot.xValueAxis.valueMax = 0, span
    plot.xValueAxis.valueSteps = list(range(0, span + 1, 7))
    plot.xValueAxis.labelTextFormat = lambda x: (start + timedelta(days=int(x))).strftime("%d.%m")
    plot.xValueAxis.labels.fontSize = 6
    plot.yValueAxis.labels.fontSize = 6
    plot.yValueAxis.strokeColor = plot.xValueAxis.strokeColor = colors.HexColor("#bbbbbb")
    vals = [v for _, v in points]
    pad = (max(vals) - min(vals)) * 0.15 or 1
    plot.yValueAxis.valueMin, plot.yValueAxis.valueMax = min(vals) - pad, max(vals) + pad

    def x_of(day: date) -> float:
        return float(plot.x + plot.width * (day - start).days / span)

    # shade the report week, then event bands/markers on top (like the dashboard)
    d.add(Rect(x_of(week_start), plot.y, x_of(end) - x_of(week_start), plot.height,
               fillColor=colors.HexColor("#f1f3f6"), strokeColor=None))  # fmt: skip
    for ev in events:
        s = max(start, date.fromisoformat(ev["start_at"][:10]))
        e = min(end, date.fromisoformat((ev["end_at"] or ev["start_at"])[:10]))
        color = colors.HexColor(ev["color"])
        if ev["end_at"]:
            d.add(Rect(x_of(s), plot.y, max(1.0, x_of(e) - x_of(s)), plot.height,
                       fillColor=colors.Color(color.red, color.green, color.blue, 0.18),
                       strokeColor=None))  # fmt: skip
        else:
            d.add(Line(x_of(s), plot.y, x_of(s), plot.y + plot.height, strokeColor=color,
                       strokeDashArray=[2, 2], strokeWidth=0.8))  # fmt: skip
    d.add(plot)
    return d


def build_weekly_report(conn: sqlite3.Connection, monday: date) -> bytes:
    sunday = monday + timedelta(days=6)
    start = monday - timedelta(days=7 * 7)  # 8 weeks incl. this one
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, textColor=colors.grey)
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=12)
    year, week, _ = monday.isocalendar()
    story: list[object] = [
        Paragraph(f"pulse-mirror · week {week}, {year}", styles["Title"]),
        Paragraph(f"{monday:%d %b} – {sunday:%d %b %Y}", small),
        Spacer(1, 6 * mm),
    ]

    # --- the week vs the four weeks before -------------------------------------------
    rows = [["Metric", "This week (avg)", "4 weeks before (avg)", "Change"]]
    for key in summary.SUMMARY_KEYS:
        meta = summary.metric_line(conn, key, sunday)
        this = _avg([v for _, v in summary.series_between(conn, key, monday, sunday)])
        prev = _avg([
            v for _, v in summary.series_between(conn, key, monday - timedelta(days=28),
                                                 monday - timedelta(days=1))
        ])  # fmt: skip
        if this is None and prev is None:
            continue
        change = "–" if this is None or prev is None else (
            ("+" if this - prev >= 0 else "−") + meta.fmt(abs(this - prev)))  # fmt: skip
        rows.append([meta.name, meta.fmt(this) if this is not None else "–",
                     meta.fmt(prev) if prev is not None else "–", change])  # fmt: skip
    table = Table(rows, colWidths=[62 * mm, 35 * mm, 38 * mm, 30 * mm])
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 9),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#999999")),
        ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#dddddd")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))  # fmt: skip
    story += [table, Spacer(1, 6 * mm)]

    # --- 8-week trends, two per row ----------------------------------------------------
    events = summary.events_between(conn, start, sunday)
    charts = [_trend_chart(conn, k, t, s, start, sunday, monday, events) for k, t, s in TREND_KEYS]
    grid = Table([charts[0:2], charts[2:4]], colWidths=[90 * mm, 90 * mm])
    story += [Paragraph("Last 8 weeks (this week shaded)", styles["Heading3"]), grid,
              Spacer(1, 4 * mm)]  # fmt: skip

    # --- activities, events, ratings --------------------------------------------------------
    acts = summary.activities_between(conn, monday, sunday)
    if acts:
        hours = sum((a["duration_s"] or 0) for a in acts) / 3600
        km = sum((a["distance_m"] or 0) for a in acts) / 1000
        story.append(
            Paragraph(f"Activities: {len(acts)} · {hours:.1f} h · {km:.0f} km", styles["Heading3"])
        )
        arows = [["Day", "Type", "Time", "km", "Climb m", "Avg W", "Load"]]
        for a in acts:
            mins = round((a["duration_s"] or 0) / 60)
            arows.append([
                date.fromisoformat(a["local_date"]).strftime("%a %d.%m"), a["type"],
                f"{mins // 60}:{mins % 60:02d}", f"{(a['distance_m'] or 0) / 1000:.1f}",
                f"{a['elevation_gain_m'] or 0:.0f}",
                f"{a['avg_power']:.0f}" if a["avg_power"] else "–",
                f"{a['training_load']:.0f}" if a["training_load"] else "–",
            ])  # fmt: skip
        at = Table(arows, colWidths=[24 * mm, 36 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm, 18 * mm])
        at.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, -1), "Helvetica", 8),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#999999")),
        ]))  # fmt: skip
        story += [at, Spacer(1, 4 * mm)]

    week_events = summary.events_between(conn, monday, sunday)
    if week_events:
        story.append(Paragraph("Events", styles["Heading3"]))
        for ev in week_events:
            when = ev["start_at"][:10] + (f" – {ev['end_at'][:10]}" if ev["end_at"] else "")
            note = f" — {_esc(ev['note'])}" if ev["note"] else ""
            story.append(Paragraph(
                f'<font color="{ev["color"]}">■</font> <b>{_esc(ev["title"])}</b> '
                f"({_esc(ev['category'])}, {when}){note}", body))  # fmt: skip
        story.append(Spacer(1, 4 * mm))

    ratings = summary.ratings_avg_between(conn, monday, sunday)
    if ratings:
        story.append(Paragraph("Wellbeing", styles["Heading3"]))
        parts = [f"<b>{_esc(name)}</b> avg {raw:.1f} ({n} ratings)" for name, _, raw, n in ratings]
        story.append(Paragraph(" · ".join(parts), body))

    story += [Spacer(1, 8 * mm), Paragraph(f"Generated by pulse-mirror {__version__}", small)]
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=f"pulse-mirror week {week} {year}")  # fmt: skip
    doc.build(story)
    return buf.getvalue()


def _esc(text: str | None) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_weekly_report(conn: sqlite3.Connection, monday: date, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / report_filename(monday)
    path.write_bytes(build_weekly_report(conn, monday))
    return path
