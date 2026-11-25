"""Authentication: one dashboard password (browser session) + per-device API tokens.

* The password hash (scrypt) and the cookie signing key live in
  ``$DATA_DIR/auth.json``, created by ``pulse-mirror auth set-password``.
* Browser: ``POST /api/v1/auth/login`` sets a signed, HttpOnly session cookie.
* Devices: ``Authorization: Bearer pm_...``. Only a SHA-256 of the token is stored.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from pulse_mirror.db.connection import utcnow

SESSION_COOKIE = "pm_session"
SESSION_MAX_AGE_S = 60 * 60 * 24 * 90  # 90 days; this is a LAN dashboard on your own phone
TOKEN_PREFIX = "pm_"

_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


@dataclass(frozen=True)
class Principal:
    kind: str  # "session" | "token"
    token_id: int | None = None

    @property
    def source(self) -> str:
        """Data source for things this principal creates."""
        return "manual" if self.kind == "session" else "app"


# --- password + signing key (auth.json) ---------------------------------------------


def _auth_file(data_dir: Path) -> Path:
    return data_dir / "auth.json"


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, _ = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    return hmac.compare_digest(hash_password(password, base64.b64decode(salt_b64)), stored)


def set_password(data_dir: Path, password: str) -> None:
    path = _auth_file(data_dir)
    data = load_auth(data_dir) or {}
    data["password_hash"] = hash_password(password)
    data.setdefault("secret_key", secrets.token_hex(32))
    data_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, 0o600)


def load_auth(data_dir: Path) -> dict[str, str] | None:
    path = _auth_file(data_dir)
    if not path.exists():
        return None
    data: dict[str, str] = json.loads(path.read_text())
    return data


# --- session cookie -------------------------------------------------------------------


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(secret: str, now: float | None = None) -> str:
    issued = str(int(now if now is not None else time.time()))
    return f"{issued}.{_sign(secret, issued)}"


def check_session(secret: str, cookie: str, now: float | None = None) -> bool:
    try:
        issued, signature = cookie.split(".")
        age = (now if now is not None else time.time()) - int(issued)
    except ValueError:
        return False
    return hmac.compare_digest(signature, _sign(secret, issued)) and 0 <= age < SESSION_MAX_AGE_S


# --- API tokens -----------------------------------------------------------------------


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_token(conn: sqlite3.Connection, name: str) -> tuple[int, str]:
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    cur = conn.execute(
        "INSERT INTO api_tokens (name, prefix, token_hash, created_at) VALUES (?, ?, ?, ?)",
        (name, token[:10], _token_hash(token), utcnow()),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid, token


def check_token(conn: sqlite3.Connection, token: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
        (_token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (utcnow(), row["id"]))
    return int(row["id"])
