"""Proposal deduplication: a configuration that already succeeded (or is
queued / in flight) is rejected at the Researcher choke point, the decision
is written to the notebook, `allow_duplicate` + a reason opts out, and the
prompt lists what has already been tried."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from efferents.agents import executor, researcher
from efferents.agents.state import (
    init_lab,
    lab_paths,
    proposal_config_hash,
    proposal_semantic_hash,
    queue_push,
    render_proposal_config,
    semantic_hash_index,
    succeeded_run_for_hash,
)
from efferents.exec import RunResult, _persist_run_result

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


def _responses(proposals: list[dict]) -> list[str]:
    """Supervisor brief / Student / Supervisor review."""
    return [
        json.dumps({"open_questions": [], "forbidden_axes": [], "encouraged_paradigms": [],
                    "expected_proposal_shape": "config", "post_mortem": ""}),
        json.dumps({"proposals": proposals}),
        json.dumps({"verdict": "approve", "redlines": [], "revised_proposals": None}),
    ]


def _proposal(**extra) -> dict:
    p = {"name": "p1", "hypothesis": "h", "expected": "e",
         "config_overrides": {"run.seed": 7}}
    p.update(extra)
    return p


def _tagged(p: dict) -> dict:
    """What the proposal looks like after propose() tags it (mode + student)."""
    return {**p, "mode": "refine", "student_id": "primary"}


def _seed_succeeded_run(paths, proposal: dict, *, run_id="seed-run", loss=0.42) -> str:
    """Persist a succeeded run for `proposal` through the real persist path."""
    config_yaml = render_proposal_config(proposal)
    _persist_run_result(
        RunResult(ok=True, metrics={"synthetic_loss": loss}), run_id,
        Path("configs/x.yaml"), db_path=paths.runs_db, proposal=proposal,
        config_yaml=config_yaml,
    )
    return proposal_config_hash(proposal)


def _propose(paths, client, tmp_path):
    return researcher.propose(
        paths=paths, context_dir=tmp_path, budget=FakeBudget(), client=client,
        mode="refine",
    )


# --- hash agreement with the executor -------------------------------------


def test_proposal_hash_matches_what_execute_persists(paths, smoke_lab_config):
    """The dedup hash must equal the ledger's config_hash for the same proposal,
    rendered end-to-end by executor.execute."""
    from dataclasses import replace
    from efferents import lab as lab_mod
    cfg = smoke_lab_config
    lab_mod.set_config(replace(cfg, executor=replace(
        cfg.executor,
        # Doubled braces: the template goes through str.format(config_path=...).
        run_command="""printf '{{"run_id": "x", "metrics": {{"synthetic_loss": 0.5}}}}'""",
    )))
    proposal = _tagged(_proposal(campaign_id="c-abc"))
    out = executor.execute(paths=paths, proposal=proposal)
    assert out["ok"], out
    row = succeeded_run_for_hash(paths.runs_db, proposal_config_hash(proposal))
    assert row is not None and row["run_id"] == out["rows"][0]["run_id"]


def test_hash_is_none_without_a_readable_template(smoke_lab_config):
    Path(smoke_lab_config.executor.config_template).unlink()
    assert proposal_config_hash(_proposal()) is None


# --- rejection at the choke point ---------------------------------------


def test_dedup_rejects_identical_hash_and_records_note(paths, fake_anthropic_factory, tmp_path):
    h = _seed_succeeded_run(paths, _tagged(_proposal()))
    client = fake_anthropic_factory(_responses([_proposal()]))

    result = _propose(paths, client, tmp_path)

    assert result["proposals"] == []
    notebook = paths.notebook.read_text()
    assert "proposal skipped: duplicate of run seed-run" in notebook
    assert h[:19] in notebook
    assert not paths.queue.read_text().strip()


def test_failed_run_with_same_hash_does_not_block(paths, fake_anthropic_factory, tmp_path):
    proposal = _tagged(_proposal())
    _persist_run_result(
        RunResult(ok=False, error="crashed"), "failed-run", Path("configs/x.yaml"),
        db_path=paths.runs_db, proposal=proposal,
        config_yaml=render_proposal_config(proposal),
    )
    client = fake_anthropic_factory(_responses([_proposal()]))
    result = _propose(paths, client, tmp_path)
    assert [p["name"] for p in result["proposals"]] == ["p1"]


def test_allow_duplicate_with_reason_passes_and_is_recorded(
    paths, fake_anthropic_factory, tmp_path
):
    _seed_succeeded_run(paths, _tagged(_proposal()))
    client = fake_anthropic_factory(_responses([
        _proposal(allow_duplicate=True, duplicate_reason="reproducibility check of seed-run"),
    ]))

    result = _propose(paths, client, tmp_path)

    [kept] = result["proposals"]
    assert kept["allow_duplicate"] is True
    assert kept["duplicate_of"] == "run seed-run"
    notebook = paths.notebook.read_text()
    assert "allowed as duplicate of run seed-run" in notebook
    assert "reproducibility check of seed-run" in notebook


def test_allow_duplicate_without_reason_is_still_rejected(
    paths, fake_anthropic_factory, tmp_path
):
    _seed_succeeded_run(paths, _tagged(_proposal()))
    client = fake_anthropic_factory(_responses([_proposal(allow_duplicate=True)]))
    result = _propose(paths, client, tmp_path)
    assert result["proposals"] == []
    assert "allow_duplicate set without duplicate_reason" in paths.notebook.read_text()


def test_queued_and_inflight_proposals_block_duplicates(paths, fake_anthropic_factory, tmp_path):
    queue_push(paths.queue, _tagged(_proposal(name="queued-one")))
    paths.inflight.write_text(json.dumps(_tagged(_proposal(name="running-one"))) + "\n")
    client = fake_anthropic_factory(_responses([
        _proposal(name="queued-one"),
        _proposal(name="running-one"),
        _proposal(name="fresh", config_overrides={"run.seed": 8}),
    ]))

    result = _propose(paths, client, tmp_path)

    assert [p["name"] for p in result["proposals"]] == ["fresh"]
    notebook = paths.notebook.read_text()
    assert "duplicate of queued proposal 'queued-one'" in notebook
    assert "duplicate of queued proposal 'running-one'" in notebook


def test_same_batch_duplicates_collapse_to_one(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal(), _proposal()]))
    result = _propose(paths, client, tmp_path)
    assert len(result["proposals"]) == 1
    assert "duplicate of proposal 'p1' in the same batch" in paths.notebook.read_text()


def test_distinct_configs_are_all_kept(paths, fake_anthropic_factory, tmp_path):
    _seed_succeeded_run(paths, _tagged(_proposal()))
    client = fake_anthropic_factory(_responses([
        _proposal(name="p2", config_overrides={"run.seed": 7, "model.width": 16}),
        _proposal(name="p3", config_overrides={"run.seed": 9}),
    ]))
    result = _propose(paths, client, tmp_path)
    assert [p["name"] for p in result["proposals"]] == ["p2", "p3"]
    assert "proposal skipped" not in paths.notebook.read_text()


# --- semantic hash: renaming does not make a configuration new -----------


def test_semantic_hash_strips_identity_keys_only():
    a = proposal_semantic_hash(_tagged(_proposal(name="a", campaign_id="c-1")))
    b = proposal_semantic_hash({
        **_proposal(name="b", campaign_id="c-2",
                    config_overrides={"run.seed": 7, "logging.notes": "renamed"}),
        "mode": "moonshot", "student_id": "other",
    })
    assert a == b
    assert a != proposal_config_hash(_tagged(_proposal(name="a", campaign_id="c-1")))
    assert proposal_semantic_hash(_tagged(_proposal(config_overrides={"run.seed": 8}))) != a


def test_persist_writes_semantic_hash_column(paths):
    _seed_succeeded_run(paths, _tagged(_proposal()))
    conn = sqlite3.connect(paths.runs_db)
    (stored,) = conn.execute("SELECT config_hash_semantic FROM runs").fetchone()
    conn.close()
    assert stored == proposal_semantic_hash(_tagged(_proposal()))


def test_renamed_proposal_with_identical_overrides_is_rejected(
    paths, fake_anthropic_factory, tmp_path
):
    _seed_succeeded_run(paths, _tagged(_proposal(name="original")))
    client = fake_anthropic_factory(_responses([_proposal(name="fresh-name")]))

    result = _propose(paths, client, tmp_path)

    assert result["proposals"] == []
    notebook = paths.notebook.read_text()
    assert (
        "proposal skipped: duplicate of run seed-run "
        "(same configuration under a different name)"
    ) in notebook


def test_proposal_differing_only_in_seed_passes(paths, fake_anthropic_factory, tmp_path):
    _seed_succeeded_run(paths, _tagged(_proposal()))
    client = fake_anthropic_factory(_responses([
        _proposal(name="p1", config_overrides={"run.seed": 8}),
    ]))
    result = _propose(paths, client, tmp_path)
    assert [p["name"] for p in result["proposals"]] == ["p1"]
    assert "proposal skipped" not in paths.notebook.read_text()


def test_renamed_queued_proposal_is_rejected(paths, fake_anthropic_factory, tmp_path):
    queue_push(paths.queue, _tagged(_proposal(name="queued-one")))
    client = fake_anthropic_factory(_responses([_proposal(name="renamed")]))
    result = _propose(paths, client, tmp_path)
    assert result["proposals"] == []
    assert (
        "duplicate of queued proposal 'queued-one' (same configuration under a different name)"
    ) in paths.notebook.read_text()


def test_renamed_same_batch_duplicates_collapse(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal(name="a"), _proposal(name="b")]))
    result = _propose(paths, client, tmp_path)
    assert [p["name"] for p in result["proposals"]] == ["a"]
    assert "duplicate of proposal 'a' in the same batch" in paths.notebook.read_text()


def test_legacy_rows_without_semantic_hash_are_rehashed(
    paths, fake_anthropic_factory, tmp_path
):
    """Rows written before the column existed carry NULL; the gate re-parses
    their config_yaml instead of letting a renamed proposal through."""
    _seed_succeeded_run(paths, _tagged(_proposal(name="original")))
    conn = sqlite3.connect(paths.runs_db)
    conn.execute("UPDATE runs SET config_hash_semantic = NULL")
    conn.commit()
    conn.close()
    client = fake_anthropic_factory(_responses([_proposal(name="renamed")]))

    result = _propose(paths, client, tmp_path)

    assert result["proposals"] == []
    assert "duplicate of run seed-run (same configuration" in paths.notebook.read_text()


def test_semantic_hash_index_without_the_column(tmp_path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT, started_at TEXT, status TEXT, config_yaml TEXT)"
    )
    yaml_a = render_proposal_config(_tagged(_proposal(name="a")))
    yaml_b = render_proposal_config(_tagged(_proposal(name="b")))
    conn.executemany(
        "INSERT INTO runs VALUES (?, ?, ?, ?)",
        [("r-a", "2026-01-01", "succeeded", yaml_a),
         ("r-b", "2026-01-02", "succeeded", yaml_b),
         ("r-f", "2026-01-03", "failed", yaml_b)],
    )
    conn.commit()
    conn.close()
    index = semantic_hash_index(db)
    [(h, row)] = index.items()  # a and b collapse; the failed row is ignored
    assert h == proposal_semantic_hash(_tagged(_proposal()))
    assert row["run_id"] == "r-b"  # most recent succeeded wins


# --- prompt context ------------------------------------------------------


def test_researcher_context_lists_tried_configs(paths, fake_anthropic_factory, tmp_path):
    _seed_succeeded_run(paths, _tagged(_proposal()), run_id="r1", loss=0.42)
    # Same overrides under a different name: same semantic group.
    _seed_succeeded_run(paths, _tagged(_proposal(name="p1-renamed")), run_id="r2", loss=0.40)
    h = proposal_semantic_hash(_tagged(_proposal()))
    _seed_succeeded_run(
        paths, _tagged(_proposal(name="wide", config_overrides={"model.width": 32})),
        run_id="r3", loss=0.9,
    )
    client = fake_anthropic_factory(_responses([_proposal(name="new", config_overrides={"run.seed": 1})]))

    _propose(paths, client, tmp_path)

    # Both the Supervisor brief (call 0) and the Student (call 1) see it.
    for call in client.calls[:2]:
        text = "\n".join(
            b["text"] for b in call["messages"][0]["content"] if b.get("type") == "text"
        )
        assert "## Already tried configurations" in text
        assert f"- `{h.split(':')[-1][:12]}` ×2 | run.seed=7 | synthetic_loss=0.4" in text
        assert "| model.width=32 | synthetic_loss=0.9" in text
        assert "run.name" not in text.split("## Already tried configurations")[1].split("## Lab notebook")[0]


def test_researcher_context_without_runs_says_so(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory(_responses([_proposal()]))
    _propose(paths, client, tmp_path)
    text = str(client.calls[1]["messages"][0]["content"])
    assert "## Already tried configurations" in text
    assert "(no runs yet)" in text
