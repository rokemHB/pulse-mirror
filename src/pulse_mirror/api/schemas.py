"""Request/response models of the public API (/api/v1). Changes must be additive."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field


def _require_tz(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp needs a timezone offset (e.g. 2025-01-15T22:00:00+01:00)")
    return value.astimezone(UTC)


AwareDatetime = Annotated[datetime, AfterValidator(_require_tz)]


def to_db(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")]


# --- status / sync ---------------------------------------------------------------------


class SyncRun(BaseModel):
    id: int
    source: str
    kind: str
    started_at: str
    finished_at: str | None
    status: str
    days_fetched: int
    error_message: str | None


class SourceStatus(BaseModel):
    source: str
    last_success_at: str | None
    last_run: SyncRun | None
    syncing: bool
    cooldown_until: str | None


class BackfillStatus(BaseModel):
    status: str
    cursor_date: str | None
    newest_date: str | None
    updated_at: str


class Status(BaseModel):
    version: str
    demo: bool
    timezone: str
    sources: list[SourceStatus]
    backfill: BackfillStatus | None
    data_range: tuple[str | None, str | None]


# --- metrics ---------------------------------------------------------------------------


class MetricType(BaseModel):
    key: str
    name: str
    unit: str | None
    kind: str
    category: str
    description: str | None


class DailySeries(BaseModel):
    """``points`` are ``[date, value]`` pairs, oldest first."""

    key: str
    source: str
    points: list[tuple[str, float | str]]


class Activity(BaseModel):
    id: int
    source: str
    source_id: str
    start_time: str
    local_date: str
    type: str
    name: str | None
    duration_s: float | None
    distance_m: float | None
    elevation_gain_m: float | None
    avg_hr: float | None
    max_hr: float | None
    avg_power: float | None
    norm_power: float | None
    calories: float | None
    training_load: float | None
    aerobic_te: float | None
    anaerobic_te: float | None


# --- events ----------------------------------------------------------------------------


class EventCategoryIn(BaseModel):
    key: Key
    name: str = Field(min_length=1, max_length=60)
    color: Color
    icon: str | None = None
    sort_order: int = 0


class EventCategoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    color: Color | None = None
    icon: str | None = None
    sort_order: int | None = None
    archived: bool | None = None


class EventCategory(BaseModel):
    id: int
    key: str
    name: str
    color: str
    icon: str | None
    sort_order: int
    archived: bool


class EventIn(BaseModel):
    id: str | None = Field(default=None, description="Optional client-generated UUID")
    category_id: int
    title: str = Field(min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=5000)
    start_at: AwareDatetime
    end_at: AwareDatetime | None = Field(default=None, description="Omit for a point in time")


class EventPatch(BaseModel):
    category_id: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=5000)
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    clear_end: bool = Field(default=False, description="Turn a span into a point in time")


class Event(BaseModel):
    id: str
    category_id: int
    title: str
    note: str | None
    start_at: str
    end_at: str | None
    source: str
    created_at: str
    updated_at: str


# --- wellbeing ---------------------------------------------------------------------------


class ScaleIn(BaseModel):
    key: Key
    name: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=1000)
    min_value: float
    max_value: float
    step: float = Field(default=1, gt=0)
    min_label: str | None = Field(default=None, max_length=60)
    max_label: str | None = Field(default=None, max_length=60)
    color: Color | None = None
    sort_order: int = 0


class ScalePatch(BaseModel):
    """Changing min/max/step creates a new scale version; the rest is edited in place."""

    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=1000)
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = Field(default=None, gt=0)
    min_label: str | None = Field(default=None, max_length=60)
    max_label: str | None = Field(default=None, max_length=60)
    color: Color | None = None
    sort_order: int | None = None
    archived: bool | None = None


class Scale(BaseModel):
    id: int
    key: str
    version: int
    version_id: int
    name: str
    description: str | None
    min_value: float
    max_value: float
    step: float
    min_label: str | None
    max_label: str | None
    color: str | None
    sort_order: int
    archived: bool


class ScaleVersion(BaseModel):
    id: int
    version: int
    name: str
    min_value: float
    max_value: float
    step: float
    min_label: str | None
    max_label: str | None
    created_at: str


class RatingIn(BaseModel):
    scale_id: int
    value: float
    note: str | None = Field(default=None, max_length=2000)
    recorded_at: AwareDatetime


class RatingPatch(BaseModel):
    value: float | None = None
    note: str | None = Field(default=None, max_length=2000)
    recorded_at: AwareDatetime | None = None


class Rating(BaseModel):
    id: str
    scale_id: int
    scale_version_id: int
    value: float
    note: str | None
    recorded_at: str
    source: str
    created_at: str
    updated_at: str
    deleted: bool


# --- auth / tokens -------------------------------------------------------------------------


class LoginIn(BaseModel):
    password: str


class Me(BaseModel):
    kind: str


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class Token(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: str
    last_used_at: str | None
    revoked: bool


class TokenCreated(Token):
    token: str = Field(description="Shown only once. Store it on the device.")
