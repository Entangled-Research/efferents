from __future__ import annotations

from types import SimpleNamespace

from efferents.agents.budget import CallUsage, cost_usd, model_for
from efferents.agents.model_client import (
    LiteLLMMessagesClient,
    RoutingMessagesClient,
    credentials_available,
    parse_chain,
    provider_for_model,
    required_key_env,
    resolve_chain,
)


def test_provider_and_credentials_follow_selected_model(monkeypatch):
    monkeypatch.setenv("EFFERENTS_MODEL", "openai/gpt-5")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert provider_for_model() == "openai"
    assert required_key_env() == "OPENAI_API_KEY"
    assert not credentials_available()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert credentials_available()


def test_local_provider_does_not_require_api_key(monkeypatch):
    monkeypatch.setenv("EFFERENTS_MODEL", "ollama/llama3.3")
    assert required_key_env() is None
    assert credentials_available()


def test_unknown_provider_uses_conventional_key_name():
    assert required_key_env("perplexity/sonar-pro") == "PERPLEXITY_API_KEY"


def test_role_model_override(monkeypatch):
    monkeypatch.setenv("EFFERENTS_MODEL", "openai/gpt-5-mini")
    monkeypatch.setenv("EFFERENTS_MODEL_CODER", "openai/gpt-5")
    assert model_for("writer") == "openai/gpt-5-mini"
    assert model_for("coder") == "openai/gpt-5"


def test_litellm_adapter_converts_text_usage_and_model(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="hello", tool_calls=[]),
            )],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
        )

    monkeypatch.setattr("litellm.completion", fake_completion)
    response = LiteLLMMessagesClient().messages.create(
        model="openai/gpt-5-mini",
        max_tokens=50,
        system=[{"type": "text", "text": "system", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    )
    assert calls[0]["model"] == "openai/gpt-5-mini"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "hi"},
    ]
    assert response.content[0].text == "hello"
    assert response.usage.input_tokens == 12
    assert response.usage.output_tokens == 3


def test_gpt56_chat_completions_uses_tool_compatible_effort(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason="stop",
                                     message=SimpleNamespace(content="ok", tool_calls=[]))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    monkeypatch.setattr("litellm.completion", fake_completion)
    client = LiteLLMMessagesClient().messages
    client.create(model="openai/gpt-5.6-sol", max_tokens=128,
                  messages=[{"role": "user", "content": "hello"}],
                  tools=[{"name": "lookup", "description": "Lookup", "input_schema": {"type": "object"}}])
    assert calls[0]["max_completion_tokens"] == 128
    assert "max_tokens" not in calls[0]
    assert calls[0]["reasoning_effort"] == "none"
    client.create(model="openai/gpt-5.6-luna", max_tokens=128,
                  messages=[{"role": "user", "content": "hello"}])
    assert calls[1]["reasoning_effort"] == "high"


def test_non_anthropic_model_uses_litellm_pricing():
    assert cost_usd("openai/gpt-5", CallUsage(1_000_000, 1_000_000)) > 0


def test_current_opus_pricing_is_used():
    assert cost_usd("claude-opus-4-7", CallUsage(1_000_000, 1_000_000)) == 30.0


def test_parse_chain_and_resolution(monkeypatch):
    assert parse_chain(" a , b ,, c ") == ["a", "b", "c"]
    monkeypatch.setenv(
        "EFFERENTS_MODEL", "moonshot/kimi-k2-thinking, claude-sonnet-5"
    )
    assert resolve_chain() == ["moonshot/kimi-k2-thinking", "claude-sonnet-5"]
    assert resolve_chain("openai/gpt-5") == ["openai/gpt-5"]


def test_chain_credentials_available_if_any_candidate_has_keys(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert credentials_available("moonshot/kimi-k2-thinking,claude-sonnet-5")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert not credentials_available("moonshot/kimi-k2-thinking,claude-sonnet-5")


def test_chain_cost_priced_at_preferred_entry():
    chained = cost_usd(
        "claude-sonnet-4-6,openai/gpt-5", CallUsage(1_000_000, 1_000_000)
    )
    assert chained == cost_usd("claude-sonnet-4-6", CallUsage(1_000_000, 1_000_000))


class _FakeDelegate:
    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result
        self._error = error
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._result


def _routing_client_with(delegates):
    client = RoutingMessagesClient()
    client.delegate_for = lambda provider: delegates[provider]
    return client


def test_routing_dispatches_by_provider_per_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    anthropic_delegate = _FakeDelegate(result="claude-response")
    litellm_delegate = _FakeDelegate(result="kimi-response")
    client = _routing_client_with(
        {"anthropic": anthropic_delegate, "moonshot": litellm_delegate}
    )
    assert client.messages.create(model="claude-sonnet-5", messages=[]) == "claude-response"
    assert client.messages.create(model="moonshot/kimi-k2-thinking", messages=[]) == "kimi-response"
    assert client.last_served_model == "moonshot/kimi-k2-thinking"


def test_anthropic_route_enables_automatic_prompt_caching(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    delegate = _FakeDelegate(result="ok")
    client = _routing_client_with({"anthropic": delegate})

    client.messages.create(
        model="claude-sonnet-4-6",
        system="stable instructions",
        messages=[{"role": "user", "content": "dynamic request"}],
    )

    assert delegate.calls[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_route_preserves_explicit_cache_strategy(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    delegate = _FakeDelegate(result="ok")
    client = _routing_client_with({"anthropic": delegate})
    system = [{
        "type": "text",
        "text": "stable instructions",
        "cache_control": {"type": "ephemeral"},
    }]

    client.messages.create(
        model="claude-sonnet-4-6", system=system, messages=[]
    )

    assert delegate.calls[0]["system"] == system
    assert "cache_control" not in delegate.calls[0]


def test_automatic_prompt_caching_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("EFFERENTS_CLAUDE_CACHE", "off")
    delegate = _FakeDelegate(result="ok")
    client = _routing_client_with({"anthropic": delegate})

    client.messages.create(model="claude-sonnet-4-6", messages=[])

    assert "cache_control" not in delegate.calls[0]


def test_non_anthropic_route_does_not_receive_claude_cache_control(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    delegate = _FakeDelegate(result="ok")
    client = _routing_client_with({"openai": delegate})

    client.messages.create(model="openai/gpt-5", messages=[])

    assert "cache_control" not in delegate.calls[0]


def test_routing_fails_over_on_provider_error(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    client = _routing_client_with({
        "moonshot": _FakeDelegate(error=RuntimeError("quota exhausted")),
        "anthropic": _FakeDelegate(result="fallback-response"),
    })
    response = client.messages.create(
        model="moonshot/kimi-k2-thinking,claude-sonnet-5", messages=[]
    )
    assert response == "fallback-response"
    assert client.last_served_model == "claude-sonnet-5"


def test_routing_skips_candidates_without_credentials(monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    kimi = _FakeDelegate(result="never")
    claude = _FakeDelegate(result="claude-response")
    client = _routing_client_with({"moonshot": kimi, "anthropic": claude})
    response = client.messages.create(
        model="moonshot/kimi-k2-thinking,claude-sonnet-5", messages=[]
    )
    assert response == "claude-response"
    assert kimi.calls == []


def test_routing_single_model_error_propagates_unchanged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    boom = ValueError("bad request")
    client = _routing_client_with({"anthropic": _FakeDelegate(error=boom)})
    try:
        client.messages.create(model="claude-sonnet-5", messages=[])
    except ValueError as exc:
        assert exc is boom
    else:
        raise AssertionError("expected the original error to propagate")


def test_routing_exhausted_chain_raises_with_detail(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "k")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = _routing_client_with({
        "moonshot": _FakeDelegate(error=RuntimeError("overloaded")),
        "anthropic": _FakeDelegate(result="never"),
    })
    try:
        client.messages.create(
            model="moonshot/kimi-k2-thinking,claude-sonnet-5", messages=[]
        )
    except RuntimeError as exc:
        assert "kimi-k2-thinking" in str(exc) and "claude-sonnet-5" in str(exc)
    else:
        raise AssertionError("expected chain exhaustion to raise")


def test_adapter_accepts_its_response_blocks_in_tool_followup():
    from efferents.agents.model_client import _convert_messages, _text_from_content
    blocks = [SimpleNamespace(type="text", text="Checking evidence"),
              SimpleNamespace(type="tool_use", id="call-1", name="lookup", input={"q": "test"})]
    converted = _convert_messages([
        {"role": "assistant", "content": blocks},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "found"}]},
    ])
    assert converted[0]["tool_calls"][0]["function"]["arguments"] == '{"q": "test"}'
    assert converted[1]["role"] == "tool"
    assert converted[1]["tool_call_id"] == "call-1"
    assert _text_from_content(blocks) == "Checking evidence"


def test_empty_reviewer_output_retries_with_string_content(tmp_path, monkeypatch):
    import json
    from efferents.agents.budget import BudgetTracker
    from efferents.agents.reviewer import review

    paper = tmp_path / "paper.md"
    paper.write_text("A bounded negative finding.")
    calls = []
    verdict = {"score": 4, "confidence": 4, "material_flaw": False,
               "material_flaw_reason": "", "summary": "Bounded evidence",
               "strengths": [], "weaknesses": [], "questions": []}

    def strict_completion(**kwargs):
        calls.append(kwargs)
        assert all(isinstance(message["content"], str) for message in kwargs["messages"])
        if len(calls) == 2:
            assert kwargs["messages"][-2] == {"role": "assistant", "content": ""}
            assert "failed JSON parsing" in kwargs["messages"][-1]["content"]
        return SimpleNamespace(
            choices=[SimpleNamespace(
                finish_reason="length" if len(calls) == 1 else "stop",
                message=SimpleNamespace(content=None if len(calls) == 1 else json.dumps(verdict),
                                        tool_calls=[]),
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    monkeypatch.setattr("litellm.completion", strict_completion)
    result = review(paper_path=paper, persona="critical", client=LiteLLMMessagesClient(),
                    budget=BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=1),
                    model="openai/gpt-5.6-sol")
    assert result.valid and result.score == 4
    assert len(calls) == 2
    assert all(call.get("max_completion_tokens", call.get("max_tokens")) == 8192 for call in calls)


def test_tool_only_and_null_assistant_content_remains_string():
    from efferents.agents.model_client import _convert_messages

    messages = _convert_messages([
        {"role": "assistant", "content": None},
        {"role": "assistant", "content": [
            SimpleNamespace(type="tool_use", id="call-1", name="lookup", input={"q": "test"})
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "found"}
        ]},
    ])
    assert messages[0] == {"role": "assistant", "content": ""}
    assert messages[1]["content"] == ""
    assert messages[1]["tool_calls"][0]["id"] == messages[2]["tool_call_id"] == "call-1"


def test_review_and_rebuttal_allow_reasoning_without_changing_explicit_limits(tmp_path):
    import json
    from efferents.agents import reviewer, rebuttal
    from efferents.agents.model_client import default_text_output_tokens

    paper = tmp_path / "paper.md"
    paper.write_text("A bounded finding.")
    calls = []
    verdict = {"score": 4, "confidence": 4, "material_flaw": False,
               "material_flaw_reason": "", "summary": "Bounded evidence"}

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(verdict))],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    budget = SimpleNamespace(record=lambda **kwargs: None)
    for model, limit in (("openai/gpt-5.6-sol", 8192), ("openai/gpt-5.6-luna", 8192),
                         ("openai/gpt-4.1-mini", 2048), ("claude-sonnet-4-6", 2048)):
        for explicit in (None, 512):
            reviewer.review(paper_path=paper, persona="critical", client=client,
                            budget=budget, model=model, max_tokens=explicit)
            rebuttal.write_rebuttal(paper_path=paper, reviews=[], client=client,
                                    budget=budget, model=model, max_tokens=explicit)
            assert all(call["max_tokens"] == (explicit or limit) for call in calls[-2:])
    assert default_text_output_tokens("claude-sonnet-4-6,openai/gpt-5.6-sol") == 8192
