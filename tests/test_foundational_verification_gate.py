from __future__ import annotations

from efferents.agents import executor
from efferents.agents.federation import (
    foundational_dependency_violations,
    is_reproduced,
    record_reproduction,
)
from efferents.agents.state import init_lab, lab_paths
from efferents.exec import RunResult


DEP = {"lab_id": "lab-a", "campaign_id": "paper-1", "why": "premise"}


def _proposal(**extra):
    value = {"name": "downstream", "foundational_external": [DEP]}
    value.update(extra)
    return value


def test_missing_pending_and_failed_dependencies_are_blocked(tmp_path):
    paper = tmp_path / "paper"
    assert foundational_dependency_violations(paper, _proposal())

    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="pending")
    assert foundational_dependency_violations(paper, _proposal())

    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="failed")
    assert foundational_dependency_violations(paper, _proposal())


def test_latest_verified_status_allows_use_and_later_failure_revokes_it(tmp_path):
    paper = tmp_path / "paper"
    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="failed")
    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="verified")
    assert foundational_dependency_violations(paper, _proposal()) == []
    assert is_reproduced(paper, lab_id="lab-a", campaign_id="paper-1")

    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="failed")
    assert foundational_dependency_violations(paper, _proposal())
    assert not is_reproduced(paper, lab_id="lab-a", campaign_id="paper-1")


def test_all_foundational_dependencies_must_be_verified(tmp_path):
    paper = tmp_path / "paper"
    record_reproduction(paper, lab_id="lab-a", campaign_id="paper-1", status="verified")
    proposal = _proposal(foundational_external=[
        DEP,
        {"lab_id": "lab-b", "campaign_id": "paper-2", "why": "second premise"},
    ])
    violations = foundational_dependency_violations(paper, proposal)
    assert [(v["lab_id"], v["campaign_id"]) for v in violations] == [
        ("lab-b", "paper-2")
    ]


def test_reproduction_may_target_its_unverified_paper_but_not_bypass_others(tmp_path):
    paper = tmp_path / "paper"
    own_reproduction = _proposal(reproduction_of={
        "lab_id": "lab-a", "campaign_id": "paper-1",
        "claimed_metric": "loss", "claimed_value": 0.1,
    })
    assert foundational_dependency_violations(paper, own_reproduction) == []

    own_reproduction["foundational_external"].append(
        {"lab_id": "lab-b", "campaign_id": "paper-2"}
    )
    violations = foundational_dependency_violations(paper, own_reproduction)
    assert len(violations) == 1
    assert violations[0]["lab_id"] == "lab-b"


def test_malformed_dependency_metadata_fails_closed(tmp_path):
    paper = tmp_path / "paper"
    assert foundational_dependency_violations(
        paper, {"foundational_external": "lab-a/paper-1"}
    )
    assert foundational_dependency_violations(
        paper, {"foundational_external": [None]}
    )
    assert foundational_dependency_violations(
        paper, {"foundational_external": [{"lab_id": "lab-a"}]}
    )


def test_executor_blocks_before_config_or_experiment_side_effects(tmp_path, monkeypatch):
    paths = lab_paths(tmp_path / "lab")
    init_lab(paths)

    monkeypatch.setattr(
        executor._lab,
        "get_config",
        lambda: (_ for _ in ()).throw(AssertionError("config was loaded")),
    )
    monkeypatch.setattr(
        executor,
        "_execute_run",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("experiment ran")),
    )

    result = executor.execute(paths=paths, proposal=_proposal())

    assert result["ok"] is False
    assert result["blocked"] is True
    assert not (paths.root / "configs").exists()
    assert "blocked unverified dependency" in paths.notebook.read_text()


def test_reproduction_outcome_verifies_match_and_failed_match_revokes(tmp_path):
    paper = tmp_path / "paper"
    proposal = {
        "reproduction_of": {
            "lab_id": "lab-a",
            "campaign_id": "paper-1",
            "claimed_metric": "score",
            "claimed_value": 0.8,
            "tolerance": 0.05,
        }
    }

    receipt = executor._record_reproduction_outcome(
        paper_dir=paper,
        proposal=proposal,
        run_id="match-run",
        result=RunResult(ok=True, metrics={"score": 0.82}),
    )
    assert receipt["status"] == "verified"
    assert is_reproduced(paper, lab_id="lab-a", campaign_id="paper-1")

    receipt = executor._record_reproduction_outcome(
        paper_dir=paper,
        proposal=proposal,
        run_id="mismatch-run",
        result=RunResult(ok=True, metrics={"score": 0.70}),
    )
    assert receipt["status"] == "failed"
    assert not is_reproduced(paper, lab_id="lab-a", campaign_id="paper-1")


def test_missing_reproduction_metric_records_failure(tmp_path):
    paper = tmp_path / "paper"
    receipt = executor._record_reproduction_outcome(
        paper_dir=paper,
        proposal={
            "reproduction_of": {
                "lab_id": "lab-a",
                "campaign_id": "paper-1",
                "claimed_metric": "score",
                "claimed_value": 0.8,
            }
        },
        run_id="missing-run",
        result=RunResult(ok=True, metrics={"other": 0.8}),
    )
    assert receipt["status"] == "failed"
    assert not is_reproduced(paper, lab_id="lab-a", campaign_id="paper-1")
