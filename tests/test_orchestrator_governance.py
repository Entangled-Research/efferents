"""Owner governance: provider-error classification, halts, backoff, stall and
crash notifications.  No network; the model client and sleeps are faked."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

import anthropic

from efferents.agents import notify as notify_mod
from efferents.agents import orchestrator as orch
from efferents.agents.budget import BudgetExhausted, CallUsage
from efferents.agents.model_client import (
    ProviderError,
    RoutingMessagesClient,
    classify_provider_error,
    probe_request,
)
from efferents.agents.state import load_state


# --- error classification ---------------------------------------------------

def _anthropic_error(cls, status, message, headers=None):
    response = httpx.Response(
        status, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
        headers=headers or {},
    )
    return cls(message, response=response, body={"error": {"message": message}})


def test_classify_credit_auth_rate_limit_and_transient():
    credit = _anthropic_error(
        anthropic.BadRequestError, 400,
        "Your credit balance is too low to access the Anthropic API.",
    )
    assert classify_provider_error(credit) == ("credit", None)
    auth = _anthropic_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    assert classify_provider_error(auth) == ("auth", None)
    limited = _anthropic_error(
        anthropic.RateLimitError, 429, "rate limited", headers={"retry-after": "7"}
    )
    assert classify_provider_error(limited) == ("rate_limit", 7.0)
    plain_400 = _anthropic_error(anthropic.BadRequestError, 400, "max_tokens too large")
    assert classify_provider_error(plain_400) == ("transient", None)
    assert classify_provider_error(RuntimeError("connection reset")) == ("transient", None)
    assert classify_provider_error(ProviderError("credit", "x")) == ("credit", None)


class _FailingDelegate:
    def __init__(self, error):
        self.messages = self
        self._error = error
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise self._error


def test_client_wraps_credit_error_but_leaves_transient_raw(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    credit = _anthropic_error(anthropic.BadRequestError, 400, "credit balance is too low")
    client = RoutingMessagesClient()
    client.delegate_for = lambda p: _FailingDelegate(credit)
    with pytest.raises(ProviderError) as exc:
        client.messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[])
    assert exc.value.kind == "credit"
    assert exc.value.__cause__ is credit

    client.delegate_for = lambda p: _FailingDelegate(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=5, messages=[])


def test_chain_failover_does_not_swallow_budget_exhausted(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    from efferents.agents.budget import BudgetTracker
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=0.0)
    delegate = _FailingDelegate(RuntimeError("should never be reached"))
    client = RoutingMessagesClient(budget=tracker)
    client.delegate_for = lambda p: delegate
    with pytest.raises(BudgetExhausted):
        client.messages.create(
            model="claude-sonnet-4-6,openai/gpt-5", max_tokens=5, messages=[]
        )
    assert delegate.calls == 0


def test_probe_request_is_minimal():
    req = probe_request("claude-haiku-4-5")
    assert req["max_tokens"] == 1
    assert req["model"] == "claude-haiku-4-5"


# --- notify -----------------------------------------------------------------

def test_webhook_posts_json_and_notify_all_is_best_effort(monkeypatch):
    monkeypatch.setenv("EFFERENTS_WEBHOOK_URL", "https://hooks.example.invalid/x")
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    captured = {}

    class _Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode())
        captured["ctype"] = req.get_header("Content-type")
        return _Resp()

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(notify_mod, "notify_macos", lambda *a, **k: (_ for _ in ()).throw(OSError))
    result = notify_mod.notify_all("t", "m", lab_id="lab-1")
    assert result == {"ntfy_sent": False, "webhook_sent": True}
    assert captured["url"] == "https://hooks.example.invalid/x"
    assert captured["ctype"] == "application/json"
    assert captured["body"]["title"] == "t"
    assert captured["body"]["message"] == "m"
    assert captured["body"]["lab_id"] == "lab-1"
    assert "ts" in captured["body"]


def test_webhook_failure_is_swallowed(monkeypatch):
    monkeypatch.setenv("EFFERENTS_WEBHOOK_URL", "https://hooks.example.invalid/x")

    def boom(req, timeout=0):
        raise notify_mod.urllib.error.URLError("down")

    monkeypatch.setattr(notify_mod.urllib.request, "urlopen", boom)
    assert notify_mod.notify_webhook("t", "m") is False


# --- orchestrator halt / backoff / stall / crash ----------------------------

@pytest.fixture
def harness(tmp_path, monkeypatch):
    o = orch.Orchestrator(
        lab_dir=tmp_path / "lab", context_dir=tmp_path / "context", dry_run=True,
        stall_hours=6.0,
    )
    o.dry_run = False
    sleeps: list[float] = []
    notifications: list[dict] = []

    def fake_sleep(secs):
        sleeps.append(secs)
        if len(sleeps) >= o._stop_after_sleeps:
            o._stop = True

    o._stop_after_sleeps = 10**9
    monkeypatch.setattr(o, "_interruptible_sleep", fake_sleep)
    monkeypatch.setattr(orch, "notify_all", lambda **k: notifications.append(k))
    monkeypatch.setattr(orch.time, "sleep", lambda s: None)
    return SimpleNamespace(o=o, sleeps=sleeps, notifications=notifications)


def _step_raises(o, exc):
    def _step():
        raise exc
    o.step = _step


def test_credit_error_halts_with_backoff_and_single_notification(harness):
    o, sleeps, notes = harness.o, harness.sleeps, harness.notifications
    credit = ProviderError("credit", "BadRequestError: credit balance is too low")
    _step_raises(o, credit)
    o.client = _FailingDelegate(credit)  # probes keep failing
    o._stop_after_sleeps = 6

    o.run()

    halt = (o.paths.root / "halt_reason.txt").read_text()
    assert halt.startswith("no credit: ")
    assert load_state(o.paths.state)["status"] == "paused"
    # 5 min -> 1 h cap, doubling.
    assert sleeps == [300, 600, 1200, 2400, 3600, 3600]
    halts = [n for n in notes if "halted" in n["title"]]
    assert len(halts) == 1 and halts[0]["priority"] == 5
    notebook = o.paths.notebook.read_text()
    assert "HALT (no credit)" in notebook
    assert "probe failed (credit)" in notebook
    # The researcher loop was never re-entered while halted.
    assert o.client.calls == 5


def test_auth_error_resumes_once_probe_succeeds(harness):
    o = harness.o
    auth = ProviderError("auth", "AuthenticationError: invalid x-api-key")
    calls = {"step": 0, "probe": 0}

    def step():
        calls["step"] += 1
        if calls["step"] == 1:
            raise auth
        o._stop = True
        return {"event": "no_proposal"}
    o.step = step

    class _Probe:
        messages = None

        def create(self, **kw):
            calls["probe"] += 1
            if calls["probe"] < 2:
                raise auth
            return SimpleNamespace(usage=None)
    probe = _Probe()
    probe.messages = probe
    o.client = probe

    o.run()

    assert calls["probe"] == 2
    assert harness.sleeps == [300, 600]
    assert not (o.paths.root / "halt_reason.txt").exists()
    state = load_state(o.paths.state)
    assert state["status"] == "running" and "halt_reason" not in state
    assert "resumed: provider probe succeeded after auth halt" in o.paths.notebook.read_text()
    assert calls["step"] == 2


def test_rate_limit_honours_retry_after_and_generic_backoff_doubles(harness):
    o, sleeps = harness.o, harness.sleeps
    limited = ProviderError("rate_limit", "429", retry_after=42.0)
    _step_raises(o, limited)
    o._stop_after_sleeps = 1
    o.run()
    assert sleeps == [42.0]
    assert not (o.paths.root / "halt_reason.txt").exists()

    o._stop = False
    sleeps.clear()
    _step_raises(o, RuntimeError("flaky executor"))
    o._stop_after_sleeps = 4
    o.run()
    assert sleeps == [60, 120, 240, 480]
    assert "orchestrator step FAILED: RuntimeError: flaky executor" in o.paths.notebook.read_text()


def test_generic_backoff_is_capped_and_resets_after_success(harness):
    o, sleeps = harness.o, harness.sleeps
    outcomes = iter([RuntimeError("a")] * 7 + [None, RuntimeError("b")])

    def step():
        item = next(outcomes)
        if item is not None:
            raise item
        return {"event": "no_proposal"}
    o.step = step
    o._stop_after_sleeps = 8
    o.run()
    assert sleeps == [60, 120, 240, 480, 960, 1920, 3600, 60]


def test_daily_budget_exhaustion_halts_then_resumes_next_day(harness):
    o, sleeps, notes = harness.o, harness.sleeps, harness.notifications
    exhausted = BudgetExhausted("daily", spend=20.05, cap=20.0, estimate=0.3)
    calls = {"n": 0}

    def step():
        calls["n"] += 1
        if calls["n"] == 1:
            raise exhausted
        o._stop = True
        return {"event": "no_proposal"}
    o.step = step

    o.run()

    assert len(sleeps) == 1 and sleeps[0] > 0  # until next UTC day
    notebook = o.paths.notebook.read_text()
    assert "HALT (budget): daily cap reached: spent $20.05 of $20.00" in notebook
    assert "resumed: new UTC day; daily cap reset" in notebook
    assert not (o.paths.root / "halt_reason.txt").exists()
    assert load_state(o.paths.state)["status"] == "running"
    assert [n for n in notes if "halted (budget)" in n["title"]]


def test_total_cap_halts_and_stops(harness):
    o = harness.o
    _step_raises(o, BudgetExhausted("total", spend=500.0, cap=500.0, estimate=0.1))
    o.run()
    assert (o.paths.root / "halt_reason.txt").read_text().startswith("budget: total cap reached")
    assert load_state(o.paths.state)["status"] == "paused"
    assert harness.sleeps == []


def test_refill_budget_pause_is_auditable(harness, monkeypatch):
    o = harness.o
    o.budget.record(agent="x", model="claude-sonnet-4-6",
                    usage=CallUsage(input_tokens=0, output_tokens=1_000_000))  # $15
    o.budget.daily_cap = 10.0
    o._stop_after_sleeps = 1
    assert o._refill_queue() == 0
    assert "HALT (budget): daily cap reached: spent $15.00 of $10.00" in o.paths.notebook.read_text()


def test_stall_notification_once_per_hour(harness, monkeypatch):
    o, notes = harness.o, harness.notifications
    o.step = lambda: {"event": "no_proposal"}
    o._started_ts = "2026-09-07T00:00:00+00:00"  # long ago
    o.run(max_iterations=3)
    stalls = [n for n in notes if "stalled" in n["title"]]
    assert len(stalls) == 1
    assert "no successful run for" in stalls[0]["message"]
    assert "STALL:" in o.paths.notebook.read_text()

    # A successful run resets the anchor and suppresses the stall.
    notes.clear()
    monkeypatch.setattr(o, "_refill_queue", lambda: 0)
    monkeypatch.setattr(orch, "queue_pop", lambda q: {"name": "p"})
    monkeypatch.setattr(orch.executor, "execute", lambda **k: {"ok": True, "name": "p"})
    for name in ("_maybe_digest", "_maybe_code", "_maybe_write"):
        monkeypatch.setattr(o, name, lambda: None)
    del o.step  # restore the real step
    o.run(max_iterations=1)
    assert "last_success_ts" in load_state(o.paths.state)
    assert not [n for n in notes if "stalled" in n["title"]]


def test_stall_hours_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_STALL_HOURS", "2.5")
    o = orch.Orchestrator(lab_dir=tmp_path / "lab", context_dir=tmp_path / "ctx", dry_run=True)
    assert o.stall_hours == 2.5


def test_total_cap_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_TOTAL_CAP_USD", "250")
    o = orch.Orchestrator(lab_dir=tmp_path / "lab", context_dir=tmp_path / "ctx", dry_run=True)
    assert o.budget.total_cap == 250.0


def test_crash_out_of_run_notifies_owner(harness, monkeypatch):
    o, notes = harness.o, harness.notifications
    _step_raises(o, RuntimeError("bad"))
    monkeypatch.setattr(o, "_record_step_failure", lambda e: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        o.run()
    crashes = [n for n in notes if "crashed" in n["title"]]
    assert len(crashes) == 1 and crashes[0]["priority"] == 5
    assert "orchestrator CRASHED: OSError: disk full" in o.paths.notebook.read_text()


def test_notify_event_rate_limited_per_event(harness):
    o, notes = harness.o, harness.notifications
    assert o._notify_event("stall", "stalled", "m") is True
    assert o._notify_event("stall", "stalled", "m") is False
    assert o._notify_event("halt:auth", "halted", "m") is True
    assert len(notes) == 2
    assert all(n["lab_id"] for n in notes)
