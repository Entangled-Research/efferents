from types import SimpleNamespace

import pytest

from efferents.agents.budget import BudgetExhausted, BudgetTracker, CallUsage, cost_usd
from efferents.agents.model_client import LiteLLMMessagesClient, RoutingMessagesClient
from efferents.agents.researcher import _simple_call
from efferents.exec import _subprocess_env

CHAIN = "claude-sonnet-5,zai/glm-5.3"


def test_gateway_detects_submission_fallback_key_without_loading_it(tmp_path, monkeypatch):
    from efferents.dashboard.control import _dotenv_has_key
    monkeypatch.setenv("EFFERENTS_MODEL", CHAIN)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    assert not _dotenv_has_key(tmp_path)
    (tmp_path / ".env").write_text("ZAI_API_KEY=test-submission-key\n")
    assert _dotenv_has_key(tmp_path)
    import os
    assert "ZAI_API_KEY" not in os.environ


@pytest.mark.parametrize("anthropic_key", [False, True])
def test_glm_fallback_records_actual_model_and_spend(tmp_path, monkeypatch, anthropic_key):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    if anthropic_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    calls = []

    def claude(**kwargs):
        calls.append("anthropic")
        error = RuntimeError("Your credit balance is too low")
        error.status_code = 400
        raise error

    def glm(**kwargs):
        calls.append("zai")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="result")],
            stop_reason="stop",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
        )

    tracker = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=1, total_cap_usd=2)
    client = RoutingMessagesClient(budget=tracker)
    client.delegate_for = lambda provider: SimpleNamespace(
        messages=SimpleNamespace(create=claude if provider == "anthropic" else glm)
    )
    assert _simple_call(client=client, system=[], messages=[], model=CHAIN,
                        agent="student", budget=tracker, max_tokens=100) == "result"
    import json
    record = json.loads(tracker.path.read_text())
    assert record["model"] == "zai/glm-5.3"
    assert record["cost_usd"] == pytest.approx(0.00184)
    assert calls == (["anthropic", "zai"] if anthropic_key else ["zai"])


def test_zai_endpoint_key_tools_and_cached_usage(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test-zai-key")
    monkeypatch.setenv("EFFERENTS_API_BASE", "https://other-provider.invalid/v1")
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
    result = client.messages.create(model="zai/glm-5.3", messages=[], max_tokens=100,
                                    tools=[{"name": "experiment", "input_schema": {"type": "object"}}])
    assert calls[0]["api_base"] == "https://api.z.ai/api/paas/v4"
    assert calls[0]["api_key"] == "test-zai-key"
    assert calls[0]["model"] == "openai/glm-5.3"
    assert result.content[0].input == {"seed": 1}
    assert result.stop_reason == "tool_use"
    assert result.usage.input_tokens == 20
    assert result.usage.cache_read_input_tokens == 80
    client.messages.create(model="openai/test-model", messages=[])
    assert calls[1]["api_base"] == "https://other-provider.invalid/v1"
    assert "api_key" not in calls[1]
    assert "ZAI_API_KEY" not in _subprocess_env(())


def test_glm_pricing_and_cap_before_request(tmp_path, monkeypatch):
    assert cost_usd("zai/glm-5.3", CallUsage(1_000_000, 1_000_000,
                   cache_read_input_tokens=1_000_000)) == pytest.approx(6.06)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ZAI_API_KEY", "test-key")
    budget = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=0.001)
    client = RoutingMessagesClient(budget=budget)
    with pytest.raises(BudgetExhausted):
        client.messages.create(model=CHAIN, messages=[], max_tokens=1000)
