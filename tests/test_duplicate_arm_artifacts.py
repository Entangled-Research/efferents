"""Byte-identical artifacts across observations (arms) are flagged after
artifacts are preserved: `flags_json.duplicate_artifacts` lists them, the
numeric `duplicate_arm_artifacts` column counts them (0 when none), a
notebook line is written, and the run is never failed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from efferents.exec import RunResult, _persist_run_result


@pytest.fixture
def lab(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    conn = sqlite3.connect(lab_dir / "runs.sqlite")
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT, loss REAL)"
    )
    conn.commit()
    conn.close()
    (lab_dir / "lab_notebook.md").write_text("# Lab notebook\n\n")
    return lab_dir


def _row(lab: Path, run_id: str) -> dict:
    conn = sqlite3.connect(lab / "runs.sqlite")
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())
    conn.close()
    return row


def _observation(name: str, dims: dict, artifacts: list[dict]) -> dict:
    return {"name": name, "dimensions": dims, "metrics": {"loss": 0.1}, "artifacts": artifacts}


def test_flags_identical_files_across_observations(lab, smoke_lab_config):
    src = smoke_lab_config.source.dir
    for arm in ("prior", "control", "other"):
        (src / arm).mkdir()
    (src / "prior" / "samples.pt").write_bytes(b"same-samples")
    (src / "control" / "samples.pt").write_bytes(b"same-samples")   # collapsed arm
    (src / "other" / "samples.pt").write_bytes(b"different")
    (src / "other" / "grid.png").write_bytes(b"same-samples")      # same bytes, other kind

    result = RunResult(ok=True, metrics={"loss": 0.1}, observations=[
        _observation("prior", {"arm": "prior"},
                     [{"kind": "sample_tensor", "path": "prior/samples.pt"}]),
        _observation("control", {"arm": "control"},
                     [{"kind": "sample_tensor", "path": "control/samples.pt"}]),
        _observation("other", {"arm": "other"},
                     [{"kind": "sample_tensor", "path": "other/samples.pt"},
                      {"kind": "sample_grid", "path": "other/grid.png"}]),
    ])
    _persist_run_result(result, "run-dup", Path("configs/x.yaml"), db_path=lab / "runs.sqlite")

    row = _row(lab, "run-dup")
    assert row["status"] == "succeeded"
    assert row["duplicate_arm_artifacts"] == 1
    assert json.loads(row["flags_json"]) == {"duplicate_artifacts": [{
        "kind": "sample_tensor",
        "observations": ["prior", "control"],
        "sha256": hashlib.sha256(b"same-samples").hexdigest(),
    }]}
    note = (lab / "lab_notebook.md").read_text()
    assert "run run-dup: 1 artifact(s) byte-identical across observations" in note
    assert "kind=sample_tensor observations=['prior', 'control']" in note


def test_ignores_identical_files_within_one_observation(lab, smoke_lab_config):
    src = smoke_lab_config.source.dir
    (src / "a").mkdir()
    (src / "b").mkdir()
    (src / "a" / "s0.pt").write_bytes(b"repeat")
    (src / "a" / "s1.pt").write_bytes(b"repeat")
    (src / "b" / "s0.pt").write_bytes(b"unique")

    result = RunResult(ok=True, metrics={"loss": 0.1}, observations=[
        _observation("a", {"arm": "a"}, [
            {"kind": "sample_tensor", "path": "a/s0.pt"},
            {"kind": "sample_tensor", "path": "a/s1.pt"},
        ]),
        _observation("b", {"arm": "b"}, [{"kind": "sample_tensor", "path": "b/s0.pt"}]),
    ])
    _persist_run_result(result, "run-ok", Path("configs/x.yaml"), db_path=lab / "runs.sqlite")

    row = _row(lab, "run-ok")
    assert row["duplicate_arm_artifacts"] == 0
    assert json.loads(row["flags_json"]) == {}
    assert "byte-identical" not in (lab / "lab_notebook.md").read_text()


def test_same_name_different_dimensions_are_different_observations(lab, smoke_lab_config):
    src = smoke_lab_config.source.dir
    (src / "s0").mkdir()
    (src / "s1").mkdir()
    (src / "s0" / "g.png").write_bytes(b"identical")
    (src / "s1" / "g.png").write_bytes(b"identical")

    result = RunResult(ok=True, metrics={"loss": 0.1}, observations=[
        _observation("arm", {"seed": 0}, [{"kind": "sample_grid", "path": "s0/g.png"}]),
        _observation("arm", {"seed": 1}, [{"kind": "sample_grid", "path": "s1/g.png"}]),
    ])
    _persist_run_result(result, "run-seeds", Path("configs/x.yaml"), db_path=lab / "runs.sqlite")
    assert _row(lab, "run-seeds")["duplicate_arm_artifacts"] == 1


def test_metric_present_as_zero_without_observations(lab):
    _persist_run_result(
        RunResult(ok=True, metrics={"loss": 0.3}), "run-plain", Path("configs/x.yaml"),
        db_path=lab / "runs.sqlite",
    )
    row = _row(lab, "run-plain")
    assert row["duplicate_arm_artifacts"] == 0
    assert row["flags_json"] == "{}"


def test_missing_artifacts_never_fail_the_run(lab):
    result = RunResult(ok=True, metrics={"loss": 0.1}, observations=[
        _observation("a", {}, [{"kind": "grid", "path": "/nonexistent/a.png"}]),
        _observation("b", {}, [{"kind": "grid", "path": "/nonexistent/b.png"}]),
    ])
    _persist_run_result(result, "run-missing", Path("configs/x.yaml"), db_path=lab / "runs.sqlite")
    row = _row(lab, "run-missing")
    assert row["status"] == "succeeded"
    assert row["duplicate_arm_artifacts"] == 0
