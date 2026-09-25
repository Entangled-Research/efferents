from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from efferents.lab import LabConfig
from efferents.onboarding import create_lab
from efferents.starter_catalog import DOCUMENTED
from efferents.journals import journal_for_domain


def test_numerical_analysis_has_a_dedicated_journal():
    assert journal_for_domain("numerical-analysis") == "Journal of Numerical Analysis"
    assert journal_for_domain("mathematics") == "Mathematics & Computation"


TEMPLATE = Path(__file__).resolve().parents[1] / "efferents" / "templates" / "starter-documented-lab"


def run_once(submission: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "run_experiment.py", "--config", str(submission / "configs" / "default.yaml")],
        cwd=submission / "src", capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_frozen_data_ships_with_the_template():
    manifest = json.loads((TEMPLATE / "data" / "manifest.json").read_text())
    for source in manifest["sources"]:
        assert (TEMPLATE / "data" / source["file"]).is_file(), source["file"]


@pytest.mark.parametrize("starter", sorted(DOCUMENTED))
def test_each_documented_starter_is_real_fast_and_deterministic(tmp_path, starter):
    submission = tmp_path / starter
    decisions = create_lab(submission, starter=starter)
    assert decisions["starter"] == starter
    cfg = LabConfig.from_submission(submission)
    assert cfg.domain == DOCUMENTED[starter]["domain"]
    assert cfg.peer_review_enabled
    if starter == "coloring":
        assert journal_for_domain(cfg.domain) == "Mathematics & Computation"
    assert yaml.safe_load((submission / "configs" / "default.yaml").read_text())["experiment"] == starter

    started = time.monotonic()
    first = run_once(submission)
    elapsed = time.monotonic() - started
    second = run_once(submission)

    assert elapsed < 30
    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["valid"] == 1
    assert {"improvement", "baseline", "candidate"} <= set(first["metrics"])
    artifacts = {item["kind"]: Path(item["path"]) for item in first["artifacts"]}
    assert artifacts["provenance"].is_file()
    assert artifacts[starter].suffix == ".svg"


def test_math_idea_starts_in_math_lab_and_targets_math_journal(tmp_path):
    submission = tmp_path / "math-idea"
    create_lab(submission, idea="A graph coloring bound for sparse graphs")

    cfg = LabConfig.from_submission(submission)
    assert cfg.domain == "mathematics"
    assert cfg.approach == DOCUMENTED["coloring"]["approach"]
    assert cfg.peer_review_enabled
    assert journal_for_domain(cfg.domain) == "Mathematics & Computation"


def test_lab_ids_come_from_the_owners_words(tmp_path, monkeypatch):
    from efferents.onboarding import suggest_lab_id

    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    assert suggest_lab_id(idea="Frequent rerouting: does it help?", taken=set()) == "frequent-rerouting-does-it-help"
    assert suggest_lab_id(name="My Lab", idea="ignored", taken=set()) == "my-lab"
    assert suggest_lab_id(starter="orbit", taken=set()) == "keep-a-planet-in-orbit"
    assert suggest_lab_id(starter="evacuation", taken=set()) == "congestion-aware-evacuation"
    assert suggest_lab_id(idea="Stable routes", taken={"stable-routes", "stable-routes-2"}) == "stable-routes-3"
    assert suggest_lab_id(idea="!!!", taken=set()) == "lab"
    long = suggest_lab_id(idea="one two three four five six seven eight", taken=set())
    assert long == "one-two-three-four-five-six" and len(long) <= 48

    decisions = create_lab(tmp_path / "lab", idea="Frequent rerouting", goal="Reduce congestion")
    assert decisions["lab_id"] == "frequent-rerouting"
    assert LabConfig.from_submission(tmp_path / "lab").lab_id == "frequent-rerouting"


def test_trial_reports_incomplete_evaluation_without_discarding_run(tmp_path, monkeypatch):
    from efferents.onboarding import trial
    import sqlite3
    monkeypatch.setenv('EFFERENTS_HOME', str(tmp_path / 'home'))
    submission = tmp_path / 'audit'
    create_lab(submission, starter='integration')
    path = submission / 'lab.yaml'
    cfg = yaml.safe_load(path.read_text())
    cfg['metrics']['constraints'] = [{'column': 'cases_completed', 'op': '>=', 'value': 100}]
    path.write_text(yaml.safe_dump(cfg))
    result = trial(submission, runs=1)
    assert not result['ok'] and result['runs'] == 1
    assert 'cases_completed' in result['evaluation_issues'][0]
    with sqlite3.connect(submission / 'lab/runs.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 1


def test_unmatched_idea_requires_a_real_evaluator(tmp_path):
    with pytest.raises(ValueError, match="No compatible starter"):
        create_lab(tmp_path / "unrelated", idea="Explain obscure reaction mechanisms")
    assert not (tmp_path / "unrelated").exists()
    result = create_lab(tmp_path / "explicit", starter="integration",
                        idea="Learn the workflow before building a chemistry evaluator")
    assert result["starter"] == "integration"
    assert "does not establish" in result["scope_notice"]
