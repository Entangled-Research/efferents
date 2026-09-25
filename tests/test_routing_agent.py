import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml

from efferents.agents import routing
from efferents.lab import LabConfig
from efferents.registry import LabRecord, Registry


@pytest.fixture
def intake(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    paths = []
    for name, approach in [("math", "explain intermediate steps"), ("newcomer", "repeat direct answers")]:
        path = tmp_path / name
        shutil.copytree(Path(__file__).parents[1] / "examples/smoke-lab", path)
        config = yaml.safe_load((path / "lab.yaml").read_text())
        config.update(lab_id=name, topic="mathematical reasoning effectiveness cost",
                      approach=approach, routing={"pool": "event", "owner": "organizer"})
        (path / "lab.yaml").write_text(yaml.safe_dump(config))
        paths.append(path)
    Registry().register(LabRecord("math", str(paths[0]), str(paths[0] / "lab"), 0, "", "stopped"))
    return paths


def test_related_approaches_become_tracks_with_preserved_budget_and_provenance(intake):
    target, incoming = intake
    before = LabConfig.from_submission(target)
    result = routing.route(incoming, apply=True, student_id="bob", use_model=False)
    assert result["action"] == "join" and result["applied"]
    after = LabConfig.from_submission(target)
    assert [s["id"] for s in after.students] == ["primary", "bob"]
    assert after.default_student_id == "primary"
    assert "repeat direct answers" in after.students[1]["focus"]
    assert after.students[1]["handle"] == "repeat direct answers"
    assert after.budget == before.budget and after.executor == before.executor
    assert Path(result["hypothesis_snapshot"]).read_text() == (incoming / "hypothesis.md").read_text()
    with sqlite3.connect(target / "lab/runs.sqlite") as conn:
        row = conn.execute("SELECT student_id, hypothesis_hash FROM campaigns WHERE id = ?", (result["campaign_id"],)).fetchone()
    assert row == ("bob", "sha256:" + result["hypothesis_hash"])
    assert Registry().get("newcomer") is None
    assert routing.route(incoming, apply=True, student_id="bob", use_model=False) == result
    assert len(LabConfig.from_submission(target).students) == 2


@pytest.mark.parametrize("difference", ["owner", "pool", "runner", "topic", "opt_out"])
def test_incompatible_or_unrelated_ideas_stay_separate(intake, difference):
    target, incoming = intake
    raw = yaml.safe_load((incoming / "lab.yaml").read_text())
    if difference in {"owner", "pool"}:
        raw["routing"][difference] = "other"
    elif difference == "topic":
        raw["topic"] = "protein structure folding stability"
    elif difference == "runner":
        (incoming / "src/stub_run.py").write_text("print('different executor')")
    else:
        other = yaml.safe_load((target / "lab.yaml").read_text())
        other["routing"]["accept_students"] = False
        (target / "lab.yaml").write_text(yaml.safe_dump(other))
    (incoming / "lab.yaml").write_text(yaml.safe_dump(raw))
    result = routing.route(incoming, apply=True, use_model=False)
    assert result["action"] == "create" and not result["applied"]
    assert len(LabConfig.from_submission(target).students) == 1


def test_model_routing_and_hallucinated_target_rejection(intake, monkeypatch):
    from efferents.agents import researcher
    target, incoming = intake
    monkeypatch.setattr(routing, "credentials_available", lambda _: True)
    def answer(**kwargs):
        data = json.loads(kwargs["messages"][0]["content"])
        assert data["idea"]["lab_id"] == "newcomer"
        assert data["labs"][0]["lab_id"] == "math"
        assert kwargs["max_tokens"] == 600
        return json.dumps(dict(action="join", target="math", confidence=0.95, reason="Shared question, different strategies"))
    monkeypatch.setattr(researcher, "_simple_call", answer)
    result = routing.route(incoming)
    assert result["method"] == "model" and result["target"] == str(target)
    assert not result["applied"]
    monkeypatch.setattr(researcher, "_simple_call", lambda **kw: json.dumps(
        dict(action="join", target="invented-lab", confidence=1, reason="wrong")))
    with pytest.raises(ValueError, match="unknown target"):
        routing.route(incoming, apply=True)
    assert len(LabConfig.from_submission(target).students) == 1


def test_roster_refresh_keeps_runtime_budget(intake, monkeypatch):
    from dataclasses import replace
    from efferents import lab
    target, incoming = intake
    original = LabConfig.from_submission(target)
    monkeypatch.setattr(lab, "_active", original)
    lab.set_config(original)
    routing.route(incoming, apply=True, student_id="bob", use_model=False)
    changed = yaml.safe_load((target / "lab.yaml").read_text())
    changed["budget"]["daily_cap_usd"] = 100
    (target / "lab.yaml").write_text(yaml.safe_dump(changed))
    routing.refresh_students(target / "lab")
    current = lab.get_config()
    assert [s["id"] for s in current.students] == ["primary", "bob"]
    assert current.budget == original.budget
    assert replace(current, students=original.students) == original


def test_gateway_routes_without_loading_provider_keys(intake, monkeypatch):
    from efferents.dashboard.control import ControlContext
    monkeypatch.setenv("EFFERENTS_MODEL", "openai/gpt-4.1-mini")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    target, incoming = intake
    context = ControlContext()
    result = context.connect(str(incoming))
    assert result["lab_id"] == "math"
    assert result["routing"]["applied"]
    assert result["routing"]["method"] == "deterministic"
    assert Registry().get("newcomer") is None


def test_changed_routed_hypothesis_cannot_overwrite_snapshot(intake):
    target, incoming = intake
    result = routing.route(incoming, apply=True, use_model=False)
    original = Path(result["hypothesis_snapshot"]).read_text()
    (incoming / "hypothesis.md").write_text(original + "\nChanged claim\n")
    with pytest.raises(ValueError, match="changed"):
        routing.route(incoming, apply=True, use_model=False)
    assert Path(result["hypothesis_snapshot"]).read_text() == original


def test_join_preserves_incoming_eval_contract_and_attributed_graphs(intake):
    from efferents.dashboard.ideas import read_idea
    from efferents.migrations.runner import ensure_runs_table
    target, incoming = intake
    raw = yaml.safe_load((incoming / "lab.yaml").read_text())
    raw["metrics"]["panels"].append({"column": "incoming_score", "label": "Incoming score"})
    raw["falsifiers"] = [{"id": "incoming-fails", "description": "Incoming threshold",
        "when": {"column": "incoming_score", "agg": "mean", "op": "<", "value": .7, "min_n": 1}}]
    (incoming / "lab.yaml").write_text(yaml.safe_dump(raw))
    presentation = {"version": 1, "title": "Incoming measured question",
        "rationale": "An independently defined idea",
        "graphs": [{"title": "Incoming score", "columns": ["incoming_score"]}], "samples": []}
    (incoming / "eval-suite.json").write_text(json.dumps(presentation))
    result = routing.route(incoming, apply=True, student_id="bob", use_model=False)
    suite_path = target / "ideas/bob/eval-suite.json"
    suite = json.loads(suite_path.read_text())
    assert suite["metrics"] == raw["metrics"] and suite["falsifiers"] == raw["falsifiers"]
    assert suite["graphs"] == presentation["graphs"]
    assert suite["routing_provenance"]["source_lab_id"] == "newcomer"
    assert "incoming_score" not in (target / "lab.yaml").read_text()
    db = target / "lab/runs.sqlite"
    ensure_runs_table(db, LabConfig.from_submission(target))
    with sqlite3.connect(db) as conn:
        conn.execute("ALTER TABLE runs ADD COLUMN incoming_score REAL")
        conn.executemany("INSERT INTO runs (run_id,started_at,student_id,campaign_id,status,incoming_score) VALUES (?,?,?,?,?,?)", [
            ("incoming-run", "2026-01-02", "bob", result["campaign_id"], "succeeded", .8),
            ("original-run", "2026-01-01", "primary", None, "succeeded", 999)])
    view = read_idea(target / "lab", LabConfig.from_submission(target), "bob")
    assert view["suite"]["status"] == "configured"
    assert view["suite"]["graphs"][0]["series"][0]["points"] == [{"run_id": "incoming-run", "value": .8}]
    assert view["verdict"]["falsifiers"][0]["id"] == "incoming-fails"
    from efferents.dashboard.reader import read_summary
    summary = read_summary(target / "lab", LabConfig.from_submission(target))
    idea = next(idea for idea in summary["ideas"] if idea["id"] == "bob")
    assert idea["name"] == "repeat direct answers"
    assert idea["verdict"] == view["verdict"]["verdict"] != "undecided"
    # The target snapshot remains readable if the temporary source is gone.
    shutil.rmtree(incoming)
    assert read_idea(target / "lab", LabConfig.from_submission(target), "bob")["suite"]["status"] == "configured"


def test_apply_repairs_old_missing_suite_but_preserves_owner_edits(intake):
    target, incoming = intake
    result = routing.route(incoming, apply=True, student_id="bob", use_model=False)
    path = target / "ideas/bob/eval-suite.json"
    original = path.read_text()
    path.unlink()
    assert routing.route(incoming, use_model=False) == result
    assert not path.exists()  # A read-only routing query never writes.
    assert routing.route(incoming, apply=True, use_model=False) == result
    assert path.read_text() == original
    updated = json.loads(original)
    updated["title"] = "Owner revised title"
    path.write_text(json.dumps(updated))
    routing.route(incoming, apply=True, use_model=False)
    assert json.loads(path.read_text())["title"] == "Owner revised title"


def test_invalid_incoming_graph_does_not_partially_join(intake):
    target, incoming = intake
    (incoming / "eval-suite.json").write_text(json.dumps({"version": 1,
        "graphs": [{"title": "Unmeasured", "columns": ["invented_metric"]}]}))
    with pytest.raises(ValueError, match="undeclared metrics"):
        routing.route(incoming, apply=True, student_id="bob", use_model=False)
    assert len(LabConfig.from_submission(target).students) == 1


def test_join_snapshots_scoped_presentation_and_rejects_external_reference(intake):
    target, incoming = intake
    suite_path = incoming / "ideas/primary/eval-suite.json"
    suite_path.parent.mkdir(parents=True)
    raw = yaml.safe_load((incoming / "lab.yaml").read_text())
    spec = {"version": 1, "metrics": raw["metrics"],
            "presentation_source": "../outside.json"}
    suite_path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="remain inside"):
        routing.route(incoming, apply=True, student_id="bob", use_model=False)
    assert len(LabConfig.from_submission(target).students) == 1
    spec["presentation_source"] = "eval-suite.json"
    suite_path.write_text(json.dumps(spec))
    (incoming / "eval-suite.json").write_text(json.dumps({"version": 1,
        "graphs": [{"title": "Scoped generated graph", "columns": ["synthetic_loss"]}]}))
    routing.route(incoming, apply=True, student_id="bob", use_model=False)
    snapshot = json.loads((target / "ideas/bob/eval-suite.json").read_text())
    assert snapshot["graphs"][0]["title"] == "Scoped generated graph"
    assert "presentation_source" not in snapshot
    assert snapshot["routing_provenance"]["source_presentation_sha256"]


def test_generated_ideas_route_within_owner_pool_and_keep_review_enabled(tmp_path, monkeypatch):
    from efferents.onboarding import create_lab
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    first, related, unrelated = [tmp_path / name for name in ("first", "related", "unrelated")]
    create_lab(first, starter="evacuation", idea="Frequent rerouting")
    create_lab(related, starter="evacuation", idea="Stable routes")
    create_lab(unrelated, starter="integration", idea="Quadrature")
    cfg = LabConfig.from_submission(first)
    assert cfg.peer_review_enabled
    Registry().register(LabRecord(cfg.lab_id, str(first), str(first / "lab"), 0, "", "stopped"))
    joined = routing.route(related, apply=True, use_model=False)
    assert joined["action"] == "join"
    assert len(LabConfig.from_submission(first).students) == 2
    assert routing.route(unrelated, apply=True, use_model=False)["action"] == "create"


def test_deleted_lab_is_not_a_routing_destination(intake):
    from efferents.lifecycle import remove
    target, incoming = intake
    remove(target / 'lab', by='owner')
    result = routing.route(incoming, use_model=False)
    assert result['action'] == 'create' and not result['candidates']
