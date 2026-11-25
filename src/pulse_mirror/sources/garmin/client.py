"""The ONLY module that imports ``garminconnect`` (see CLAUDE.md, rule 4).

It exposes a narrow protocol (:class:`GarminApi`) that returns raw JSON
payloads, and translates the library's exceptions into our own. Mapping to the
domain model happens in :mod:`mapping`, never here.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

import garminconnect
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectNotFoundError,
    GarminConnectTooManyRequestsError,
)

from pulse_mirror.sources.garmin.errors import (
    AuthRequiredError,
    GarminUnavailableError,
    RateLimitedError,
)

log = logging.getLogger(__name__)


class GarminApi(Protocol):
    """What the rest of the adapter needs from Garmin. Fakes implement this in tests."""

    def daily_endpoint(self, endpoint: str, day: str) -> Any: ...

    def activities(self, start: str, end: str) -> list[dict[str, Any]]: ...


# endpoint name (stored in raw_responses) -> library method taking a date string
DAILY_ENDPOINTS: dict[str, str] = {
    "user_summary": "get_user_summary",
    "sleep": "get_sleep_data",
    "hrv": "get_hrv_data",
    "max_metrics": "get_max_metrics",
    "training_status": "get_training_status",
}


def _translate[T](call: Callable[[], T]) -> T:
    try:
        return call()
    except GarminConnectTooManyRequestsError as e:
        raise RateLimitedError(str(e)) from e
    except GarminConnectAuthenticationError as e:
        raise AuthRequiredError(
            f"Garmin rejected the stored login ({e}). Run `pulse-mirror login garmin`."
        ) from e
    except GarminConnectNotFoundError:
        raise
    except GarminConnectConnectionError as e:
        raise GarminUnavailableError(str(e)) from e


class GarminClient:
    """Real implementation of :class:`GarminApi` on top of ``garminconnect``."""

    def __init__(self, garmin: Any) -> None:
        self._garmin = garmin

    @classmethod
    def from_tokenstore(cls, tokenstore: Path) -> GarminClient:
        """Resume from stored tokens. Never falls back to a credential login."""
        if not tokenstore.exists() or not any(tokenstore.iterdir()):
            raise AuthRequiredError(
                f"No Garmin tokens in {tokenstore}. Run `pulse-mirror login garmin` first."
            )
        # retry_attempts=1: one retry on 5xx/network errors; 401/429 never retry.
        garmin = garminconnect.Garmin(retry_attempts=1)
        _translate(lambda: garmin.login(str(tokenstore)))
        return cls(garmin)

    def daily_endpoint(self, endpoint: str, day: str) -> Any:
        method = getattr(self._garmin, DAILY_ENDPOINTS[endpoint])
        try:
            return _translate(lambda: method(day))
        except GarminConnectNotFoundError:
            return None
        except GarminUnavailableError as e:
            # get_user_summary raises "No data received" for empty days.
            if "no data" in str(e).lower():
                return None
            raise

    def activities(self, start: str, end: str) -> list[dict[str, Any]]:
        result = _translate(lambda: self._garmin.get_activities_by_date(start, end))
        return list(result or [])


def login_interactive(
    tokenstore: Path,
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
) -> str | None:
    """One credential login; writes tokens to ``tokenstore``. Returns the display name.

    Called only from the interactive ``login`` command. No retries on failure.
    """
    tokenstore.mkdir(parents=True, exist_ok=True)
    os.chmod(tokenstore, 0o700)
    garmin = garminconnect.Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
    _translate(lambda: garmin.login(str(tokenstore)))
    name: str | None = garmin.get_full_name()
    return name
