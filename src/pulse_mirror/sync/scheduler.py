"""In-process scheduler: the daily Garmin sync (and, later, weekly jobs).

Deliberately tiny (one daemon thread, no dependency). Catches up once after a
restart if today's run was missed, and never runs more than once per day.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta

from pulse_mirror.config import Settings
from pulse_mirror.db import connect
from pulse_mirror.sources.garmin.client import GarminApi
from pulse_mirror.sources.garmin.throttle import Throttle
from pulse_mirror.sync import service as sync

log = logging.getLogger(__name__)

AfterSyncHook = Callable[[sync.SyncResult], None]


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def next_run(now: datetime, at: time) -> datetime:
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def missed_today(now: datetime, at: time, last_scheduled_start: datetime | None) -> bool:
    """True if today's slot has passed and no scheduled run started since then."""
    slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if now < slot:
        return False
    return last_scheduled_start is None or last_scheduled_start < slot


@dataclass
class DailySync:
    settings: Settings
    garmin_factory: Callable[[], GarminApi]
    after_sync: list[AfterSyncHook] = field(default_factory=list)
    _stop: threading.Event = field(default_factory=threading.Event)

    def _last_scheduled_start(self) -> datetime | None:
        conn = connect(self.settings.db_path)
        try:
            row = conn.execute(
                "SELECT MAX(started_at) FROM sync_runs WHERE kind = 'scheduled'"
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)

    def run_once(self) -> sync.SyncResult | None:
        conn = connect(self.settings.db_path)
        try:
            result = sync.sync_recent(
                conn,
                self.garmin_factory,
                Throttle(self.settings.garmin_delay_s),
                datetime.now(self.settings.timezone).date(),
                self.settings.sync_days,
                kind="scheduled",
            )
        except sync.SyncRefusedError as e:
            log.info("scheduled sync skipped: %s", e)
            return None
        finally:
            conn.close()
        log.info("scheduled sync: %s", result.message)
        for hook in self.after_sync:
            try:
                hook(result)
            except Exception:
                log.exception("after-sync hook failed")
        return result

    def _loop(self) -> None:
        tz = self.settings.timezone
        at = parse_hhmm(self.settings.sync_time)
        last = self._last_scheduled_start()
        if missed_today(datetime.now(tz), at, last.astimezone(tz) if last else None):
            log.info("catching up on today's missed sync")
            self.run_once()
        while not self._stop.is_set():
            wake = next_run(datetime.now(tz), at)
            log.info("next scheduled sync at %s", wake.isoformat())
            # wake up at least hourly so clock changes (DST, NTP) can't drift us far
            while not self._stop.is_set():
                remaining = (wake - datetime.now(tz)).total_seconds()
                if remaining <= 0:
                    break
                self._stop.wait(min(remaining, 3600))
            if not self._stop.is_set():
                self.run_once()

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._loop, name="daily-sync", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
