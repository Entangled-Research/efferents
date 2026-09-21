"""Cross-process call limiter (EFFERENTS_MAX_CONCURRENT_CALLS)."""
from __future__ import annotations

import os
import threading
import time

import pytest

from efferents.agents import model_client as mc


def test_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("EFFERENTS_MAX_CONCURRENT_CALLS", raising=False)
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    with mc.call_slot():
        pass
    assert not (tmp_path / "locks").exists()


def test_slots_serialize_concurrent_callers(monkeypatch, tmp_path):
    monkeypatch.setenv("EFFERENTS_MAX_CONCURRENT_CALLS", "1")
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    monkeypatch.setenv("EFFERENTS_CALL_SLOT_WAIT_S", "10")
    active, peak, lock = 0, 0, threading.Lock()

    def worker():
        nonlocal active, peak
        # flock is per open file description, so threads in one process must
        # each open their own handle — call_slot does, so this exercises it.
        with mc.call_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.2)
            with lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak == 1
    assert (tmp_path / "locks" / "slot-0.lock").exists()


def test_timeout_raises_rate_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("EFFERENTS_MAX_CONCURRENT_CALLS", "1")
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    monkeypatch.setenv("EFFERENTS_CALL_SLOT_WAIT_S", "0.6")
    release = threading.Event()
    held = threading.Event()

    def holder():
        with mc.call_slot():
            held.set()
            release.wait(5)

    t = threading.Thread(target=holder)
    t.start()
    held.wait(2)
    try:
        with pytest.raises(mc.ProviderError) as excinfo:
            with mc.call_slot():
                pass
        assert excinfo.value.kind == "rate_limit"
        assert excinfo.value.retry_after == 30.0
    finally:
        release.set()
        t.join()


def test_slot_released_when_holder_dies(monkeypatch, tmp_path):
    monkeypatch.setenv("EFFERENTS_MAX_CONCURRENT_CALLS", "1")
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    monkeypatch.setenv("EFFERENTS_CALL_SLOT_WAIT_S", "5")
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:  # child: take the slot, signal, then die without releasing
        os.close(r)
        ctx = mc.call_slot()
        ctx.__enter__()
        os.write(w, b"x")
        os._exit(0)
    os.close(w)
    os.read(r, 1)
    os.waitpid(pid, 0)
    started = time.monotonic()
    with mc.call_slot():
        pass
    assert time.monotonic() - started < 2.0


def test_routing_wraps_delegate_call_in_slot(monkeypatch, tmp_path):
    monkeypatch.setenv("EFFERENTS_MAX_CONCURRENT_CALLS", "1")
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = {}

    class Delegate:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            # While the request is in flight the single slot must be held.
            seen["slot_busy"] = _slot_busy(tmp_path / "locks" / "slot-0.lock")
            return "ok"

    client = mc.RoutingMessagesClient()
    client.delegate_for = lambda provider: Delegate()
    assert client.messages.create(model="claude-sonnet-4-6", messages=[]) == "ok"
    assert seen["slot_busy"] is True


def _slot_busy(path) -> bool:
    import fcntl
    with open(path, "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fh, fcntl.LOCK_UN)
        return False


def test_anthropic_max_retries_env(monkeypatch):
    monkeypatch.delenv("EFFERENTS_ANTHROPIC_MAX_RETRIES", raising=False)
    assert mc.anthropic_max_retries() == 2
    monkeypatch.setenv("EFFERENTS_ANTHROPIC_MAX_RETRIES", "4")
    assert mc.anthropic_max_retries() == 4
    monkeypatch.setenv("EFFERENTS_ANTHROPIC_MAX_RETRIES", "junk")
    assert mc.anthropic_max_retries() == 2
