# pulse-mirror

> A local mirror of your Garmin data, visualized your way – self-hosted on a Synology NAS.

Garmin keeps the original data. **pulse-mirror** keeps a complete local copy (including your full history) on your own hardware, and shows it the way you want: personal baselines, long-term trends, and your own annotations and wellbeing ratings overlaid on the Garmin values.

**Status:** early development. See [PLAN.md](PLAN.md) for the design and roadmap.

**Live demo (synthetic data):** https://rokemhb.github.io/pulse-mirror/

## Features (planned)

- Daily sync from Garmin Connect (resting HR, HRV, sleep, Body Battery, stress, VO2max, training load, activities) plus a full, resumable historical backfill
- A source-agnostic data model: Garmin is one source; manual events and self-rated wellbeing (with data-defined scales) are others
- A versioned JSON API (`/api/v1`) with per-device tokens, ready for a mobile app
- A mobile-friendly dashboard with interactive charts and events shown as bands and markers in every chart
- PDF/CSV exports and a morning summary by email
- Runs as a single Docker container (amd64 + arm64)

## Running it

See **[docs/nas-setup.md](docs/nas-setup.md)** for a step-by-step Synology setup. In short: copy `compose.yaml`, create `.env` from `.env.example`, `docker compose up -d`, then run the two interactive logins with `docker exec -it`.

## Development

```bash
uv sync
uv run pulse-mirror db migrate          # create/upgrade the local database in ./data
uv run pulse-mirror login garmin        # one-time interactive login (password + MFA typed by you)
uv run pulse-mirror sync                # fetch the last few days
uv run pulse-mirror backfill garmin     # walk back through the whole history (slow, resumable)
uv run pulse-mirror auth set-password   # dashboard password (stored hashed in ./data/auth.json)
uv run pulse-mirror serve               # API + dashboard on http://localhost:8420 (docs: /api/docs)
uv run pulse-mirror demo serve          # the dashboard on throwaway synthetic data, no login
uv run pytest
```

Configuration comes from environment variables. See [.env.example](.env.example).

## API

Everything the dashboard does goes through the versioned JSON API under `/api/v1` (OpenAPI docs at `/api/docs`). Browsers use a session cookie; other clients (e.g. a future mobile app) use per-device tokens created in *Settings*: `Authorization: Bearer pm_…`. Ratings use client-generated UUIDs with `PUT /api/v1/ratings/{id}`, so offline clients can retry uploads safely.

## Credits

Garmin access uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) by cyberjunky and contributors (MIT license), as a pinned dependency. Charts use [Apache ECharts](https://echarts.apache.org/) (Apache-2.0, vendored in `src/pulse_mirror/web/vendor/`). pulse-mirror is not affiliated with or endorsed by Garmin.

## License

[MIT](LICENSE)
