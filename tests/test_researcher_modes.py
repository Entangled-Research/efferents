"""Researcher accepts a mode argument, injects it into the prompt, and
tags emitted proposals with it."""
from __future__ import annotations

import json

import pytest

from efferents.agents import researcher
from efferents.agents.state import lab_paths, init_lab


@pytest.fixture
def paths(tmp_lab):
    p = lab_paths(tmp_lab)
    init_lab(p)
    return p


def _fake_proposals_json(mode: str) -> str:
    return json.dumps(
        {
            "proposals": [
                {
                    "name": "p1",
                    "hypothesis": "test",
                    "expected": "x",
                    "config_overrides": {"run.seed": 7},
                }
            ]
        }
    )


def _fake_supervisor_brief_json() -> str:
    return json.dumps(
        {
            "open_questions": [],
            "forbidden_axes": [],
            "encouraged_paradigms": [],
            "expected_proposal_shape": "architectural",
            "post_mortem": "all good",
        }
    )


def _fake_supervisor_review_json() -> str:
    return json.dumps(
        {
            "verdict": "approve",
            "redlines": [],
            "revised_proposals": None,
        }
    )


class FakeBudget:
    def should_pause(self): return False
    def record(self, *a, **k): pass
    def daily_total(self): return 0.0
    def spend_today(self, today=None): return 0.0


def test_mode_injected_into_user_message(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory([
        _fake_supervisor_brief_json(),
        _fake_proposals_json("moonshot"),
        _fake_supervisor_review_json(),
    ])

    researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
        mode="moonshot",
    )

    user_msg = client.calls[0]["messages"][0]["content"]
    assert "<<MODE: moonshot>>" in str(user_msg)


def test_proposals_tagged_with_mode(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory([
        _fake_supervisor_brief_json(),
        _fake_proposals_json("devils_advocate"),
        _fake_supervisor_review_json(),
    ])

    result = researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
        mode="devils_advocate",
    )
    proposals = result.get("proposals", [])
    assert proposals
    assert all(p.get("mode") == "devils_advocate" for p in proposals)


def test_mode_defaults_to_refine_when_omitted(paths, fake_anthropic_factory, tmp_path):
    client = fake_anthropic_factory([
        _fake_supervisor_brief_json(),
        _fake_proposals_json("refine"),
        _fake_supervisor_review_json(),
    ])

    researcher.propose(
        paths=paths,
        context_dir=tmp_path,
        budget=FakeBudget(),
        client=client,
    )
    user_msg = client.calls[0]["messages"][0]["content"]
    assert "<<MODE: refine>>" in str(user_msg)


def test_empty_round_fallback_keeps_existing_experiments_runnable_when_coder_disabled(paths, fake_anthropic_factory, tmp_path, monkeypatch):
    from dataclasses import replace
    from efferents import lab
    from efferents.agents.state import save_state
    cfg=lab.get_config()
    monkeypatch.setattr(lab,'_active',replace(cfg,autonomy=replace(cfg.autonomy,coder_enabled=False)))
    save_state(paths.state,{'researcher_consecutive_empty':2})
    client=fake_anthropic_factory([_fake_proposals_json('refine')])
    result=researcher.propose(paths=paths,context_dir=tmp_path,budget=FakeBudget(),client=client)
    assert result['proposals'] and len(client.calls)==1
    assert '"expected_proposal_shape": "refine"' in str(client.calls[0]['messages'])
    assert 'Automatic code changes are disabled' in str(client.calls[0]['system'])
