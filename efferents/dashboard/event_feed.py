"""Optional read-only projection of remote event snapshots into the console."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from efferents.journals import journal_for_domain


def read_remote_event() -> dict[str, Any] | None:
    url = os.environ.get("EFFERENTS_EVENT_NETWORK_URL", "").strip()
    admin_key = os.environ.get("EFFERENTS_EVENT_ADMIN_KEY", "").strip()
    if not url or not admin_key:
        return None
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "X-Efferents-Event-Admin": admin_key,
            "User-Agent": "efferents-dashboard/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            payload = json.loads(response.read(512 * 1024))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {"available": False, "labs": [], "tokens": [], "event": {}}
    if not isinstance(payload, dict) or not isinstance(payload.get("labs"), list):
        return {"available": False, "labs": [], "tokens": [], "event": {}}
    return {**payload, "available": True}


def dashboard_payload(remote: dict[str, Any] | None) -> dict[str, Any]:
    if remote is None:
        return {"configured": False, "available": False, "labs": [], "tokens": [], "event": {}}
    labs = []
    for snapshot in remote.get("labs") or []:
        if not isinstance(snapshot, dict):
            continue
        headline = snapshot.get("headline") if isinstance(snapshot.get("headline"), dict) else {}
        labs.append({
            "lab_id": snapshot.get("lab_id"),
            "domain": snapshot.get("domain"),
            "journal": journal_for_domain(snapshot.get("domain") or ""),
            "topic": snapshot.get("topic"),
            "approach": snapshot.get("approach"),
            "goal": snapshot.get("goal", ""),
            "status": snapshot.get("runtime_status", "stale"),
            "last_activity": snapshot.get("last_activity_at"),
            "received_at": snapshot.get("received_at"),
            "headline": {
                "column": headline.get("name"),
                "direction": headline.get("direction"),
                "latest": headline.get("latest_value"),
                "observations": snapshot.get("run_count", 0),
            },
            "verdict": {
                "status": snapshot.get("verdict_status", "undecided"),
                "line": f"verdict: {snapshot.get('verdict_status', 'undecided')}",
            },
            "budget_state": snapshot.get("budget_state"),
            "remote": True,
            "read_only": True,
        })
    return {
        "configured": True,
        "available": bool(remote.get("available")),
        "labs": labs,
        "tokens": remote.get("tokens") or [],
        "event": remote.get("event") or {},
        "generated_at": remote.get("generated_at"),
        "findings": remote.get("findings") or [],
        "observations": remote.get("observations") or [],
    }
