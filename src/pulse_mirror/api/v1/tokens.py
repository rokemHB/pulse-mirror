"""/api/v1: per-device API tokens (dashboard session only)."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException

from pulse_mirror.api import auth
from pulse_mirror.api import schemas as s
from pulse_mirror.api.deps import Db, SessionPrincipal
from pulse_mirror.db.connection import utcnow

router = APIRouter(tags=["auth"])


def _token(r: sqlite3.Row) -> s.Token:
    return s.Token(
        id=r["id"],
        name=r["name"],
        prefix=r["prefix"],
        created_at=r["created_at"],
        last_used_at=r["last_used_at"],
        revoked=r["revoked_at"] is not None,
    )


@router.get("/tokens", response_model=list[s.Token])
def list_tokens(db: Db, _: SessionPrincipal) -> list[s.Token]:
    return [_token(r) for r in db.execute("SELECT * FROM api_tokens ORDER BY id DESC")]


@router.post("/tokens", response_model=s.TokenCreated, status_code=201)
def create_token(body: s.TokenIn, db: Db, _: SessionPrincipal) -> s.TokenCreated:
    token_id, token = auth.create_token(db, body.name)
    row = db.execute("SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone()
    return s.TokenCreated(**_token(row).model_dump(), token=token)


@router.delete("/tokens/{token_id}", status_code=204)
def revoke_token(token_id: int, db: Db, _: SessionPrincipal) -> None:
    cur = db.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
        (utcnow(), token_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "Token not found or already revoked.")
