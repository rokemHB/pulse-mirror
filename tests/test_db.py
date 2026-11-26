from __future__ import annotations

import sqlite3

import pytest

from pulse_mirror.db import connect, migrate
from pulse_mirror.db.migrate import available_migrations, current_version


def test_migrate_fresh_and_idempotent(tmp_path):
    conn = connect(tmp_path / "x.db")
    applied = migrate(conn)
    assert applied[0] == "0001_init.sql"
    assert current_version(conn) == len(available_migrations())
    assert migrate(conn) == []


def test_schema_seeds(conn):
    sources = {r[0] for r in conn.execute("SELECT key FROM sources")}
    assert {"garmin", "manual", "app", "demo"} <= sources
    assert conn.execute("SELECT COUNT(*) FROM metric_types").fetchone()[0] >= 25
    cats = {r[0] for r in conn.execute("SELECT key FROM event_categories")}
    assert {"trip", "illness", "alcohol", "other"} <= cats


def test_daily_metric_requires_a_value(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO daily_metrics VALUES "
            "('2025-01-01', 'resting_hr', 'garmin', NULL, NULL, 'x')"
        )


def test_unknown_source_rejected(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO daily_metrics VALUES ('2025-01-01', 'resting_hr', 'nope', 1, NULL, 'x')"
        )


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    import importlib

    m = importlib.import_module("pulse_mirror.db.migrate")

    conn = connect(tmp_path / "y.db")
    monkeypatch.setattr(
        m, "available_migrations", lambda: [(1, "0001_bad.sql", "CREATE TABLE a(x); BROKEN SQL;")]
    )
    with pytest.raises(sqlite3.Error):
        m.migrate(conn)
    assert current_version(conn) == 0
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='a'").fetchone() is None
