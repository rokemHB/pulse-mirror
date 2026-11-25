from __future__ import annotations

import pytest

from pulse_mirror.sources.garmin import mapping
from tests.conftest import FIXTURE_DAY, fixture_sets, load_fixture_dir


def values(payloads):
    return {
        v.metric_key: v.value_num if v.value_text is None else v.value_text
        for v in mapping.map_day(FIXTURE_DAY, payloads)
    }


def test_synthetic_day_maps_all_metrics():
    v = values(load_fixture_dir("synthetic"))
    assert v == {
        "resting_hr": 52,
        "steps": 9120,
        "stress_avg": 31,
        "stress_max": 92,
        "stress_rest_duration": 27600,
        "stress_low_duration": 18900,
        "stress_medium_duration": 7800,
        "stress_high_duration": 1500,
        "body_battery_max": 88,
        "body_battery_min": 24,
        "body_battery_charged": 61,
        "body_battery_drained": 55,
        "sleep_duration": 26460,
        "sleep_deep": 5100,
        "sleep_light": 14460,
        "sleep_rem": 6060,
        "sleep_awake": 840,
        "sleep_score": 81,
        "hrv_overnight_avg": 58,
        "hrv_overnight_5min_high": 79,
        "hrv_weekly_avg": 55,
        "hrv_baseline_low": 49,
        "hrv_baseline_high": 63,
        "hrv_status": "BALANCED",
        "vo2max_running": 51.3,
        "vo2max_cycling": 54.6,
        "training_load_acute": 612,
        "training_load_chronic": 538,
        "training_acwr": 1.1,
        "training_status": "PRODUCTIVE",
    }


@pytest.mark.parametrize("fixture_set", fixture_sets())
def test_every_fixture_set_maps_core_metrics(fixture_set):
    """Recorded (anonymized) responses must still yield the core metrics.

    This is the canary for library updates that change response formats.
    """
    v = values(load_fixture_dir(fixture_set))
    for key in ("resting_hr", "sleep_duration", "hrv_overnight_avg", "body_battery_max"):
        assert key in v, f"{fixture_set}: {key} missing"


def test_empty_and_garbage_payloads_yield_nothing():
    assert mapping.map_day(FIXTURE_DAY, {}) == []
    garbage = {
        k: g for k, g in zip(mapping._DAILY_MAPPERS, [None, [], "x", 42, {"a": 1}], strict=True)
    }
    assert mapping.map_day(FIXTURE_DAY, garbage) == []
    assert not mapping.day_has_data(FIXTURE_DAY, garbage)


def test_no_data_markers_are_dropped():
    payloads = {
        "user_summary": {
            "restingHeartRate": 0,
            "averageStressLevel": -1,
            "totalSteps": 0,
            "restStressDuration": 0,
        },
        "sleep": {"dailySleepDTO": {"sleepTimeSeconds": 0, "deepSleepSeconds": 0}},
    }
    v = values(payloads)
    assert "resting_hr" not in v and "stress_avg" not in v and "steps" not in v
    assert "sleep_duration" not in v and "sleep_deep" not in v
    # zero durations are kept as values but don't count as "the watch was worn"
    assert not mapping.day_has_data(FIXTURE_DAY, payloads)


def test_vo2max_and_training_status_from_other_days_are_ignored():
    p = load_fixture_dir("synthetic")
    other_day = mapping.map_day(
        "2025-01-16", {"max_metrics": p["max_metrics"], "training_status": p["training_status"]}
    )
    assert other_day == []


def test_training_status_prefers_primary_device():
    p = {
        "mostRecentTrainingStatus": {
            "latestTrainingStatusData": {
                "1": {"calendarDate": FIXTURE_DAY, "trainingStatusFeedbackPhrase": "DETRAINING_2"},
                "2": {
                    "calendarDate": FIXTURE_DAY,
                    "trainingStatusFeedbackPhrase": "MAINTAINING_1",
                    "primaryTrainingDevice": True,
                },
            }
        }
    }
    assert values({"training_status": p})["training_status"] == "MAINTAINING"


def test_map_activity():
    raw = load_fixture_dir("synthetic")["activities"]
    ride = mapping.map_activity(raw[0])
    assert ride is not None
    assert ride.source_id == "1000004"
    assert ride.start_time == "2025-01-15T16:05:12Z"
    assert ride.local_date == "2025-01-15"
    assert ride.type == "cycling"
    assert ride.avg_power == 196 and ride.norm_power == 214
    run = mapping.map_activity(raw[1])
    assert run is not None and run.avg_power is None and run.elevation_gain_m == 0
    assert mapping.map_activity({"activityId": 1}) is None
    assert mapping.map_activity("nope") is None
