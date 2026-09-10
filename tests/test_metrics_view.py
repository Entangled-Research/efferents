import sqlite3
from pathlib import Path

from efferents import metrics_view as mv


def test_finite():
    assert mv.finite(0.04) == 0.04
    assert mv.finite(3) == 3.0
    assert mv.finite(True) is None
    assert mv.finite(float("nan")) is None
    assert mv.finite(float("inf")) is None
    assert mv.finite("0.04") is None
    assert mv.finite(None) is None


def test_discover_columns(tmp_path):
    db = tmp_path / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT, started_at TEXT, campaign_id TEXT, "
        "synthetic_loss REAL, coefficient REAL)"
    )
    conn.commit()
    conn.close()
    assert set(mv.discover_columns(db)) == {"synthetic_loss", "coefficient"}


def test_discover_columns_missing_db(tmp_path):
    assert mv.discover_columns(tmp_path / "nope.sqlite") == []


def test_discover_columns_db_without_runs_table(tmp_path):
    db = tmp_path / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.commit()
    conn.close()
    assert mv.discover_columns(db) == []


def test_best_run_min(smoke_lab_config):
    rows = [{"run_id": "a", "synthetic_loss": 0.08},
            {"run_id": "b", "synthetic_loss": 0.03},
            {"run_id": "c", "synthetic_loss": float("nan")}]
    assert mv.best_run(rows)["run_id"] == "b"


def test_best_run_empty_or_unscored(smoke_lab_config):
    assert mv.best_run([]) is None
    assert mv.best_run([{"run_id": "x", "synthetic_loss": None}]) is None


def test_best_run_max():
    from efferents import lab as lab_mod
    from efferents.lab import (
        Budget, Executor, Headline, LabConfig, Metrics, Source,
    )
    cfg = LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(run_command="x {config_path}", smoke_command=None,
                          config_template=Path("c.yaml")),
        metrics=Metrics(headline=Headline(column="accuracy", direction="max"),
                        panels=()),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    try:
        rows = [{"run_id": "a", "accuracy": 0.7}, {"run_id": "b", "accuracy": 0.9}]
        assert mv.best_run(rows)["run_id"] == "b"
    finally:
        lab_mod._active = None


def test_improved_min():
    assert mv.improved(0.10, 0.04, direction="min", epsilon=0.005) is True
    assert mv.improved(0.10, 0.099, direction="min", epsilon=0.005) is False
    assert mv.improved(None, 0.04, direction="min", epsilon=0.005) is True
    assert mv.improved(0.04, None, direction="min", epsilon=0.005) is False


def test_improved_max():
    assert mv.improved(0.80, 0.90, direction="max", epsilon=0.005) is True
    assert mv.improved(0.80, 0.802, direction="max", epsilon=0.005) is False
