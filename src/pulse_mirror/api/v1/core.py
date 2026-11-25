"""/api/v1: auth, status & sync, metrics, activities."""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from pulse_mirror import __version__
from pulse_mirror.api import auth
from pulse_mirror.api import schemas as s
from pulse_mirror.api.deps import Db, Principal
from pulse_mirror.db import connect, repo
from pulse_mirror.sync import service as sync

log = logging.getLogger(__name__)
router = APIRouter()

DateParam = Annotated[date | None, Query(description="YYYY-MM-DD, inclusive")]


# --- auth -----------------------------------------------------------------------------


@router.post("/auth/login", response_model=s.Me, tags=["auth"])
def login(body: s.LoginIn, request: Request, response: Response) -> s.Me:
    stored = request.app.state.password_hash
    if not stored or not auth.verify_password(body.password, stored):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong password.")
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.make_session(request.app.state.secret_key),
        max_age=auth.SESSION_MAX_AGE_S,
        httponly=True,
        samesite="strict",
    )
    return s.Me(kind="session")


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(response: Response) -> None:
    response.delete_cookie(auth.SESSION_COOKIE)


@router.get("/auth/me", response_model=s.Me, tags=["auth"])
def me(principal: Principal) -> s.Me:
    return s.Me(kind=principal.kind)


# --- status & sync ---------------------------------------------------------------------


def _run(row: Any) -> s.SyncRun | None:
    return s.SyncRun(**dict(row)) if row is not None else None


@router.get("/status", response_model=s.Status, tags=["sync"])
def get_status(request: Request, db: Db, _: Principal) -> s.Status:
    settings = request.app.state.settings
    sources = []
    for source in ("garmin",):
        ok = repo.last_sync_run(db, source, "ok")
        cooldown = sync.cooldown_until(db)
        sources.append(
            s.SourceStatus(
                source=source,
                last_success_at=ok["finished_at"] if ok else None,
                last_run=_run(repo.last_sync_run(db, source)),
                syncing=sync.is_running(),
                cooldown_until=cooldown.strftime("%Y-%m-%dT%H:%M:%SZ") if cooldown else None,
            )
        )
    bf = db.execute("SELECT * FROM backfill_state WHERE source = 'garmin'").fetchone()
    first, last = db.execute("SELECT MIN(date), MAX(date) FROM daily_metrics").fetchone()
    return s.Status(
        version=__version__,
        demo=request.app.state.demo,
        timezone=str(settings.timezone),
        sources=sources,
        backfill=s.BackfillStatus(
            status=bf["status"],
            cursor_date=bf["cursor_date"],
            newest_date=bf["newest_date"],
            updated_at=bf["updated_at"],
        )
        if bf
        else None,
        data_range=(first, last),
    )


@router.post("/sync", status_code=202, tags=["sync"])
def trigger_sync(request: Request, db: Db, _: Principal) -> dict[str, str]:
    """Refresh from Garmin Connect in the background (lock + 10-min cooldown)."""
    state = request.app.state
    if state.garmin_factory is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Sync is disabled in demo mode.")
    try:
        sync.check_allowed(db, "manual")
    except sync.SyncRefusedError as e:
        code = status.HTTP_429_TOO_MANY_REQUESTS if e.reason == "cooldown" else 409
        raise HTTPException(code, str(e)) from e
    settings = state.settings

    def worker() -> None:
        conn = connect(settings.db_path)
        try:
            result = sync.sync_recent(
                conn,
                state.garmin_factory,
                state.throttle_factory(),
                datetime.now(settings.timezone).date(),
                settings.sync_days,
                kind="manual",
            )
            log.info("manual sync: %s", result.message)
        except sync.SyncRefusedError as e:
            log.info("manual sync refused: %s", e)
        except Exception:
            log.exception("manual sync crashed")
        finally:
            conn.close()

    threading.Thread(target=worker, name="manual-sync", daemon=True).start()
    return {"status": "started"}


@router.get("/sync/runs", response_model=list[s.SyncRun], tags=["sync"])
def sync_runs(db: Db, _: Principal, limit: int = Query(20, le=200)) -> list[s.SyncRun]:
    rows = db.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,))
    return [s.SyncRun(**dict(r)) for r in rows]


# --- metrics & activities ------------------------------------------------------------


@router.get("/metrics", response_model=list[s.MetricType], tags=["metrics"])
def metric_types(db: Db, _: Principal) -> list[s.MetricType]:
    rows = db.execute("SELECT * FROM metric_types ORDER BY sort_order, key")
    return [s.MetricType(**{k: r[k] for k in s.MetricType.model_fields}) for r in rows]


@router.get("/metrics/daily", response_model=list[s.DailySeries], tags=["metrics"])
def daily_metrics(
    db: Db,
    _: Principal,
    keys: Annotated[str | None, Query(description="Comma-separated metric keys")] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: DateParam = None,
    source: str | None = None,
) -> list[s.DailySeries]:
    sql = "SELECT date, metric_key, source, value_num, value_text FROM daily_metrics WHERE 1=1"
    params: list[Any] = []
    if keys:
        wanted = [k.strip() for k in keys.split(",") if k.strip()]
        sql += f" AND metric_key IN ({','.join('?' * len(wanted))})"
        params += wanted
    if from_:
        sql += " AND date >= ?"
        params.append(from_.isoformat())
    if to:
        sql += " AND date <= ?"
        params.append(to.isoformat())
    if source:
        sql += " AND source = ?"
        params.append(source)
    series: dict[tuple[str, str], list[tuple[str, float | str]]] = {}
    for r in db.execute(sql + " ORDER BY metric_key, source, date", params):
        value = r["value_num"] if r["value_num"] is not None else r["value_text"]
        series.setdefault((r["metric_key"], r["source"]), []).append((r["date"], value))
    return [s.DailySeries(key=k, source=src, points=p) for (k, src), p in series.items()]


@router.get("/activities", response_model=list[s.Activity], tags=["metrics"])
def activities(
    db: Db,
    _: Principal,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: DateParam = None,
    type: str | None = None,
    limit: int = Query(500, le=5000),
) -> list[s.Activity]:
    sql = "SELECT * FROM activities WHERE 1=1"
    params: list[Any] = []
    if from_:
        sql += " AND local_date >= ?"
        params.append(from_.isoformat())
    if to:
        sql += " AND local_date <= ?"
        params.append(to.isoformat())
    if type:
        sql += " AND type = ?"
        params.append(type)
    rows = db.execute(sql + " ORDER BY start_time DESC LIMIT ?", [*params, limit])
    return [s.Activity(**{k: r[k] for k in s.Activity.model_fields}) for r in rows]


@router.get("/activities/{activity_id}", response_model=s.Activity, tags=["metrics"])
def activity(activity_id: int, db: Db, _: Principal) -> s.Activity:
    r = db.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "Activity not found.")
    return s.Activity(**{k: r[k] for k in s.Activity.model_fields})
