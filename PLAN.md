# pulse-mirror – Plan

> A local mirror of your Garmin data, visualized your way – self-hosted on a Synology NAS.

Status: **phase 0 approved; phases 1–2 implemented** (see section 10).

---

## 1. Goals and non-goals

**Goals**
- Keep a local, complete copy of my own Garmin data (including the full history) on my NAS.
- Show it my way: baselines, long-term trends, and my own annotations (events) and wellbeing ratings overlaid on the Garmin data.
- Source-agnostic data model: Garmin is one source among several (`garmin`, `manual`, `app`, `demo`, …).
- A stable, versioned JSON API that the dashboard uses and a later Android app can use too.
- A public repo with a public demo (synthetic data), and no personal data in it, ever.

**Non-goals (for now)**
- The Android app (we only make the API ready for it).
- Access from outside the home network.
- Writing anything back to Garmin.
- Multiple users. It's a single-user system; the auth exists to protect the data, not to separate tenants.

---

## 2. Garmin access: current state and risks

Researched 2026-11-25.

**Library:** [`garminconnect`](https://github.com/cyberjunky/python-garminconnect) (MIT), latest **0.3.20** (2026-10-23), Python ≥ 3.12. It depends on `curl_cffi`, `requests` and `ua-generator`. About 3.3k stars, actively maintained, with 0 open bug issues at the time of writing and 3 open feature PRs.

**Risks**

| # | Risk | Evidence | Mitigation |
|---|------|----------|------------|
| R1 | **The login is unofficial and breaks when Garmin changes things.** | In March 2026 Garmin added Cloudflare TLS fingerprinting and changed SSO. `garth` was deprecated and the libraries were broken for days. The library now uses `curl_cffi` and tries several login strategies (mobile → widget → portal). | Pin the exact version. All Garmin access goes through our adapter. Show "last successful sync" prominently, so a breakage is visible within a day. |
| R2 | **Very fast release cadence, with breaking changes in patch releases.** | 0.3.8 → 0.3.20 between July and October 2026 (roughly weekly). 0.3.17 changed `get_goals()` semantics. 0.3.19 changed how "today" and naive timestamps are interpreted (they now follow the Garmin profile timezone) and restructured the modules. | Pin the exact version with `uv.lock`. Dependabot PRs are merged only when the fixture tests pass **and** the release notes have been read. Always pass explicit dates and never rely on the library's idea of "today". |
| R3 | **Rate limiting and account lockouts on login.** | From April to October 2026 the mobile login answered 429 on every attempt (fixed in 0.3.20). The Garmin forum reports login blocks of 48 h and more after repeated attempts. | Log in **once** interactively and then reuse the token store. **Never retry a login automatically.** On auth errors we stop and report, and you run `login` again. Data requests are throttled (a fixed delay between calls, exponential backoff on 429, abort after N failures). |
| R4 | **Upgrades can invalidate stored tokens.** | #439: the move from garth to the native auth silently broke token resume. | Detect "token invalid" separately and report it as "re-login required" rather than as a generic error. |
| R5 | **`curl_cffi` is required in practice.** | #444: plain `requests` gets a 403 from Cloudflare even with valid tokens. | `curl_cffi` is an explicit, pinned dependency. Check for it in a smoke test. |
| R6 | **There is now an official Garmin "Personal" API, which may change the picture.** | Since October 2026 the [Garmin Connect Developer Program](https://developer.garmin.com/gc-developer-program/overview/) has a *Personal* tier: an API key from the Connect settings, **pull only**, with "Activity, Health and Women's Health data". The upstream maintainer says its scope is "very limited" (the newer endpoints aren't there; #476). Its rate limits and history depth are undocumented on the public page. Some users worry Garmin may crack down on unofficial clients now that an official path exists. | Phase 1 stays on `garminconnect`, as agreed. The adapter boundary means an official-API source can be added later as a second source (`garmin_api`) or as a replacement, without touching the rest of the app. **Decision D1** (section 11). |
| R7 | **The backfill volume is large.** | Roughly 6–8 endpoints per day × several years ≈ 10–20k requests. | A slow, resumable, idempotent backfill spread over one or more nights, with the ZIP export as a fallback for deep history (see section 5). |

---

## 3. Architecture

```
Laptop (Claude Code) ──push──▶ GitHub (public: code only)
                                 │  GitHub Actions: tests, gitleaks, image build → GHCR
                                 ▼
                      Synology NAS (Docker / Container Manager)
                      └─ pulse-mirror container (one image)
                         ├─ FastAPI app: /api/v1/* + static dashboard
                         ├─ scheduler (in-process): daily sync 08:00, weekly report Sun
                         ├─ CLI: login, sync, backfill, import-export, demo, …
                         └─ /data volume: pulse.db (SQLite), garmin tokens, raw/, reports/
```

**One container instead of separate collector and API containers.** SQLite works best with a single writer process. One process makes the refresh lock, the cooldown and the scheduler simple (no IPC, no cross-container locking). The CLI commands (`login`, `backfill`) run inside the same container via `docker exec`. If this ever becomes a problem, the image can be started twice with different commands; the code already keeps the collector and the API in separate modules.

---

## 4. Tech choices

| Area | Choice | Why |
|------|--------|-----|
| Language | Python 3.13 (≥ 3.12 supported) | Required by the library. |
| Packaging | `uv`, `pyproject.toml`, `uv.lock` | Exact pins, fast, reproducible Docker builds. |
| Garmin | `garminconnect==0.3.20` + `curl_cffi` (exact pins) | As agreed, behind the adapter layer. |
| DB | SQLite (stdlib `sqlite3`, WAL mode) | No server, a single file on the volume. |
| Migrations | Numbered plain `.sql` files plus a ~50-line runner (`PRAGMA user_version`) | Simple and transparent. No ORM or Alembic needed for this size. |
| API | FastAPI + Pydantic v2 + Uvicorn | Versioned routes, automatic OpenAPI docs. |
| Scheduler | APScheduler (in-process, cron triggers, configurable times) | Small and reliable. The alternative (Synology Task Scheduler + `docker exec`) splits the logic. |
| Dashboard | Static HTML + vanilla JS modules + **Apache ECharts** (one vendored file), no build step | Zoom and range selection, shaded bands (`markArea`) and markers (`markPoint`) for events, works on mobile. With no Node toolchain it also deploys trivially to GitHub Pages. |
| CLI | `argparse` (stdlib) | No extra dependency. |
| Tests | `pytest`, fixtures as anonymized JSON | No network access in tests. |
| Lint and format | `ruff` (lint + format), `mypy` on `sources/` and `api/` | |
| Secrets scan | `gitleaks` as a pre-commit hook (via the `pre-commit` framework) and in CI | |
| Deps updates | Dependabot (`uv` + GitHub Actions + Docker ecosystems) | Built into GitHub, no extra app. |
| PDF (phase 5) | To be decided in phase 5: probably WeasyPrint (HTML → PDF, reusing the dashboard templates) or matplotlib + a simple PDF. | We decide when we get there, based on image size on the NAS. |
| Notifications (phase 5) | `Notifier` interface with pluggable backends (Telegram, SMTP email) | Decision D3: email. |

---

## 5. Garmin adapter (`sources/garmin/`)

```
sources/garmin/
  client.py      # the ONLY module that imports garminconnect
  auth.py        # interactive login, token store at $DATA_DIR/garmin_tokens/
  mapping.py     # raw responses → our model (pure functions, fully tested)
  fetch.py       # "fetch day D" = the set of calls for one day, throttled
  backfill.py    # walk backwards day by day, resumable
  export_zip.py  # import Garmin's full account data export (fallback)
```

- **Public surface (used by the rest of the app):** `login_interactive()`, `fetch_day(date) -> DayBundle`, `fetch_activities(since) -> list[Activity]`, `check_auth() -> AuthStatus`. Nothing else.
- **Raw-first storage:** every raw response is stored in `raw_responses` (in the DB, on the NAS only). Mapping runs on the stored raw data, so if we improve the mapping later we can **re-parse the history without fetching it again**. This matters a lot for a multi-night backfill.
- **Throttling:** at least 3 s between calls (configurable), jitter, backoff on 429/5xx, and a stop after N consecutive errors with a clear error status.
- **Backfill:** `pulse-mirror backfill garmin [--from YYYY-MM-DD] [--until-empty]`
  - Walks backwards from yesterday, one day at a time. The state lives in `backfill_state` (oldest fully fetched date and its status).
  - Idempotent: a day is written in one transaction with upserts, so re-running a day is harmless.
  - Resumable: it continues from the cursor after a crash or Ctrl-C.
  - Stop condition: N consecutive days with no data at all (before you owned the watch), or `--from`.
  - Runs well in the background via `docker exec -d` and logs its progress.
- **ZIP import:** `pulse-mirror import garmin-export <zip>` parses the daily summaries, sleep and activities from the official export into the same tables (`source='garmin'`, with a marker for the import origin). We only build this if the API backfill turns out not to be enough.
- **Fixtures:** recorded with a dev command (`pulse-mirror dev record-fixtures --date …`) into `tests/fixtures/garmin/_raw/` (git-ignored). Then `anonymize` replaces names, IDs, GPS coordinates, device serials and similar → `tests/fixtures/garmin/*.json` (committed only after a manual review).

**Metrics (daily level):** resting HR, HRV (overnight average, 5-min high, weekly average, status, baseline range), sleep (duration, deep/light/REM/awake, score), Body Battery (charged, drained, max, min), stress (average, max, rest/low/medium/high durations), VO2max (cycling/running), training load (acute, chronic, status), steps (optional).
**Activities:** type, start, duration, distance, elevation gain, average/max HR, average/normalized power, calories, training effect, training load.
Intraday series (Body Battery curve, HR curve) are **kept as raw data only** for now and can be mapped later.

---

## 6. Data model

All timestamps are ISO 8601 UTC (`…Z`). Daily values are keyed by the **local calendar date** (`YYYY-MM-DD`) as Garmin reports it. Every row carries `source`.

```sql
-- metadata ---------------------------------------------------------------
sources(key TEXT PK, name TEXT)                      -- garmin, manual, app, demo

metric_types(key TEXT PK, name, unit, kind,          -- kind: number | text
             description, sort_order)
-- e.g. resting_hr (bpm), hrv_overnight_avg (ms), hrv_status (text),
--      sleep_duration (s), sleep_score, body_battery_max, stress_avg,
--      vo2max_cycling, training_load_acute, steps …

-- measurements ------------------------------------------------------------
daily_metrics(date TEXT, metric_key → metric_types, source → sources,
              value_num REAL, value_text TEXT,
              updated_at TEXT,
              PRIMARY KEY (date, metric_key, source))

activities(id INTEGER PK, source, source_id TEXT,     -- UNIQUE(source, source_id)
           start_time, local_date, type, name,
           duration_s, distance_m, elevation_gain_m,
           avg_hr, max_hr, avg_power, norm_power, calories,
           training_load, aerobic_te, anaerobic_te, updated_at)

raw_responses(id PK, source, endpoint, key TEXT,      -- key: date or activity id
              fetched_at, payload_json,               -- UNIQUE(source, endpoint, key)
              mapper_version INTEGER)

-- sync bookkeeping ----------------------------------------------------------
sync_runs(id PK, source, kind,                        -- kind: scheduled | manual | backfill
          started_at, finished_at, status,            -- running | ok | error | auth_required
          days_fetched, error_message)
backfill_state(source PK, cursor_date, oldest_date, status, updated_at)

-- events / annotations -------------------------------------------------------
event_categories(id PK, key UNIQUE, name, color, icon, sort_order, archived_at)
events(id TEXT PK /*uuid*/, category_id, title, note,
       start_at, end_at NULL,                         -- end_at NULL = single point
       source, created_at, updated_at, deleted_at)

-- wellbeing scales & ratings ----------------------------------------------------
scales(id PK, key UNIQUE, created_at, archived_at)
scale_versions(id PK, scale_id, version INTEGER,      -- UNIQUE(scale_id, version)
               name, description, min_value, max_value, step,
               min_label, max_label, color,
               created_at)
ratings(id TEXT PK /*client UUID*/, scale_version_id,
        value REAL, note, recorded_at,
        source, device_id NULL → api_tokens,
        created_at, updated_at, deleted_at)

-- auth --------------------------------------------------------------------------
api_tokens(id PK, name, token_hash, prefix, created_at, last_used_at, revoked_at)
```

**Scale versioning rule:** cosmetic fields (name, description, color, labels) can be edited in place. Changing the **meaning** (`min_value`, `max_value`, `step`) creates a new `scale_version`, and new ratings reference it. Old ratings keep pointing at their version, so they stay interpretable (and can be normalized to 0–1 for charts). Archiving hides a scale from input but keeps its history.

**Ratings sync:** `id` is a client-generated UUID. `PUT /api/v1/ratings/{id}` is an idempotent upsert, so an offline app can retry uploads safely. Soft delete (`deleted_at`) lets clients sync deletions too.

**Default event categories (seeded and editable):** trip/vacation, illness, alcohol, poor sleep, stress, injury, vaccination, other.

---

## 7. API (`/api/v1`)

```
GET    /status                          version, last successful sync per source, auth state
POST   /sync                            trigger a refresh (lock + 10-min cooldown) → 202 / 409 / 429
GET    /sync/runs?limit=

GET    /metrics                         metric_types catalogue
GET    /metrics/daily?keys=a,b&from=&to=&source=
GET    /metrics/baselines?date=         7d / 28d baselines for the Today view
GET    /activities?from=&to=&type=
GET    /activities/{id}

GET|POST           /event-categories     PATCH|DELETE /event-categories/{id}
GET|POST           /events?from=&to=&category=
GET|PATCH|DELETE   /events/{id}

GET|POST           /scales               PATCH /scales/{id} (may create a new version)
POST               /scales/{id}/archive
GET                /ratings?from=&to=&scale=
PUT|PATCH|DELETE   /ratings/{id}         (PUT = idempotent create with client UUID)

GET|POST           /tokens               DELETE /tokens/{id}  (dashboard session only)
GET                /export/csv?what=all|metrics|events|ratings|activities
```

- **Auth:** the browser uses a login form → a signed session cookie (password hash from `.env`). Devices use `Authorization: Bearer <token>`. Tokens are shown once and stored as hashes.
- **Stability:** `/api/v1` changes are additive only. Breaking changes go to `/api/v2`.
- OpenAPI docs are at `/api/docs`.

---

## 8. Dashboard

- **Header:** a prominent sync status ("updated 12 min ago" / a red "last sync failed 2 days ago: re-login required"), a refresh button (disabled during the cooldown) and a quick-add "+" for events and ratings.
- **Views:** Today (values against 7d/28d baselines), Trends (4 weeks), Long-term (months and years, weekly or monthly aggregates), Activities, Wellbeing (ratings, overlayable with a Garmin metric; the overlay is indexed to 0–100 % of each series' own range with raw values in the tooltip, because a chart never gets a second y-axis).
- **Events in every chart:** spans as shaded bands, points as marker lines, with details on hover or tap and tap-to-edit. Quick-add defaults to "now" or "last night" (22:00–07:00).
- **Rating form:** one slider per active scale and an optional note. It writes through `PUT /ratings/{uuid}`, the same as the app will.
- **Demo mode:** the same frontend with a `static` data adapter that reads pre-generated JSON snapshots (produced by the real endpoint functions) and keeps edits in localStorage. `pulse-mirror demo serve` runs the real server on throwaway synthetic data. `pulse-mirror demo build-pages` generates synthetic data (two-plus years with a realistic resting HR/HRV/sleep relationship, seasonality, training blocks, a few made-up events like "Alpine cycling trip" or "Flu", and ratings), then writes `site/`, which a GitHub Action publishes to GitHub Pages.

---

## 9. Repo layout

```
pulse_mirror/
├─ CLAUDE.md  PLAN.md  README.md  LICENSE
├─ pyproject.toml  uv.lock  .python-version
├─ .env.example  .gitignore  .gitleaks.toml  .pre-commit-config.yaml
├─ Dockerfile  compose.yaml
├─ src/pulse_mirror/
│  ├─ cli.py  config.py  logging.py
│  ├─ db/            connection.py  migrate.py  migrations/0001_init.sql …
│  ├─ model/         dataclasses / pydantic schemas shared by api + sources
│  ├─ sources/garmin/   (see section 5)
│  ├─ sync/          service.py (lock, cooldown, run bookkeeping)  scheduler.py
│  ├─ api/           app.py  auth.py  v1/{status,metrics,events,scales,ratings,tokens,export}.py
│  ├─ web/           index.html  app.js  charts.js  api-client.js  vendor/echarts.min.js
│  ├─ demo/          generator.py  build_pages.py
│  ├─ reports/       (phase 5) pdf.py
│  └─ notify/        (phase 5) base.py telegram.py email.py
├─ tests/  fixtures/garmin/*.json (anonymized only)
├─ docs/   nas-setup.md  garmin-login.md  backup.md
└─ .github/ workflows/{ci.yml,image.yml,pages.yml}  dependabot.yml
```

---

## 10. Phases (with the concrete deliverables)

0. **Plan** (this document + `CLAUDE.md`). ✅
   - *Status 2026-11-25:* phases 1 and 2 are implemented. Phase 1 is still waiting for validation against real, recorded Garmin responses (needs the first interactive login).
1. **Adapter + DB + backfill, local on the laptop:** project skeleton, migrations, the Garmin adapter, recording and anonymizing fixtures, mapping tests, `login` / `sync` / `backfill` CLI, a first real backfill test over a few days. *(Touches your Garmin account: I'll ask before the first login.)*
2. **API + dashboard + events + scales/ratings + demo:** all of section 7 and 8, the demo generator, GitHub Pages.
3. **Docker + NAS:** Dockerfile, `compose.yaml`, the data volume, the scheduler, login on the NAS, starting the full backfill. Guide: `docs/nas-setup.md`.
4. **Automation:** CI (ruff, mypy, pytest, gitleaks), the multi-arch image build to GHCR, Dependabot, auto-update (Watchtower, or a documented `docker compose pull && up -d`).
5. **Exports + notifications:** CSV export, the PDF button, the Sunday PDF into a Synology Drive folder, the morning summary after the 08:00 sync.

Small, testable steps, with one commit per finished step on feature branches (`main` stays releasable).

---

## 11. Decisions (phase 0)

| # | Topic | Decision |
|---|-------|----------|
| D1 | Garmin access | `garminconnect` as agreed. The official Personal API isn't available in the account settings yet. Re-evaluate it later as an optional second source behind the adapter boundary. |
| D2 | Image architectures | Multi-arch `linux/amd64` + `linux/arm64`, so other people's NAS models work too. |
| D3 | Morning notification | **Email (SMTP)** as the first backend, behind the pluggable `Notifier` interface. SMTP credentials go only in `.env`. |
| D4 | Commit identity | GitHub `noreply` address (set as repo-local git config). |
| D5 | Repo | `pulse-mirror` on GitHub, public, MIT license. |
| D6 | Port | Default **`8420`** (an unassigned high port that avoids DSM's 5000/5001 and the commonly used 8080), configurable via `PULSE_PORT`. LAN only. The host name goes only in the local `.env` and is never in the repo. |
| D7 | Locale | Timezone `Europe/Berlin` by default (configurable via `TZ`), metric units. |
| D8 | Backfill depth | Under 2 years of history: backfill everything (≈ 700 days ≈ 5k requests, one or two nights at the default throttle). |
