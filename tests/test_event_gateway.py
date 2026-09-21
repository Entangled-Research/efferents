from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from deploy.event_gateway import app


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_ID", "night-1")
    monkeypatch.setenv("EVENT_ENROLLMENT_CODE", "join-secret-with-sufficient-entropy")
    monkeypatch.setenv("EVENT_ADMIN_KEY", "admin-secret-with-sufficient-entropy")
    monkeypatch.setenv("EVENT_PUBLIC_URL", "https://events.example")
    monkeypatch.setenv("EVENT_AZURE_OPENAI_BASE", "https://test.openai.azure.com/openai/v1")
    monkeypatch.setenv("EVENT_AZURE_OPENAI_API_KEY", "vendor-secret")
    for tier in ("FAST", "STANDARD", "DEEP"):
        monkeypatch.setenv(f"EVENT_AZURE_{tier}_DEPLOYMENT", f"{tier.lower()}-model")
        monkeypatch.setenv(f"EVENT_AZURE_{tier}_INPUT_USD_PER_MTOK", "1.0")
        monkeypatch.setenv(f"EVENT_AZURE_{tier}_OUTPUT_USD_PER_MTOK", "4.0")
    monkeypatch.setenv(
        "EVENT_EXPIRES_AT", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    )
    monkeypatch.setenv("EVENT_TOKEN_CAP_USD", "1.0")
    monkeypatch.setenv("EVENT_TOTAL_CAP_USD", "2.0")
    monkeypatch.setenv("EVENT_REQUESTS_PER_MINUTE", "30")
    monkeypatch.setenv("EVENT_STALE_AFTER_SECONDS", "60")
    value = app.Store(tmp_path / "event.sqlite")
    value.configure_event()
    return value


def join_payload(lab_id="lab-one"):
    return {
        "protocol": app.PROTOCOL,
        "event_id": "night-1",
        "enrollment_code": "join-secret-with-sufficient-entropy",
        "lab_id": lab_id,
        "domain": "routing",
        "topic": "evacuation",
        "approach": "local-cost",
    }


def snapshot(lab_id="lab-one", status="running"):
    return {
        "protocol": app.PROTOCOL,
        "event_id": "night-1",
        "lab_id": lab_id,
        "domain": "routing",
        "topic": "evacuation",
        "approach": "local-cost",
        "runtime_status": status,
        "last_activity_at": datetime.now(timezone.utc).isoformat(),
        "headline": {"name": "improvement", "direction": "max", "latest_value": 12.5},
        "run_count": 4,
        "verdict_status": "undecided",
        "budget_state": "available",
    }


def test_join_hashes_token_and_exposes_proxy_settings(store):
    result = store.join(join_payload())
    assert result["token"].startswith("evt_")
    assert result["api_base"] == "https://events.example/v1"
    assert set(result["models"]) == set(app.EVENT_MODELS)
    with store.connect() as conn:
        row = conn.execute("SELECT token_hash FROM tokens").fetchone()
    assert row["token_hash"] == hashlib.sha256(result["token"].encode()).hexdigest()
    assert result["token"] not in store.path.read_bytes().decode(errors="ignore")


def test_startup_rejects_weak_secrets_and_insecure_public_url(store, monkeypatch):
    monkeypatch.setenv("EVENT_ENROLLMENT_CODE", "short")
    with pytest.raises(RuntimeError, match="at least 24"):
        store.configure_event()
    monkeypatch.setenv("EVENT_ENROLLMENT_CODE", "join-secret-with-sufficient-entropy")
    monkeypatch.setenv("EVENT_PUBLIC_URL", "http://events.example")
    with pytest.raises(RuntimeError, match="HTTPS origin"):
        store.configure_event()
    monkeypatch.setenv("EVENT_PUBLIC_URL", "https://events.example")
    monkeypatch.setenv("EVENT_AZURE_DEEP_OUTPUT_USD_PER_MTOK", "")
    with pytest.raises(RuntimeError, match="numeric"):
        store.configure_event()


def test_snapshot_is_strict_append_only_and_idempotent(store):
    joined = store.join(join_payload())
    first = store.sync(joined["token"], snapshot(), "same-request")
    second = store.sync(joined["token"], snapshot(status="paused"), "same-request")
    assert first["sequence"] == second["sequence"]
    assert second["runtime_status"] == "running"
    assert second["received_at"] == first["received_at"]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshot_history").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM snapshot_current").fetchone()[0] == 1
    bad = snapshot()
    bad["source"] = "private.py"
    with pytest.raises(app.ApiError, match="unknown"):
        store.sync(joined["token"], bad, "bad-request")


def test_revocation_is_per_token_and_expiry_is_clear(store):
    first = store.join(join_payload("lab-one"))
    second = store.join(join_payload("lab-two"))
    store.revoke(first["token_id"])
    with pytest.raises(app.ApiError, match="revoked"):
        store.status(first["token"])
    assert store.status(second["token"])["status"] == "active"
    with store.connect() as conn:
        conn.execute(
            "UPDATE tokens SET expires_at=? WHERE token_id=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), second["token_id"]),
        )
    with pytest.raises(app.ApiError, match="expired"):
        store.status(second["token"])


def test_quota_and_rate_limit_are_enforced_before_upstream(store, monkeypatch):
    joined = store.join(join_payload())
    with pytest.raises(app.ApiError, match="quota exhausted"):
        store.reserve_model(joined["token"], 1.01)
    with store.connect() as conn:
        conn.execute("UPDATE tokens SET rate_count=0")
    monkeypatch.setenv("EVENT_REQUESTS_PER_MINUTE", "1")
    store.authenticate(joined["token"], rate_limit=True)
    with pytest.raises(app.ApiError, match="rate limited"):
        store.authenticate(joined["token"], rate_limit=True)


def test_network_marks_silent_running_lab_stale_without_deleting_history(store):
    joined = store.join(join_payload())
    store.sync(joined["token"], snapshot(), "heartbeat-1")
    old = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE snapshot_current SET received_at=?", (old,))
    network = store.network("night-1")
    assert network["labs"][0]["runtime_status"] == "stale"
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshot_history").fetchone()[0] == 1


def test_remote_exchange_consent_identity_provenance_and_cross_domain_cadence(store):
    def enroll(name, domain="routing", goal=""):
        return store.join({**join_payload(name), "domain": domain, "goal": goal, "share_findings": True})
    author = enroll("author", goal="Shared objective")
    collaborator = enroll("collaborator", domain="math", goal="Shared objective")
    outsider = enroll("outsider", domain="biology")
    request = {"protocol": app.PROTOCOL, "publications": [], "observed": []}
    measurement = {"kind": "publication", "body": "Reviewed paper: error = 0.01", "campaign_id": "paper-1",
                   "publication_status": "accepted", "journal": "Methods",
                   "review_scores": {"critical": 6, "neutral": 7, "optimistic": 8}}
    for kind in ("measurement", "hypothesis", "question", "discussion"):
        with pytest.raises(app.ApiError, match="direct messages"):
            store.exchange(author["token"], {**request, "publications": [{"kind": kind, "body": "No"}]})
    store.exchange(author["token"], {**request, "publications": [measurement]})
    incoming = store.exchange(collaborator["token"], request)["talks"]
    assert len(incoming) == 1 and incoming[0]["lab_id"] == "author"
    assert not store.network("night-1")["observations"]  # Delivery alone is not an observation receipt.
    store.exchange(collaborator["token"], {**request, "observed": [incoming[0]["id"]]})
    assert store.network("night-1")["observations"][0]["target"] == "collaborator"
    assert store.exchange(outsider["token"], request)["talks"] == []
    assert store.exchange(outsider["token"], request)["talks"] == []
    cross = store.exchange(outsider["token"], request)["talks"]
    assert cross[0]["track"] == "interdisciplinary"
    with pytest.raises(app.ApiError, match="invalid bounded"):
        store.exchange(author["token"], {**request, "publications": [{**measurement, "lab_id": "spoof"}]})
    nonparticipant = store.join(join_payload("private"))
    with pytest.raises(app.ApiError, match="not enabled"):
        store.exchange(nonparticipant["token"], request)
    store.revoke(collaborator["token_id"])
    with pytest.raises(app.ApiError, match="revoked"):
        store.exchange(collaborator["token"], request)


def test_leave_stops_future_writes_but_keeps_snapshot(store):
    joined = store.join(join_payload())
    store.sync(joined["token"], snapshot(status="stopped"), "final")
    assert store.leave(joined["token"])["status"] == "left"
    with pytest.raises(app.ApiError, match="left the event"):
        store.sync(joined["token"], snapshot(), "later")
    assert store.network("night-1")["labs"][0]["runtime_status"] == "stopped"


def test_retained_export_omits_per_token_accounting(store, capsys):
    joined = store.join(join_payload())
    store.sync(joined["token"], snapshot(), "heartbeat-1")
    assert app.admin(store, ["export"]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert "tokens" not in exported
    assert exported["event"]["token_count"] == 1
    assert exported["labs"][0]["lab_id"] == "lab-one"


def test_close_is_durable_and_purge_requires_closure(store, monkeypatch):
    joined = store.join(join_payload())
    store.sync(joined["token"], snapshot(), "heartbeat-1")
    with pytest.raises(app.ApiError, match="close the event"):
        store.purge_tokens("night-1")
    assert store.close_event("night-1") == 1
    with pytest.raises(app.ApiError, match="closed"):
        store.join(join_payload("new-lab"))
    with pytest.raises(app.ApiError, match="revoked"):
        store.status(joined["token"])
    monkeypatch.setenv("EVENT_ENROLLMENT_CODE", "different-strong-secret-after-restart")
    store.configure_event()
    assert store.network("night-1")["event"]["closed_at"] is not None
    assert store.purge_tokens("night-1") == 1
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshot_history").fetchone()[0] == 0
    assert store.network("night-1")["labs"][0]["lab_id"] == "lab-one"
    with pytest.raises(app.ApiError, match="closed"):
        store.join(join_payload("new-lab"))


def test_online_backup_is_private_and_restorable(store, tmp_path):
    joined = store.join(join_payload())
    store.sync(joined["token"], snapshot(), "heartbeat-1")
    backup = store.backup("night-1")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    restored = app.Store(tmp_path / "restored.sqlite")
    with sqlite3.connect(backup) as source, sqlite3.connect(restored.path) as target:
        source.backup(target)
    assert restored.network("night-1")["labs"][0]["lab_id"] == "lab-one"
    assert restored.network("night-1")["event"]["token_count"] == 1


@pytest.mark.parametrize("alias,deployment", [
    ("openai/event-fast", "fast-model"),
    ("openai/event-model", "real-model"),
    ("openai/event-deep", "deep-model"),
])
@pytest.mark.parametrize("with_tools", [False, True])
def test_model_proxy_uses_vendor_key_and_accounts_usage_without_storing_prompt(
    store, monkeypatch, alias, deployment, with_tools
):
    seen = {}

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            size = int(self.headers["Content-Length"])
            seen["authorization"] = self.headers.get("Authorization")
            seen["payload"] = json.loads(self.rfile.read(size))
            body = json.dumps({
                "id": "upstream-1", "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    joined = store.join(join_payload())
    monkeypatch.setenv("EVENT_AZURE_OPENAI_BASE", f"http://127.0.0.1:{upstream.server_address[1]}")
    monkeypatch.setenv("EVENT_AZURE_OPENAI_API_KEY", "vendor-secret")
    monkeypatch.setenv("EVENT_AZURE_STANDARD_DEPLOYMENT", "real-model")
    monkeypatch.setenv("EVENT_MAX_OUTPUT_TOKENS", "1000")
    app.Handler.store = store
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    prompt = "PRIVATE PROMPT MUST NOT BE STORED"
    payload = {
        "model": alias, "max_tokens": 50,
        "messages": [{"role": "user", "content": prompt}],
    }
    if with_tools:
        payload["tools"] = [{"type": "function", "function": {
            "name": "experiment", "parameters": {"type": "object"},
        }}]
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {joined['token']}"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            assert response.status == 200
            assert json.loads(response.read())["id"] == "upstream-1"
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()
    assert seen["authorization"] == "Bearer vendor-secret"
    assert seen["payload"]["model"] == deployment
    if alias == "openai/event-fast":
        assert seen["payload"]["max_tokens"] == 50
        assert "reasoning_effort" not in seen["payload"]
    else:
        assert seen["payload"]["max_completion_tokens"] == 50
        assert "max_tokens" not in seen["payload"]
        assert seen["payload"]["reasoning_effort"] == ("none" if with_tools else "medium")
    with store.connect() as conn:
        row = conn.execute("SELECT input_tokens,output_tokens,cost_usd FROM model_requests").fetchone()
    assert (row["input_tokens"], row["output_tokens"]) == (100, 20)
    assert prompt not in store.path.read_bytes().decode(errors="ignore")
    assert "vendor-secret" not in store.path.read_bytes().decode(errors="ignore")


def test_proxy_outage_releases_reserved_quota(store, monkeypatch):
    joined = store.join(join_payload())
    monkeypatch.setenv("EVENT_AZURE_OPENAI_BASE", "http://127.0.0.1:1")
    monkeypatch.setenv("EVENT_AZURE_OPENAI_API_KEY", "vendor-secret")
    monkeypatch.setenv("EVENT_AZURE_STANDARD_DEPLOYMENT", "real-model")
    monkeypatch.setenv("EVENT_MAX_OUTPUT_TOKENS", "1000")
    app.Handler.store = store
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    invalid = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
        data=json.dumps({"model": "openai/arbitrary", "max_tokens": 50, "messages": []}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {joined['token']}"},
    )
    request = urllib.request.Request(
        f"http://127.0.0.1:{proxy.server_address[1]}/v1/chat/completions",
        data=json.dumps({"model": "openai/event-model", "max_tokens": 50, "messages": []}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {joined['token']}"},
    )
    try:
        with pytest.raises(urllib.error.HTTPError) as invalid_exc:
            urllib.request.urlopen(invalid)
        assert invalid_exc.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 503
    finally:
        proxy.shutdown()
        proxy.server_close()
    with store.connect() as conn:
        row = conn.execute("SELECT reserved_usd,spent_usd FROM tokens").fetchone()
        assert row["reserved_usd"] == 0
        assert row["spent_usd"] == 0


def test_real_model_client_can_use_event_proxy(store, monkeypatch):
    from efferents.agents.model_client import make_client

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            size = int(self.headers["Content-Length"])
            json.loads(self.rfile.read(size))
            body = json.dumps({
                "id": "upstream-probe", "object": "chat.completion", "created": 1,
                "model": "real-model",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    joined = store.join(join_payload())
    monkeypatch.setenv("EVENT_AZURE_OPENAI_BASE", f"http://127.0.0.1:{upstream.server_address[1]}")
    monkeypatch.setenv("EVENT_AZURE_OPENAI_API_KEY", "vendor-secret")
    monkeypatch.setenv("EVENT_AZURE_STANDARD_DEPLOYMENT", "real-model")
    app.Handler.store = store
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    monkeypatch.setenv("OPENAI_API_KEY", joined["token"])
    monkeypatch.setenv("EFFERENTS_API_BASE", f"http://127.0.0.1:{proxy.server_address[1]}/v1")
    monkeypatch.delenv("EFFERENTS_MODEL_PROVIDER", raising=False)
    try:
        response = make_client().messages.create(
            model="openai/event-model", max_tokens=8,
            messages=[{"role": "user", "content": "ping"}],
        )
        assert response.content[0].text == "pong"
        assert response.usage.output_tokens == 2
    finally:
        proxy.shutdown()
        upstream.shutdown()
        proxy.server_close()
        upstream.server_close()
