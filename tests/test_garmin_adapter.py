"""Adapter boundary: exception translation, anonymizer, and the import rule."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

from pulse_mirror.sources.garmin import client
from pulse_mirror.sources.garmin.errors import (
    AuthRequiredError,
    GarminUnavailableError,
    RateLimitedError,
)
from pulse_mirror.sources.garmin.fixtures import FIXTURE_DATE, anonymize

SRC = Path(__file__).parent.parent / "src" / "pulse_mirror"


class Raising:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def __getattr__(self, name):
        def method(*args):
            raise self.exc

        return method


@pytest.mark.parametrize(
    ("exc", "ours"),
    [
        (GarminConnectTooManyRequestsError("429"), RateLimitedError),
        (GarminConnectAuthenticationError("401"), AuthRequiredError),
        (GarminConnectConnectionError("boom"), GarminUnavailableError),
    ],
)
def test_library_errors_are_translated(exc, ours):
    c = client.GarminClient(Raising(exc))
    with pytest.raises(ours):
        c.daily_endpoint("sleep", "2025-01-15")


def test_no_data_is_none_not_an_error():
    c = client.GarminClient(Raising(GarminConnectConnectionError("No data received from server")))
    assert c.daily_endpoint("user_summary", "2025-01-15") is None


def test_missing_tokenstore_requires_login(tmp_path):
    with pytest.raises(AuthRequiredError, match="login garmin"):
        client.GarminClient.from_tokenstore(tmp_path / "nope")


def test_only_client_imports_garminconnect():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+garminconnect\b", p.read_text(), re.M)
    ]
    assert offenders == ["sources/garmin/client.py"]


def test_anonymize_removes_identifying_data_and_real_values():
    raw = {
        "userProfilePK": 987654321,
        "displayName": "real-person",
        "ownerFullName": "Real Person",
        "email": "real@example.com",
        "calendarDate": "2026-11-20",
        "startTimeLocal": "2026-11-20 07:30:00",
        "sleepStartTimestampGMT": 1795240800000,
        "startLatitude": 52.52,
        "activityName": "Morning ride to the office",
        "restingHeartRate": 50,
        "latestTrainingStatusData": {"3412882339": {"deviceId": 3412882339, "trainingStatus": 7}},
        "notes": ["contact me at real@example.com"],
    }
    out = anonymize(raw, date(2026, 11, 20))
    text = json.dumps(out)
    for secret in (
        "987654321",
        "real-person",
        "Real Person",
        "real@example.com",
        "52.52",
        "office",
        "3412882339",
        "2026-11-20",
    ):
        assert secret not in text
    assert out["calendarDate"] == FIXTURE_DATE.isoformat()
    assert out["startTimeLocal"] == "2025-01-15 07:30:00"
    assert (
        out["sleepStartTimestampGMT"]
        == 1795240800000 - (date(2026, 11, 20) - FIXTURE_DATE).days * 86_400_000
    )
    assert 45 <= out["restingHeartRate"] <= 55
    device = next(iter(out["latestTrainingStatusData"].values()))
    assert device["trainingStatus"] == 7
    assert out["activityName"] == "Activity"


def test_anonymize_is_deterministic():
    raw = {"a": {"b": 123.4}, "userId": 5}
    assert anonymize(raw, date(2026, 1, 1)) == anonymize(raw, date(2026, 1, 1))
