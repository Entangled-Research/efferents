"""Per-user registry mapping lab_id → submission metadata.

Single JSON file at ~/.efferents/registry.json (or $EFFERENTS_HOME/registry.json)
with fcntl-based locking for multi-CLI safety.
"""
from __future__ import annotations

import fcntl
import json
import os
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class LabRecord:
    lab_id: str
    submission_dir: str
    lab_root: str
    pid: int
    started_at: str
    status: str  # "running" | "stopped" | "crashed"


def _home() -> Path:
    return Path(os.environ.get("EFFERENTS_HOME", str(Path.home() / ".efferents")))


class Registry:
    """File-backed lab registry with fcntl locking. Single-host."""

    def __init__(self) -> None:
        self._home = _home()
        self._home.mkdir(parents=True, exist_ok=True)
        self._path = self._home / "registry.json"

    @contextmanager
    def _locked(self, *, write: bool = False):
        """Open + lock the registry file. Yields list[LabRecord].

        With ``write=True`` the (possibly mutated) list is written back and
        flushed to disk *before* the lock is released. Read-only callers must
        not write back: rewriting on every read is what let concurrent
        readers observe a truncated, not-yet-flushed file and "reset" it.
        """
        if not self._path.exists():
            self._path.write_text("[]")
        with open(self._path, "r+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                raw = fh.read()
                try:
                    data = json.loads(raw) if raw.strip() else []
                except json.JSONDecodeError:
                    backup = self._path.with_suffix(".json.corrupt")
                    backup.write_text(raw)
                    print(
                        f"efferents.registry: corrupted JSON at {self._path}, "
                        f"resetting (previous content saved to {backup})",
                        file=sys.stderr,
                    )
                    data = []
                records = [LabRecord(**r) for r in data]
                yield records
                if write:
                    fh.seek(0)
                    fh.truncate()
                    fh.write(json.dumps([asdict(r) for r in records], indent=2))
                    # Flush while still holding the lock; a buffered write that
                    # lands after LOCK_UN is visible to the next locker as an
                    # empty file.
                    fh.flush()
                    os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def list(self) -> list[LabRecord]:
        with self._locked() as records:
            return list(records)

    def get(self, lab_id: str) -> LabRecord | None:
        with self._locked() as records:
            for r in records:
                if r.lab_id == lab_id:
                    return r
            return None

    def register(self, rec: LabRecord) -> None:
        with self._locked(write=True) as records:
            records[:] = [r for r in records if r.lab_id != rec.lab_id]
            records.append(rec)

    def update_status(self, lab_id: str, status: str) -> bool:
        """Set ``status`` on an existing record. Returns False (no-op) if absent."""
        with self._locked(write=True) as records:
            for r in records:
                if r.lab_id == lab_id:
                    r.status = status
                    return True
            return False
