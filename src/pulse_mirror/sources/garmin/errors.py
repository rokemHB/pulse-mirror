"""Our own error types. The rest of the app never sees garminconnect exceptions."""

from __future__ import annotations


class GarminError(Exception):
    """Base class for all Garmin adapter errors."""


class AuthRequiredError(GarminError):
    """No usable token: someone has to run ``pulse-mirror login garmin`` again.

    Never retried automatically (repeated logins get accounts blocked).
    """


class RateLimitedError(GarminError):
    """Garmin answered 429. Back off; don't hammer."""


class GarminUnavailableError(GarminError):
    """Network problem, 5xx, or an unexpected response."""
