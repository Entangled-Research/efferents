"""``cadence:`` block in lab.yaml and EFFERENTS_CADENCE_* overrides."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from efferents import lab as lab_mod
from efferents.lab import Cadence, LabConfig, SubmissionError, cadence_with_env

SMOKE = Path(__file__).resolve().parents[1] / "examples" / "smoke-lab"


def _submission(tmp_path: Path, cadence: dict | None) -> Path:
    sub = tmp_path / "sub"
    shutil.copytree(SMOKE, sub, ignore=shutil.ignore_patterns("lab", "__pycache__"))
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw.pop("cadence", None)
    if cadence is not None:
        raw["cadence"] = cadence
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return sub


def test_defaults_match_legacy_orchestrator_values(tmp_path):
    cfg = LabConfig.from_submission(_submission(tmp_path, None))
    assert cfg.cadence == Cadence()
    kw = cfg.cadence.as_kwargs()
    assert kw["runs_per_digest"] == 40 and kw["hours_per_paper"] == 6.0
    assert kw["empty_queue_sleep_s"] == 60.0 and kw["step_pause_s"] == 0.0


def test_event_cadence_parses(tmp_path):
    cfg = LabConfig.from_submission(_submission(tmp_path, {
        "runs_per_digest": 3, "hours_per_digest": 0.1667, "min_runs_for_digest": 1,
        "runs_per_paper": 5, "hours_per_paper": 0.5, "empty_queue_sleep_s": 20,
        "step_pause_s": 20, "researcher_min_interval_s": 360, "stall_hours": 0.25,
        "backoff_cap_s": 300,
    }))
    c = cfg.cadence
    assert c.runs_per_digest == 3 and isinstance(c.runs_per_digest, int)
    assert c.hours_per_digest == pytest.approx(0.1667)
    assert c.step_pause_s == 20.0 and c.stall_hours == 0.25 and c.backoff_cap_s == 300.0


@pytest.mark.parametrize("bad", [
    {"runs_per_digest": 0},
    {"hours_per_paper": -1},
    {"step_pause_s": -5},
    {"unknown_key": 1},
    {"runs_per_paper": "many"},
    {"runs_per_paper": 2.5},
])
def test_invalid_cadence_rejected(tmp_path, bad):
    with pytest.raises(SubmissionError):
        LabConfig.from_submission(_submission(tmp_path, bad))


def test_cadence_must_be_mapping(tmp_path):
    with pytest.raises(SubmissionError):
        LabConfig.from_submission(_submission(tmp_path, [1, 2]))


def test_env_overrides_win_over_yaml():
    base = Cadence(runs_per_digest=3, researcher_min_interval_s=0.0)
    out = cadence_with_env(base, {
        "EFFERENTS_CADENCE_RESEARCHER_MIN_INTERVAL_S": "600",
        "EFFERENTS_CADENCE_RUNS_PER_DIGEST": "7",
        "EFFERENTS_CADENCE_STALL_HOURS": "",  # blank means "not set"
    })
    assert out.researcher_min_interval_s == 600.0
    assert out.runs_per_digest == 7
    assert out.stall_hours is None


def test_env_override_validation():
    with pytest.raises(SubmissionError):
        cadence_with_env(Cadence(), {"EFFERENTS_CADENCE_RUNS_PER_PAPER": "-1"})


def test_orchestrator_loop_threads_cadence(tmp_path, monkeypatch):
    sub = _submission(tmp_path, {"runs_per_digest": 3, "step_pause_s": 20})
    cfg = LabConfig.from_submission(sub)
    lab_mod.set_config(cfg)
    monkeypatch.setenv("EFFERENTS_CADENCE_RESEARCHER_MIN_INTERVAL_S", "360")
    captured = {}

    class FakeOrch:
        def __init__(self, **kw):
            captured.update(kw)
            self.paths = None

        def run(self, *, max_iterations=None):
            captured["ran"] = max_iterations

    from efferents.agents import orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "Orchestrator", FakeOrch)
    monkeypatch.setattr("efferents.agents.progress.write_progress", lambda *a, **k: None)
    from efferents.cli import _orchestrator_loop
    _orchestrator_loop(
        lab_root=sub / "lab", context_dir=sub / "context", dry_run=True,
        max_iterations=1, submission_dir=sub,
    )
    assert captured["runs_per_digest"] == 3
    assert captured["step_pause_s"] == 20.0
    assert captured["researcher_min_interval_s"] == 360.0
    assert captured["submission_dir"] == sub
    assert captured["ran"] == 1
