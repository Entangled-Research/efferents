"""`autonomy.coder_mode: review` — the Coder writes a unified diff plus a
rationale under lab/patches/ for the owner and leaves source.dir untouched;
`auto` (the default when the Coder is enabled) still applies, smoke-tests and
commits."""
from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from efferents import lab as lab_mod
from efferents.agents import coder
from efferents.agents.state import (
    init_lab,
    lab_paths,
    load_state,
    mark_patch,
    open_blocks,
    pending_patches,
    read_jsonl,
    record_blocked,
)
from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Source,
)

ORIGINAL = "def f():\n    return 1\n"


class FakeBudget:
    def should_pause(self): return False
    def record(self, *a, **k): pass
    def spend_today(self, today=None): return 0.0


def _install(tmp_path: Path, *, coder_mode: str | None = None, allowed=("**/*.py",)):
    src = tmp_path / "src"
    src.mkdir()
    (src / "model.py").write_text(ORIGINAL)
    (src / "default.yaml").write_text("run:\n  seed: 0\n")
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src, allowed_patterns=allowed),
        executor=Executor(
            run_command="echo {config_path}",
            smoke_command="echo smoke {config_path}",
            config_template=src / "default.yaml",
        ),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    if coder_mode is not None:
        # LabConfig.Autonomy may not declare coder_mode yet; the Coder reads it
        # defensively, so a stand-in object stands in for the parsed field.
        cfg = replace(cfg, autonomy=SimpleNamespace(coder_enabled=True, coder_mode=coder_mode))
    lab_mod.set_config(cfg)
    paths = lab_paths(tmp_path / "lab")
    init_lab(paths)
    return cfg, paths


def _plan(src: Path, *, new_file: bool = True) -> dict:
    plan = {
        "feasible": True,
        "summary": "f returns 2",
        "rationale": "two is better",
        "edits": [{
            "file_path": str(src / "model.py"),
            "old_string": "return 1",
            "new_string": "return 2",
        }],
        "verifies_change": "smoke imports model",
    }
    if new_file:
        plan["new_files"] = [{"file_path": str(src / "extra.py"), "content": "X = 1\n"}]
    return plan


PROPOSAL = {"name": "Fix Encoder", "what": "edit f", "why": "because", "student_id": "primary"}


def _implement(paths, tmp_path, **kw):
    return coder.implement_proposal(
        proposal=dict(PROPOSAL), paths=paths, budget=FakeBudget(), client=object(),
        repo_root=tmp_path, **kw,
    )


# --- mode parsing ---------------------------------------------------------


def test_coder_mode_defaults_to_auto_and_never_widens_on_bad_input(tmp_path):
    _install(tmp_path)  # LabConfig without a coder_mode field
    assert coder._coder_mode() == "auto"
    for raw, expected in (("auto", "auto"), ("review", "review"), (" Review ", "review"),
                          ("bogus", "review"), (None, "auto"), ("", "auto")):
        cfg = lab_mod.get_config()
        lab_mod.set_config(replace(cfg, autonomy=SimpleNamespace(coder_enabled=True, coder_mode=raw)))
        assert coder._coder_mode() == expected, raw


# --- review mode ----------------------------------------------------------


def test_review_mode_writes_diff_and_rationale_and_leaves_source_untouched(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="review")
    src = cfg.source.dir
    block = record_blocked(paths.root, student_id="primary", summary="encoder collapse", evidence=["r-1"])
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(src))
    monkeypatch.setattr(coder, "run_smoke", lambda *a, **k: pytest.fail("smoke must not run in review mode"))
    monkeypatch.setattr(coder, "_git_commit", lambda *a, **k: pytest.fail("must not commit in review mode"))

    result = _implement(paths, tmp_path)

    assert result.ok is False and result.feasible is True
    assert result.patch_path and "awaiting owner review" in result.error
    diff_path = Path(result.patch_path)
    assert diff_path.parent == paths.root / "patches"
    assert re.fullmatch(r"\d{8}T\d{6}Z-fix-encoder\.diff", diff_path.name)

    diff = diff_path.read_text()
    assert "--- a/src/model.py\n+++ b/src/model.py" in diff
    assert "-    return 1\n+    return 2" in diff
    assert "--- /dev/null\n+++ b/src/extra.py" in diff and "+X = 1" in diff
    # The diff is a real patch: git can check it against the repo root.
    check = subprocess.run(["git", "apply", "--check", str(diff_path)], cwd=tmp_path,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr

    md = diff_path.with_suffix(".md").read_text()
    assert "f returns 2" in md and "two is better" in md
    assert f"`{block['id']}` — encoder collapse" in md
    assert "## Tests run" in md and "None — review mode" in md
    assert "echo smoke" in md

    # Source untouched.
    assert (src / "model.py").read_text() == ORIGINAL
    assert not (src / "extra.py").exists()

    assert f"Coder patch awaiting owner review: {diff_path}" in paths.notebook.read_text()
    [pending] = pending_patches(paths.root)
    assert pending["path"] == str(diff_path) and pending["blocked_id"] == block["id"]
    assert pending["files"] == ["src/extra.py", "src/model.py"]
    assert load_state(paths.state)["pending_patches"] == [pending]
    [log] = read_jsonl(paths.root / "coder_log.jsonl")
    assert log["patch_path"] == str(diff_path) and log["ok"] is False
    # The proposal is not re-attempted on the next Coder cycle.
    coder_dir = paths.root
    (coder_dir / "proposed_changes.md").write_text("# x\n\n### Fix Encoder\n\n- **Principle**: p\n")
    assert coder.select_pending_proposal(paths=paths) is None


def test_mark_patch_applied_closes_the_block(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="review")
    block = record_blocked(paths.root, student_id="primary", summary="encoder collapse")
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(cfg.source.dir))
    result = _implement(paths, tmp_path)

    assert mark_patch(paths.root, "nope.diff", "rejected") is False
    with pytest.raises(ValueError):
        mark_patch(paths.root, result.patch_path, "merged")
    assert mark_patch(paths.root, result.patch_path, "applied") is True

    assert pending_patches(paths.root) == []
    assert load_state(paths.state)["pending_patches"] == []
    [rec] = read_jsonl(paths.root / "patches" / "patches.jsonl")
    assert rec["status"] == "applied" and rec["status_by"] == "owner"
    assert open_blocks(paths.root) == []
    assert load_state(paths.state)["blocked_on_infrastructure"] == []
    assert block["id"] not in [b["id"] for b in open_blocks(paths.root)]


def test_mark_patch_rejected_keeps_block_open(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="review")
    block = record_blocked(paths.root, student_id="primary", summary="encoder collapse")
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(cfg.source.dir))
    result = _implement(paths, tmp_path)
    assert mark_patch(paths.root, result.patch_path, "rejected") is True
    assert pending_patches(paths.root) == []
    assert [b["id"] for b in open_blocks(paths.root)] == [block["id"]]


def test_review_mode_respects_allowed_patterns(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="review", allowed=("models/*.py",))
    src = cfg.source.dir
    plan = _plan(src, new_file=False)  # edits src/model.py, which is not under models/
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: plan)

    result = _implement(paths, tmp_path)

    assert result.ok is False and result.patch_path is None
    assert "allowed_patterns" in result.error
    assert not (paths.root / "patches").exists()
    assert pending_patches(paths.root) == []
    assert (src / "model.py").read_text() == ORIGINAL


def test_review_mode_retries_once_on_unmatched_old_string(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="review")
    src = cfg.source.dir
    bad = _plan(src, new_file=False)
    bad["edits"][0]["old_string"] = "return 999"
    plans = [bad, _plan(src, new_file=False)]
    seen = []
    def _plan_fn(**kw):
        seen.append(kw["proposal"])
        return plans.pop(0)
    monkeypatch.setattr(coder, "get_edit_plan", _plan_fn)

    result = _implement(paths, tmp_path)

    assert result.patch_path and "_apply_error" in seen[1]
    assert "+    return 2" in Path(result.patch_path).read_text()
    assert (src / "model.py").read_text() == ORIGINAL


# --- auto mode unchanged --------------------------------------------------


def test_auto_mode_applies_smokes_and_commits(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path)  # no coder_mode field -> auto
    src = cfg.source.dir
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(src))
    monkeypatch.setattr(coder, "run_smoke", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(coder, "_git_commit", lambda *a, **k: "abc1234")

    result = _implement(paths, tmp_path)

    assert result.ok is True and result.commit_sha == "abc1234" and result.patch_path is None
    assert (src / "model.py").read_text() == "def f():\n    return 2\n"
    assert (src / "extra.py").read_text() == "X = 1\n"
    assert not (paths.root / "patches").exists()
    assert pending_patches(paths.root) == []


def test_auto_mode_restores_on_smoke_failure(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="auto")
    src = cfg.source.dir
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(src))
    monkeypatch.setattr(coder, "run_smoke", lambda *a, **k: (False, "boom"))
    monkeypatch.setattr(coder, "_git_commit", lambda *a, **k: pytest.fail("must not commit"))

    result = _implement(paths, tmp_path)

    assert result.ok is False and result.error == "smoke test failed"
    assert (src / "model.py").read_text() == ORIGINAL
    assert not (src / "extra.py").exists()


def test_explicit_mode_argument_overrides_config(tmp_path, monkeypatch):
    cfg, paths = _install(tmp_path, coder_mode="auto")
    src = cfg.source.dir
    monkeypatch.setattr(coder, "get_edit_plan", lambda **kw: _plan(src, new_file=False))
    monkeypatch.setattr(coder, "run_smoke", lambda *a, **k: pytest.fail("no smoke in review"))
    result = _implement(paths, tmp_path, mode="review")
    assert result.patch_path and (src / "model.py").read_text() == ORIGINAL
