import sqlite3
from pathlib import Path

from efferents import lab as lab_mod
from efferents.lab import Budget, Executor, Headline, LabConfig, Metrics, Panel, Source
from efferents.agents.researcher import _saturation_metrics, _saturation_report
from efferents.agents.state import lab_paths


def _cfg(*, headline_col="loss", headline_dir="min", panels=(), bucket_axes=()):
    return LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(run_command="x {config_path}", smoke_command=None,
                          config_template=Path("c.yaml")),
        metrics=Metrics(
            headline=Headline(column=headline_col, direction=headline_dir),
            panels=panels, bucket_axes=bucket_axes,
        ),
        budget=Budget(),
    )


def test_saturation_metrics_dedups_headline_and_panels():
    lab_mod.set_config(_cfg(
        headline_col="loss", headline_dir="min",
        panels=(Panel(column="loss", label="Loss"),
                Panel(column="acc", label="Acc", direction="max")),
    ))
    try:
        assert _saturation_metrics() == [("loss", "min"), ("acc", "max")]
    finally:
        lab_mod._active = None


def test_saturation_report_no_config_is_empty(tmp_path):
    lab_mod._active = None
    db = tmp_path / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE runs (run_id TEXT, started_at TEXT, loss REAL)")
    conn.commit()
    conn.close()
    paths = lab_paths(tmp_path)
    assert _saturation_report(paths) == {"saturated_axes": [], "score": 0, "evidence": []}


def test_saturation_report_missing_db_is_empty(tmp_path):
    lab_mod.set_config(_cfg())
    try:
        paths = lab_paths(tmp_path / "nope")
        assert _saturation_report(paths) == {"saturated_axes": [], "score": 0, "evidence": []}
    finally:
        lab_mod._active = None


def test_saturation_report_buckets_by_config_axis(tmp_path):
    # One bucket axis ("model"); 6 distinct configs in the "a" bucket all with a
    # tightly-clustered min loss -> n_configs>=6 & floor_ratio<0.10 -> saturated.
    lab_mod.set_config(_cfg(headline_col="loss", headline_dir="min", bucket_axes=("model",)))
    try:
        db = tmp_path / "runs.sqlite"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE runs (run_id TEXT, started_at TEXT, config_path TEXT, "
            "model TEXT, loss REAL)"
        )
        for i in range(6):
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?)",
                (f"r{i}", f"2026-01-0{i+1}", f"cfg{i}.yaml", "a", 0.100 + i * 0.0001),
            )
        conn.commit()
        conn.close()
        paths = lab_paths(tmp_path)
        rep = _saturation_report(paths)
        assert rep["evidence"], "expected at least one bucket"
        ev = rep["evidence"][0]
        assert ev["bucket"] == "model=a"
        assert ev["n_configs"] == 6
        assert ev["saturated"] is True
        assert "model=a" in rep["saturated_axes"][0]
    finally:
        lab_mod._active = None
