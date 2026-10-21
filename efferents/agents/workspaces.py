"""Independent retained source snapshots for concurrently executing ideas.

These directories isolate relative outputs. They are not a security sandbox:
lab commands must use relative output paths to benefit from this isolation.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


def prepare_run_workspace(lab_root: Path, source: Path, proposal: dict[str, Any], run_id: str) -> Path:
    source = source.resolve()
    lab_root = lab_root.resolve()
    idea = str(proposal.get("student_id") or "primary")
    key = hashlib.sha256(idea.encode()).hexdigest()[:20]
    destination = lab_root / "workspaces" / key / run_id / "source"
    excluded = {".git", ".venv", "__pycache__", ".pytest_cache", ".env"}

    def ignore(directory: str, names: list[str]) -> list[str]:
        return [name for name in names if name in excluded or name.startswith(".env.") or
                (Path(directory) / name).resolve() == lab_root]

    destination.parent.mkdir(parents=True, exist_ok=False)
    try:
        shutil.copytree(source, destination, ignore=ignore)
        (destination.parent / "workspace.json").write_text(json.dumps({
            "student_id": idea, "source": str(source), "run_id": run_id,
        }, indent=2))
    except BaseException:
        shutil.rmtree(destination.parent)
        raise
    return destination
