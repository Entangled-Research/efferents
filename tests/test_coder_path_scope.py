"""Coder's target globs, new-file regex, and smoke command read from LabConfig."""
from __future__ import annotations
import subprocess
from pathlib import Path

from efferents.agents import coder
from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Source,
)
from efferents import lab as lab_mod


def _install(tmp_path: Path, source_subdir: str = "my_research", allowed=("**/*.py",)):
    src = tmp_path / source_subdir
    src.mkdir()
    (src / "default.yaml").touch()
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src, allowed_patterns=allowed),
        executor=Executor(
            run_command=f"python -m {source_subdir}.run --config {{config_path}}",
            smoke_command=f"python -m {source_subdir}.run --config {{config_path}} --smoke",
            config_template=src / "default.yaml",
        ),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    return cfg


def test_target_globs_use_source_dir(tmp_path):
    _install(tmp_path)
    globs = coder._target_globs()
    src_abs = str((tmp_path / "my_research").resolve())
    assert any(src_abs in g for g in globs)
    # config_template is also in target globs
    assert any("default.yaml" in g for g in globs)


def test_new_file_path_re_uses_source_dir(tmp_path):
    _install(tmp_path)
    pattern = coder._new_file_path_re()
    src_abs = str((tmp_path / "my_research").resolve())
    assert pattern.match(f"{src_abs}/foo.py")
    assert not pattern.match(f"{src_abs}/sub/foo.py")  # no nested dirs
    assert not pattern.match("reference_lab/foo.py")  # legacy path no longer matches


def test_smoke_command_renders_config_path(tmp_path):
    _install(tmp_path)
    cmd = coder._smoke_command(Path("/some/config.yaml"))
    assert "{config_path}" not in cmd
    assert "/some/config.yaml" in cmd
    assert "--smoke" in cmd


def test_smoke_command_falls_back_to_run_command(tmp_path):
    src = tmp_path / "r"
    src.mkdir()
    (src / "c.yaml").touch()
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(
            run_command="python -m r.run --config {config_path}",
            smoke_command=None,  # no smoke variant
            config_template=src / "c.yaml",
        ),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    cmd = coder._smoke_command(Path("/some/config.yaml"))
    assert "--smoke" not in cmd
    assert "/some/config.yaml" in cmd


def test_resolve_coder_path_rejects_dotdot_escape(tmp_path):
    cfg = _install(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n")
    candidate = cfg.source.dir / ".." / "outside.py"
    try:
        coder._resolve_coder_path(str(candidate), tmp_path)
    except ValueError as exc:
        assert "escapes source.dir" in str(exc)
    else:
        raise AssertionError("dotdot escape was accepted")


def test_resolve_coder_path_rejects_symlink_escape(tmp_path):
    cfg = _install(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n")
    link = cfg.source.dir / "linked.py"
    link.symlink_to(outside)
    try:
        coder._resolve_coder_path(str(link), tmp_path)
    except ValueError as exc:
        assert "escapes source.dir" in str(exc)
    else:
        raise AssertionError("symlink escape was accepted")


def test_resolve_coder_path_obeys_allowed_patterns(tmp_path):
    cfg = _install(tmp_path, allowed=("models/*.py",))
    models = cfg.source.dir / "models"
    models.mkdir()
    allowed = models / "model.py"
    allowed.write_text("x = 1\n")
    denied = cfg.source.dir / "run.py"
    denied.write_text("x = 1\n")

    assert coder._resolve_coder_path(str(allowed), tmp_path) == allowed.resolve()
    try:
        coder._resolve_coder_path(str(denied), tmp_path)
    except ValueError as exc:
        assert "allowed_patterns" in str(exc)
    else:
        raise AssertionError("disallowed source path was accepted")


def test_gather_source_skips_symlink_escape(tmp_path):
    cfg = _install(tmp_path)
    inside = cfg.source.dir / "inside.py"
    inside.write_text("inside = True\n")
    outside = tmp_path / "outside.py"
    outside.write_text("daemon_secret = 'do-not-read'\n")
    (cfg.source.dir / "linked.py").symlink_to(outside)

    gathered = coder.gather_source(tmp_path)

    assert str(inside.resolve()) in gathered
    assert all("daemon_secret" not in text for text in gathered.values())


def test_git_commit_refuses_pre_staged_user_changes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Coder Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "coder@example.invalid"], cwd=repo, check=True
    )
    user_file = repo / "user.txt"
    coder_file = repo / "coder.py"
    user_file.write_text("initial\n")
    coder_file.write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repo, check=True)

    user_file.write_text("user staged work\n")
    subprocess.run(["git", "add", "user.txt"], cwd=repo, check=True)
    coder_file.write_text("coder work\n")

    sha = coder._git_commit(repo, "summary", "proposal", [str(coder_file)])

    assert sha is None
    staged = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert staged == ["user.txt"]
