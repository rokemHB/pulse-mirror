"""Source-agnostic domain types shared by sources, storage and the API."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricValue:
    """One daily value of one metric from one source."""

    date: str  # local date YYYY-MM-DD
    metric_key: str
    value_num: float | None = None
    value_text: str | None = None


@dataclass(frozen=True)
class Activity:
    source_id: str
    start_time: str  # ISO 8601 UTC
    local_date: str
    type: str
    name: str | None = None
    duration_s: float | None = None
    distance_m: float | None = None
    elevation_gain_m: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_power: float | None = None
    norm_power: float | None = None
    calories: float | None = None
    training_load: float | None = None
    aerobic_te: float | None = None
    anaerobic_te: float | None = None
