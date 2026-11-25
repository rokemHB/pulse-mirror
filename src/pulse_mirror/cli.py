"""Command line interface: ``pulse-mirror <command>``."""

from __future__ import annotations

import argparse
import getpass
import logging
import sqlite3
import sys
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pulse_mirror import __version__
from pulse_mirror.config import Settings
from pulse_mirror.db import connect, migrate, repo
from pulse_mirror.sources.garmin.errors import GarminError
from pulse_mirror.sources.garmin.throttle import Throttle

if TYPE_CHECKING:
    from pulse_mirror.sources.garmin.client import GarminApi

log = logging.getLogger("pulse_mirror")

FIXTURE_RAW_DIR = Path("tests/fixtures/garmin/_raw")
FIXTURE_DIR = Path("tests/fixtures/garmin/recorded")


def _today(settings: Settings) -> date:
    return datetime.now(settings.timezone).date()


def _open_db(settings: Settings) -> sqlite3.Connection:
    conn = connect(settings.db_path)
    applied = migrate(conn)
    for name in applied:
        log.info("applied migration %s", name)
    return conn


def _throttle(settings: Settings) -> Throttle:
    return Throttle(settings.garmin_delay_s)


def _garmin_client(settings: Settings) -> GarminApi:
    # Imported lazily so commands that don't talk to Garmin don't load the library.
    from pulse_mirror.sources.garmin.client import GarminClient

    return GarminClient.from_tokenstore(settings.garmin_tokenstore)


# --- commands -------------------------------------------------------------------


def cmd_db_migrate(settings: Settings, args: argparse.Namespace) -> int:
    conn = connect(settings.db_path)
    applied = migrate(conn)
    print(f"Applied: {', '.join(applied)}" if applied else "Database is up to date.")
    return 0


def cmd_db_backup(settings: Settings, args: argparse.Namespace) -> int:
    """Consistent online copy (SQLite backup API), safe while the server runs."""
    import sqlite3

    target_dir = settings.data_dir / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"pulse-{datetime.now(settings.timezone):%Y%m%d-%H%M%S}.db"
    src = connect(settings.db_path)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    dst.close()
    backups = sorted(target_dir.glob("pulse-*.db"))
    for old in backups[: -args.keep]:
        old.unlink()
    print(f"Backup written to {target} (keeping the newest {args.keep}).")
    return 0


def cmd_login_garmin(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sources.garmin.client import login_interactive

    if not sys.stdin.isatty():
        print("The login is interactive: run it in a terminal (docker exec -it ...).")
        return 2
    print("Garmin Connect login. Credentials are used once and never stored;")
    print(f"only the resulting tokens are saved to {settings.garmin_tokenstore}.")
    email = input("Email: ").strip()
    password = getpass.getpass("Password: ")
    name = login_interactive(
        settings.garmin_tokenstore,
        email,
        password,
        prompt_mfa=lambda: input("MFA code: ").strip(),
    )
    del password
    print(f"Logged in{f' as {name}' if name else ''}. Tokens saved.")
    return 0


def cmd_sync(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sync.service import sync_recent

    conn = _open_db(settings)
    result = sync_recent(
        conn,
        lambda: _garmin_client(settings),
        _throttle(settings),
        _today(settings),
        args.days or settings.sync_days,
        kind="cli",
    )
    print(result.message)
    return 0 if result.status == "ok" else 1


def cmd_backfill_garmin(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sources.garmin import backfill

    conn = _open_db(settings)
    if args.restart:
        backfill.reset(conn)
    result = backfill.run_backfill(
        conn,
        _garmin_client(settings),
        _throttle(settings),
        _today(settings),
        stop_before=date.fromisoformat(args.since) if args.since else None,
        max_days=args.max_days,
        empty_stop=args.empty_stop,
        progress=print,
    )
    return 0 if result.status in ("done", "paused") and "429" not in result.message else 1


def cmd_remap(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sources.garmin.service import remap_all

    conn = _open_db(settings)
    print(f"Re-mapped {remap_all(conn)} days from stored raw data.")
    return 0


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    conn = _open_db(settings)
    last_ok = repo.last_sync_run(conn, "garmin", "ok")
    last = repo.last_sync_run(conn, "garmin")
    tokens = settings.garmin_tokenstore
    print(f"pulse-mirror {__version__}  db={settings.db_path}")
    print(f"garmin tokens: {'present' if tokens.exists() and any(tokens.iterdir()) else 'missing'}")
    print(f"last successful sync: {last_ok['finished_at'] if last_ok else 'never'}")
    if last and last["status"] != "ok":
        print(f"last run: {last['kind']} {last['status']}: {last['error_message']}")
    bf = conn.execute("SELECT * FROM backfill_state WHERE source = 'garmin'").fetchone()
    if bf:
        print(
            f"backfill: {bf['status']}, oldest day {bf['cursor_date']} (from {bf['newest_date']})"
        )
    days, first, latest = conn.execute(
        "SELECT COUNT(DISTINCT date), MIN(date), MAX(date) FROM daily_metrics WHERE source='garmin'"
    ).fetchone()
    print(f"daily data: {days} days ({first} .. {latest})")
    n_act = conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    print(f"activities: {n_act}")
    return 0


def cmd_dev_record(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sources.garmin.fixtures import record_day

    day = date.fromisoformat(args.date)
    files = record_day(_garmin_client(settings), _throttle(settings), day, FIXTURE_RAW_DIR)
    print(f"Wrote {len(files)} raw files to {FIXTURE_RAW_DIR / day.isoformat()} (git-ignored).")
    return 0


def cmd_dev_anonymize(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.sources.garmin.fixtures import anonymize_dir

    src = FIXTURE_RAW_DIR / args.date
    files = anonymize_dir(src, FIXTURE_DIR)
    print(f"Wrote {len(files)} anonymized files to {FIXTURE_DIR}. Review them before committing!")
    return 0


def cmd_auth_set_password(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.api.auth import set_password

    first = getpass.getpass("New dashboard password: ")
    if len(first) < 8:
        print("Use at least 8 characters.")
        return 2
    if getpass.getpass("Repeat: ") != first:
        print("Passwords don't match.")
        return 2
    set_password(settings.data_dir, first)
    print(f"Saved (hashed) to {settings.data_dir / 'auth.json'}. Restart the server to apply.")
    return 0


def cmd_serve(settings: Settings, args: argparse.Namespace) -> int:
    import uvicorn

    from pulse_mirror.api.app import create_app
    from pulse_mirror.sync.scheduler import DailySync

    app = create_app(settings, garmin_factory=lambda: _garmin_client(settings))
    if not args.no_scheduler:
        DailySync(settings, lambda: _garmin_client(settings)).start()
    uvicorn.run(app, host=args.host, port=args.port or settings.port, log_level="info")
    return 0


def cmd_demo_serve(settings: Settings, args: argparse.Namespace) -> int:
    import tempfile

    import uvicorn

    from pulse_mirror.api.app import create_app
    from pulse_mirror.demo.generator import generate

    with tempfile.TemporaryDirectory() as tmp:
        demo_settings = replace(settings, data_dir=Path(tmp))
        conn = connect(demo_settings.db_path)
        migrate(conn)
        generate(conn, _today(settings), args.days)
        conn.close()
        app = create_app(demo_settings, demo=True, auth_disabled=True)
        print("Demo mode: synthetic data, no login, nothing is saved.")
        uvicorn.run(app, host=args.host, port=args.port or settings.port, log_level="info")
    return 0


def cmd_demo_build_pages(settings: Settings, args: argparse.Namespace) -> int:
    from pulse_mirror.demo.pages import build_pages

    out = build_pages(Path(args.out), _today(settings), args.days)
    print(f"Static demo written to {out}")
    return 0


# --- parser ------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pulse-mirror", description=__doc__)
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    db = sub.add_parser("db", help="database maintenance").add_subparsers(required=True)
    db.add_parser("migrate", help="apply pending migrations").set_defaults(func=cmd_db_migrate)
    b = db.add_parser("backup", help="write a consistent copy to $DATA_DIR/backups/")
    b.add_argument("--keep", type=int, default=14, help="number of backups to keep")
    b.set_defaults(func=cmd_db_backup)

    login = sub.add_parser("login", help="interactive login").add_subparsers(required=True)
    login.add_parser("garmin", help="log in to Garmin Connect").set_defaults(func=cmd_login_garmin)

    s = sub.add_parser("sync", help="fetch the last few days and recent activities")
    s.add_argument("--days", type=int, help="number of days ending today (default PULSE_SYNC_DAYS)")
    s.set_defaults(func=cmd_sync)

    bf = sub.add_parser("backfill", help="load history").add_subparsers(required=True)
    g = bf.add_parser("garmin", help="walk back day by day through the Garmin history")
    g.add_argument("--since", help="stop at this date (YYYY-MM-DD); default: until no more data")
    g.add_argument("--max-days", type=int, help="stop after N days (resume later)")
    g.add_argument(
        "--empty-stop",
        type=int,
        default=60,
        help="consecutive empty days that mark the start of history (default 60)",
    )
    g.add_argument("--restart", action="store_true", help="forget progress and start over")
    g.set_defaults(func=cmd_backfill_garmin)

    srv = sub.add_parser("serve", help="run the API + dashboard")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, help="default PULSE_PORT (8420)")
    srv.add_argument("--no-scheduler", action="store_true", help="don't run the daily sync")
    srv.set_defaults(func=cmd_serve)

    au = sub.add_parser("auth", help="dashboard login").add_subparsers(required=True)
    au.add_parser("set-password", help="set the dashboard password").set_defaults(
        func=cmd_auth_set_password
    )

    demo = sub.add_parser("demo", help="synthetic demo data").add_subparsers(required=True)
    ds = demo.add_parser("serve", help="run the dashboard on throwaway synthetic data")
    ds.add_argument("--host", default="127.0.0.1")
    ds.add_argument("--port", type=int)
    ds.add_argument("--days", type=int, default=900)
    ds.set_defaults(func=cmd_demo_serve)
    dp = demo.add_parser("build-pages", help="build the static GitHub Pages demo")
    dp.add_argument("--out", default="site")
    dp.add_argument("--days", type=int, default=900)
    dp.set_defaults(func=cmd_demo_build_pages)

    sub.add_parser("remap", help="re-run mapping over stored raw data").set_defaults(func=cmd_remap)
    sub.add_parser("status", help="show sync status").set_defaults(func=cmd_status)

    dev = sub.add_parser("dev", help="developer tools").add_subparsers(required=True)
    r = dev.add_parser("record-fixtures", help="record one day of raw responses (git-ignored)")
    r.add_argument("--date", required=True)
    r.set_defaults(func=cmd_dev_record)
    a = dev.add_parser("anonymize", help="anonymize a recorded day into tests/fixtures")
    a.add_argument("--date", required=True)
    a.set_defaults(func=cmd_dev_anonymize)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return int(args.func(Settings.from_env(), args))
    except GarminError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
