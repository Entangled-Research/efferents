"""Auditable removal from active research; evidence is never deleted."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def state(root: Path) -> dict:
    path = Path(root) / "lifecycle.json"
    if not path.exists():
        return {"ideas": {}}
    return json.loads(path.read_text())


def inactive(root: Path, student_id: str | None = None) -> bool:
    data = state(root)
    return bool(data.get("deleted") or (student_id is not None and student_id in data.get("ideas", {})))


def remove(root: Path, *, by: str, student_id: str | None = None) -> dict:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".lifecycle.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = state(root)
        record = {"by": by, "at": datetime.now(timezone.utc).isoformat()}
        if student_id is None:
            data.setdefault("deleted", record)
        else:
            data.setdefault("ideas", {}).setdefault(student_id, record)
        tmp = root / "lifecycle.json.tmp"
        with tmp.open("w") as handle:
            json.dump(data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        tmp.replace(root / "lifecycle.json")
    return data
