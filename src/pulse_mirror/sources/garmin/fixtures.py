"""Developer tooling: record real responses and anonymize them into test fixtures.

Recorded raw files go to ``tests/fixtures/garmin/_raw/`` (git-ignored). Only
the output of :func:`anonymize` may be committed, and only after a manual review.

Anonymization:
* identifying fields (ids, names, emails, image URLs, GPS) are replaced or dropped
* dict keys that are numeric ids (Garmin keys some maps by device id) are replaced
* all dates are shifted so the recorded day becomes :data:`FIXTURE_DATE`
* other numeric values are perturbed by a deterministic ±10 %, so real health
  values never end up in the public repo while the response shape stays intact
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from pulse_mirror.sources.garmin.client import DAILY_ENDPOINTS, GarminApi
from pulse_mirror.sources.garmin.throttle import Throttle

FIXTURE_DATE = date(2025, 1, 15)

_ID_KEYS = {
    "userprofilepk",
    "userprofileid",
    "userid",
    "ownerid",
    "profileid",
    "deviceid",
    "activityid",
    "parentid",
    "sleepid",
    "id",
    "summaryid",
    "usersummaryid",
    "primarydeviceid",
    "devicefullname",
    "unitid",
    "applicationkey",
}
_DROP_KEYS_CONTAINING = (
    "latitude",
    "longitude",
    "polyline",
    "location",
    "imageurl",
    "profileimage",
    "displayname",
    "fullname",
    "username",
    "email",
    "uuid",
    "serial",
)
_TEXT_KEYS = {"activityname": "Activity", "description": None, "devicename": "Device"}
# numeric fields whose exact value is structural, not a measurement
_KEEP_NUMBER_KEYS_CONTAINING = ("typeid", "version", "sortorder", "status", "zone")

_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})")


def _jitter(path: str, value: float) -> float:
    digest = hashlib.sha256(path.encode()).digest()
    factor = 0.9 + (digest[0] / 255) * 0.2
    return value * factor


class _Anonymizer:
    def __init__(self, recorded_day: date) -> None:
        self.delta = FIXTURE_DATE - recorded_day
        self.ids: dict[str, int] = {}

    def fake_id(self, value: Any) -> int:
        key = str(value)
        if key not in self.ids:
            self.ids[key] = 1000001 + len(self.ids)
        return self.ids[key]

    def shift_date_str(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            try:
                return (date.fromisoformat(m.group(1)) + self.delta).isoformat()
            except ValueError:
                return m.group(1)

        return _DATE_RE.sub(repl, text)

    def value(self, key: str, value: Any, path: str) -> Any:
        k = key.lower()
        if k in _ID_KEYS and value is not None and not isinstance(value, dict | list):
            return self.fake_id(value)
        if any(part in k for part in _DROP_KEYS_CONTAINING):
            return None
        if k in _TEXT_KEYS and isinstance(value, str):
            return _TEXT_KEYS[k]
        return self.walk(value, path, k)

    def walk(self, node: Any, path: str = "", key: str = "") -> Any:
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                new_key = str(self.fake_id(k)) if str(k).isdigit() and len(str(k)) > 4 else k
                out[new_key] = self.value(str(k), v, f"{path}.{k}")
            return out
        if isinstance(node, list):
            return [self.walk(v, f"{path}[{i}]", key) for i, v in enumerate(node)]
        if isinstance(node, str):
            return "redacted@example.invalid" if "@" in node else self.shift_date_str(node)
        if isinstance(node, bool) or node is None:
            return node
        if isinstance(node, int | float):
            if 1_000_000_000_000 <= node < 3_000_000_000_000:  # epoch milliseconds
                return int(node + self.delta.days * 86_400_000)
            if 1_000_000_000 <= node < 3_000_000_000 and "time" in key:  # epoch seconds
                return int(node + self.delta.days * 86_400)
            if any(part in key for part in _KEEP_NUMBER_KEYS_CONTAINING):
                return node
            jittered = _jitter(path, float(node))
            return round(jittered) if isinstance(node, int) else round(jittered, 2)
        return node


def anonymize(payload: Any, recorded_day: date) -> Any:
    return _Anonymizer(recorded_day).walk(payload)


def record_day(api: GarminApi, throttle: Throttle, day: date, out_dir: Path) -> list[Path]:
    """Fetch one day (+ activities of that week) and write raw JSON files."""
    target = out_dir / day.isoformat()
    target.mkdir(parents=True, exist_ok=True)
    written = []
    payloads: dict[str, Any] = {}
    for endpoint in DAILY_ENDPOINTS:
        throttle.wait()
        payloads[endpoint] = api.daily_endpoint(endpoint, day.isoformat())
    throttle.wait()
    payloads["activities"] = api.activities((day - timedelta(days=6)).isoformat(), day.isoformat())
    for endpoint, payload in payloads.items():
        path = target / f"{endpoint}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
        written.append(path)
    return written


def anonymize_dir(raw_day_dir: Path, out_dir: Path) -> list[Path]:
    recorded_day = date.fromisoformat(raw_day_dir.name)
    anon = _Anonymizer(recorded_day)  # one instance: ids stay consistent across files
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for src in sorted(raw_day_dir.glob("*.json")):
        payload = json.loads(src.read_text())
        dst = out_dir / src.name
        dst.write_text(json.dumps(anon.walk(payload), indent=2, ensure_ascii=False) + "\n")
        written.append(dst)
    return written
