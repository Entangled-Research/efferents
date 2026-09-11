from __future__ import annotations

import sqlite3
from pathlib import Path


from efferents.agents import orchestrator as orch
from efferents.agents.state import campaign_insert, now_iso, load_state, save_state
from efferents.migrations.runner import apply_campaigns_migration
from efferents import lab as _lab


def _seed_runs(db: Path, n: int):
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS runs (run_id TEXT, started_at TEXT, "
        "campaign_id TEXT, synthetic_loss REAL)"
    )
    for i in range(n):
        conn.execute(
            "INSERT INTO runs (run_id, started_at, campaign_id, synthetic_loss) "
            "VALUES (?, ?, ?, ?)",
            (f"r{i}", "2026-01-01T00:00:00+00:00", "c1", 0.1),
        )
    conn.commit()
    conn.close()


def _make_orch(tmp_path):
    o = orch.Orchestrator(
        lab_dir=tmp_path / "lab",
        context_dir=tmp_path / "context",
        dry_run=True,
    )
    o.dry_run = False
    o.client = object()  # never used: write_phase_a_paper is monkeypatched
    return o


def _seed_campaign(o):
    apply_campaigns_migration(o.paths.runs_db)
    campaign_insert(
        o.paths.runs_db,
        id="c1",
        lab_id=_lab.LAB_ID,
        question="q",
        hypothesis_path="h.md",
        hypothesis_hash="sha256:" + "a" * 64,
        headline_metric="synthetic_loss",
        headline_direction="min",
    )


def test_writes_when_due(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)
    _seed_runs(o.paths.runs_db, 25)  # >= runs_per_paper (20)

    monkeypatch.setattr(orch, "notify_all", lambda **k: None)

    calls = []
    monkeypatch.setattr(
        orch.writer, "write_phase_a_paper",
        lambda paths, campaign, **kw: calls.append(campaign["id"]) or "artifact",
    )
    o._maybe_write()
    assert calls == ["c1"]
    state = load_state(o.paths.state)
    assert state["last_paper_runs"] == 25


def test_skips_when_below_threshold(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)
    _seed_runs(o.paths.runs_db, 25)
    state = load_state(o.paths.state)
    state["last_paper_runs"] = 25
    state["last_paper_ts"] = now_iso()
    save_state(o.paths.state, state)

    calls = []
    monkeypatch.setattr(orch.writer, "write_phase_a_paper", lambda *a, **k: calls.append(1))
    o._maybe_write()
    assert calls == []


def test_skips_campaign_with_existing_paper(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)
    _seed_runs(o.paths.runs_db, 25)
    # Canonical Writer output lives beside lab/, in <submission>/paper/.
    paper_dir = o.submission_dir / "paper"
    assert paper_dir == o.paths.runs_db.parent.parent / "paper"
    paper_dir.mkdir(parents=True, exist_ok=True)
    (paper_dir / "c1.md").write_text("already written")

    calls = []
    monkeypatch.setattr(
        orch.writer, "write_phase_a_paper",
        lambda paths, campaign, **kw: calls.append(campaign["id"]),
    )
    o._maybe_write()
    assert calls == []


def test_budget_pause_short_circuits(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)
    _seed_runs(o.paths.runs_db, 25)
    monkeypatch.setattr(o.budget, "should_pause", lambda: True)

    calls = []
    monkeypatch.setattr(orch.writer, "write_phase_a_paper", lambda *a, **k: calls.append(1))
    o._maybe_write()
    assert calls == []
    state = load_state(o.paths.state)
    assert "last_paper_runs" not in state or state.get("last_paper_runs") == 0


def test_step_calls_maybe_write_before_close_stale(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)

    order = []
    monkeypatch.setattr(o, "_refill_queue", lambda: 0)
    monkeypatch.setattr(orch, "queue_pop", lambda q: None)  # force no-proposal branch
    monkeypatch.setattr(o, "_maybe_digest", lambda: order.append("digest"))
    monkeypatch.setattr(o, "_maybe_code", lambda: order.append("code"))
    monkeypatch.setattr(o, "_maybe_write", lambda: order.append("write"))
    monkeypatch.setattr(orch, "close_stale_campaigns", lambda *a, **k: order.append("close") or [])
    monkeypatch.setattr(orch.time, "sleep", lambda s: None)

    result = o.step()
    assert result["event"] == "no_proposal"
    assert "write" in order
    assert order.index("write") < order.index("close")


def test_step_ran_branch_calls_maybe_write(tmp_path, monkeypatch):
    o = _make_orch(tmp_path)
    _seed_campaign(o)

    called = []
    monkeypatch.setattr(o, "_refill_queue", lambda: 0)
    monkeypatch.setattr(orch, "queue_pop", lambda q: {"name": "p"})  # force ran branch
    monkeypatch.setattr(orch.executor, "execute", lambda **k: {"ok": True, "name": "p"})
    monkeypatch.setattr(o, "_maybe_digest", lambda: None)
    monkeypatch.setattr(o, "_maybe_code", lambda: None)
    monkeypatch.setattr(o, "_maybe_write", lambda: called.append("write"))

    result = o.step()
    assert result["event"] == "ran"
    assert called == ["write"]
