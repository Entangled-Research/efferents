from __future__ import annotations

import pytest

from efferents.dashboard.cache import TTLCache


def test_ttl_cache_reuses_within_ttl_and_expires():
    now = [0.0]
    cache = TTLCache(ttl_s=2.0, clock=lambda: now[0])
    calls = []
    produce = lambda: calls.append(1) or len(calls)  # noqa: E731
    assert cache.get("k", produce) == 1
    assert cache.get("k", produce) == 1
    now[0] = 2.5
    assert cache.get("k", produce) == 2


def test_ttl_cache_serves_stale_on_named_error():
    now = [0.0]
    cache = TTLCache(ttl_s=1.0, clock=lambda: now[0])
    assert cache.get("k", lambda: "fresh") == "fresh"
    now[0] = 5.0

    def boom():
        raise RuntimeError("database is locked")

    assert cache.get("k", boom, stale_on=(RuntimeError,)) == "fresh"
    with pytest.raises(RuntimeError):
        cache.get("other", boom, stale_on=(RuntimeError,))


def test_invalidate():
    cache = TTLCache(ttl_s=100)
    assert cache.get("k", lambda: 1) == 1
    cache.invalidate("k")
    assert cache.get("k", lambda: 2) == 2
    cache.invalidate()
    assert cache.get("k", lambda: 3) == 3
