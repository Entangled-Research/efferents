from __future__ import annotations

import json
import os
import shutil
import stat
from pathlib import Path

import pytest

from efferents import event


SAMPLE = Path(__file__).parent / "fixtures" / "sample_submission"
MODELS = {alias: {"input": 1.0, "output": 4.0} for alias in event.EVENT_MODELS}


def make_submission(tmp_path: Path) -> Path:
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    return sub


def test_join_writes_mode_600_without_printing_or_storing_in_lab_yaml(tmp_path, monkeypatch):
    sub = make_submission(tmp_path)
    secret = "evt_super-secret"

    def fake_request(method, url, **kwargs):
        assert kwargs["payload"]["enrollment_code"] == "enroll"
        return {
            "event_id": "night-1", "lab_id": "sample-conjecture",
            "token_id": "tok_1", "token": secret,
            "expires_at": "2099-01-01T00:00:00+00:00", "model": "openai/event-model",
            "models": MODELS,
            "api_base": "https://events.example/v1",
        }

    monkeypatch.setattr(event, "_request", fake_request)
    visible = event.join(sub, event_url="https://events.example", event_id="night-1", enrollment_code="enroll")
    path = event.credential_path(sub)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "token" not in visible
    assert secret not in (sub / "lab.yaml").read_text()
    assert json.loads(path.read_text())["token"] == secret


def test_event_settings_use_existing_openai_client_boundary(tmp_path, monkeypatch):
    from efferents.agents.budget import ROLE_MODEL

    for key in ("EFFERENTS_MODEL", "EFFERENTS_API_BASE", "OPENAI_API_KEY", "EFFERENTS_EVENT_PROXY_ACTIVE"):
        monkeypatch.setenv(key, os.environ.get(key, ""))
    for role in ROLE_MODEL:
        key = f"EFFERENTS_MODEL_{role.upper()}"
        monkeypatch.setenv(key, os.environ.get(key, ""))
    sub = make_submission(tmp_path)
    event._atomic_private_json(event.credential_path(sub), {
        "protocol": event.PROTOCOL_VERSION,
        "token": "evt_x",
        "event_url": "https://events.example",
        "model": "openai/event-model",
        "models": MODELS,
        "api_base": "https://events.example/v1",
    })
    monkeypatch.setenv("EFFERENTS_MODEL_SUPERVISOR", "claude-sonnet-4-6")
    monkeypatch.setenv("EFFERENTS_MODEL_PROVIDER", "anthropic")
    assert event.configure_model_environment(sub)
    assert os.environ["OPENAI_API_KEY"] == "evt_x"
    assert os.environ["EFFERENTS_API_BASE"] == "https://events.example/v1"
    assert os.environ["EFFERENTS_MODEL_STUDENT"] == "openai/event-model"
    assert os.environ["EFFERENTS_MODEL_ROUTER"] == "openai/event-fast"
    assert os.environ["EFFERENTS_MODEL_LIBRARIAN"] == "openai/event-model"
    assert os.environ["EFFERENTS_MODEL_SUPERVISOR"] == "openai/event-deep"
    assert os.environ["EFFERENTS_MODEL_ANALYST"] == "openai/event-deep"
    assert json.loads(os.environ["EFFERENTS_EVENT_MODEL_PRICING"]) == MODELS
    assert os.environ["EFFERENTS_EVENT_PROXY_ACTIVE"] == "1"
    assert "EFFERENTS_MODEL_PROVIDER" not in os.environ
    from efferents.exec import _subprocess_env

    assert "OPENAI_API_KEY" not in _subprocess_env(())

    from efferents.agents import model_client
    from efferents.agents.budget import billing_model

    seen = {}

    def fake_call(_self, **kwargs):
        seen["model"] = kwargs["model"]
        return object()

    monkeypatch.setattr(model_client._Messages, "create", fake_call)
    client = model_client.make_client()
    client.messages.create(
        model="claude-sonnet-4-6", max_tokens=1,
        messages=[{"role": "user", "content": "ping"}],
    )
    assert seen["model"] == "openai/event-model"
    assert billing_model(client, "claude-sonnet-4-6") == "openai/event-model"

    from efferents.agents.budget import CallUsage, cost_usd
    assert cost_usd("openai/event-deep", CallUsage(1_000_000, 1_000_000)) == 5.0


def test_offline_sync_keeps_latest_sanitized_snapshot(tmp_path, monkeypatch):
    sub = make_submission(tmp_path)
    (sub / "lab").mkdir()
    event._atomic_private_json(event.credential_path(sub), {
        "protocol": event.PROTOCOL_VERSION,
        "event_url": "https://offline.invalid",
        "event_id": "night-1",
        "lab_id": "sample-conjecture",
        "token_id": "tok_1",
        "token": "evt_x",
        "model": "openai/event-model",
        "models": MODELS,
        "api_base": "https://offline.invalid/v1",
        "expires_at": "2099-01-01T00:00:00+00:00",
    })
    monkeypatch.setattr(event, "_request", lambda *a, **k: (_ for _ in ()).throw(event.EventClientError("offline")))
    result = event.sync(sub, quiet=True)
    assert result == {"joined": True, "queued": True}
    pending = json.loads(event.pending_path(sub).read_text())
    queued = pending["snapshot"]
    assert pending["idempotency_key"].startswith("tok_1:")
    assert set(queued) == {
        "protocol", "event_id", "lab_id", "domain", "topic", "approach",
        "runtime_status", "last_activity_at", "headline", "run_count",
        "verdict_status", "budget_state",
    }
    assert "token" not in queued and "source" not in queued and "prompt" not in queued


def test_heartbeat_refreshes_after_success_and_reuses_key_after_failed_delivery(
    tmp_path, monkeypatch
):
    sub = make_submission(tmp_path)
    (sub / "lab").mkdir()
    (sub / "lab" / "state.json").write_text("{}")
    event._atomic_private_json(event.credential_path(sub), {
        "protocol": event.PROTOCOL_VERSION,
        "event_url": "https://events.example",
        "event_id": "night-1",
        "lab_id": "sample-conjecture",
        "token_id": "tok_1",
        "token": "evt_x",
        "model": "openai/event-model",
        "models": MODELS,
        "api_base": "https://events.example/v1",
        "expires_at": "2099-01-01T00:00:00+00:00",
    })
    keys = []

    def deliver(_method, _url, **kwargs):
        keys.append(kwargs["idempotency_key"])
        return {"sequence": len(keys)}

    monkeypatch.setattr(event, "_request", deliver)
    event.sync(sub)
    event.sync(sub)
    assert keys[0] != keys[1]

    def fail(_method, _url, **kwargs):
        keys.append(kwargs["idempotency_key"])
        raise event.EventClientError("offline")

    monkeypatch.setattr(event, "_request", fail)
    with pytest.raises(event.EventClientError):
        event.sync(sub)
    monkeypatch.setattr(event, "_request", deliver)
    event.sync(sub)
    assert keys[2] == keys[3]


def test_join_rejects_non_https_and_cross_origin_proxy(tmp_path, monkeypatch):
    sub = make_submission(tmp_path)
    with pytest.raises(event.EventClientError, match="HTTPS origin"):
        event.join(sub, event_url="http://events.example", event_id="night-1", enrollment_code="enroll")

    def wrong_proxy(*_args, **_kwargs):
        return {
            "event_id": "night-1", "lab_id": "sample-conjecture",
            "token_id": "tok_1", "token": "evt_x",
            "expires_at": "2099-01-01T00:00:00+00:00", "model": "openai/event-model",
            "models": MODELS,
            "api_base": "https://other.example/v1",
        }

    monkeypatch.setattr(event, "_request", wrong_proxy)
    with pytest.raises(event.EventClientError, match="unexpected model or proxy URL"):
        event.join(sub, event_url="https://events.example", event_id="night-1", enrollment_code="enroll")
    assert not event.credential_path(sub).exists()


def test_provider_error_classifies_event_failures():
    from efferents.agents.model_client import classify_provider_error

    assert classify_provider_error(RuntimeError("event token revoked"))[0] == "event_revoked"
    assert classify_provider_error(RuntimeError("event token expired"))[0] == "event_expired"
    assert classify_provider_error(RuntimeError("event token quota exhausted"))[0] == "event_quota"


def test_starter_command_creates_complete_ignored_local_boundary(tmp_path):
    from efferents.cli import main

    target = tmp_path / "starter"
    assert main(["starter", "evacuation", "--out", str(target)]) == 0
    assert (target / "src" / "run_experiment.py").is_file()
    # With no idea given, the directory the owner named becomes the lab id.
    assert (target / "lab.yaml").read_text().startswith("lab_id: starter\n")
    assert "lab_id: evacuation-starter" not in (target / "lab.yaml").read_text()
    other = tmp_path / "other-starter"
    assert main(["starter", "evacuation", "--out", str(other)]) == 0
    assert (other / "lab.yaml").read_text().splitlines()[0] != (target / "lab.yaml").read_text().splitlines()[0]
    assert (target / "popper-corpus" / "congestion-aware-evacuation" / "hypothesis.md").read_text() == (target / "hypothesis.md").read_text()
    ignored = (target / ".gitignore").read_text()
    for value in (".env", ".efferents-event.json", ".efferents-event-pending.json", "lab/"):
        assert value in ignored
    assert main(["validate", "--submission", str(target)]) == 0
