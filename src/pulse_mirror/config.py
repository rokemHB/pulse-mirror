"""Runtime configuration from environment variables (see .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    timezone: ZoneInfo
    garmin_delay_s: float
    sync_time: str
    sync_days: int
    port: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "pulse.db"

    @property
    def garmin_tokenstore(self) -> Path:
        return self.data_dir / "garmin_tokens"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(_env("PULSE_DATA_DIR", "./data")).expanduser(),
            timezone=ZoneInfo(_env("TZ", "Europe/Berlin")),
            garmin_delay_s=float(_env("PULSE_GARMIN_DELAY_S", "3.0")),
            sync_time=_env("PULSE_SYNC_TIME", "08:00"),
            sync_days=int(_env("PULSE_SYNC_DAYS", "3")),
            port=int(_env("PULSE_PORT", "8420")),
        )
