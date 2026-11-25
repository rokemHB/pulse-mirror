# pulse-mirror

> A local mirror of your Garmin data, visualized your way – self-hosted on a Synology NAS.

Garmin keeps the original data. **pulse-mirror** keeps a complete local copy (including your full history) on your own hardware, and shows it the way you want: personal baselines, long-term trends, and your own annotations and wellbeing ratings overlaid on the Garmin values.

**Status:** early development. See [PLAN.md](PLAN.md) for the design and roadmap.

## Features (planned)

- Daily sync from Garmin Connect (resting HR, HRV, sleep, Body Battery, stress, VO2max, training load, activities) plus a full, resumable historical backfill
- A source-agnostic data model: Garmin is one source; manual events and self-rated wellbeing (with data-defined scales) are others
- A versioned JSON API (`/api/v1`) with per-device tokens, ready for a mobile app
- A mobile-friendly dashboard with interactive charts and events shown as bands and markers in every chart
- PDF/CSV exports and a morning summary by email
- Runs as a single Docker container (amd64 + arm64)

## Development

```bash
uv sync
uv run pulse-mirror db migrate          # create/upgrade the local database in ./data
uv run pulse-mirror login garmin        # one-time interactive login (password + MFA typed by you)
uv run pulse-mirror sync                # fetch the last few days
uv run pulse-mirror backfill garmin     # walk back through the whole history (slow, resumable)
uv run pytest
```

Configuration comes from environment variables. See [.env.example](.env.example).

## Credits

Garmin access uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) by cyberjunky and contributors (MIT license), as a pinned dependency. pulse-mirror is not affiliated with or endorsed by Garmin.

## License

[MIT](LICENSE)
