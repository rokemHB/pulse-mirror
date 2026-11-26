from __future__ import annotations

import random
import time
from collections.abc import Callable


class Throttle:
    """Guarantees a minimum gap (plus jitter) between consecutive Garmin requests."""

    def __init__(
        self,
        min_interval_s: float,
        jitter_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval_s = min_interval_s
        self.jitter_s = jitter_s
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self.min_interval_s + random.uniform(0, self.jitter_s)
            remaining = self._last + gap - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last = self._clock()

    def pause(self, seconds: float) -> None:
        """Explicit long pause, e.g. after a 429."""
        self._sleep(seconds)
        self._last = self._clock()
