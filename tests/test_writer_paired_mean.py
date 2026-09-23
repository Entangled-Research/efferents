"""Publication uses the paired campaign mean and retains negative evidence."""
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from efferents import lab
from efferents.agents.writer import _paper_evidence, write_phase_a_paper, writer_paths


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("finding_kind,n_valid,expected", [
    ("negative_result", 3, True), ("improvement", 3, False),
    ("negative_result", 2, False),
])
def test_paired_mean_reports_fired_falsifier_without_cross_campaign_cherry_pick(
    tmp_path, monkeypatch, fake_anthropic_factory, finding_kind, n_valid, expected, scoped
):
    sub = tmp_path / "submission"
    shutil.copytree(Path(__file__).parent / "fixtures/sample_submission", sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw["metrics"] = {
        "headline": {"column": "candidate", "direction": "max", "comparator_column": "prior", "aggregate": "mean"},
        "panels": [{"column": "gain", "label": "paired gain"}],
        "constraints": [{"column": "revision", "op": "==", "value": 2}],
    }
    raw["falsifiers"] = [{"id": "gain-target", "description": "Must gain five points",
                          "when": {"column": "gain", "agg": "mean", "op": "<", "value": 5, "min_n": 3}}]
    if scoped:
        idea = sub / "ideas" / "sibling"
        idea.mkdir(parents=True)
        suite = {"version": 1, "metrics": {**raw["metrics"],
                 "headline": {"column": "gain", "direction": "max"},
                 "panels": [{"column": "candidate", "label": "candidate"}, {"column": "prior", "label": "prior"}]},
                 "falsifiers": raw["falsifiers"]}
        (idea / "eval-suite.json").write_text(json.dumps(suite))
        raw["metrics"] = {"headline": {"column": "unrelated", "direction": "min"}}
        raw["falsifiers"] = [{"id": "other-idea-rule", "description": "Different idea",
                              "when": {"column": "unrelated", "agg": "min", "op": ">", "value": 1, "min_n": 999}}]
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    cfg = lab.LabConfig.from_submission(sub)
    monkeypatch.setattr(lab, "_active", cfg)
    monkeypatch.setattr(lab, "PEER_REVIEW_ENABLED", False)
    root = sub / "lab"
    root.mkdir(exist_ok=True)
    paths = writer_paths(lab=root, paper=sub / "paper", reports=root / "reports", context=sub / "context")
    with sqlite3.connect(paths.runs_db) as conn:
        conn.execute("CREATE TABLE runs (run_id TEXT, campaign_id TEXT, started_at TEXT, status TEXT, seed INTEGER, candidate REAL, prior REAL, gain REAL, revision INTEGER, config_yaml TEXT)")
        for i in range(n_valid):
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (f"r{i}", "v2", str(i), "succeeded", 101+i, .7+i*.1, .8+i*.1, -10, 2, f"seed: {101+i}\n"))
        for rid, campaign, status, revision in [("other", "v1", "succeeded", 2), ("failed", "v2", "failed", 2), ("old", "v2", "succeeded", 1)]:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, campaign, rid, status, 1, 100, .01, 9999, revision, "excluded: true\n"))
    hypothesis = (sub / "hypothesis.md").read_text()
    client = fake_anthropic_factory(["\n".join(f"## {s}\n\nBounded negative result.\n" for s in ("Motivation", "Methods", "Results", "Conclusion", "Next questions"))])
    result = write_phase_a_paper(paths, {
        "id": "v2", "question": "Bounded result below the target", "finding_kind": finding_kind,
        "student_id": "sibling" if scoped else "primary",
        "hypothesis_path": "hypothesis.md", "hypothesis_hash": "sha256:"+hashlib.sha256(hypothesis.encode()).hexdigest(),
        "headline_metric": "candidate", "headline_direction": "max",
        "publication_context": "Retrospective gate; bounded sample; no mechanism ground truth.",
    }, client, publication_comparison=lab.Headline("candidate", "max", "prior", "mean") if scoped else None)
    if not expected:
        assert result is None and client.calls == []
        assert "Aggregate falsifier gain-target" in paths.notebook.read_text()
        return
    fm = yaml.safe_load(result.split("---", 2)[1])
    metric = fm["metric_provenance"][0]
    assert fm["finding_kind"] == "negative_result"
    assert metric["aggregate"] == "mean"
    assert metric["value"] == pytest.approx(.8)
    assert metric["comparator_value"] == pytest.approx(.9)
    assert metric["runs"] == ["r0", "r1", "r2"]
    assert metric["seeds"] == [101, 102, 103]
    record = json.loads(result.split("## Recorded evidence", 1)[1].split("```json\n", 1)[1].split("\n```", 1)[0])
    assert record["hypothesis"]["status"] == "verified"
    assert record["hypothesis"]["text"] == hypothesis
    assert record["falsifiers"][0]["status"] == "fired"
    assert {r["run_id"] for r in record["runs"]} == {"r0", "r1", "r2"}
    assert "no mechanism ground truth" in client.calls[0]["messages"][0]["content"]


def test_writer_does_not_quote_hash_mismatched_hypothesis(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (tmp_path / "hypothesis.md").write_text("new text not matching the campaign")
    paths = writer_paths(lab=tmp_path / "lab", paper=tmp_path / "paper", reports=tmp_path / "reports", context=context)
    record = _paper_evidence(paths, {"hypothesis_path": "hypothesis.md", "hypothesis_hash": "sha256:"+"0"*64}, [], [])
    assert record["hypothesis"]["status"] == "hash_mismatch"
    assert "text" not in record["hypothesis"]


def test_missing_sibling_contract_never_borrows_the_default_idea(tmp_path):
    from efferents.eval_suite import resolve_idea_config
    sub = tmp_path / "submission"
    shutil.copytree(Path(__file__).parent / "fixtures/sample_submission", sub)
    cfg = lab.LabConfig.from_submission(sub)
    assert resolve_idea_config(sub, cfg, cfg.default_student_id) is cfg
    with pytest.raises(ValueError, match="no evaluation contract"):
        resolve_idea_config(sub, cfg, "other")
    with pytest.raises(ValueError, match="Invalid idea identity"):
        resolve_idea_config(sub, cfg, "../../other")
