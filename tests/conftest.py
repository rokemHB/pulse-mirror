from __future__ import annotations

import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from pulse_mirror.db import connect, migrate
from pulse_mirror.sources.garmin.errors import RateLimitedError
from pulse_mirror.sources.garmin.throttle import Throttle

FIXTURES = Path(__file__).parent / "fixtures" / "garmin"
FIXTURE_DAY = "2025-01-15"


def load_fixture_dir(name: str) -> dict[str, Any]:
    return {p.stem: json.loads(p.read_text()) for p in sorted((FIXTURES / name).glob("*.json"))}


def fixture_sets() -> list[str]:
    return [d for d in ("synthetic", "recorded") if (FIXTURES / d).is_dir()]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = connect(tmp_path / "test.db")
    migrate(c)
    return c


@pytest.fixture
def throttle() -> Throttle:
    return Throttle(0, jitter_s=0, sleep=lambda s: None)


class FakeGarmin:
    """In-memory GarminApi: serves the synthetic day for every date in ``days_with_data``."""

    def __init__(self, days_with_data: set[str], rate_limit_on: set[str] | None = None) -> None:
        self.days_with_data = days_with_data
        self.rate_limit_on = set(rate_limit_on or ())
        self.template = load_fixture_dir("synthetic")
        self.calls: list[tuple[str, str]] = []

    def daily_endpoint(self, endpoint: str, day: str) -> Any:
        self.calls.append((endpoint, day))
        if day in self.rate_limit_on:
            self.rate_limit_on.discard(day)  # only once
            raise RateLimitedError("429")
        if day not in self.days_with_data:
            return None
        text = json.dumps(self.template[endpoint]).replace(FIXTURE_DAY, day)
        return json.loads(text)

    def activities(self, start: str, end: str) -> list[dict[str, Any]]:
        """One ride per day with data in [start, end]."""
        self.calls.append(("activities", f"{start}..{end}"))
        template = self.template["activities"][0]
        out = []
        for day in sorted(days_between(start, end) & self.days_with_data):
            item = json.loads(json.dumps(template).replace(template["startTimeLocal"][:10], day))
            item["activityId"] = f"{template['activityId']}{day.replace('-', '')}"
            out.append(item)
        return out


def days_between(first: str, last: str) -> set[str]:
    d0, d1 = date.fromisoformat(first), date.fromisoformat(last)
    return {(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)}
