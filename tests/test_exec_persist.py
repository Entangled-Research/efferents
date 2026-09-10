"""_persist_run_result and _execute_run live in efferents.exec."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path

from efferents.exec import RunResult, _persist_run_result


def test_persist_run_result_inserts_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT, synthetic_loss REAL, duration_seconds REAL, git_commit TEXT)"
    )
    conn.commit()
    conn.close()

    result = RunResult(
        ok=True,
        metrics={"synthetic_loss": 0.42},
        elapsed_s=12.3,
        git_commit="abc123",
    )
    _persist_run_result(result, "test-1", Path("configs/default.yaml"))

    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT run_id, synthetic_loss, duration_seconds, git_commit FROM runs"))
    conn.close()
    assert rows == [("test-1", 0.42, 12.3, "abc123")]


def test_persist_run_result_records_failed_attempt_without_scored_metrics(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT)"
    )
    conn.commit()
    conn.close()

    result = RunResult(ok=False, metrics=None, error="run failed")
    _persist_run_result(result, "test-2", Path("configs/x.yaml"))

    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT run_id, status, error FROM runs"))
    conn.close()
    assert rows == [("test-2", "failed", "run failed")]


def test_persist_run_result_adds_missing_column_and_retries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    conn = sqlite3.connect(db)
    # Pre-create table WITHOUT the synthetic_loss column — _persist_run_result
    # must ALTER + retry.
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT)"
    )
    conn.commit()
    conn.close()

    result = RunResult(
        ok=True,
        metrics={"synthetic_loss": 0.42},
        elapsed_s=1.2,
        git_commit=None,
    )
    _persist_run_result(result, "run-x", Path("configs/x.yaml"))

    conn = sqlite3.connect(db)
    rows = list(conn.execute("SELECT run_id, synthetic_loss FROM runs"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert rows == [("run-x", 0.42)]
    assert "synthetic_loss" in cols


def test_persist_run_result_preserves_observation_envelope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT, loss REAL)"
    )
    conn.commit()
    conn.close()

    result = RunResult(
        ok=True,
        metrics={"loss": 0.2},
        observations=[{
            "name": "control",
            "dimensions": {"variant": "control"},
            "metrics": {"loss": 0.3},
            "artifacts": [],
        }],
    )
    _persist_run_result(result, "run-observed", Path("configs/x.yaml"))

    conn = sqlite3.connect(db)
    stored = conn.execute(
        "SELECT observations_json FROM runs WHERE run_id = 'run-observed'"
    ).fetchone()[0]
    conn.close()
    assert '"variant": "control"' in stored


def _runs_table(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT, loss REAL)"
    )
    conn.commit()
    conn.close()


def _stored(db: Path, run_id: str, column: str) -> str:
    conn = sqlite3.connect(db)
    value = conn.execute(
        f"SELECT {column} FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    conn.close()
    return value


def test_persist_copies_artifacts_per_run_so_reruns_cannot_overwrite(
    tmp_path, monkeypatch, smoke_lab_config
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    _runs_table(db)
    grid = smoke_lab_config.source.dir / "native-artifacts" / "compare-seed0.png"
    grid.parent.mkdir(parents=True)
    grid.write_bytes(b"first-run")

    result = RunResult(ok=True, metrics={"loss": 0.1}, artifacts=[
        {"kind": "sample_grid", "path": str(grid)},
    ])
    _persist_run_result(result, "run-a", Path("configs/x.yaml"), db_path=db)
    grid.write_bytes(b"second-run")  # a re-run of the same parameters

    [record] = json.loads(_stored(db, "run-a", "artifacts_json"))
    copied = Path(record["path"])
    assert copied == tmp_path / "lab" / "artifacts" / "run-a" / "sample_grid" / "compare-seed0.png"
    assert copied.read_bytes() == b"first-run"
    assert record["source_path"] == str(grid)
    assert "missing" not in record


def test_persist_copies_nested_observation_artifacts_and_dedupes_basenames(
    tmp_path, monkeypatch, smoke_lab_config
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    _runs_table(db)
    src = smoke_lab_config.source.dir
    (src / "a").mkdir()
    (src / "b").mkdir()
    (src / "a" / "grid.png").write_bytes(b"arm-a")
    (src / "b" / "grid.png").write_bytes(b"arm-b")

    result = RunResult(ok=True, metrics={"loss": 0.1}, observations=[
        {"name": "a", "dimensions": {}, "metrics": {"loss": 0.1},
         "artifacts": [{"kind": "grid", "path": "a/grid.png"}]},
        {"name": "b", "dimensions": {}, "metrics": {"loss": 0.2},
         "artifacts": [{"kind": "grid", "path": "b/grid.png"}]},
    ])
    _persist_run_result(result, "run-b", Path("configs/x.yaml"), db_path=db)

    observations = json.loads(_stored(db, "run-b", "observations_json"))
    paths = [Path(o["artifacts"][0]["path"]) for o in observations]
    assert [p.name for p in paths] == ["grid.png", "grid-1.png"]
    assert all(p.parent == tmp_path / "lab" / "artifacts" / "run-b" / "grid" for p in paths)
    assert [p.read_bytes() for p in paths] == [b"arm-a", b"arm-b"]
    assert [o["artifacts"][0]["source_path"] for o in observations] == [
        "a/grid.png", "b/grid.png",
    ]


def test_persist_marks_missing_or_outside_artifacts_and_keeps_the_row(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lab").mkdir()
    db = tmp_path / "lab" / "runs.sqlite"
    _runs_table(db)
    outside = tmp_path.parent / "outside-artifact.png"
    outside.write_bytes(b"not ours")

    result = RunResult(ok=True, metrics={"loss": 0.1}, artifacts=[
        {"kind": "grid", "path": str(tmp_path / "never-written.png")},
        {"kind": "grid", "path": str(outside)},
    ])
    _persist_run_result(result, "run-c", Path("configs/x.yaml"), db_path=db)

    assert _stored(db, "run-c", "status") == "succeeded"
    records = json.loads(_stored(db, "run-c", "artifacts_json"))
    assert [r["missing"] for r in records] == [True, True]
    assert [r["path"] for r in records] == [r["source_path"] for r in records]
    assert not (tmp_path / "lab" / "artifacts").exists()
