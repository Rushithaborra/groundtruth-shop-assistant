"""A sliding-window rate limiter, keyed by session or client address. Thread-safe and bounded in memory."""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict, deque
from typing import Callable


class RateLimiter:
    """At most `limit` requests per `window_s` seconds for each key; the oldest keys are forgotten past `max_keys`."""

    def __init__(
        self, limit: int, window_s: float, max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic
    ):
        self.limit, self.window_s, self.max_keys, self._clock = limit, window_s, max_keys, clock
        self._hits: OrderedDict[str, deque] = OrderedDict()
        self._lock = threading.Lock()

    def check(self, key: str) -> float:
        """Record a request; returns 0 if allowed, otherwise the seconds to wait before the next one is."""
        now = self._clock()
        with self._lock:
            hits = self._hits.pop(key, None) or deque()
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            self._hits[key] = hits  # most recently seen keys go last
            while len(self._hits) > self.max_keys:
                self._hits.popitem(last=False)
            if len(hits) >= self.limit:
                return max(0.001, hits[0] + self.window_s - now)
            hits.append(now)
            return 0.0

    @staticmethod
    def retry_after_header(wait_s: float) -> str:
        return str(max(1, math.ceil(wait_s)))
