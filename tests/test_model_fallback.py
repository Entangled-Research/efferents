"""Provider-neutral fallback, OpenAI-compatible tools, and spend isolation."""
import json
import os
from types import SimpleNamespace

import pytest

from efferents.agents.budget import BudgetExhausted, BudgetTracker, PRICING_PER_MTOK
from efferents.agents.model_client import LiteLLMMessagesClient, RoutingMessagesClient
from efferents.agents.researcher import _simple_call
from efferents.exec import _subprocess_env


CHAIN = "claude-sonnet-5,openai/gpt-4.1-mini"


@pytest.fixture(autouse=True)
def fixed_test_price(monkeypatch):
    monkeypatch.setitem(PRICING_PER_MTOK, "openai/gpt-4.1-mini", {"input": 1.0, "output": 4.0})


def test_gateway_detects_submission_fallback_key_without_loading_it(tmp_path, monkeypatch):
    from efferents.dashboard.control import _dotenv_has_key

    monkeypatch.setenv("EFFERENTS_MODEL", CHAIN)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert not _dotenv_has_key(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test-submission-key\n")
    assert _dotenv_has_key(tmp_path)
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.parametrize("anthropic_key", [False, True])
def test_fallback_records_actual_model_and_spend(tmp_path, monkeypatch, anthropic_key):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    if anthropic_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    calls = []

    def claude(**kwargs):
        calls.append("anthropic")
        error = RuntimeError("Your credit balance is too low")
        error.status_code = 400
        raise error

    def openai(**kwargs):
        calls.append("openai")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="result")],
            stop_reason="stop",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )

    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=1, total_cap_usd=2)
    client = RoutingMessagesClient(budget=tracker)
    client.delegate_for = lambda provider: SimpleNamespace(
        messages=SimpleNamespace(create=claude if provider == "anthropic" else openai)
    )
    assert _simple_call(client=client, system=[], messages=[], model=CHAIN,
                        agent="student", budget=tracker, max_tokens=100) == "result"
    record = json.loads(tracker.path.read_text())
    assert record["model"] == "openai/gpt-4.1-mini"
    assert record["cost_usd"] == pytest.approx(0.0014)
    assert calls == (["anthropic", "openai"] if anthropic_key else ["openai"])


def test_openai_compatible_endpoint_tools_and_cached_usage(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("EFFERENTS_API_BASE", "https://example.invalid/v1")
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(
                content=None, tool_calls=[SimpleNamespace(id="t1", function=SimpleNamespace(
                    name="experiment", arguments='{"seed":1}'
                ))]
            ))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                  prompt_tokens_details=SimpleNamespace(cached_tokens=80)),
        )

    monkeypatch.setattr("litellm.completion", completion)
    client = LiteLLMMessagesClient()
    result = client.messages.create(model="openai/gpt-4.1-mini", messages=[], max_tokens=100,
                                    tools=[{"name": "experiment", "input_schema": {"type": "object"}}])
    assert calls[0]["api_base"] == "https://example.invalid/v1"
    assert calls[0]["model"] == "openai/gpt-4.1-mini"
    assert result.content[0].input == {"seed": 1}
    assert result.stop_reason == "tool_use"
    assert result.usage.input_tokens == 20
    assert result.usage.cache_read_input_tokens == 80
    assert "OPENAI_API_KEY" not in _subprocess_env(())


def test_fallback_pricing_and_cap_before_request(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    budget = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=0.001)
    client = RoutingMessagesClient(budget=budget)
    with pytest.raises(BudgetExhausted):
        client.messages.create(model=CHAIN, messages=[], max_tokens=1000)
