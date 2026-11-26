"""Minimal forward-only migration runner.

Migrations are ``migrations/NNNN_description.sql`` files applied in order. The
schema version is tracked in ``PRAGMA user_version``. Never edit a migration
that has been applied anywhere; add a new one instead.
"""

from __future__ import annotations

import re
import sqlite3
from importlib import resources

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


def available_migrations() -> list[tuple[int, str, str]]:
    """Return ``(version, filename, sql)`` sorted by version."""
    found = []
    for entry in resources.files("pulse_mirror.db.migrations").iterdir():
        match = _NAME.match(entry.name)
        if match:
            found.append((int(match.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort()
    versions = [v for v, _, _ in found]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"Migration numbering has gaps or duplicates: {versions}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Apply all pending migrations. Returns the filenames that were applied."""
    applied = []
    version = current_version(conn)
    for number, name, sql in available_migrations():
        if number <= version:
            continue
        # executescript() commits implicitly, so the version bump is part of
        # the script itself to keep "schema change + version" atomic.
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        applied.append(name)
    return applied
