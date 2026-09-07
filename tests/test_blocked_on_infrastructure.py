"""A Student can declare that the executor itself needs a code change
(`blocked_on_infrastructure`). The block is recorded in lab/blocked.jsonl,
narrated in the notebook, mirrored into state.json, surfaced to every student
on the next turn, capped at one open block per student, and closed by the
owner or the Coder."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from efferents.agents import researcher
from efferents.agents.state import (
    init_lab,
    lab_paths,
    load_state,
    open_blocks,
    read_jsonl,
    record_blocked,
    resolve_block,
)

DEFAULT_YAML = "run:\n  seed: 0\nmodel:\n  width: 8\n"


class FakeBudget:
    def should_pause(self): return False
    def record(self, *a, **k): pass
    def daily_total(self): return 0.0
    def spend_today(self, today=None): return 0.0


@pytest.fixture
def paths(tmp_lab, smoke_lab_config):
    Path(smoke_lab_config.executor.config_template).write_text(DEFAULT_YAML)
    p = lab_paths(tmp_lab)
    init_lab(p)
    conn = sqlite3.connect(p.runs_db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, ended_at TEXT, "
        "config_path TEXT, synthetic_loss REAL)"
    )
    conn.commit()
    conn.close()
    return p


def _responses(proposals: list[dict], blocked: dict | None = None) -> list[str]:
    student: dict = {"proposals": proposals}
    if blocked is not None:
        student["blocked_on_infrastructure"] = blocked
    return [
        json.dumps({"open_questions": [], "forbidden_axes": [], "encouraged_paradigms": [],
                    "expected_proposal_shape": "config", "post_mortem": ""}),
        json.dumps(student),
        json.dumps({"verdict": "approve", "redlines": [], "revised_proposals": None}),
    ]


def _proposal(**extra) -> dict:
    p = {"name": "p1", "hypothesis": "h", "expected": "e",
         "config_overrides": {"run.seed": 7}}
    p.update(extra)
    return p


def _propose(paths, client, tmp_path):
    return researcher.propose(
        paths=paths, context_dir=tmp_path, budget=FakeBudget(), client=client,
        mode="refine",
    )


BLOCK = {
    "summary": "the encoder collapses to a constant code; no config knob changes that",
    "evidence": ["r-11", "r-12"],
    "proposed_change": "add a commitment loss in encoder.py:Encoder.forward",
}


def test_block_is_recorded_narrated_and_exposed(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal()], blocked=BLOCK))

    result = _propose(paths, client, tmp_path)

    # Proposals in other directions still flow.
    assert [p["name"] for p in result["proposals"]] == ["p1"]
    block = result["blocked_on_infrastructure"]
    assert block["student_id"] == "primary" and block["summary"] == BLOCK["summary"]
    assert block["evidence"] == ["r-11", "r-12"] and block["resolved"] is None

    [record] = read_jsonl(paths.root / "blocked.jsonl")
    assert record["id"] == block["id"]
    assert record["proposed_change"] == BLOCK["proposed_change"]

    notebook = paths.notebook.read_text()
    assert f"student primary BLOCKED on infrastructure: {BLOCK['summary']}" in notebook
    assert "r-11, r-12" in notebook

    state = load_state(paths.state)
    assert [b["id"] for b in state["blocked_on_infrastructure"]] == [block["id"]]


def test_open_blocks_are_shown_to_every_student_next_turn(
    paths, fake_anthropic_factory, tmp_path
):
    _propose(paths, fake_anthropic_factory(_responses([_proposal()], blocked=BLOCK)), tmp_path)

    client = fake_anthropic_factory(_responses([_proposal(name="p2", config_overrides={"run.seed": 8})]))
    _propose(paths, client, tmp_path)

    # Supervisor brief (call 0) and Student (call 1) both see the block.
    for call in client.calls[:2]:
        text = "\n".join(
            b["text"] for b in call["messages"][0]["content"] if b.get("type") == "text"
        )
        section = text.split("## Open infrastructure blocks")[1].split("## Lab notebook")[0]
        assert BLOCK["summary"] in section
        assert "evidence: r-11, r-12" in section
        assert BLOCK["proposed_change"] in section


def test_no_blocks_says_none(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal()]))
    _propose(paths, client, tmp_path)
    text = str(client.calls[1]["messages"][0]["content"])
    assert "## Open infrastructure blocks" in text
    assert "(none)" in text.split("## Open infrastructure blocks")[1]
    assert not (paths.root / "blocked.jsonl").exists()


def test_one_open_block_per_student(paths, fake_anthropic_factory, tmp_path):
    _propose(paths, fake_anthropic_factory(_responses([_proposal()], blocked=BLOCK)), tmp_path)
    second = {**BLOCK, "summary": "decoder emits checkerboard artifacts"}
    result = _propose(
        paths,
        fake_anthropic_factory(_responses([_proposal(name="p2", config_overrides={"run.seed": 8})], blocked=second)),
        tmp_path,
    )

    assert result["blocked_on_infrastructure"] is None
    records = read_jsonl(paths.root / "blocked.jsonl")
    assert len(records) == 1 and records[0]["summary"] == BLOCK["summary"]
    assert "re-declared an infrastructure block" in paths.notebook.read_text()
    assert len(load_state(paths.state)["blocked_on_infrastructure"]) == 1


def test_block_without_summary_is_ignored(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal()], blocked={"evidence": ["r-1"]}))
    result = _propose(paths, client, tmp_path)
    assert result["blocked_on_infrastructure"] is None
    assert not (paths.root / "blocked.jsonl").exists()
    assert "blocked_on_infrastructure without a summary; ignored" in paths.notebook.read_text()


def test_resolve_block_closes_it_and_reopens_the_students_slot(paths):
    rec = record_blocked(paths.root, student_id="primary", summary="s", evidence=["r-1"])
    other = record_blocked(paths.root, student_id="sibling", summary="t")
    assert record_blocked(paths.root, student_id="primary", summary="again") is None
    assert [b["id"] for b in open_blocks(paths.root)] == [rec["id"], other["id"]]

    assert resolve_block(paths.root, rec["id"], by="coder") is True

    assert [b["id"] for b in open_blocks(paths.root)] == [other["id"]]
    closed = [r for r in read_jsonl(paths.root / "blocked.jsonl") if r["id"] == rec["id"]][0]
    assert closed["resolved"] and closed["resolved_by"] == "coder"
    assert [b["id"] for b in load_state(paths.state)["blocked_on_infrastructure"]] == [other["id"]]
    assert resolve_block(paths.root, rec["id"]) is False  # already closed
    assert resolve_block(paths.root, "blk-nope") is False
    # The student may declare a new block now.
    assert record_blocked(paths.root, student_id="primary", summary="again") is not None
