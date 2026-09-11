import json
import shutil
from pathlib import Path

import pytest

from efferents.agents import conference
from efferents.agents.federation import foundational_dependency_violations
from efferents.lab import Conference, LabConfig, SubmissionError
from efferents.registry import LabRecord, Registry


@pytest.fixture
def labs(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    registry = Registry()
    result = {}
    for name, domain in [("geometry", "math"), ("sampling", "math"), ("biology", "biology")]:
        submission = tmp_path / name
        shutil.copytree(Path(__file__).parents[1] / "examples/smoke-lab", submission)
        yaml = submission / "lab.yaml"
        yaml.write_text(yaml.read_text().replace("smoke-coefficient", name).replace(
            "domain: synthetic", f"domain: {domain}"
        ) + "\nconference:\n  enabled: true\n  venue: test-event\n")
        root = submission / "lab"
        root.mkdir(exist_ok=True)
        cfg = LabConfig.from_submission(submission)
        registry.register(LabRecord(name, str(submission), str(root), 0, "", "stopped"))
        result[name] = (cfg, root)
    return registry, result


def test_routing_cadence_restart_and_response_roundtrip(labs):
    registry, pairs = labs
    cfg, root = pairs["geometry"]
    first = conference.attend(cfg=cfg, lab_root=root, registry=registry, now=0)
    assert len(first["received"]) == 1
    inbox = conference._rows(root / "conference/inbox.jsonl")
    assert inbox[0]["lab_id"] == "sampling"
    assert inbox[0]["kind"] == "hypothesis"
    assert conference.attend(cfg=cfg, lab_root=root, registry=registry, now=599) is None
    for visit in range(2, 6):
        result = conference.attend(cfg=cfg, lab_root=root, registry=Registry(), now=(visit-1)*600)
        assert len(result["received"]) == (1 if visit == 5 else 0)
    inbox = conference._rows(root / "conference/inbox.jsonl")
    assert inbox[-1]["lab_id"] == "biology"
    assert inbox[-1]["track"] == "interdisciplinary"
    response = dict(reply_to=inbox[0]["id"], kind="question", body="How will you control answer length?")
    assert conference.record_responses(root, cfg, [response], "primary") == 1
    assert conference.record_responses(root, cfg, [response], "primary") == 0
    peer_cfg, peer_root = pairs["sampling"]
    conference.attend(cfg=peer_cfg, lab_root=peer_root, registry=registry, now=3000)
    received = conference._rows(peer_root / "conference/inbox.jsonl")
    question = next(row for row in received if row["kind"] == "question")
    assert question["reply_to"] == inbox[0]["id"]
    assert question["id"] in conference.prompt_context(peer_root, peer_cfg)
    reply = dict(reply_to=question["id"], kind="discussion", body="We will hold the output limit fixed.")
    assert conference.record_responses(peer_root, peer_cfg, [reply], "primary") == 1
    conference.attend(cfg=cfg, lab_root=root, registry=registry, now=3600)
    assert "hold the output limit" in conference.prompt_context(root, cfg)


def test_opt_in_venue_isolation_and_disabled_no_state(labs):
    registry, pairs = labs
    from dataclasses import replace
    cfg, root = pairs["geometry"]
    disabled = replace(cfg, conference=Conference())
    assert conference.attend(cfg=disabled, lab_root=root, registry=registry) is None
    assert not (root / "conference").exists()
    peer_yaml = pairs["sampling"][1].parent / "lab.yaml"
    original = peer_yaml.read_text()
    peer_yaml.write_text(original.replace("enabled: true", "enabled: false"))
    assert conference.attend(cfg=cfg, lab_root=root, registry=registry, now=0)["received"] == []
    peer_yaml.write_text(original.replace("test-event", "different-event"))
    assert conference.attend(cfg=cfg, lab_root=root, registry=registry, now=600)["received"] == []


def test_finding_snapshot_provenance_and_no_automatic_verification(labs):
    registry, pairs = labs
    cfg, root = pairs["geometry"]
    peer_cfg, peer_root = pairs["sampling"]
    paper = peer_root / "paper"
    paper.mkdir()
    body = "## 2026-09-11 10:00 UTC — experiment-1\n**Lab**: sampling\n**Headline**: Pilot result\n"
    (paper / "journal.md").write_text(body)
    (paper / "experiment-1.md").write_text("## Methods\nExact-answer comparison.\n## Results\nrun_id: actual-run-id")
    conference.attend(cfg=cfg, lab_root=root, registry=registry, now=0)
    findings = [r for r in conference._rows(root / "conference/inbox.jsonl") if r["kind"] == "finding"]
    assert len(findings) == 1
    assert "actual-run-id" in findings[0]["body"]
    assert findings[0]["campaign_id"] == "experiment-1"
    original = findings[0]["id"]
    (paper / "experiment-1.md").write_text("Revised result")
    conference.attend(cfg=cfg, lab_root=root, registry=registry, now=600)
    findings = [r for r in conference._rows(root / "conference/inbox.jsonl") if r["kind"] == "finding"]
    assert len(findings) == 2 and findings[0]["id"] == original
    assert findings[1]["id"] != original
    assert foundational_dependency_violations(root / "paper", {
        "foundational_external": [{"lab_id": "sampling", "campaign_id": "experiment-1"}]
    })


def test_invalid_responses_and_source_path_escape(labs, tmp_path):
    registry, pairs = labs
    cfg, root = pairs["geometry"]
    conference.attend(cfg=cfg, lab_root=root, registry=registry, now=0)
    assert conference.record_responses(root, cfg, [
        {"reply_to": "invented", "kind": "question", "body": "hello"},
        {"reply_to": [], "kind": "discussion", "body": "hello"},
    ], "primary") == 0
    secret = tmp_path / "secret"
    secret.write_text("PRIVATE")
    link = root / "escape.md"
    link.symlink_to(secret)
    assert conference._read(link, root.parent) == ""
    assert json.loads((root / "conference/inbox.jsonl").read_text().splitlines()[0])["body"]


@pytest.mark.parametrize("raw", [{"enabled": "false"}, {"interval_minutes": 0},
                                {"interdisciplinary_every": 1}, {"venue": ""}, {"typo": 1}])
def test_bad_configuration_fails_closed(raw):
    with pytest.raises(SubmissionError):
        Conference.from_dict(raw)


def test_daemon_attends_before_research_and_respects_pause(labs, monkeypatch):
    from efferents import lab
    from efferents.agents import orchestrator

    registry, pairs = labs
    cfg, root = pairs["geometry"]
    monkeypatch.setattr(lab, "get_config", lambda: cfg)
    daemon = orchestrator.Orchestrator(lab_dir=root, context_dir=root.parent / "context", dry_run=True)
    monkeypatch.setattr(orchestrator._steer, "step_hook", lambda _: True)
    assert daemon.step()["event"] == "owner_paused"
    assert not (root / "conference").exists()
    monkeypatch.setattr(orchestrator._steer, "step_hook", lambda _: False)
    observed = []
    def refill():
        observed.append(conference.prompt_context(root, cfg))
        return 0
    monkeypatch.setattr(daemon, "_refill_queue", refill)
    for method in ("_maybe_digest", "_maybe_code", "_maybe_write"):
        monkeypatch.setattr(daemon, method, lambda: None)
    monkeypatch.setattr(orchestrator, "close_stale_campaigns", lambda *a, **kw: [])
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _: None)
    assert daemon.step()["event"] == "no_proposal"
    assert '"lab_id": "sampling"' in observed[0]
    assert "conference visit 1" in daemon.paths.notebook.read_text()
