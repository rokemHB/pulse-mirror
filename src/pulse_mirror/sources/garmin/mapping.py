"""Pure functions: raw Garmin payloads -> our domain model.

Garmin's responses are undocumented and change now and then, so every lookup
is defensive: a missing or renamed field yields "no value", never a crash.
Bump :data:`MAPPER_VERSION` whenever the output for the same input changes;
``pulse-mirror remap`` then re-runs the mapping over all stored raw data.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from pulse_mirror.model import Activity, MetricValue

MAPPER_VERSION = 1


def dig(data: Any, *path: str | int) -> Any:
    """Safe nested lookup: ``dig(d, "a", 0, "b")`` or ``None``."""
    for step in path:
        if isinstance(step, int):
            if not isinstance(data, list) or not -len(data) <= step < len(data):
                return None
            data = data[step]
        else:
            if not isinstance(data, dict):
                return None
            data = data.get(step)
        if data is None:
            return None
    return data


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _positive(value: Any) -> float | None:
    """Garmin uses 0 or negative values (e.g. -1, -2 for stress) as "no data"."""
    n = _num(value)
    return n if n is not None and n > 0 else None


def _non_negative(value: Any) -> float | None:
    n = _num(value)
    return n if n is not None and n >= 0 else None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _primary_device_entry(device_map: Any) -> Any:
    """Training status maps are keyed by device id; prefer the primary device."""
    if not isinstance(device_map, dict) or not device_map:
        return None
    entries = [v for v in device_map.values() if isinstance(v, dict)]
    for entry in entries:
        if entry.get("primaryTrainingDevice"):
            return entry
    return entries[0] if entries else None


# --- daily endpoints -------------------------------------------------------------


def map_user_summary(day: str, p: Any) -> Iterator[MetricValue]:
    if not isinstance(p, dict):
        return
    yield MetricValue(day, "resting_hr", _positive(p.get("restingHeartRate")))
    yield MetricValue(day, "steps", _positive(p.get("totalSteps")))
    yield MetricValue(day, "stress_avg", _positive(p.get("averageStressLevel")))
    yield MetricValue(day, "stress_max", _positive(p.get("maxStressLevel")))
    for key, field in [
        ("stress_rest_duration", "restStressDuration"),
        ("stress_low_duration", "lowStressDuration"),
        ("stress_medium_duration", "mediumStressDuration"),
        ("stress_high_duration", "highStressDuration"),
    ]:
        yield MetricValue(day, key, _non_negative(p.get(field)))
    yield MetricValue(day, "body_battery_max", _positive(p.get("bodyBatteryHighestValue")))
    yield MetricValue(day, "body_battery_min", _non_negative(p.get("bodyBatteryLowestValue")))
    yield MetricValue(day, "body_battery_charged", _non_negative(p.get("bodyBatteryChargedValue")))
    yield MetricValue(day, "body_battery_drained", _non_negative(p.get("bodyBatteryDrainedValue")))


def map_sleep(day: str, p: Any) -> Iterator[MetricValue]:
    dto = dig(p, "dailySleepDTO")
    if not isinstance(dto, dict):
        return
    duration = _positive(dto.get("sleepTimeSeconds"))
    yield MetricValue(day, "sleep_duration", duration)
    if duration is None:
        return  # no sleep recorded: the stage fields are zeros, not data
    yield MetricValue(day, "sleep_deep", _non_negative(dto.get("deepSleepSeconds")))
    yield MetricValue(day, "sleep_light", _non_negative(dto.get("lightSleepSeconds")))
    yield MetricValue(day, "sleep_rem", _non_negative(dto.get("remSleepSeconds")))
    yield MetricValue(day, "sleep_awake", _non_negative(dto.get("awakeSleepSeconds")))
    yield MetricValue(day, "sleep_score", _positive(dig(dto, "sleepScores", "overall", "value")))


def map_hrv(day: str, p: Any) -> Iterator[MetricValue]:
    summary = dig(p, "hrvSummary")
    if not isinstance(summary, dict):
        return
    yield MetricValue(day, "hrv_overnight_avg", _positive(summary.get("lastNightAvg")))
    yield MetricValue(day, "hrv_overnight_5min_high", _positive(summary.get("lastNight5MinHigh")))
    yield MetricValue(day, "hrv_weekly_avg", _positive(summary.get("weeklyAvg")))
    yield MetricValue(day, "hrv_baseline_low", _positive(dig(summary, "baseline", "balancedLow")))
    yield MetricValue(
        day, "hrv_baseline_high", _positive(dig(summary, "baseline", "balancedUpper"))
    )
    yield MetricValue(day, "hrv_status", value_text=_text(summary.get("status")))


def map_max_metrics(day: str, p: Any) -> Iterator[MetricValue]:
    entries = p if isinstance(p, list) else [p] if isinstance(p, dict) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for key, sport in [("vo2max_running", "generic"), ("vo2max_cycling", "cycling")]:
            block = entry.get(sport)
            if not isinstance(block, dict) or block.get("calendarDate", day) != day:
                continue
            value = _positive(block.get("vo2MaxPreciseValue")) or _positive(
                block.get("vo2MaxValue")
            )
            yield MetricValue(day, key, value)


def map_training_status(day: str, p: Any) -> Iterator[MetricValue]:
    latest = _primary_device_entry(dig(p, "mostRecentTrainingStatus", "latestTrainingStatusData"))
    if not isinstance(latest, dict) or latest.get("calendarDate", day) != day:
        return
    load = latest.get("acuteTrainingLoadDTO")
    if isinstance(load, dict):
        yield MetricValue(
            day, "training_load_acute", _non_negative(load.get("dailyTrainingLoadAcute"))
        )
        yield MetricValue(
            day, "training_load_chronic", _non_negative(load.get("dailyTrainingLoadChronic"))
        )
        yield MetricValue(
            day, "training_acwr", _non_negative(load.get("dailyAcuteChronicWorkloadRatio"))
        )
    phrase = _text(latest.get("trainingStatusFeedbackPhrase"))
    if phrase:
        # "PRODUCTIVE_1" -> "PRODUCTIVE"
        yield MetricValue(
            day,
            "training_status",
            value_text=phrase.rsplit("_", 1)[0] if phrase.rsplit("_", 1)[-1].isdigit() else phrase,
        )


_DAILY_MAPPERS = {
    "user_summary": map_user_summary,
    "sleep": map_sleep,
    "hrv": map_hrv,
    "max_metrics": map_max_metrics,
    "training_status": map_training_status,
}


def map_day(day: str, payloads: dict[str, Any]) -> list[MetricValue]:
    """Map all raw daily payloads for one day; drops empty values."""
    out: dict[str, MetricValue] = {}
    for endpoint, mapper in _DAILY_MAPPERS.items():
        for value in mapper(day, payloads.get(endpoint)):
            if value.value_num is not None or value.value_text is not None:
                out[value.metric_key] = value
    return list(out.values())


def day_has_data(day: str, payloads: dict[str, Any]) -> bool:
    """True if Garmin had anything for this day (used to detect the start of history)."""
    # Zeros don't count: Garmin sometimes reports 0 durations for days without a watch.
    return any(v.value_text or (v.value_num or 0) > 0 for v in map_day(day, payloads))


# --- activities --------------------------------------------------------------------


def _garmin_ts_to_utc(value: Any) -> str | None:
    """'2026-04-21 02:30:00' (GMT) -> '2026-04-21T02:30:00Z'."""
    text = _text(value)
    if not text or len(text) < 19:
        return None
    return text[:10] + "T" + text[11:19] + "Z"


def map_activity(raw: Any) -> Activity | None:
    if not isinstance(raw, dict) or raw.get("activityId") is None:
        return None
    start = _garmin_ts_to_utc(raw.get("startTimeGMT"))
    local = _text(raw.get("startTimeLocal"))
    if start is None or local is None:
        return None
    return Activity(
        source_id=str(raw["activityId"]),
        start_time=start,
        local_date=local[:10],
        type=_text(dig(raw, "activityType", "typeKey")) or "unknown",
        name=_text(raw.get("activityName")),
        duration_s=_positive(raw.get("duration")),
        distance_m=_positive(raw.get("distance")),
        elevation_gain_m=_non_negative(raw.get("elevationGain")),
        avg_hr=_positive(raw.get("averageHR")),
        max_hr=_positive(raw.get("maxHR")),
        avg_power=_positive(raw.get("avgPower")),
        norm_power=_positive(raw.get("normPower")),
        calories=_positive(raw.get("calories")),
        training_load=_non_negative(raw.get("activityTrainingLoad")),
        aerobic_te=_non_negative(raw.get("aerobicTrainingEffect")),
        anaerobic_te=_non_negative(raw.get("anaerobicTrainingEffect")),
    )
