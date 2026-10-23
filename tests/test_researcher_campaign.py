"""Researcher campaign open path: popper_gate runs, campaigns row inserted,
proposals tagged. ≤2 open campaigns cap enforced."""
from __future__ import annotations

import json

import pytest

from efferents.agents import researcher
from efferents.agents.state import (
    campaign_insert,
    campaign_open_list,
    init_lab,
    lab_paths,
)
from efferents.migrations.runner import apply_campaigns_migration


@pytest.fixture
def paths(tmp_lab):
    p = lab_paths(tmp_lab)
    init_lab(p)
    import sqlite3
    conn = sqlite3.connect(p.runs_db)
    conn.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            config_path TEXT,
            config_yaml TEXT NOT NULL,
            config_hash TEXT NOT NULL,
            seed INTEGER,
            model TEXT,
            raw_q INTEGER,
            raw_px INTEGER,
            epochs INTEGER,
            aug_depth INTEGER,
            aug_shared_unitary INTEGER,
            cond_drop_p REAL,
            eval_kind TEXT,
            eval_n INTEGER,
            val_x0_mse REAL,
            e_w1 REAL,
            active_frac_w1 REAL,
            radial_l2 REAL,
            radial_l2_log REAL,
            gen_max_to_real_max REAL,
            duration_seconds REAL,
            notes TEXT,
            git_commit TEXT,
            samples_png TEXT,
            lit_context_json TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    apply_campaigns_migration(p.runs_db)
    return p


class FakeBudget:
    def should_pause(self): return False
    def record(self, *a, **k): pass
    def daily_total(self): return 0.0
    def spend_today(self, today=None): return 0.0


def _researcher_response_with_new_campaign() -> str:
    """The Student turn returns proposals + a new_campaign declaration."""
    return json.dumps({
        "proposals": [
            {"name": "p1", "hypothesis": "h", "expected": "e",
             "config_overrides": {"run.seed": 1}}
        ],
        "new_campaign": {
            "question": "does X help?",
            "draft_hypothesis": "X reduces W1 by 10% under default config."
        }
    })


def _dialog_responses(student_response: str) -> list[str]:
    """Three canned responses matching the 3-turn dialogue
    (Supervisor brief / Student / Supervisor review)."""
    # Supervisor brief and review can return empty-ish JSON; the Student turn
    # carries the real payload.
    return [
        json.dumps({"saturation_score": 0, "advice": "proceed", "expected_proposal_shape": "refine"}),
        student_response,
        json.dumps({"approved": True, "verdict": "approve"}),
    ]


@pytest.mark.skip(reason="reference-domain-specific; lives with reference-lab")
def test_new_campaign_calls_gate_and_inserts_row(
    paths, fake_anthropic_factory, tmp_path, monkeypatch
):
    from efferents.agents import popper_gate

    # Stub popper_gate to accept.
    fake_result = popper_gate.GateResult(
        ok=True,
        path=paths.root / "popper-corpus/test/hypothesis.md",
        hash="sha256:" + "a" * 64,
        reason=None,
    )
    monkeypatch.setattr(popper_gate, "run_gate", lambda **kw: fake_result)

    client = fake_anthropic_factory(_dialog_responses(_researcher_response_with_new_campaign()))

    result = researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
        mode="refine",
    )

    opens = campaign_open_list(paths.runs_db, "qfm-diffusion")
    assert len(opens) == 1
    assert opens[0]["hypothesis_hash"] == "sha256:" + "a" * 64
    proposals = result["proposals"]
    assert proposals[0]["campaign_id"] == opens[0]["id"]


@pytest.mark.skip(reason="reference-domain-specific; lives with reference-lab")
def test_cap_blocks_third_open_campaign(
    paths, fake_anthropic_factory, tmp_path, monkeypatch
):
    # Pre-insert two open campaigns
    for i in (1, 2):
        campaign_insert(
            paths.runs_db,
            id=f"c{i}",
            lab_id="qfm-diffusion",
            question=f"q{i}",
            hypothesis_path=f"popper-corpus/c{i}/hypothesis.md",
            hypothesis_hash="sha256:" + str(i) * 64,
        )

    from efferents.agents import popper_gate
    def _no_gate(**kw):
        raise AssertionError("gate must not be called when at cap")
    monkeypatch.setattr(popper_gate, "run_gate", _no_gate)

    client = fake_anthropic_factory(_dialog_responses(_researcher_response_with_new_campaign()))

    researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
        mode="refine",
    )

    # Still only 2 open campaigns
    assert len(campaign_open_list(paths.runs_db, "qfm-diffusion")) == 2


def test_gate_reject_drops_new_campaign_but_keeps_proposals_only_if_existing_campaign(
    paths, fake_anthropic_factory, tmp_path, monkeypatch
):
    from efferents.agents import popper_gate
    fake_reject = popper_gate.GateResult(
        ok=False, path=None, hash=None, reason="validate failed: missing falsifier"
    )
    monkeypatch.setattr(popper_gate, "run_gate", lambda **kw: fake_reject)

    client = fake_anthropic_factory(_dialog_responses(_researcher_response_with_new_campaign()))

    result = researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
        mode="refine",
    )

    # No campaign opened.
    assert campaign_open_list(paths.runs_db, "qfm-diffusion") == []
    # No proposals enqueued, since they had no campaign to route to.
    assert result.get("proposals") == []


def test_campaign_binding_resolves_registered_aliases_and_new_sentinel():
    from efferents.agents.researcher import _bind_campaign_proposals
    opens = [{'id':'c-real','hypothesis_path':'popper-corpus/known-slug/hypothesis.md'}]
    for value in ('c-real','known-slug'):
        proposals, errors = _bind_campaign_proposals([{'name':'p','campaign_id':value}], opens)
        assert not errors and proposals[0]['campaign_id']=='c-real'
    proposals, errors = _bind_campaign_proposals([{'name':'p','campaign_id':'new'}], opens,
                                                new_campaign_declared=True, new_campaign_id='c-new')
    assert not errors and proposals[0]['campaign_id']=='c-new'
    proposals, errors = _bind_campaign_proposals([{'name':'p','config_overrides':{'run.campaign_id':'known-slug'}}], opens)
    assert not errors and proposals[0]['campaign_id']=='c-real'


def test_campaign_binding_refuses_ambiguous_or_unregistered_evidence():
    from efferents.agents.researcher import _bind_campaign_proposals
    opens = [{'id':'c-a','hypothesis_path':'ideas/a/hypothesis.md'},
             {'id':'c-b','hypothesis_path':'ideas/b/hypothesis.md'}]
    for value in (None,'nonexistent','another-students-campaign'):
        proposals, errors = _bind_campaign_proposals([{'name':'p','campaign_id':value}], opens)
        assert not proposals and errors
    proposals, errors = _bind_campaign_proposals([{'name':'p'}], opens[:1])
    assert not errors and proposals[0]['campaign_id']=='c-a'
    proposals, errors = _bind_campaign_proposals([{'name':'p','campaign_id':'new'}], opens,
                                                new_campaign_declared=True, new_campaign_id=None)
    assert not proposals and errors
