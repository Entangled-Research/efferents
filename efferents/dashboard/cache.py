"""Small time-bounded caches for read endpoints polled by many browsers.

Many viewers polling the same lab every few seconds would otherwise re-read
sqlite and markdown for every request. A short TTL keeps the view fresh
while bounding the file work to one computation per key per interval. When
the producer raises (typically sqlite "database is locked" while the daemon
writes), the last good value is served instead of an error.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable


class TTLCache:
    def __init__(self, ttl_s: float = 2.0, *, clock: Callable[[], float] = time.monotonic):
        self.ttl_s = float(ttl_s)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[Hashable, tuple[float, Any]] = {}
        # One in-flight producer per key so a burst of pollers shares work.
        self._inflight: dict[Hashable, threading.Lock] = {}

    def get(
        self,
        key: Hashable,
        produce: Callable[[], Any],
        *,
        stale_on: tuple[type[BaseException], ...] = (),
    ) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._entries.get(key)
            if hit is not None and now - hit[0] < self.ttl_s:
                return hit[1]
            gate = self._inflight.setdefault(key, threading.Lock())
        with gate:
            with self._lock:  # another thread may have filled it meanwhile
                hit = self._entries.get(key)
                if hit is not None and self._clock() - hit[0] < self.ttl_s:
                    return hit[1]
            try:
                value = produce()
            except stale_on:
                with self._lock:
                    hit = self._entries.get(key)
                if hit is None:
                    raise
                return hit[1]
            with self._lock:
                self._entries[key] = (self._clock(), value)
            return value

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            if key is None:
                self._entries.clear()
            else:
                self._entries.pop(key, None)
