"""A small in-memory cache with a time to live, so the report is calculated once per period, not once per request."""

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any

DEFAULT_TTL_SECONDS = 60
DEFAULT_MAX_ENTRIES = 64


class TTLCache:
    """Keeps a computed value for `ttl_seconds`. Thread-safe; `clock` is injectable so tests need not sleep."""

    def __init__(self, ttl_seconds: float = DEFAULT_TTL_SECONDS, max_entries: int = DEFAULT_MAX_ENTRIES, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: Hashable, compute: Callable[[], Any]) -> tuple[Any, bool]:
        """Returns (value, cache_hit). `compute` runs only on a miss, and only once per key at a time."""
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and now - entry[0] < self.ttl_seconds:
                return entry[1], True
            value = compute()
            self._entries = {k: e for k, e in self._entries.items() if now - e[0] < self.ttl_seconds}
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                del self._entries[oldest]
            self._entries[key] = (now, value)
            return value, False

    def clear(self) -> None:
        with self._lock:
            self._entries = {}
