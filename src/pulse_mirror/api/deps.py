"""FastAPI dependencies: settings, per-request DB connection, authentication."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from pulse_mirror.api import auth
from pulse_mirror.config import Settings
from pulse_mirror.db import connect


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[sqlite3.Connection]:
    conn = connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


Db = Annotated[sqlite3.Connection, Depends(get_db)]


def get_principal(request: Request, db: Db) -> auth.Principal:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token_id = auth.check_token(db, header[7:].strip())
        if token_id is not None:
            return auth.Principal("token", token_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or revoked API token.")
    cookie = request.cookies.get(auth.SESSION_COOKIE)
    secret = request.app.state.secret_key
    if cookie and secret and auth.check_session(secret, cookie):
        return auth.Principal("session")
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in.")


Principal = Annotated[auth.Principal, Depends(get_principal)]


def require_session(principal: Principal) -> auth.Principal:
    """For dashboard-only endpoints (e.g. managing tokens): devices can't do this."""
    if principal.kind != "session":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only available from the dashboard.")
    return principal


SessionPrincipal = Annotated[auth.Principal, Depends(require_session)]
