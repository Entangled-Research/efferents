from __future__ import annotations

import json
import subprocess

from efferents.agents.budget import BudgetTracker, CallUsage
from efferents.agents.journal import auto_commit_paper


def test_budget_record_includes_external_tool_cost(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=1.0)

    record = tracker.record(
        agent="librarian",
        model="claude-sonnet-4-6",
        usage=CallUsage(input_tokens=1000, output_tokens=100),
        extra_cost_usd=0.03,
        notes="three searches",
    )

    assert record["cost_usd"] == record["token_cost_usd"] + 0.03
    persisted = json.loads((tmp_path / "budget.jsonl").read_text())
    assert persisted["extra_cost_usd"] == 0.03
    assert tracker.spend_total() == record["cost_usd"]


def test_paper_commit_refuses_pre_staged_user_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Writer Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "writer@example.invalid"], cwd=repo, check=True
    )
    user_file = repo / "user.txt"
    user_file.write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)
    user_file.write_text("user staged work\n")
    subprocess.run(["git", "add", "user.txt"], cwd=repo, check=True)

    sha = auto_commit_paper(
        repo_root=repo,
        campaign_id="c1",
        headline="result",
        decision={"mean_score": 7, "min_score": 6},
    )

    assert sha is None
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged == ["user.txt"]


# --- hard pre-call cap ------------------------------------------------------

import pytest  # noqa: E402

from efferents.agents.budget import BudgetExhausted, estimate_call_cost_usd  # noqa: E402
from efferents.agents.model_client import RoutingMessagesClient  # noqa: E402


class _Usage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0


class _Delegate:
    """Anthropic-shaped fake whose *actual* usage may exceed the estimate."""

    def __init__(self, input_tokens, output_tokens):
        self.messages = self
        self.calls = 0
        self._usage = _Usage(input_tokens, output_tokens)

    def create(self, **kwargs):
        self.calls += 1
        return type("Resp", (), {"usage": self._usage, "content": []})()


def test_estimate_is_worst_case_for_output_and_cache_write():
    # 1000 output tokens on Sonnet = $0.015; 1000 input at cache-write rate = $0.00375
    est = estimate_call_cost_usd("claude-sonnet-4-6", 1000, 1000)
    assert est == pytest.approx(0.015 + 0.003 * 1.25)


def test_reserve_refuses_when_worst_case_breaches_daily_cap(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=0.10)
    # 10k Sonnet output tokens could cost $0.15 > $0.10 cap: refused before any spend.
    with pytest.raises(BudgetExhausted) as exc:
        tracker.reserve("claude-sonnet-4-6", 10_000)
    assert exc.value.scope == "daily"
    assert tracker.spend_today() == 0.0
    # A small call fits.
    assert tracker.reserve("claude-sonnet-4-6", 100) > 0


def test_reserve_enforces_total_cap(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=100.0, total_cap_usd=0.05)
    tracker.record(agent="x", model="claude-sonnet-4-6",
                   usage=CallUsage(input_tokens=0, output_tokens=3000))  # $0.045
    with pytest.raises(BudgetExhausted) as exc:
        tracker.reserve("claude-sonnet-4-6", 1000)  # +$0.015 would be $0.06
    assert exc.value.scope == "total"
    assert not tracker.should_pause()  # $0.045 < $0.05: coarse check still passes
    tracker.record(agent="x", model="claude-sonnet-4-6",
                   usage=CallUsage(input_tokens=0, output_tokens=1000))  # now $0.06
    assert tracker.should_pause()


def test_met_cap_refuses_even_unpriced_models(tmp_path):
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=0.0)
    with pytest.raises(BudgetExhausted):
        tracker.reserve("some-unknown/model", 10)


def test_hard_cap_overshoot_bounded_by_one_call(tmp_path, monkeypatch):
    """Spend may exceed the daily cap by at most one call's actual cost, and
    only when that call's real usage exceeds its worst-case estimate."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cap = 1.0
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=cap)
    # Estimate per call: 10k output tokens = $0.15 (tiny prompt). Actual usage
    # is deliberately larger: 150k input ($0.45) + 10k output ($0.15) = $0.60.
    delegate = _Delegate(input_tokens=150_000, output_tokens=10_000)
    client = RoutingMessagesClient(budget=tracker)
    client.delegate_for = lambda provider: delegate

    spend_before_each_call = []
    per_call_costs = []
    with pytest.raises(BudgetExhausted):
        for _ in range(100):
            spend_before_each_call.append(tracker.spend_today())
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=10_000,
                messages=[{"role": "user", "content": "go"}],
            )
            rec = tracker.record(
                agent="test", model="claude-sonnet-4-6",
                usage=CallUsage(resp.usage.input_tokens, resp.usage.output_tokens),
            )
            per_call_costs.append(rec["cost_usd"])

    # The refused reservation never reached the provider.
    assert delegate.calls == len(per_call_costs) == 2
    # Every call that was allowed started strictly under the cap ...
    assert all(s < cap for s in spend_before_each_call[:-1])
    # ... so the final overshoot is bounded by a single call's actual cost.
    assert tracker.spend_today() <= cap + max(per_call_costs)
    assert tracker.spend_today() == pytest.approx(1.20)
    # And no further call is admitted.
    with pytest.raises(BudgetExhausted):
        client.messages.create(model="claude-sonnet-4-6", max_tokens=1, messages=[])


def test_exact_estimates_never_overshoot(tmp_path, monkeypatch):
    """When actual usage matches the worst case, spend stays at or under the cap."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=1.0)
    delegate = _Delegate(input_tokens=0, output_tokens=10_000)  # exactly $0.15
    client = RoutingMessagesClient(budget=tracker)
    client.delegate_for = lambda provider: delegate
    with pytest.raises(BudgetExhausted):
        for _ in range(100):
            resp = client.messages.create(
                model="claude-sonnet-4-6", max_tokens=10_000, messages=[]
            )
            tracker.record(agent="t", model="claude-sonnet-4-6",
                           usage=CallUsage(resp.usage.input_tokens, resp.usage.output_tokens))
    assert delegate.calls == 6
    assert tracker.spend_today() <= 1.0
