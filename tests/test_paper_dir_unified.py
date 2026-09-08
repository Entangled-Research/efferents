"""The Writer, the readers and the dashboard agree on <submission>/paper/."""
from __future__ import annotations

import shutil
from pathlib import Path

from efferents import lab as lab_mod
from efferents.cli import main
from efferents.dashboard import reader
from efferents.lab import LabConfig

SMOKE = Path(__file__).resolve().parents[1] / "examples" / "smoke-lab"

_PAPER = (
    "---\nlab_id: smoke-fixture\ncampaign_id: camp-1\n"
    "novelty_claim: A real finding.\npublished_at: 2026-06-09\n"
    "status: preprint\n---\n\n# Title\n\nbody\n"
)


def _submission(tmp_path: Path) -> Path:
    sub = tmp_path / "sub"
    shutil.copytree(SMOKE, sub, ignore=shutil.ignore_patterns("lab", "__pycache__"))
    (sub / "lab").mkdir()
    return sub


def test_orchestrator_submission_dir_defaults_to_lab_parent(tmp_path, monkeypatch):
    from efferents.agents import orchestrator as orch
    sub = _submission(tmp_path)
    lab_mod.set_config(LabConfig.from_submission(sub))
    monkeypatch.setattr(orch, "make_client", lambda **kw: object())
    monkeypatch.setattr(orch, "notify_all", lambda **kw: None)
    o = orch.Orchestrator(lab_dir=sub / "lab", context_dir=sub / "context", dry_run=True)
    assert o.submission_dir == sub.resolve()
    o2 = orch.Orchestrator(
        lab_dir=sub / "lab", context_dir=sub / "context", dry_run=True,
        submission_dir=tmp_path,
    )
    assert o2.submission_dir == tmp_path.resolve()


def test_reader_lists_canonical_and_legacy_paper_dirs(tmp_path, smoke_lab_config):
    sub = tmp_path / "sub"
    lab_root = sub / "lab"
    (sub / "paper").mkdir(parents=True)
    (lab_root / "paper").mkdir(parents=True)
    (sub / "paper" / "camp-1.md").write_text(_PAPER)
    (lab_root / "paper" / "camp-2.md").write_text(_PAPER.replace("camp-1", "camp-2"))
    dirs = reader.paper_dirs(lab_root)
    assert dirs[0] == sub / "paper"
    ids = {p["campaign_id"] for p in reader.read_papers(lab_root)}
    assert ids == {"camp-1", "camp-2"}


def test_migrate_paper_dir_moves_and_refuses_collisions(tmp_path, capsys):
    sub = _submission(tmp_path)
    old = sub / "lab" / "paper"
    old.mkdir()
    (old / "a.md").write_text("a")
    (old / "bundles").mkdir()
    (old / "bundles" / "b.tar.gz").write_bytes(b"b")
    assert main(["migrate-paper-dir", "--submission", str(sub)]) == 0
    assert (sub / "paper" / "a.md").read_text() == "a"
    assert (sub / "paper" / "bundles" / "b.tar.gz").exists()
    assert not old.exists()

    # A second legacy copy that collides is left in place and reported.
    old.mkdir()
    (old / "a.md").write_text("different")
    assert main(["migrate-paper-dir", "--submission", str(sub)]) == 1
    assert (sub / "paper" / "a.md").read_text() == "a"
    assert (old / "a.md").read_text() == "different"
    assert "a.md" in capsys.readouterr().err


def test_migrate_paper_dir_noop_without_legacy_dir(tmp_path, capsys):
    sub = _submission(tmp_path)
    assert main(["migrate-paper-dir", "--submission", str(sub)]) == 0
    assert "nothing to migrate" in capsys.readouterr().out
