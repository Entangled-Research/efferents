"""The digest carries a deterministic evidence block (buckets + falsifiers)
above the LLM narrative, and the narrative prompt sees it."""
from __future__ import annotations

import json
import sqlite3

from efferents import lab as lab_mod
from efferents.agents import analyst
from efferents.agents.budget import BudgetTracker
from efferents.agents.state import lab_paths
from tests.test_evidence import agg, make_cfg, make_rows, paired

FALSIFIERS = [
    agg("F1", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=16),
    paired("F2", column="delta_e_w1", ci95_excludes_zero=False, bucket=16),
    agg("F3", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=32),
]
ROWS = make_rows(deltas_by_q={8: [0.1, 0.3, 0.2], 16: [-0.2, -0.1, -0.3, -0.4, -0.25]})


def test_evidence_section_has_bucket_and_falsifier_tables(tmp_path):
    lab_mod.set_config(make_cfg(tmp_path, falsifiers=FALSIFIERS))
    text = analyst._evidence_section(ROWS)
    assert text.startswith("## Evidence (computed)")
    assert "| bucket | runs | e_w1 | delta_e_w1 |" in text
    assert "| raw_q=16 | 5 | 0.75 (n=5) | -0.25 (n=5) |" in text
    assert "Seed-paired deltas (quantum − classical)" in text
    assert "| raw_q=16 | e_w1 | 5 | -0.25 |" in text
    assert "### Falsifiers" in text
    assert "| F1 | survived |" in text
    assert "| F2 | survived |" in text
    assert "| F3 | insufficient_data |" in text
    assert text.rstrip().endswith("**Verdict:** undecided")


def test_evidence_section_without_runs_or_falsifiers(tmp_path):
    lab_mod.set_config(make_cfg(tmp_path))
    text = analyst._evidence_section([])
    assert "(no runs yet)" in text
    assert "No falsifiers declared" in text
    assert "**Verdict:** undecided" in text


def test_write_digest_prepends_evidence_and_prompts_with_it(tmp_path, fake_anthropic_factory):
    lab_mod.set_config(make_cfg(tmp_path, falsifiers=FALSIFIERS[:2]))
    paths = lab_paths(tmp_path / "lab")
    paths.digests_dir.mkdir(parents=True)
    conn = sqlite3.connect(paths.runs_db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, status TEXT, "
        "campaign_id TEXT, raw_q INTEGER, seed INTEGER, e_w1 REAL, delta_e_w1 REAL, "
        "observations_json TEXT)"
    )
    for r in ROWS:
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?)",
            (r["run_id"], r["started_at"], "succeeded", r["raw_q"], r["seed"],
             r["e_w1"], r["delta_e_w1"], r["observations_json"]),
        )
    conn.commit()
    conn.close()

    client = fake_anthropic_factory(["# Digest\n\n## TL;DR\n- narrative bullet\n"])
    out = analyst.write_digest(
        paths=paths, context_dir=tmp_path / "context",
        budget=BudgetTracker(paths.budget, daily_cap_usd=5.0),
        client=client, model="fake-model", notify=False,
    )
    digest = open(out["path"]).read()
    assert digest.startswith("## Evidence (computed)")
    assert "| F1 | survived |" in digest and "| F2 | survived |" in digest
    assert "**Verdict:** survives" in digest
    assert digest.index("**Verdict:**") < digest.index("narrative bullet")

    prompt = json.dumps(client.calls[0]["messages"])
    assert "Evidence summary (computed, deterministic)" in prompt
    assert "### Falsifiers" in prompt and "Verdict:** survives" in prompt


def test_independent_ideas_never_pool_opposing_results(tmp_path, monkeypatch):
    from dataclasses import replace
    import efferents.eval_suite as suites
    cfg = replace(make_cfg(tmp_path, falsifiers=FALSIFIERS[:1]),
                  students=({"id": "a"}, {"id": "b"}))
    monkeypatch.setattr(suites, "resolve_idea_config", lambda root, cfg, sid: cfg)
    rows = [{**r, "student_id": sid} for sid, delta in (("a", -1.), ("b", 1.))
            for r in make_rows(deltas_by_q={16: [delta]*5})]
    text = analyst._evidence_section(rows, cfg, submission=tmp_path)
    a, b = text.split("### Idea: a")[1].split("### Idea: b")
    assert "**Verdict:** survives" in a
    assert "**Verdict:** falsified" in b
    assert "no pooled scientific verdict" in text


def test_missing_idea_contract_never_borrows_global_verdict(tmp_path):
    from dataclasses import replace
    cfg = replace(make_cfg(tmp_path), students=({"id": "a"}, {"id": "b"}))
    text = analyst._evidence_section(ROWS, cfg, submission=tmp_path)
    assert text.count("No verdict.") == 2
    assert "**Verdict:**" not in text


def test_lab_verdict_defers_to_independent_idea_verdicts(tmp_path, monkeypatch):
    from dataclasses import replace
    from efferents.dashboard import ideas, reader
    cfg = replace(make_cfg(tmp_path), students=({"id": "a"}, {"id": "b"}))
    monkeypatch.setattr(ideas, "read_idea", lambda root, cfg, sid: {
        "student_id": sid, "verdict": {"n_runs": 5, "verdict": "survives" if sid == "a" else "falsified"}})
    result = reader.read_verdict(tmp_path, cfg)
    assert result["verdict"] == "undecided" and result["n_runs"] == 10
    assert [idea["verdict"] for idea in result["ideas"]] == ["survives", "falsified"]
    assert result["falsifiers"] == []
