# CLAUDE.md – pulse-mirror

A self-hosted local mirror of Garmin data plus manual events and wellbeing ratings, with a versioned JSON API and a web dashboard. It runs as a Docker container on a Synology NAS. See `PLAN.md` for the full design.

## Hard rules

1. **Never commit data or secrets.** That means no `.env`, no tokens, no `data/`, no databases, no exports or reports, no un-anonymized Garmin responses. The public repo contains code, docs and synthetic or anonymized fixtures only. If in doubt, don't commit it and ask.
2. **No personal details in public files** (names, emails, locations, hostnames, IPs, device serials, real health values). The demo uses synthetic data only.
3. **Never ask for Garmin credentials in chat** and never write them to a file. The login is interactive in a terminal (`pulse-mirror login garmin`). Tokens live only in `$DATA_DIR/garmin_tokens/`.
4. **Only `src/pulse_mirror/sources/garmin/client.py` may import `garminconnect`.** Everything else goes through the adapter's public functions and our own model types.
5. **Pin dependencies exactly** (`uv.lock`). Merge a `garminconnect` update only when the fixture tests pass and its release notes have been read for breaking changes.
6. **Be gentle with Garmin:** throttle every request (≥ 3 s by default), back off on 429, **never retry a login automatically**, and don't poll. Scheduled sync runs once a day, plus manual refreshes with a 10-minute cooldown.
7. **The API is versioned and stable:** changes to `/api/v1` are additive only. Breaking changes go to a new version. The dashboard uses the same API, with no private side channels.
8. **Schema changes only via a new numbered migration** in `src/pulse_mirror/db/migrations/`. Never edit an applied migration.
9. **Data is source-agnostic:** every stored value has a `source`. Don't hard-code wellbeing scales or event categories; they're data.
10. **Ask first** before anything irreversible or anything that touches the NAS or the Garmin account (logins, backfills, deleting data, pushing to `main`, publishing).

## Architecture in one breath

One image and one container: FastAPI (`/api/v1`, `/api/docs`, static dashboard) + an in-process APScheduler (daily sync, weekly report) + CLI commands (`login`, `sync`, `backfill`, `import`, `demo`) run via `docker exec`. SQLite (WAL) lives in the `/data` volume. Raw Garmin responses are stored first and mapped second, so mappings can be re-run without fetching again.

## Layout

- `src/pulse_mirror/db/`: connection, migration runner, `migrations/NNNN_*.sql`
- `src/pulse_mirror/sources/garmin/`: the adapter (`client`, `auth`, `mapping`, `fetch`, `backfill`, `export_zip`)
- `src/pulse_mirror/sync/`: sync service (lock, cooldown, run log), scheduler
- `src/pulse_mirror/api/`: FastAPI app, auth, `v1/` routers
- `src/pulse_mirror/web/`: static dashboard (vanilla JS modules + vendored ECharts, no build step). `js/api.js` has a live and a static (demo) adapter; keep both in sync with the API.
- `src/pulse_mirror/demo/`: synthetic data generator and GitHub Pages builder
- `tests/fixtures/garmin/`: **anonymized** responses only. `_raw/` is git-ignored.

## Conventions

- Python 3.13, `uv` for everything (`uv run pytest`, `uv run ruff check`, `uv run ruff format`, `uv run mypy`).
- Code, comments, docs and commit messages are in English.
- Timestamps are stored as ISO 8601 UTC. Daily values are keyed by the local date `YYYY-MM-DD`. Pass explicit dates to Garmin and never rely on the library's idea of "today".
- Tests never touch the network. Mapping code consists of pure functions with fixture tests.
- Work on feature branches with small commits. Run before every commit: `ruff check`, `ruff format --check`, `mypy`, `pytest`, `gitleaks` (via pre-commit).
- Keep it simple: add a dependency only when it's clearly worth it.
- Charts: one y-axis per chart (small multiples or indexed overlays instead), fixed color per metric (`METRIC_SLOT`), events in every time chart, a table view for every chart. Escape user text (`esc()`) in any HTML string.
- Check UI changes by rendering them (`pulse-mirror demo serve` + a headless browser), on desktop and phone widths, light and dark.

## Credits

Garmin access uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) (MIT) as a pinned dependency.
