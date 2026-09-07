"""Owner notifications: macOS banner, ntfy.sh push, and a generic webhook.

ntfy.sh is unauthenticated by default. The topic acts as a shared secret —
choose something unguessable and put it in NTFY_TOPIC env var. Subscribe to it
on the iPhone via the ntfy iOS app.

The webhook (``EFFERENTS_WEBHOOK_URL``) receives ``POST`` JSON
``{"title", "message", "lab_id", "ts"}`` so a funder can route lab events
into Slack, PagerDuty, e-mail, or their own dashboard without a phone app.
All channels are best-effort: ``notify_all`` never raises.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any

import urllib.error
import urllib.request


def _ascript_str(s: str) -> str:
    """Quote a Python string for embedding in an AppleScript literal."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def notify_macos(title: str, message: str, *, sound: bool = False) -> None:
    """Fire a macOS Banner notification via osascript. Best-effort, never raises."""
    sound_part = ' sound name "Glass"' if sound else ""
    script = f"display notification {_ascript_str(message[:300])} with title {_ascript_str(title[:80])}{sound_part}"
    try:
        subprocess.run(["osascript", "-e", script], check=False, timeout=5, capture_output=True)
    except Exception:
        pass


def notify_ntfy(message: str, *, title: str | None = None, priority: int = 3) -> bool:
    """POST to ntfy.sh with topic from $NTFY_TOPIC. Returns True on 2xx."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return False
    url = f"https://ntfy.sh/{topic}"
    headers: dict[str, str] = {"Priority": str(priority)}
    if title:
        headers["Title"] = title
    req = urllib.request.Request(
        url, data=message.encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def notify_webhook(title: str, message: str, *, lab_id: str | None = None) -> bool:
    """POST ``{title, message, lab_id, ts}`` to $EFFERENTS_WEBHOOK_URL. True on 2xx."""
    url = os.environ.get("EFFERENTS_WEBHOOK_URL", "").strip()
    if not url:
        return False
    payload = {
        "title": title,
        "message": message,
        "lab_id": lab_id,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


def notify_all(
    title: str,
    message: str,
    *,
    sound: bool = False,
    priority: int = 3,
    lab_id: str | None = None,
) -> dict[str, Any]:
    """Fire macOS + ntfy.sh + webhook sequentially; each is best-effort, none raise."""
    result: dict[str, Any] = {"ntfy_sent": False, "webhook_sent": False}
    try:
        notify_macos(title, message, sound=sound)
    except Exception:
        pass
    try:
        result["ntfy_sent"] = notify_ntfy(message, title=title, priority=priority)
    except Exception:
        pass
    try:
        result["webhook_sent"] = notify_webhook(title, message, lab_id=lab_id)
    except Exception:
        pass
    return result
