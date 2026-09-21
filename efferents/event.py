"""Private event enrollment and sanitized heartbeat client.

The event credential is deliberately separate from ``lab.yaml`` and runtime
state.  It is loaded only by the Efferents process, mapped to the existing
OpenAI-compatible model settings, and never reaches experiment subprocesses.
"""
from __future__ import annotations

import json
import re
import os
import secrets
import shutil
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from efferents import lab as lab_mod
from efferents.dashboard import reader
from efferents.lab import LabConfig


PROTOCOL_VERSION = "efferents-event/v1"
EVENT_MODEL = "openai/event-model"
EVENT_MODELS = ("openai/event-fast", EVENT_MODEL, "openai/event-deep")
EVENT_ROLE_MODELS = {
    "router": "openai/event-fast",
    "librarian": EVENT_MODEL,
    "supervisor": "openai/event-deep",
    "analyst": "openai/event-deep",
    "coder": "openai/event-deep",
}
CREDENTIAL_FILE = ".efferents-event.json"
PENDING_FILE = ".efferents-event-pending.json"
_MAX_RESPONSE_BYTES = 256 * 1024


class EventClientError(RuntimeError):
    """A participant-facing event API failure with an optional HTTP status."""

    def __init__(self, message: str, *, status: int | None = None):
        self.status = status
        super().__init__(message)


def credential_path(submission: str | Path) -> Path:
    return Path(submission).expanduser().resolve() / CREDENTIAL_FILE


def pending_path(submission: str | Path) -> Path:
    return Path(submission).expanduser().resolve() / PENDING_FILE


def _atomic_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
        path.chmod(0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def load_credentials(submission: str | Path) -> dict[str, Any] | None:
    path = credential_path(submission)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise EventClientError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("protocol") != PROTOCOL_VERSION:
        raise EventClientError(f"{path.name} is not an {PROTOCOL_VERSION} credential")
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise EventClientError(f"{path.name} does not contain an event token")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise EventClientError(f"{path.name} must be private (chmod 600)")
    if payload.get("model") != EVENT_MODEL:
        raise EventClientError(f"{path.name} has an unsupported event model")
    _validate_models(payload.get("models"))
    event_url = payload.get("event_url")
    api_base = payload.get("api_base")
    if not isinstance(event_url, str) or not isinstance(api_base, str):
        raise EventClientError(f"{path.name} has invalid proxy URLs")
    if not _valid_event_origin(event_url) or api_base != f"{event_url.rstrip('/')}/v1":
        raise EventClientError(f"{path.name} has an invalid or mismatched proxy URL")
    return payload


def _validate_models(value: Any) -> dict[str, dict[str, float]]:
    if not isinstance(value, dict) or set(value) != set(EVENT_MODELS):
        raise EventClientError("event model pricing is missing or invalid")
    for alias, prices in value.items():
        if not isinstance(prices, dict) or set(prices) != {"input", "output"}:
            raise EventClientError(f"event pricing is invalid for {alias}")
        if any(not isinstance(p, (int, float)) or isinstance(p, bool) or not 0 < p < 1000
               for p in prices.values()):
            raise EventClientError(f"event pricing is invalid for {alias}")
    return value


def configure_model_environment(submission: str | Path) -> bool:
    """Load a joined event's proxy settings into the daemon environment."""
    credential = load_credentials(submission)
    if credential is None:
        return False
    # Every role stays on the capped proxy. Operator-published tiers drive
    # local estimates; the server remains authoritative for actual spend.
    from efferents.agents.budget import ROLE_MODEL

    os.environ.pop("EFFERENTS_MODEL_PROVIDER", None)
    os.environ["EFFERENTS_EVENT_PROXY_ACTIVE"] = "1"
    for role in set(ROLE_MODEL) | {"router"}:
        os.environ[f"EFFERENTS_MODEL_{role.upper()}"] = EVENT_ROLE_MODELS.get(role, EVENT_MODEL)
    os.environ["EFFERENTS_MODEL"] = EVENT_MODEL
    os.environ["EFFERENTS_EVENT_MODEL_PRICING"] = json.dumps(credential["models"], sort_keys=True)
    os.environ["EFFERENTS_API_BASE"] = str(credential["api_base"])
    os.environ["OPENAI_API_KEY"] = str(credential["token"])
    return True


def _endpoint(base_url: str, suffix: str) -> str:
    return f"{base_url.rstrip('/')}/event/v1/{suffix.lstrip('/')}"


def _valid_event_origin(value: str) -> bool:
    parsed = urlsplit(value)
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
    )


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: float = 8.0,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Accept": "application/json", "User-Agent": "efferents-event/1"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(raw) > _MAX_RESPONSE_BYTES:
                raise EventClientError("event server response was too large")
    except urllib.error.HTTPError as exc:
        raw = exc.read(_MAX_RESPONSE_BYTES)
        try:
            detail = json.loads(raw).get("error")
            if isinstance(detail, dict):
                detail = detail.get("message")
        except (json.JSONDecodeError, AttributeError):
            detail = None
        raise EventClientError(
            str(detail or f"event server returned HTTP {exc.code}"), status=exc.code
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EventClientError(f"event server is unreachable: {exc}") from exc
    try:
        value = json.loads(raw or b"{}")
    except json.JSONDecodeError as exc:
        raise EventClientError("event server returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise EventClientError("event server returned a non-object response")
    return value


def _profile(submission: Path, cfg: LabConfig) -> dict[str, Any]:
    try:
        raw = yaml.safe_load((submission / "lab.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    return {
        "lab_id": cfg.lab_id,
        "domain": cfg.domain,
        "topic": raw.get("topic") or cfg.subdomain,
        "approach": raw.get("approach"),
        **({"goal": cfg.research_goal} if cfg.research_goal else {}),
    }


def join(
    submission: str | Path,
    *,
    event_url: str,
    event_id: str,
    enrollment_code: str,
    share_findings: bool = False,
) -> dict[str, Any]:
    sub = Path(submission).expanduser().resolve()
    if not _valid_event_origin(event_url):
        raise EventClientError("event URL must be an HTTPS origin without a path or credentials")
    cfg = LabConfig.from_submission(sub)
    if credential_path(sub).exists():
        raise EventClientError(
            f"{CREDENTIAL_FILE} already exists; run `efferents event leave` before joining again"
        )
    request_payload = {
        "protocol": PROTOCOL_VERSION,
        "event_id": event_id,
        "enrollment_code": enrollment_code,
        **_profile(sub, cfg),
        "share_findings": share_findings,
    }
    response = _request("POST", _endpoint(event_url, "join"), payload=request_payload)
    expected_base = f"{event_url.rstrip('/')}/v1"
    if response.get("model") != EVENT_MODEL or response.get("api_base") != expected_base:
        raise EventClientError("event server returned an unexpected model or proxy URL")
    models = _validate_models(response.get("models"))
    if response.get("event_id") != event_id or response.get("lab_id") != cfg.lab_id:
        raise EventClientError("event server returned a mismatched lab identity")
    if not all(isinstance(response.get(field), str) and response[field] for field in ("token", "token_id", "expires_at")):
        raise EventClientError("event server did not issue a complete credential")
    credential = {
        "protocol": PROTOCOL_VERSION,
        "event_url": event_url.rstrip("/"),
        "event_id": response.get("event_id", event_id),
        "lab_id": cfg.lab_id,
        "token_id": response["token_id"],
        "token": response["token"],
        "expires_at": response["expires_at"],
        "model": EVENT_MODEL,
        "models": models,
        "api_base": expected_base,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "share_findings": share_findings,
    }
    _atomic_private_json(credential_path(sub), credential)
    return {key: value for key, value in credential.items() if key != "token"}


def exchange(submission: str | Path, *, lab_root: str | Path | None = None,
             force: bool = False) -> dict:
    """Publish allowed summaries and import peers at most once every two minutes."""
    sub = Path(submission).expanduser().resolve()
    credential = load_credentials(sub)
    if credential is None or not credential.get("share_findings"):
        return {"enabled": False}
    root = Path(lab_root).resolve() if lab_root else sub / "lab"
    cfg = LabConfig.from_submission(sub, check_paths=False)
    from efferents.agents.conference import _locked, _rows, _append, _talks
    from efferents.journal.reviews import is_publication
    with _locked(root) as directory:
        ledger = directory / "remote-visits.jsonl"
        visits = _rows(ledger)
        if not force and visits and time.time() - visits[-1]["at"] < 120:
            return {"enabled": True, "waiting": True}
        received = [row for row in _rows(directory / "inbox.jsonl") if row.get("transport") == "event"]
        received_ids = {row["id"] for row in received}
        allowed = {"kind", "body", "campaign_id", "publication_status", "review_scores", "journal"}
        publications = [{key: (value[:4000] if key == "body" else value)
                         for key, value in talk.items() if key in allowed}
                        for talk in _talks(cfg, sub, root)]
        try:
            response = _request("POST", _endpoint(credential["event_url"], "exchange"),
                                token=credential["token"], payload={"protocol": PROTOCOL_VERSION,
                                "publications": publications[:6], "observed": [r["id"] for r in received[-200:]]})
        except EventClientError as exc:
            _append(ledger, {"at": time.time(), "error": str(exc), "received": 0})
            return {"enabled": True, "error": str(exc)}
        count = 0
        for talk in response.get("talks", []):
            if (not isinstance(talk, dict) or not is_publication(talk) or not isinstance(talk.get("id"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", talk["id"])
                    or not isinstance(talk.get("body"), str) or len(talk["body"]) > 4000
                    or not isinstance(talk.get("lab_id"), str)):
                continue
            if talk["id"] not in received_ids:
                _append(directory / "inbox.jsonl", {**talk, "transport": "event",
                        "received_at": time.time(), "visit": response.get("visit")})
                received_ids.add(talk["id"])
                count += 1
        if count:
            # Acknowledge only after the inbox append was fsynced. This request
            # does not advance the conference cadence or fetch another batch.
            try:
                _request("POST", _endpoint(credential["event_url"], "exchange"), token=credential["token"],
                         payload={"protocol": PROTOCOL_VERSION, "publications": [],
                                  "observed": list(received_ids)[-200:], "receive": False})
            except EventClientError:
                pass  # the next visit retries receipts from the durable inbox
        _append(ledger, {"at": time.time(), "visit": response.get("visit"), "received": count})
        return {"enabled": True, "received": count}


def _budget_state(spent: float, cap: float) -> str:
    if cap <= 0 or spent >= cap:
        return "exhausted"
    if spent / cap >= 0.8:
        return "near_limit"
    return "available"


def build_snapshot(
    submission: str | Path,
    *,
    lab_root: str | Path | None = None,
    runtime_status: str | None = None,
) -> dict[str, Any]:
    sub = Path(submission).expanduser().resolve()
    root = Path(lab_root).expanduser().resolve() if lab_root else sub / "lab"
    cfg = LabConfig.from_submission(sub, check_paths=False)
    lab_mod.set_config(cfg)
    state = reader.read_state(root, cfg=cfg)
    runs = reader.read_runs(root, n=1, cfg=cfg)
    verdict = reader.read_verdict(root, cfg=cfg)
    profile = _profile(sub, cfg)
    credential = load_credentials(sub)
    recent = runs.get("runs") or []
    latest = recent[0].get("value") if recent else None
    state_file = root / "state.json"
    try:
        internal_state = json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        internal_state = {}
    if not isinstance(internal_state, dict):
        internal_state = {}
    observed_status = state.get("status") or "stopped"
    if observed_status == "running" and internal_state.get("status") == "paused":
        observed_status = "paused"
    last_activity = (
        datetime.fromtimestamp(state_file.stat().st_mtime, tz=timezone.utc).isoformat()
        if state_file.exists()
        else datetime.now(timezone.utc).isoformat()
    )
    spent = float(state["budget"].get("spent") or 0.0)
    cap = float(state["budget"].get("cap") or 0.0)
    return {
        "protocol": PROTOCOL_VERSION,
        "event_id": credential["event_id"] if credential else "",
        **profile,
        "runtime_status": runtime_status or observed_status,
        "last_activity_at": last_activity,
        "headline": {
            "name": cfg.metrics.headline.column,
            "direction": cfg.metrics.headline.direction,
            "latest_value": latest,
        },
        "run_count": int(runs.get("history", {}).get("total") or 0),
        "verdict_status": verdict.get("verdict") or "undecided",
        "budget_state": _budget_state(spent, cap),
    }


def sync(
    submission: str | Path,
    *,
    lab_root: str | Path | None = None,
    runtime_status: str | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    sub = Path(submission).expanduser().resolve()
    credential = load_credentials(sub)
    if credential is None:
        if quiet:
            return {"joined": False}
        raise EventClientError("this lab has not joined an event")
    queued = pending_path(sub)
    snapshot = build_snapshot(sub, lab_root=lab_root, runtime_status=runtime_status)
    try:
        previous = json.loads(queued.read_text())
    except (OSError, json.JSONDecodeError):
        previous = {}
    key = (
        previous.get("idempotency_key")
        if previous.get("snapshot") == snapshot
        else None
    ) or f"{credential['token_id']}:{secrets.token_urlsafe(18)}"
    _atomic_private_json(queued, {"idempotency_key": key, "snapshot": snapshot})
    root = Path(lab_root).expanduser().resolve() if lab_root else sub / "lab"
    retry_state = root / "event_sync.json"
    try:
        retry = json.loads(retry_state.read_text())
    except (OSError, json.JSONDecodeError):
        retry = {}
    if quiet and float(retry.get("next_attempt_epoch") or 0) > time.time():
        return {"joined": True, "queued": True, "backing_off": True}
    try:
        response = _request(
            "POST",
            _endpoint(str(credential["event_url"]), "sync"),
            payload=snapshot,
            token=str(credential["token"]),
            idempotency_key=key,
        )
    except EventClientError:
        attempts = min(int(retry.get("attempts") or 0) + 1, 9)
        _atomic_private_json(retry_state, {
            "attempts": attempts,
            "next_attempt_epoch": time.time() + min(300, 2 ** attempts),
        })
        if quiet:
            return {"joined": True, "queued": True}
        raise
    try:
        queued.unlink()
    except FileNotFoundError:
        pass
    retry_state.unlink(missing_ok=True)
    return response


def status(submission: str | Path) -> dict[str, Any]:
    credential = load_credentials(submission)
    if credential is None:
        raise EventClientError("this lab has not joined an event")
    return _request(
        "GET",
        _endpoint(str(credential["event_url"]), "status"),
        token=str(credential["token"]),
    )


def leave(submission: str | Path, *, lab_root: str | Path | None = None) -> dict[str, Any]:
    sub = Path(submission).expanduser().resolve()
    credential = load_credentials(sub)
    if credential is None:
        raise EventClientError("this lab has not joined an event")
    try:
        sync(sub, lab_root=lab_root, runtime_status="stopped")
    except EventClientError:
        # Leaving still revokes future writes even when the final status update
        # could not be delivered.
        pass
    response = _request(
        "POST",
        _endpoint(str(credential["event_url"]), "leave"),
        payload={"protocol": PROTOCOL_VERSION, "event_id": credential["event_id"]},
        token=str(credential["token"]),
    )
    credential_path(sub).unlink(missing_ok=True)
    pending_path(sub).unlink(missing_ok=True)
    return response


def doctor(submission: str | Path, *, run_smoke: bool = True) -> list[dict[str, Any]]:
    """Run local/event preflight checks; the smoke command never calls a model."""
    sub = Path(submission).expanduser().resolve()
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("python", sys.version_info >= (3, 10), sys.version.split()[0])
    add("git", shutil.which("git") is not None, shutil.which("git") or "not found")
    add("uv", shutil.which("uv") is not None, shutil.which("uv") or "not found")
    try:
        cfg = LabConfig.from_submission(sub)
    except Exception as exc:
        add("submission", False, str(exc))
        cfg = None
    else:
        add("submission", True, f"lab_id={cfg.lab_id}")
    writable = os.access(sub, os.W_OK) and (not (sub / "lab").exists() or os.access(sub / "lab", os.W_OK))
    add("writable", writable, str(sub))
    try:
        remote = status(sub)
    except EventClientError as exc:
        add("event", False, str(exc))
    else:
        add("event", remote.get("status") == "active", f"token={remote.get('status', 'unknown')}")
    credential = load_credentials(sub)
    if credential is not None:
        try:
            models = _request(
                "GET", f"{credential['api_base']}/models", token=str(credential["token"])
            )
        except EventClientError as exc:
            add("proxy", False, str(exc))
        else:
            names = {item.get("id") for item in models.get("data", []) if isinstance(item, dict)}
            add("proxy", EVENT_MODEL in names, "event model route reachable")
    if run_smoke and cfg is not None:
        try:
            lab_mod.set_config(cfg)
            from efferents.exec import _execute_run

            result = _execute_run(cfg.executor.config_template, smoke=True)
        except Exception as exc:
            add("smoke", False, str(exc))
        else:
            add("smoke", result.ok, result.error or f"elapsed={result.elapsed_s or 0:.3f}s")
    return checks
