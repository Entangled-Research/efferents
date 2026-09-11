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
    monkeypatch.setenv("EFFERENTS_MODEL", "zai/glm-5.3")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
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
