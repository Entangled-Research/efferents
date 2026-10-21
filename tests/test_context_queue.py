import json
import sqlite3

from efferents.agents.context_queue import _select_context, checkpoint, record_uses
from efferents.steer import record_steering


def test_checkpoint_routes_only_successful_local_evidence_and_tracks_use(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    with sqlite3.connect(root / "runs.sqlite") as db:
        db.execute("CREATE TABLE runs (run_id TEXT, student_id TEXT, status TEXT, started_at TEXT, score REAL)")
        db.executemany("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", [
            ("own-good", "primary", "succeeded", "2026-10-01", 0.2),
            ("peer-good", "second", "succeeded", "2026-10-02", 0.4),
            ("peer-failed", "second", "failed", "2026-10-03", None),
        ])
    record_steering(root, text="Prioritize sample efficiency", by="owner")
    prompt, received = checkpoint(root, "primary")
    assert "own-good" in prompt and "peer-good" in prompt
    assert "peer-failed" not in prompt
    assert "Prioritize sample efficiency" in prompt
    assert len(received) == 3
    checkpoint(root, "primary")
    queue = root / "context_queues" / "primary.jsonl"
    assert len(queue.read_text().splitlines()) == 3

    sibling = next(json.loads(line) for line in queue.read_text().splitlines()
                   if json.loads(line)["kind"] == "sibling_result")
    proposal = {"student_id": "primary", "context_uses": [
        {"context_id": sibling["id"], "why": "Matched its approach"}],
        "replication_of": sibling["id"]}
    result = {"ok": True, "rows": [{"run_id": "replicate-1"}]}
    assert record_uses(root, proposal, {"ok": False, "rows": result["rows"]}) == 0
    assert record_uses(root, proposal, result) == 1
    assert record_uses(root, proposal, result) == 0
    entry = json.loads((root / "context_uses.jsonl").read_text())
    assert entry["use_kind"] == "replication_attempt"
    assert entry["source_kind"] == "sibling_result"
    assert entry["run_ids"] == ["replicate-1"]


def test_unreceived_or_undeclared_context_is_not_influence(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    assert record_uses(root, {"student_id": "primary", "context_uses": [
        {"context_id": "missing", "why": "invented"}]},
        {"ok": True, "rows": [{"run_id": "r"}]}) == 0
    assert not (root / "context_uses.jsonl").exists()


def test_only_accepted_publications_enter_cross_lab_context(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    inbox = root / "conference" / "inbox.jsonl"
    inbox.parent.mkdir()
    valid = {"id": "paper-1", "kind": "publication", "publication_status": "accepted",
             "campaign_id": "study", "lab_id": "other-lab", "journal": "Research",
             "review_scores": {"critical": 7, "neutral": 7, "optimistic": 8},
             "body": "A reviewed method"}
    invalid = {**valid, "id": "paper-2", "review_scores": {"critical": 7}}
    message = {"id": "message-1", "kind": "measurement", "body": "Raw run result"}
    inbox.write_text("\n".join(json.dumps(row) for row in (valid, invalid, message)) + "\n")
    prompt, received = checkpoint(root, "primary")
    assert len(received) == 1
    assert "A reviewed method" in prompt
    assert "Raw run result" not in prompt
    assert "paper-2" not in prompt


def test_busy_idea_does_not_bury_sibling_context():
    rows = ([{"id": "sibling", "kind": "sibling_result"}]
            + [{"id": f"own-{n}", "kind": "own_result"} for n in range(20)])
    selected = _select_context(rows, 12)
    assert any(row["id"] == "sibling" for row in selected)
    assert len(selected) == 12
