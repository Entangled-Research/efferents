"""efferents CLI subcommand integration tests."""
from __future__ import annotations
import os
import shutil
from pathlib import Path

import pytest

from efferents.cli import main


SAMPLE = Path(__file__).parent / "fixtures" / "sample_submission"


def test_validate_ok(tmp_path, capsys):
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    exit_code = main(["validate", "--submission", str(sub)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "OK" in captured.out
    assert "sample-conjecture" in captured.out


def test_validate_missing_submission(tmp_path, capsys):
    exit_code = main(["validate", "--submission", str(tmp_path / "nope")])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "hypothesis.md" in captured.err or "hypothesis.md" in captured.out


def test_validate_unknown_subcommand_exits_2(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["bogus"])
    assert exc.value.code == 2  # argparse-style


def test_start_foreground_registers_and_runs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)

    called = []
    def fake_loop(**kwargs):
        called.append(1)
    monkeypatch.setattr("efferents.cli._orchestrator_loop", fake_loop)

    exit_code = main(["start", "--submission", str(sub)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "lab_id=sample-conjecture" in captured.out
    assert called == [1]

    from efferents.registry import Registry
    rec = Registry().get("sample-conjecture")
    assert rec is not None
    assert rec.lab_id == "sample-conjecture"

    # _init_lab_root must have provisioned runs table and context scaffold
    import sqlite3
    db = sub / "lab" / "runs.sqlite"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    finally:
        conn.close()
    assert "run_id" in cols
    assert "synthetic_loss" in cols, f"expected synthetic_loss column, got {cols}"

    context_log = sub / "context" / "research_log.md"
    assert context_log.exists()
    assert "sample-conjecture research log" in context_log.read_text()


def test_start_seeds_campaign_from_popper_operational_restatement(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    (sub / "hypothesis.md").write_text(
        "---\nslug: sample-conjecture\nfalsifiability_gate: passed\n"
        "status: active\n---\n\n"
        "# Claim title\n\n"
        "## Operational restatement\n\n"
        "Treatment improves the score by at least 10%.\n\n"
        "## Falsifier(s)\n\n- Less than 10% improvement.\n"
    )
    monkeypatch.setattr("efferents.cli._orchestrator_loop", lambda **kwargs: None)

    assert main(["start", "--submission", str(sub)]) == 0

    import sqlite3
    with sqlite3.connect(sub / "lab" / "runs.sqlite") as conn:
        question = conn.execute("SELECT question FROM campaigns").fetchone()[0]
    assert question == "Treatment improves the score by at least 10%."


def test_start_detach_writes_pidfile(tmp_path, monkeypatch):
    """Detach path forks; we test the post-fork bookkeeping via a stubbed daemonize call."""
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)

    fake_child_pid = 4242
    def fake_daemonize(lab_root, loop):
        return fake_child_pid
    monkeypatch.setattr("efferents.cli.daemon.daemonize_and_run", fake_daemonize)

    exit_code = main(["start", "--submission", str(sub), "--detach"])
    assert exit_code == 0

    from efferents.registry import Registry
    rec = Registry().get("sample-conjecture")
    assert rec is not None
    assert rec.pid == fake_child_pid


def test_status_running_lab(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    reg = Registry()
    reg.register(LabRecord(
        lab_id="x", submission_dir=str(tmp_path / "s"),
        lab_root=str(tmp_path / "s/lab"), pid=os.getpid(),
        started_at="2026-05-26T10:00:00Z", status="running",
    ))
    (tmp_path / "s" / "lab").mkdir(parents=True)
    (tmp_path / "s" / "lab" / "state.json").write_text("{}")

    exit_code = main(["status", "--lab-id", "x"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "running" in captured.out
    assert "x" in captured.out


def test_status_dead_pid_marks_crashed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    reg = Registry()
    reg.register(LabRecord(
        lab_id="y", submission_dir=str(tmp_path / "s"),
        lab_root=str(tmp_path / "s/lab"), pid=999999,
        started_at="2026-05-26T10:00:00Z", status="running",
    ))

    exit_code = main(["status", "--lab-id", "y"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "crashed" in captured.out.lower() or "dead" in captured.out.lower()


def test_status_unknown_lab(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    exit_code = main(["status", "--lab-id", "nope"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not found" in captured.err.lower() or "unknown" in captured.err.lower()


def test_stop_marks_registry_stopped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    reg = Registry()
    reg.register(LabRecord(
        lab_id="z", submission_dir="/x", lab_root="/x/lab",
        pid=999999, started_at="t", status="running",
    ))

    monkeypatch.setattr("efferents.cli.os.kill", lambda pid, sig: None)
    monkeypatch.setattr("efferents.cli.daemon.is_pid_alive", lambda pid: False)

    exit_code = main(["stop", "--lab-id", "z"])
    assert exit_code == 0
    assert reg.get("z").status == "stopped"


def test_stop_unknown_lab(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    exit_code = main(["stop", "--lab-id", "ghost"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unknown" in captured.err.lower() or "not found" in captured.err.lower()


def test_list_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    exit_code = main(["list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "no labs registered" in captured.out.lower() or "LAB_ID" in captured.out


def test_list_with_entries(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    reg = Registry()
    reg.register(LabRecord(
        lab_id="alpha", submission_dir="/a", lab_root="/a/lab",
        pid=os.getpid(), started_at="2026-05-26T10:00:00Z", status="running",
    ))
    reg.register(LabRecord(
        lab_id="beta", submission_dir="/b", lab_root="/b/lab",
        pid=999999, started_at="2026-05-25T10:00:00Z", status="running",
    ))
    exit_code = main(["list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "alpha" in captured.out
    assert "beta" in captured.out


# --- lifecycle robustness (registry loss, pidfile fallbacks, guards) ---------


def _sample(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    return sub


def test_foreground_then_detach_then_status_stop_survives_concurrent_readers(
    tmp_path, monkeypatch, capsys
):
    """Regression for the 2026-09-07 session: a bounded foreground run, then a
    detached start, then status/stop of the same lab — while something else
    (the dashboard's polling) keeps reading the registry. The record used to
    vanish (registry.json -> []) and `status`/`stop` reported "unknown lab_id".
    """
    import threading
    from efferents.registry import Registry

    sub = _sample(tmp_path, monkeypatch)
    monkeypatch.setattr("efferents.cli._orchestrator_loop", lambda **kw: None)
    fake_child = 4242
    monkeypatch.setattr("efferents.cli.daemon.daemonize_and_run", lambda root, loop: fake_child)
    alive = {fake_child}
    monkeypatch.setattr("efferents.cli.daemon.is_pid_alive", lambda pid: pid in alive)
    killed: list[int] = []
    monkeypatch.setattr(
        "efferents.cli.os.kill", lambda pid, sig: (killed.append(pid), alive.discard(pid)))

    stop = threading.Event()

    def poll():
        while not stop.is_set():
            Registry().list()
            Registry().get("sample-conjecture")

    pollers = [threading.Thread(target=poll) for _ in range(3)]
    for t in pollers:
        t.start()
    try:
        assert main(["start", "--submission", str(sub), "--max-iterations", "1"]) == 0
        assert Registry().get("sample-conjecture").status == "stopped"

        assert main(["start", "--submission", str(sub), "--detach"]) == 0
        rec = Registry().get("sample-conjecture")
        assert rec is not None and rec.pid == fake_child and rec.status == "running"

        assert main(["status", "--lab-id", "sample-conjecture"]) == 0
        out = capsys.readouterr().out
        assert "status=running" in out and f"pid={fake_child}" in out

        assert main(["stop", "--lab-id", "sample-conjecture"]) == 0
    finally:
        stop.set()
        for t in pollers:
            t.join()
    assert killed == [fake_child]
    assert Registry().get("sample-conjecture").status == "stopped"


def test_start_foreground_does_not_crash_when_record_lost(tmp_path, monkeypatch, capsys):
    """`update_status` in the `finally` used to raise KeyError."""
    sub = _sample(tmp_path, monkeypatch)
    registry_file = tmp_path / "home" / "registry.json"

    def loop_that_loses_registry(**kwargs):
        registry_file.write_text("[]")

    monkeypatch.setattr("efferents.cli._orchestrator_loop", loop_that_loses_registry)
    assert main(["start", "--submission", str(sub), "--max-iterations", "1"]) == 0
    assert not (sub / "lab" / "daemon.pid").exists()


def test_detach_reregisters_child_pid_even_if_record_lost(tmp_path, monkeypatch):
    sub = _sample(tmp_path, monkeypatch)
    registry_file = tmp_path / "home" / "registry.json"

    def fake_daemonize(lab_root, loop):
        registry_file.write_text("[]")  # record lost while forking
        return 4242

    monkeypatch.setattr("efferents.cli.daemon.daemonize_and_run", fake_daemonize)
    assert main(["start", "--submission", str(sub), "--detach"]) == 0
    from efferents.registry import Registry
    rec = Registry().get("sample-conjecture")
    assert rec is not None and rec.pid == 4242 and rec.status == "running"


def test_stop_by_submission_falls_back_to_pidfile(tmp_path, monkeypatch, capsys):
    sub = _sample(tmp_path, monkeypatch)
    (sub / "lab").mkdir()
    (sub / "lab" / "daemon.pid").write_text("31337")
    alive = {31337}
    killed: list[int] = []
    monkeypatch.setattr("efferents.cli.daemon.is_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr("efferents.cli.os.kill", lambda pid, sig: (killed.append(pid), alive.discard(pid)))

    assert main(["stop", "--submission", str(sub)]) == 0
    out = capsys.readouterr().out
    assert killed == [31337]
    assert "stopped lab_id=sample-conjecture" in out
    assert not (sub / "lab" / "daemon.pid").exists()


def test_status_by_lab_root_without_registry(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    root = tmp_path / "somewhere" / "lab"
    root.mkdir(parents=True)
    (root / "daemon.pid").write_text(str(os.getpid()))

    assert main(["status", "--lab-root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "status=running" in out
    assert f"pid={os.getpid()} (alive=True)" in out
    assert "registry record missing" in out


def test_stop_reports_missing_registry_and_pidfile(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    root = tmp_path / "lab"
    root.mkdir()
    assert main(["stop", "--lab-root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "no registry record" in err and "daemon.pid" in err

    assert main(["stop", "--lab-id", "ghost"]) == 1
    err = capsys.readouterr().err
    assert "unknown lab_id: ghost" in err and "--submission" in err


def test_stop_requires_a_selector(capsys):
    assert main(["stop"]) == 2


def test_start_refuses_live_pidfile_without_registry_unless_force(tmp_path, monkeypatch, capsys):
    sub = _sample(tmp_path, monkeypatch)
    (sub / "lab").mkdir()
    (sub / "lab" / "daemon.pid").write_text(str(os.getpid()))  # alive, not in registry
    called = []
    monkeypatch.setattr("efferents.cli._orchestrator_loop", lambda **kw: called.append(1))

    assert main(["start", "--submission", str(sub)]) == 1
    assert "already has a live daemon" in capsys.readouterr().err
    assert called == []

    assert main(["start", "--submission", str(sub), "--force"]) == 0
    assert called == [1]


def test_start_replaces_running_record_with_dead_pid(tmp_path, monkeypatch):
    sub = _sample(tmp_path, monkeypatch)
    from efferents.registry import LabRecord, Registry
    Registry().register(LabRecord(
        lab_id="sample-conjecture", submission_dir=str(sub), lab_root=str(sub / "lab"),
        pid=999999, started_at="old", status="running",
    ))
    (sub / "lab").mkdir()
    (sub / "lab" / "halt_reason.txt").write_text("unhandled exception: boom")
    monkeypatch.setattr("efferents.cli._orchestrator_loop", lambda **kw: None)

    assert main(["start", "--submission", str(sub)]) == 0
    rec = Registry().get("sample-conjecture")
    assert rec.pid == os.getpid() and rec.started_at != "old"
    assert not (sub / "lab" / "halt_reason.txt").exists()


def test_status_reports_workspace_url_when_serve_alive(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    root = tmp_path / "s" / "lab"
    root.mkdir(parents=True)
    Registry().register(LabRecord(
        lab_id="w", submission_dir=str(tmp_path / "s"), lab_root=str(root),
        pid=os.getpid(), started_at="t", status="running",
    ))
    import json
    (root / "serve.json").write_text(json.dumps(
        {"pid": os.getpid(), "port": 8800, "url": "http://localhost:8800"}))
    assert main(["status", "--lab-id", "w"]) == 0
    assert "workspace=http://localhost:8800" in capsys.readouterr().out

    (root / "serve.json").write_text(json.dumps(
        {"pid": 999999, "port": 8800, "url": "http://localhost:8800"}))
    assert main(["status", "--lab-id", "w"]) == 0
    assert "workspace=" not in capsys.readouterr().out


def test_serve_writes_and_removes_serve_json(tmp_path, monkeypatch):
    import json
    root = tmp_path / "lab"
    root.mkdir()
    observed = {}

    def fake_serve(connected_root, port, open_browser, paused_demo=False):
        observed.update(json.loads((root / "serve.json").read_text()))

    monkeypatch.setattr("efferents.dashboard.server.serve", fake_serve)
    assert main(["serve", "--lab-root", str(root), "--port", "8123", "--no-open"]) == 0
    assert observed == {"pid": os.getpid(), "port": 8123, "url": "http://localhost:8123"}
    assert not (root / "serve.json").exists()


def test_list_shows_halt_reason_truncated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    root = tmp_path / "lab"
    root.mkdir()
    Registry().register(LabRecord(
        lab_id="h", submission_dir="/h", lab_root=str(root),
        pid=999999, started_at="t", status="running",
    ))
    (root / "halt_reason.txt").write_text("unhandled exception: " + "x" * 200)
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    line = next(row for row in out.splitlines() if row.startswith("h "))
    assert "crashed" in line
    assert "unhandled exception: xxx" in line and line.endswith("…")
    assert "x" * 100 not in line


def test_orchestrator_loop_passes_total_cap_from_lab_yaml(tmp_path, monkeypatch):
    import yaml
    from efferents import lab as lab_mod
    from efferents.agents import orchestrator as orch_mod
    from efferents.lab import LabConfig

    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw["budget"] = {"daily_cap_usd": 3, "total_cap_usd": 42}
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    lab_mod.set_config(LabConfig.from_submission(sub))

    seen: dict = {}

    class FakeOrchestrator:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.paths = object()

        def run(self, *, max_iterations=None):
            seen["ran"] = max_iterations

    monkeypatch.setattr(orch_mod, "Orchestrator", FakeOrchestrator)
    monkeypatch.setattr("efferents.agents.progress.write_progress", lambda *a, **k: None)

    from efferents.cli import _orchestrator_loop
    _orchestrator_loop(lab_root=sub / "lab", context_dir=sub / "context", dry_run=True, max_iterations=1)
    assert seen["daily_cap_usd"] == 3.0
    assert seen["total_cap_usd"] == 42.0
    assert seen["ran"] == 1


def test_steer_cli_records_charter_and_queue(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    before = (sub / "hypothesis.md").read_text()

    rc = main(["steer", "--submission", str(sub), "use literature; no repeats", "--by", "funder"])
    out = capsys.readouterr().out
    assert rc == 0
    assert f"charter={sub / 'context' / 'popper.md'}" in out
    assert f"steering={sub / 'lab' / 'steering.jsonl'}" in out
    assert "daemon will pick this up on its next step" in out
    assert "> use literature; no repeats" in (sub / "context" / "popper.md").read_text()
    assert '"ack": null' in (sub / "lab" / "steering.jsonl").read_text()
    assert (sub / "hypothesis.md").read_text() == before

    # Mutually exclusive pause/resume is an argparse error.
    with pytest.raises(SystemExit) as exc:
        main(["steer", "--submission", str(sub), "--pause", "--resume"])
    assert exc.value.code == 2


# --- patch / block review commands ------------------------------------------

def _git(cwd: Path, *argv: str) -> str:
    import subprocess
    res = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    return res.stdout


def _git_submission(tmp_path: Path) -> tuple[Path, Path]:
    """A submission dir that is its own git repo with one committed file and
    an initialized lab root."""
    sub = tmp_path / "sub"
    shutil.copytree(SAMPLE, sub)
    (sub / "hello.txt").write_text("hello\n")
    _git(sub, "init", "-q")
    _git(sub, "config", "user.email", "t@example.com")
    _git(sub, "config", "user.name", "t")
    _git(sub, "add", "-A")
    _git(sub, "commit", "-q", "-m", "init")
    lab_root = sub / "lab"
    lab_root.mkdir()
    (lab_root / "state.json").write_text("{}")
    return sub, lab_root


def _register_diff(lab_root: Path, name: str, body: str) -> Path:
    from efferents.agents.state import register_patch
    pdir = lab_root / "patches"
    pdir.mkdir(exist_ok=True)
    diff = pdir / f"{name}.diff"
    diff.write_text(body)
    md = diff.with_suffix(".md")
    md.write_text(f"# {name}\n")
    register_patch(lab_root, path=diff, rationale_path=md, name=name, files=["hello.txt"])
    return diff


HELLO_DIFF = (
    "--- a/hello.txt\n"
    "+++ b/hello.txt\n"
    "@@ -1 +1 @@\n"
    "-hello\n"
    "+hello world\n"
)


def test_patch_list_apply_and_reject(tmp_path, capsys):
    import json
    from efferents.agents.state import pending_patches, read_jsonl
    sub, lab_root = _git_submission(tmp_path)
    diff = _register_diff(lab_root, "greet", HELLO_DIFF)
    other = _register_diff(lab_root, "later", HELLO_DIFF)

    assert main(["patch", "list", "--submission", str(sub)]) == 0
    out = capsys.readouterr().out
    assert str(diff) in out and "name=greet" in out and "files=hello.txt" in out

    assert main(["patch", "apply", str(diff), "--submission", str(sub), "--by", "masha"]) == 0
    out = capsys.readouterr().out
    assert f"applied {diff}" in out
    assert (sub / "hello.txt").read_text() == "hello world\n"
    assert [p["name"] for p in pending_patches(lab_root)] == ["later"]
    ledger = {r["name"]: r for r in read_jsonl(lab_root / "patches" / "patches.jsonl")}
    assert ledger["greet"]["status"] == "applied"
    assert ledger["greet"]["status_by"] == "masha"
    state = json.loads((lab_root / "state.json").read_text())
    assert [p["name"] for p in state["pending_patches"]] == ["later"]

    # The second patch no longer applies (hello.txt already changed) and the
    # working tree is now dirty for that file: refuse and keep it pending.
    assert main(["patch", "apply", str(other), "--submission", str(sub)]) == 1
    err = capsys.readouterr().err
    assert "hello.txt" in err
    assert pending_patches(lab_root)[0]["name"] == "later"

    # Reject by bare file name; the ledger is keyed by the recorded path.
    assert main(["patch", "reject", other.name, "--submission", str(sub)]) == 0
    assert f"rejected {other}" in capsys.readouterr().out
    assert pending_patches(lab_root) == []
    assert main(["patch", "list", "--submission", str(sub)]) == 0
    assert "no pending patches" in capsys.readouterr().out


def test_patch_apply_refuses_unstaged_changes_to_touched_files(tmp_path, capsys):
    from efferents.agents.state import pending_patches
    sub, lab_root = _git_submission(tmp_path)
    diff = _register_diff(lab_root, "greet", HELLO_DIFF)
    (sub / "hello.txt").write_text("hello\nlocal edit\n")

    assert main(["patch", "apply", str(diff), "--submission", str(sub)]) == 1
    err = capsys.readouterr().err
    assert "unstaged changes" in err
    assert "hello.txt" in err
    assert (sub / "hello.txt").read_text() == "hello\nlocal edit\n"
    assert [p["name"] for p in pending_patches(lab_root)] == ["greet"]

    # Unstaged changes elsewhere do not block the patch.
    (sub / "hello.txt").write_text("hello\n")
    (sub / "README.md").write_text("unrelated\n")
    assert main(["patch", "apply", str(diff), "--submission", str(sub)]) == 0
    assert (sub / "hello.txt").read_text() == "hello world\n"


def test_patch_apply_errors(tmp_path, capsys):
    sub, lab_root = _git_submission(tmp_path)
    assert main(["patch", "apply", "--submission", str(sub)]) == 2
    assert "needs the patch path" in capsys.readouterr().err
    assert main(["patch", "apply", str(lab_root / "nope.diff"), "--submission", str(sub)]) == 1
    assert "no such file" in capsys.readouterr().err
    assert main(["patch", "reject", "nope.diff", "--submission", str(sub)]) == 1
    assert "not in the ledger" in capsys.readouterr().err


def test_block_list_and_resolve(tmp_path, capsys):
    import json
    from efferents.agents.state import open_blocks, record_blocked
    sub = tmp_path / "sub"
    lab_root = sub / "lab"
    lab_root.mkdir(parents=True)
    (lab_root / "state.json").write_text("{}")

    assert main(["block", "list", "--submission", str(sub)]) == 0
    assert "no open blocks" in capsys.readouterr().out

    rec = record_blocked(
        lab_root, student_id="primary", summary="executor lacks a --seed flag",
        evidence=["run-12"], proposed_change="add --seed",
    )
    assert main(["block", "list", "--submission", str(sub)]) == 0
    out = capsys.readouterr().out
    assert rec["id"] in out and "--seed flag" in out and "evidence: run-12" in out

    assert main(["block", "resolve", "--submission", str(sub)]) == 2
    assert main(["block", "resolve", rec["id"], "--submission", str(sub), "--by", "masha"]) == 0
    assert f"resolved {rec['id']} by masha" in capsys.readouterr().out
    assert open_blocks(lab_root) == []
    assert json.loads((lab_root / "state.json").read_text())["blocked_on_infrastructure"] == []
    assert main(["block", "resolve", rec["id"], "--submission", str(sub)]) == 1
    assert "no open block" in capsys.readouterr().err


def test_status_prints_pending_patch_and_block_counts(tmp_path, monkeypatch, capsys):
    import json
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    from efferents.registry import LabRecord, Registry
    Registry().register(LabRecord(
        lab_id="x", submission_dir=str(tmp_path / "s"),
        lab_root=str(tmp_path / "s/lab"), pid=os.getpid(),
        started_at="2026-05-26T10:00:00Z", status="running",
    ))
    root = tmp_path / "s" / "lab"
    root.mkdir(parents=True)
    (root / "state.json").write_text(json.dumps({
        "pending_patches": [{"path": "a.diff"}, {"path": "b.diff"}],
        "blocked_on_infrastructure": [{"id": "blk-1"}],
    }))

    assert main(["status", "--lab-id", "x"]) == 0
    out = capsys.readouterr().out
    assert "pending_patches=2" in out
    assert "blocked_on_infrastructure=1" in out

    (root / "state.json").write_text("{}")
    assert main(["status", "--lab-id", "x"]) == 0
    out = capsys.readouterr().out
    assert "pending_patches" not in out and "blocked_on_infrastructure" not in out
