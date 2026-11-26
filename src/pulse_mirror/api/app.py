"""FastAPI application: /api/v1 + OpenAPI docs + the static dashboard."""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable
from importlib import resources
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from pulse_mirror import __version__
from pulse_mirror.api import auth
from pulse_mirror.api.v1 import router as v1
from pulse_mirror.config import Settings
from pulse_mirror.db import connect, migrate
from pulse_mirror.sources.garmin.client import GarminApi
from pulse_mirror.sources.garmin.throttle import Throttle

log = logging.getLogger(__name__)

WEB_DIR = Path(str(resources.files("pulse_mirror") / "web"))


def create_app(
    settings: Settings,
    *,
    garmin_factory: Callable[[], GarminApi] | None = None,
    throttle_factory: Callable[[], Throttle] | None = None,
    demo: bool = False,
    auth_disabled: bool = False,
) -> FastAPI:
    """Build the app. ``garmin_factory=None`` disables syncing (demo mode, tests)."""
    conn = connect(settings.db_path)
    migrate(conn)
    conn.close()

    app = FastAPI(
        title="pulse-mirror",
        version=__version__,
        description="Local mirror of your Garmin data, plus your own events and ratings.",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    stored = auth.load_auth(settings.data_dir) or {}
    app.state.settings = settings
    app.state.demo = demo
    app.state.auth_disabled = auth_disabled
    app.state.garmin_factory = garmin_factory
    app.state.throttle_factory = throttle_factory or (lambda: Throttle(settings.garmin_delay_s))
    app.state.password_hash = stored.get("password_hash")
    # Without auth.json nobody can log in; a random key still keeps cookies unforgeable.
    app.state.secret_key = stored.get("secret_key") or secrets.token_hex(32)
    if not app.state.password_hash and not auth_disabled:
        log.warning("No dashboard password set. Run `pulse-mirror auth set-password`.")

    app.include_router(v1)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "version": __version__})

    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
