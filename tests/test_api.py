from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from pulse_mirror.api import auth
from pulse_mirror.api.app import create_app
from pulse_mirror.config import Settings
from pulse_mirror.db import connect
from pulse_mirror.sources.garmin.throttle import Throttle
from tests.conftest import FakeGarmin, days_between

PASSWORD = "correct horse battery staple"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("PULSE_DATA_DIR", str(tmp_path))
    s = Settings.from_env()
    auth.set_password(s.data_dir, PASSWORD)
    return s


@pytest.fixture
def anon(settings) -> TestClient:
    api = FakeGarmin(days_between("2025-01-01", "2026-12-31"))
    no_wait = lambda: Throttle(0, jitter_s=0, sleep=lambda _: None)  # noqa: E731
    return TestClient(create_app(settings, garmin_factory=lambda: api, throttle_factory=no_wait))


@pytest.fixture
def client(anon) -> TestClient:
    assert anon.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 200
    return anon


# --- auth ------------------------------------------------------------------------------


def test_requires_login(anon):
    assert anon.get("/api/v1/status").status_code == 401
    assert anon.post("/api/v1/auth/login", json={"password": "nope"}).status_code == 401
    assert anon.get("/healthz").json()["ok"] is True
    assert anon.get("/api/openapi.json").status_code == 200


def test_session_and_logout(client):
    assert client.get("/api/v1/auth/me").json() == {"kind": "session"}
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_device_tokens(client, anon):
    created = client.post("/api/v1/tokens", json={"name": "Phone"}).json()
    token = created["token"]
    assert token.startswith("pm_")
    device = TestClient(anon.app)
    headers = {"Authorization": f"Bearer {token}"}
    assert device.get("/api/v1/auth/me", headers=headers).json() == {"kind": "token"}
    # devices can't manage tokens
    assert device.get("/api/v1/tokens", headers=headers).status_code == 403
    listed = client.get("/api/v1/tokens").json()
    assert listed[0]["last_used_at"] is not None and "token" not in listed[0]
    assert client.delete(f"/api/v1/tokens/{created['id']}").status_code == 204
    assert device.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_session_cookie_cannot_be_forged():
    assert not auth.check_session("secret", "1700000000.deadbeef")
    good = auth.make_session("secret", now=1000)
    assert auth.check_session("secret", good, now=1001)
    assert not auth.check_session("other", good, now=1001)
    assert not auth.check_session("secret", good, now=1000 + auth.SESSION_MAX_AGE_S + 1)


# --- status, sync, metrics ---------------------------------------------------------


def test_status_and_manual_sync(client, settings):
    st = client.get("/api/v1/status").json()
    assert st["sources"][0]["last_success_at"] is None
    assert client.post("/api/v1/sync").status_code == 202
    # wait for the background worker
    import time

    for _ in range(100):
        st = client.get("/api/v1/status").json()
        if st["sources"][0]["last_success_at"]:
            break
        time.sleep(0.05)
    assert st["sources"][0]["last_success_at"] is not None
    assert st["sources"][0]["cooldown_until"] is not None
    again = client.post("/api/v1/sync")
    assert again.status_code == 429 and "Cooldown" in again.json()["detail"]

    series = client.get("/api/v1/metrics/daily", params={"keys": "resting_hr,hrv_status"}).json()
    by_key = {x["key"]: x for x in series}
    assert set(by_key) == {"resting_hr", "hrv_status"}
    assert by_key["resting_hr"]["points"][-1][1] == 52
    assert by_key["hrv_status"]["points"][0][1] == "BALANCED"
    assert len(client.get("/api/v1/metrics").json()) >= 25
    acts = client.get("/api/v1/activities").json()
    assert acts and acts[0]["type"] == "cycling"
    assert client.get(f"/api/v1/activities/{acts[0]['id']}").status_code == 200


def test_sync_disabled_in_demo(settings):
    c = TestClient(create_app(settings, garmin_factory=None, demo=True))
    c.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert c.post("/api/v1/sync").status_code == 409
    assert c.get("/api/v1/status").json()["demo"] is True


# --- events ------------------------------------------------------------------------------


def test_event_crud(client):
    cats = client.get("/api/v1/event-categories").json()
    trip = next(c for c in cats if c["key"] == "trip")
    body = {
        "category_id": trip["id"],
        "title": "Gravel tour",
        "start_at": "2025-03-01T08:00:00+01:00",
        "end_at": "2025-03-10T18:00:00+01:00",
    }
    ev = client.post("/api/v1/events", json=body).json()
    assert ev["start_at"] == "2025-03-01T07:00:00Z" and ev["source"] == "manual"
    point = client.post(
        "/api/v1/events",
        json={"category_id": trip["id"], "title": "x", "start_at": "2025-05-01T22:00:00Z"},
    ).json()
    assert point["end_at"] is None

    in_range = client.get("/api/v1/events", params={"from": "2025-03-05", "to": "2025-03-06"})
    assert [e["id"] for e in in_range.json()] == [ev["id"]]

    patched = client.patch(f"/api/v1/events/{ev['id']}", json={"clear_end": True, "note": "n"})
    assert patched.json()["end_at"] is None and patched.json()["note"] == "n"
    bad = client.patch(f"/api/v1/events/{point['id']}", json={"end_at": "2025-04-01T00:00:00Z"})
    assert bad.status_code == 422
    naive = client.post("/api/v1/events", json=body | {"start_at": "2025-03-01T08:00:00"})
    assert naive.status_code == 422

    assert client.delete(f"/api/v1/events/{ev['id']}").status_code == 204
    assert client.get(f"/api/v1/events/{ev['id']}").status_code == 404


def test_event_client_id_is_idempotent(client):
    cat = client.get("/api/v1/event-categories").json()[0]["id"]
    eid = str(uuid.uuid4())
    body = {"id": eid, "category_id": cat, "title": "t", "start_at": "2025-01-01T00:00:00Z"}
    assert client.post("/api/v1/events", json=body).json()["id"] == eid
    assert client.post("/api/v1/events", json=body).status_code == 201
    assert len(client.get("/api/v1/events").json()) == 1


def test_categories(client):
    new = client.post(
        "/api/v1/event-categories",
        json={"key": "travel_work", "name": "Work trip", "color": "#112233"},
    )
    assert new.status_code == 201
    dup = client.post(
        "/api/v1/event-categories", json={"key": "travel_work", "name": "x", "color": "#112233"}
    )
    assert dup.status_code == 409
    archived = client.patch(f"/api/v1/event-categories/{new.json()['id']}", json={"archived": True})
    assert archived.json()["archived"] is True


# --- scales & ratings -----------------------------------------------------------------------


def _energy_scale(client):
    return client.post(
        "/api/v1/scales",
        json={
            "key": "energy",
            "name": "Energy",
            "min_value": 1,
            "max_value": 10,
            "min_label": "terrible",
            "max_label": "excellent",
            "color": "#ff8800",
        },
    ).json()


def test_rating_upsert_is_idempotent(client):
    scale = _energy_scale(client)
    rid = str(uuid.uuid4())
    body = {"scale_id": scale["id"], "value": 7, "recorded_at": "2025-01-15T07:30:00+01:00"}
    first = client.put(f"/api/v1/ratings/{rid}", json=body)
    assert first.status_code == 201 and first.json()["recorded_at"] == "2025-01-15T06:30:00Z"
    again = client.put(f"/api/v1/ratings/{rid}", json=body | {"value": 8})
    assert again.status_code == 200 and again.json()["value"] == 8
    assert len(client.get("/api/v1/ratings").json()) == 1
    assert client.put(f"/api/v1/ratings/{rid}", json=body | {"value": 11}).status_code == 422
    assert client.put(f"/api/v1/ratings/{rid}", json=body | {"value": 7.5}).status_code == 422


def test_changing_scale_meaning_creates_version_and_keeps_old_ratings(client):
    scale = _energy_scale(client)
    old_id = str(uuid.uuid4())
    client.put(
        f"/api/v1/ratings/{old_id}",
        json={"scale_id": scale["id"], "value": 9, "recorded_at": "2025-01-01T08:00:00Z"},
    )
    cosmetic = client.patch(f"/api/v1/scales/{scale['id']}", json={"name": "Energy level"}).json()
    assert cosmetic["version"] == 1 and cosmetic["name"] == "Energy level"
    v2 = client.patch(f"/api/v1/scales/{scale['id']}", json={"min_value": 0, "max_value": 5}).json()
    assert v2["version"] == 2 and v2["max_value"] == 5 and v2["name"] == "Energy level"

    old = client.get("/api/v1/ratings").json()[0]
    assert old["scale_version_id"] == scale["version_id"] and old["value"] == 9
    # the old rating can still be edited within its own version's range
    assert client.patch(f"/api/v1/ratings/{old_id}", json={"value": 8}).status_code == 200
    new = client.put(
        f"/api/v1/ratings/{uuid.uuid4()}",
        json={"scale_id": scale["id"], "value": 9, "recorded_at": "2025-01-02T08:00:00Z"},
    )
    assert new.status_code == 422  # 9 is outside the new 0..5 range
    versions = client.get(f"/api/v1/scales/{scale['id']}/versions").json()
    assert [v["version"] for v in versions] == [1, 2]


def test_archived_scale_hidden_and_closed(client):
    scale = _energy_scale(client)
    client.patch(f"/api/v1/scales/{scale['id']}", json={"archived": True})
    assert client.get("/api/v1/scales").json() == []
    assert len(client.get("/api/v1/scales", params={"include_archived": True}).json()) == 1
    r = client.put(
        f"/api/v1/ratings/{uuid.uuid4()}",
        json={"scale_id": scale["id"], "value": 5, "recorded_at": "2025-01-02T08:00:00Z"},
    )
    assert r.status_code == 422


def test_rating_delete_and_sync_feed(client):
    scale = _energy_scale(client)
    rid = str(uuid.uuid4())
    body = {"scale_id": scale["id"], "value": 5, "recorded_at": "2025-01-02T08:00:00Z"}
    client.put(f"/api/v1/ratings/{rid}", json=body)
    assert client.delete(f"/api/v1/ratings/{rid}").status_code == 204
    assert client.get("/api/v1/ratings").json() == []
    feed = client.get("/api/v1/ratings", params={"updated_since": "2000-01-01T00:00:00Z"}).json()
    assert feed[0]["deleted"] is True
    assert client.put(f"/api/v1/ratings/{rid}", json=body).status_code == 410


def test_ratings_from_device_are_tagged_app(client, settings):
    token = client.post("/api/v1/tokens", json={"name": "Phone"}).json()["token"]
    scale = _energy_scale(client)
    device = TestClient(client.app)
    r = device.put(
        f"/api/v1/ratings/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
        json={"scale_id": scale["id"], "value": 6, "recorded_at": "2025-01-02T08:00:00Z"},
    ).json()
    assert r["source"] == "app"
    row = connect(settings.db_path).execute("SELECT device_id FROM ratings").fetchone()
    assert row["device_id"] is not None


def test_exports_and_weekly_pdf(client):
    assert client.get("/api/v1/export/metrics.csv").headers["content-type"].startswith("text/csv")
    assert client.get("/api/v1/export/nope.csv").status_code == 404
    assert client.get("/api/v1/export/all.zip").content[:2] == b"PK"
    r = client.get("/api/v1/reports/weekly.pdf", params={"week": "2025-W03"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get("/api/v1/reports/weekly.pdf", params={"week": "2025-W99"}).status_code == 422
