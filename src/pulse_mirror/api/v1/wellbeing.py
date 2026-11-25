"""/api/v1: wellbeing scales (data-defined, versioned) and ratings (offline-safe upserts)."""

from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response

from pulse_mirror.api import schemas as s
from pulse_mirror.api.deps import Db, Principal
from pulse_mirror.db import transaction
from pulse_mirror.db.connection import utcnow

router = APIRouter(tags=["wellbeing"])

_LATEST = """
    SELECT sc.id, sc.key, sc.sort_order, sc.archived_at, v.id AS version_id, v.version, v.name,
           v.description, v.min_value, v.max_value, v.step, v.min_label, v.max_label, v.color
    FROM scales sc
    JOIN scale_versions v ON v.scale_id = sc.id
     AND v.version = (SELECT MAX(version) FROM scale_versions WHERE scale_id = sc.id)
"""

_MEANING = ("min_value", "max_value", "step")
_COSMETIC = ("name", "description", "min_label", "max_label", "color")


def _scale(r: sqlite3.Row) -> s.Scale:
    return s.Scale(
        **{k: r[k] for k in s.Scale.model_fields if k != "archived"},
        archived=r["archived_at"] is not None,
    )


def _get_scale(db: sqlite3.Connection, scale_id: int) -> sqlite3.Row:
    row: sqlite3.Row | None = db.execute(_LATEST + " WHERE sc.id = ?", (scale_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Scale not found.")
    return row


def _validate_range(min_value: float, max_value: float, step: float) -> None:
    if max_value <= min_value:
        raise HTTPException(422, "max_value must be greater than min_value.")
    if step <= 0 or step > max_value - min_value:
        raise HTTPException(422, "step must be > 0 and fit into the range.")


def _validate_value(scale: sqlite3.Row, value: float) -> None:
    lo, hi, step = scale["min_value"], scale["max_value"], scale["step"]
    if not lo <= value <= hi:
        raise HTTPException(422, f"value must be between {lo:g} and {hi:g}.")
    steps = (value - lo) / step
    if not math.isclose(steps, round(steps), abs_tol=1e-6):
        raise HTTPException(422, f"value must be a multiple of {step:g} from {lo:g}.")


# --- scales -------------------------------------------------------------------------


@router.get("/scales", response_model=list[s.Scale])
def list_scales(db: Db, _: Principal, include_archived: bool = False) -> list[s.Scale]:
    sql = _LATEST + ("" if include_archived else " WHERE sc.archived_at IS NULL")
    return [_scale(r) for r in db.execute(sql + " ORDER BY sc.sort_order, v.name")]


@router.post("/scales", response_model=s.Scale, status_code=201)
def create_scale(body: s.ScaleIn, db: Db, _: Principal) -> s.Scale:
    _validate_range(body.min_value, body.max_value, body.step)
    now = utcnow()
    with transaction(db):
        try:
            cur = db.execute(
                "INSERT INTO scales (key, sort_order, created_at) VALUES (?, ?, ?)",
                (body.key, body.sort_order, now),
            )
        except sqlite3.IntegrityError as e:
            raise HTTPException(409, f"Scale key '{body.key}' already exists.") from e
        scale_id = cur.lastrowid
        db.execute(
            """INSERT INTO scale_versions (scale_id, version, name, description, min_value,
                   max_value, step, min_label, max_label, color, created_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scale_id,
                body.name,
                body.description,
                body.min_value,
                body.max_value,
                body.step,
                body.min_label,
                body.max_label,
                body.color,
                now,
            ),
        )
    assert scale_id is not None
    return _scale(_get_scale(db, scale_id))


@router.patch("/scales/{scale_id}", response_model=s.Scale)
def update_scale(scale_id: int, body: s.ScalePatch, db: Db, _: Principal) -> s.Scale:
    """Cosmetic changes edit the current version; a new min/max/step creates a new version.

    Old ratings keep pointing at the version they were made with.
    """
    current = _get_scale(db, scale_id)
    changes = body.model_dump(exclude_unset=True)
    meaning = {k: changes[k] for k in _MEANING if k in changes and changes[k] != current[k]}
    cosmetic = {k: changes[k] for k in _COSMETIC if k in changes}
    with transaction(db):
        if meaning:
            merged = {k: current[k] for k in (*_MEANING, *_COSMETIC)} | cosmetic | meaning
            _validate_range(merged["min_value"], merged["max_value"], merged["step"])
            db.execute(
                """INSERT INTO scale_versions (scale_id, version, name, description, min_value,
                       max_value, step, min_label, max_label, color, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scale_id,
                    current["version"] + 1,
                    merged["name"],
                    merged["description"],
                    merged["min_value"],
                    merged["max_value"],
                    merged["step"],
                    merged["min_label"],
                    merged["max_label"],
                    merged["color"],
                    utcnow(),
                ),
            )
        elif cosmetic:
            sets = ", ".join(f"{k} = ?" for k in cosmetic)
            db.execute(
                f"UPDATE scale_versions SET {sets} WHERE id = ?",
                [*cosmetic.values(), current["version_id"]],
            )
        if "sort_order" in changes:
            db.execute(
                "UPDATE scales SET sort_order = ? WHERE id = ?", (changes["sort_order"], scale_id)
            )
        if "archived" in changes:
            db.execute(
                "UPDATE scales SET archived_at = ? WHERE id = ?",
                (utcnow() if changes["archived"] else None, scale_id),
            )
    return _scale(_get_scale(db, scale_id))


@router.get("/scales/{scale_id}/versions", response_model=list[s.ScaleVersion])
def scale_versions(scale_id: int, db: Db, _: Principal) -> list[s.ScaleVersion]:
    _get_scale(db, scale_id)
    rows = db.execute(
        "SELECT * FROM scale_versions WHERE scale_id = ? ORDER BY version", (scale_id,)
    )
    return [s.ScaleVersion(**{k: r[k] for k in s.ScaleVersion.model_fields}) for r in rows]


# --- ratings -------------------------------------------------------------------------

_RATING = """
    SELECT r.*, v.scale_id FROM ratings r JOIN scale_versions v ON v.id = r.scale_version_id
"""


def _rating(r: sqlite3.Row) -> s.Rating:
    return s.Rating(
        **{k: r[k] for k in s.Rating.model_fields if k != "deleted"},
        deleted=r["deleted_at"] is not None,
    )


def _get_rating(db: sqlite3.Connection, rating_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = db.execute(_RATING + " WHERE r.id = ?", (rating_id,)).fetchone()
    return row


@router.get("/ratings", response_model=list[s.Rating])
def list_ratings(
    db: Db,
    _: Principal,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    scale_id: int | None = None,
    updated_since: Annotated[str | None, Query(description="ISO UTC; for client sync")] = None,
    include_deleted: bool = False,
) -> list[s.Rating]:
    sql = _RATING + " WHERE 1=1"
    params: list[Any] = []
    if not include_deleted and updated_since is None:
        sql += " AND r.deleted_at IS NULL"
    if from_:
        sql += " AND r.recorded_at >= ?"
        params.append(from_.isoformat())
    if to:
        sql += " AND r.recorded_at < ?"
        params.append((to + timedelta(days=1)).isoformat())
    if scale_id is not None:
        sql += " AND v.scale_id = ?"
        params.append(scale_id)
    if updated_since:
        sql += " AND r.updated_at > ?"
        params.append(updated_since)
    return [_rating(r) for r in db.execute(sql + " ORDER BY r.recorded_at", params)]


@router.put("/ratings/{rating_id}", response_model=s.Rating)
def put_rating(
    rating_id: str,
    body: s.RatingIn,
    db: Db,
    principal: Principal,
    response: Response,
) -> s.Rating:
    """Idempotent create-or-update with a client-generated UUID (safe to retry offline)."""
    if not 8 <= len(rating_id) <= 64:
        raise HTTPException(422, "rating id must be a client-generated UUID.")
    scale = _get_scale(db, body.scale_id)
    existing = _get_rating(db, rating_id)
    now = utcnow()
    if existing is not None:
        if existing["deleted_at"] is not None:
            raise HTTPException(410, "This rating was deleted.")
        version = db.execute(
            "SELECT * FROM scale_versions WHERE id = ?", (existing["scale_version_id"],)
        ).fetchone()
        if existing["scale_id"] != body.scale_id:
            raise HTTPException(409, "A rating with this id exists for another scale.")
        _validate_value(version, body.value)
        db.execute(
            "UPDATE ratings SET value = ?, note = ?, recorded_at = ?, updated_at = ? WHERE id = ?",
            (body.value, body.note, s.to_db(body.recorded_at), now, rating_id),
        )
    else:
        if scale["archived_at"] is not None:
            raise HTTPException(422, "This scale is archived.")
        _validate_value(scale, body.value)
        db.execute(
            """INSERT INTO ratings (id, scale_version_id, value, note, recorded_at, source,
                                    device_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rating_id,
                scale["version_id"],
                body.value,
                body.note,
                s.to_db(body.recorded_at),
                principal.source,
                principal.token_id,
                now,
                now,
            ),
        )
        response.status_code = 201
    row = _get_rating(db, rating_id)
    assert row is not None
    return _rating(row)


@router.patch("/ratings/{rating_id}", response_model=s.Rating)
def patch_rating(rating_id: str, body: s.RatingPatch, db: Db, _: Principal) -> s.Rating:
    existing = _get_rating(db, rating_id)
    if existing is None or existing["deleted_at"] is not None:
        raise HTTPException(404, "Rating not found.")
    fields: dict[str, Any] = {}
    if body.value is not None:
        version = db.execute(
            "SELECT * FROM scale_versions WHERE id = ?", (existing["scale_version_id"],)
        ).fetchone()
        _validate_value(version, body.value)
        fields["value"] = body.value
    if "note" in body.model_fields_set:
        fields["note"] = body.note
    if body.recorded_at is not None:
        fields["recorded_at"] = s.to_db(body.recorded_at)
    if fields:
        fields["updated_at"] = utcnow()
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE ratings SET {sets} WHERE id = ?", [*fields.values(), rating_id])
    row = _get_rating(db, rating_id)
    assert row is not None
    return _rating(row)


@router.delete("/ratings/{rating_id}", status_code=204)
def delete_rating(rating_id: str, db: Db, _: Principal) -> None:
    existing = _get_rating(db, rating_id)
    if existing is None:
        raise HTTPException(404, "Rating not found.")
    if existing["deleted_at"] is None:
        now = utcnow()
        db.execute(
            "UPDATE ratings SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, rating_id)
        )
