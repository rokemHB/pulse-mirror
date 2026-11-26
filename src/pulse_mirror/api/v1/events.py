"""/api/v1: event categories and events (manual annotations)."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from pulse_mirror.api import schemas as s
from pulse_mirror.api.deps import Db, Principal
from pulse_mirror.db.connection import utcnow

router = APIRouter(tags=["events"])


def _category(r: sqlite3.Row) -> s.EventCategory:
    return s.EventCategory(
        id=r["id"],
        key=r["key"],
        name=r["name"],
        color=r["color"],
        icon=r["icon"],
        sort_order=r["sort_order"],
        archived=r["archived_at"] is not None,
    )


def _event(r: sqlite3.Row) -> s.Event:
    return s.Event(**{k: r[k] for k in s.Event.model_fields})


def _get_event(db: sqlite3.Connection, event_id: str) -> sqlite3.Row:
    row: sqlite3.Row | None = db.execute(
        "SELECT * FROM events WHERE id = ? AND deleted_at IS NULL", (event_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Event not found.")
    return row


def _check_category(db: sqlite3.Connection, category_id: int) -> None:
    if db.execute("SELECT 1 FROM event_categories WHERE id = ?", (category_id,)).fetchone() is None:
        raise HTTPException(422, f"Unknown category_id {category_id}.")


# --- categories --------------------------------------------------------------------


@router.get("/event-categories", response_model=list[s.EventCategory])
def list_categories(db: Db, _: Principal) -> list[s.EventCategory]:
    rows = db.execute("SELECT * FROM event_categories ORDER BY sort_order, name")
    return [_category(r) for r in rows]


@router.post("/event-categories", response_model=s.EventCategory, status_code=201)
def create_category(body: s.EventCategoryIn, db: Db, _: Principal) -> s.EventCategory:
    try:
        cur = db.execute(
            "INSERT INTO event_categories (key, name, color, icon, sort_order) VALUES (?,?,?,?,?)",
            (body.key, body.name, body.color, body.icon, body.sort_order),
        )
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, f"Category key '{body.key}' already exists.") from e
    return _category(
        db.execute("SELECT * FROM event_categories WHERE id = ?", (cur.lastrowid,)).fetchone()
    )


@router.patch("/event-categories/{category_id}", response_model=s.EventCategory)
def update_category(
    category_id: int, body: s.EventCategoryPatch, db: Db, _: Principal
) -> s.EventCategory:
    _check_category(db, category_id)
    fields = body.model_dump(exclude_unset=True, exclude={"archived"})
    if "archived" in body.model_fields_set:
        fields["archived_at"] = utcnow() if body.archived else None
    if fields:
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(
            f"UPDATE event_categories SET {sets} WHERE id = ?", [*fields.values(), category_id]
        )
    return _category(
        db.execute("SELECT * FROM event_categories WHERE id = ?", (category_id,)).fetchone()
    )


# --- events ------------------------------------------------------------------------


@router.get("/events", response_model=list[s.Event])
def list_events(
    db: Db,
    _: Principal,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: Annotated[date | None, Query()] = None,
    category_id: int | None = None,
) -> list[s.Event]:
    """Events overlapping [from, to] (by UTC timestamps)."""
    sql = "SELECT * FROM events WHERE deleted_at IS NULL"
    params: list[Any] = []
    if to:
        sql += " AND start_at < ?"
        params.append((to + timedelta(days=1)).isoformat())
    if from_:
        sql += " AND COALESCE(end_at, start_at) >= ?"
        params.append(from_.isoformat())
    if category_id is not None:
        sql += " AND category_id = ?"
        params.append(category_id)
    return [_event(r) for r in db.execute(sql + " ORDER BY start_at", params)]


@router.post("/events", response_model=s.Event, status_code=201)
def create_event(body: s.EventIn, db: Db, principal: Principal) -> s.Event:
    _check_category(db, body.category_id)
    if body.end_at is not None and body.end_at < body.start_at:
        raise HTTPException(422, "end_at is before start_at.")
    event_id = body.id or str(uuid.uuid4())
    if db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone():
        return _event(_get_event(db, event_id))  # idempotent retry from a client
    now = utcnow()
    db.execute(
        """INSERT INTO events (id, category_id, title, note, start_at, end_at, source,
                               created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            event_id,
            body.category_id,
            body.title,
            body.note,
            s.to_db(body.start_at),
            s.to_db(body.end_at) if body.end_at else None,
            principal.source,
            now,
            now,
        ),
    )
    return _event(_get_event(db, event_id))


@router.get("/events/{event_id}", response_model=s.Event)
def get_event(event_id: str, db: Db, _: Principal) -> s.Event:
    return _event(_get_event(db, event_id))


@router.patch("/events/{event_id}", response_model=s.Event)
def update_event(event_id: str, body: s.EventPatch, db: Db, _: Principal) -> s.Event:
    current = _get_event(db, event_id)
    fields: dict[str, Any] = {}
    for name in ("category_id", "title", "note"):
        if name in body.model_fields_set:
            fields[name] = getattr(body, name)
    if body.start_at is not None:
        fields["start_at"] = s.to_db(body.start_at)
    if body.end_at is not None:
        fields["end_at"] = s.to_db(body.end_at)
    if body.clear_end:
        fields["end_at"] = None
    if "category_id" in fields:
        _check_category(db, fields["category_id"])
    start = fields.get("start_at", current["start_at"])
    end = fields.get("end_at", current["end_at"])
    if end is not None and end < start:
        raise HTTPException(422, "end_at is before start_at.")
    if fields:
        fields["updated_at"] = utcnow()
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE events SET {sets} WHERE id = ?", [*fields.values(), event_id])
    return _event(_get_event(db, event_id))


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: str, db: Db, _: Principal) -> None:
    _get_event(db, event_id)
    now = utcnow()
    db.execute(
        "UPDATE events SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, event_id)
    )
