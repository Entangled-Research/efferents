"""Review-mode Coder results notify the owner once per hour that a patch is
waiting; nothing is committed and no "code committed" notification fires."""
from __future__ import annotations

import dataclasses

from efferents import lab as lab_mod
from efferents.agents import orchestrator as orch
from efferents.agents.coder import CoderResult
from efferents.lab import Autonomy


def test_maybe_code_notifies_owner_about_pending_patch_once(tmp_path, monkeypatch):
    cfg = lab_mod.get_config()
    lab_mod.set_config(dataclasses.replace(
        cfg, autonomy=Autonomy(coder_enabled=True, coder_mode="review"),
    ))
    o = orch.Orchestrator(lab_dir=tmp_path / "lab", context_dir=tmp_path / "context", dry_run=True)
    o.dry_run = False
    o.client = object()
    notifications: list[dict] = []
    monkeypatch.setattr(orch, "notify_all", lambda **k: notifications.append(k))
    monkeypatch.setattr(orch, "runs_count", lambda db: 0)
    monkeypatch.setattr(orch.coder, "select_pending_proposal", lambda **k: {"name": "add-seed"})
    patch = str(tmp_path / "lab" / "patches" / "20260907T000000Z-add-seed.diff")
    monkeypatch.setattr(
        orch.coder, "implement_proposal",
        lambda **k: CoderResult(ok=False, name="add-seed", patch_path=patch),
    )

    o._maybe_code()
    assert len(notifications) == 1
    assert "patch awaits review" in notifications[0]["title"]
    assert patch in notifications[0]["message"]
    assert "add-seed" in notifications[0]["message"]

    # The Coder cadence cursor advanced; force it due again and confirm the
    # owner is not re-notified within the hour.
    state = orch.load_state(o.paths.state)
    for key in list(state):
        if isinstance(state[key], dict):
            state[key].pop("last_coder_ts", None)
            state[key].pop("last_coder_runs", None)
    orch.save_state(o.paths.state, state)
    o._maybe_code()
    assert len(notifications) == 1

    # A plain failure (no patch, not ok) notifies nobody.
    o._notified_at.clear()
    orch.save_state(o.paths.state, {})
    monkeypatch.setattr(
        orch.coder, "implement_proposal",
        lambda **k: CoderResult(ok=False, name="add-seed", error="plan failed"),
    )
    o._maybe_code()
    assert len(notifications) == 1
