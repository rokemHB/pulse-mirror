"""In-process scheduler: daily Garmin sync (+ morning email) and the weekly PDF report.

Deliberately tiny (one daemon thread, no dependency). It ticks every minute and
asks each job whether it is due, based on persistent state (the sync run log,
the report files), so restarts catch up exactly once and nothing runs twice.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from pulse_mirror.config import Settings
from pulse_mirror.db import connect
from pulse_mirror.sources.garmin.client import GarminApi
from pulse_mirror.sources.garmin.throttle import Throttle
from pulse_mirror.sync import service as sync

log = logging.getLogger(__name__)

AfterSyncHook = Callable[[sync.SyncResult], None]
TICK_S = 60


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def next_run(now: datetime, at: time) -> datetime:
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=1)


def missed_today(now: datetime, at: time, last_start: datetime | None) -> bool:
    """True if today's slot has passed and no run started since then."""
    slot = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if now < slot:
        return False
    return last_start is None or last_start < slot


class Job(Protocol):
    name: str

    def due(self, now: datetime) -> bool: ...

    def run(self) -> object: ...


@dataclass
class DailySync:
    settings: Settings
    garmin_factory: Callable[[], GarminApi]
    after_sync: list[AfterSyncHook] = field(default_factory=list)
    name: str = "daily-sync"

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

    def due(self, now: datetime) -> bool:
        last = self._last_scheduled_start()
        return missed_today(now, parse_hhmm(self.settings.sync_time), last)

    def run(self) -> sync.SyncResult | None:
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

    # kept for the CLI and tests
    run_once = run


@dataclass
class WeeklyReport:
    """Writes last week's PDF on Sundays after ``report_time`` (for the week ending today)."""

    settings: Settings
    name: str = "weekly-report"

    def week_monday(self, now: datetime) -> date:
        return now.date() - timedelta(days=now.weekday())

    def due(self, now: datetime) -> bool:
        from pulse_mirror.reports.pdf import report_filename

        if now.weekday() != 6 or now.time() < parse_hhmm(self.settings.report_time):
            return False
        return not (self.settings.report_dir / report_filename(self.week_monday(now))).exists()

    def run(self) -> object:
        from pulse_mirror.reports.pdf import write_weekly_report

        conn = connect(self.settings.db_path)
        try:
            path = write_weekly_report(
                conn,
                self.week_monday(datetime.now(self.settings.timezone)),
                self.settings.report_dir,
            )
        finally:
            conn.close()
        log.info("weekly report written to %s", path)
        return path


@dataclass
class Scheduler:
    settings: Settings
    jobs: list[Job]
    _stop: threading.Event = field(default_factory=threading.Event)

    def tick(self, now: datetime | None = None) -> list[str]:
        now = now or datetime.now(self.settings.timezone)
        ran = []
        for job in self.jobs:
            try:
                if job.due(now):
                    log.info("running %s", job.name)
                    job.run()
                    ran.append(job.name)
            except Exception:
                log.exception("job %s failed", job.name)
        return ran

    def _loop(self) -> None:
        log.info("scheduler started: %s", ", ".join(j.name for j in self.jobs))
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(TICK_S)

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self._stop.set()
