"""ensure_runs_table provisions a domain-agnostic runs schema from LabConfig."""
from __future__ import annotations

import sqlite3
from pathlib import Path


from efferents.migrations.runner import ensure_runs_table
from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Panel, Source,
)


def _cfg(tmp_path: Path, headline="synthetic_loss", panels=()):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "c.yaml").touch()
    return LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(
            run_command="echo {config_path}", smoke_command=None,
            config_template=src / "c.yaml",
        ),
        metrics=Metrics(
            headline=Headline(column=headline, direction="min"),
            panels=tuple(Panel(column=c, label=c) for c in panels),
        ),
        budget=Budget(),
    )


def _cols(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()


def test_creates_runs_table_with_base_columns(tmp_path):
    db = tmp_path / "state.db"
    ensure_runs_table(db, _cfg(tmp_path))
    cols = _cols(db)
    assert {"run_id", "started_at", "ended_at", "config_path",
            "campaign_id", "researcher_mode", "student_id",
            "git_commit", "duration_seconds"}.issubset(cols)


def test_creates_metric_columns_from_config(tmp_path):
    db = tmp_path / "state.db"
    ensure_runs_table(db, _cfg(tmp_path, headline="loss", panels=("loss", "accuracy")))
    cols = _cols(db)
    assert "loss" in cols
    assert "accuracy" in cols


def test_idempotent_on_second_call(tmp_path):
    db = tmp_path / "state.db"
    cfg = _cfg(tmp_path, headline="loss")
    ensure_runs_table(db, cfg)
    cols_before = _cols(db)
    ensure_runs_table(db, cfg)
    cols_after = _cols(db)
    assert cols_before == cols_after


def test_adds_new_metric_column_to_existing_table(tmp_path):
    db = tmp_path / "state.db"
    ensure_runs_table(db, _cfg(tmp_path, headline="loss"))
    ensure_runs_table(db, _cfg(tmp_path, headline="loss", panels=("accuracy",)))
    cols = _cols(db)
    assert "loss" in cols
    assert "accuracy" in cols


def test_leaves_unrelated_existing_columns_untouched(tmp_path):
    db = tmp_path / "state.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, "
        "legacy_col TEXT)"
    )
    conn.commit()
    conn.close()
    ensure_runs_table(db, _cfg(tmp_path, headline="loss"))
    cols = _cols(db)
    assert "legacy_col" in cols
    assert "loss" in cols


def test_runs_table_primary_key_is_run_id(tmp_path):
    db = tmp_path / "state.db"
    ensure_runs_table(db, _cfg(tmp_path))
    conn = sqlite3.connect(db)
    try:
        pk_cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)") if row[5] == 1]
        assert pk_cols == ["run_id"]
    finally:
        conn.close()
