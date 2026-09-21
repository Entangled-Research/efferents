from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

from efferents import lab as lab_mod
from efferents.dashboard import reader
from efferents.lab import LabConfig
from efferents.migrations.runner import ensure_runs_table


TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "efferents" / "templates" / "starter-evacuation-lab"
)


def starter(tmp_path: Path) -> tuple[Path, LabConfig]:
    sub = tmp_path / "starter"
    shutil.copytree(TEMPLATE, sub, ignore=shutil.ignore_patterns("artifacts", "__pycache__"))
    cfg = LabConfig.from_submission(sub)
    lab_mod.set_config(cfg)
    (sub / "lab").mkdir()
    ensure_runs_table(sub / "lab" / "runs.sqlite", cfg)
    return sub, cfg


def insert_result(db: Path, number: int, *, improvement: float = 5.0) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO runs(run_id,status,started_at,evacuation_improvement_pct,"
            "candidate_completion_rate) VALUES(?,?,?,?,?)",
            (f"seed-{number}", "succeeded", f"2026-09-15T00:{number:02}:00+00:00", improvement, 1.0),
        )


def test_falsifier_waits_for_twelve_runs_then_decides(tmp_path):
    sub, cfg = starter(tmp_path)
    db = sub / "lab" / "runs.sqlite"
    for number in range(11):
        insert_result(db, number)
    early = reader.read_verdict(sub / "lab", cfg=cfg)
    assert {item["status"] for item in early["falsifiers"]} == {"insufficient_data"}

    insert_result(db, 11)
    decided = reader.read_verdict(sub / "lab", cfg=cfg)
    by_id = {item["id"]: item["status"] for item in decided["falsifiers"]}
    assert by_id == {
        "improvement-below-target": "fired",
        "completion-below-target": "survived",
    }
    assert decided["verdict"] == "falsified"


def test_default_experiment_is_real_fast_and_deterministic(tmp_path):
    sub, _cfg = starter(tmp_path)
    import sys

    sys.path.insert(0, str(sub / "src"))
    try:
        from simulate import paired_run
        import yaml

        config = yaml.safe_load((sub / "configs" / "default.yaml").read_text())
        started = time.monotonic()
        first = paired_run(config)
        elapsed = time.monotonic() - started
        second = paired_run(config)
    finally:
        sys.path.pop(0)
        sys.modules.pop("simulate", None)
        sys.modules.pop("policies", None)
    assert first == second
    assert elapsed < 30
    assert first[1].completion_rate == 1.0
    assert first[2].completion_rate == 1.0
