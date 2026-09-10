"""Analyst groups recent runs by campaign_id for the digest prompt."""
from __future__ import annotations


from efferents.agents.analyst import (
    _format_campaign_blocks,
    _format_recent_runs,
    group_runs_by_campaign,
)


def test_groups_runs_with_campaign_id():
    runs = [
        {"run_id": "r1", "campaign_id": "c1", "e_w1": 0.02},
        {"run_id": "r2", "campaign_id": "c1", "e_w1": 0.01},
        {"run_id": "r3", "campaign_id": "c2", "e_w1": 0.03},
    ]
    grouped = group_runs_by_campaign(runs)
    assert set(grouped.keys()) == {"c1", "c2"}
    assert len(grouped["c1"]) == 2
    assert len(grouped["c2"]) == 1


def test_runs_without_campaign_under_none_key():
    runs = [
        {"run_id": "r1", "campaign_id": None, "e_w1": 0.02},
        {"run_id": "r2", "campaign_id": "c1", "e_w1": 0.01},
    ]
    grouped = group_runs_by_campaign(runs)
    assert None in grouped
    assert len(grouped[None]) == 1


def test_empty_input():
    assert group_runs_by_campaign([]) == {}


def test_recent_runs_table_uses_configured_headline_not_reference_domain(tmp_path):
    # Under smoke_lab_config the headline column is `synthetic_loss`. The
    # rendered table must use the configured column names, not reference-domain columns.
    rows = [
        {"run_id": "run-aaaa", "started_at": "2026-01-01", "campaign_id": "c1",
         "researcher_mode": "explore", "synthetic_loss": 0.123, "raw_q": 7},
    ]
    table = _format_recent_runs(rows, tmp_path / "missing.sqlite")
    assert "synthetic_loss" in table
    assert "e_w1" not in table


def test_campaign_blocks_use_configured_headline_not_reference_domain(tmp_path):
    rows = [
        {"run_id": "run-bbbb", "campaign_id": "c1", "synthetic_loss": 0.05, "raw_q": 3},
    ]
    groups = group_runs_by_campaign(rows)
    blocks = _format_campaign_blocks(groups, tmp_path / "missing.sqlite")
    assert "synthetic_loss" in blocks
    assert "e_w1" not in blocks
    assert "raw_q" not in blocks
