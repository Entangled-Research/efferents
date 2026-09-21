from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required to build the wheel")
def test_offline_event_bundle_has_saved_evidence_and_no_credentials(tmp_path):
    root = Path(__file__).resolve().parents[1]
    target = tmp_path / "fallback"
    subprocess.run(
        [sys.executable, str(root / "scripts" / "build_event_fallback.py"), "--out", str(target)],
        cwd=root, check=True, capture_output=True, text=True,
    )
    db = target / "evacuation-lab" / "lab" / "runs.sqlite"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs WHERE status='succeeded'").fetchone()[0] >= 1
    assert (target / "evacuation-lab" / "lab" / "progress.html").is_file()
    assert list((target / "evacuation-lab" / "lab" / "artifacts").glob("*.svg"))
    with zipfile.ZipFile(tmp_path / "fallback.zip") as bundle:
        names = bundle.namelist()
    assert any(name.endswith(".whl") for name in names)
    assert not any(".efferents-event" in name or "/.env" in name for name in names)
