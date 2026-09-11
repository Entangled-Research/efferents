"""efferents CLI entry point.

  efferents validate --submission <dir>
  efferents start    --submission <dir> [--detach] [--lab-root <path>]
  efferents status   [--lab-id <id> | --submission <dir> | --lab-root <dir>]
  efferents stop     (--lab-id <id> | --submission <dir> | --lab-root <dir>)
  efferents list
  efferents steer    --submission <dir> ["<text>" | --file <path>] [--by <who>]
                     [--pause | --resume] [--supersede <hypothesis.md>]
  efferents patch    --submission <dir> (list | apply <path> | reject <path>) [--by <who>]
  efferents block    --submission <dir> (list | resolve <id>) [--by <who>]
  efferents public-check [repository]

The `main(argv=None)` entry point is exposed for tests; pyproject.toml
console_scripts will point at `efferents.cli:main` (Task 16).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from efferents import daemon
from efferents import lab as lab_mod
from efferents.envfile import load_dotenv
from efferents.lab import LabConfig, SubmissionError
from efferents.registry import LabRecord, Registry


def _cmd_validate(args: argparse.Namespace) -> int:
    sub = Path(args.submission).resolve()
    try:
        cfg = LabConfig.from_submission(sub)
    except SubmissionError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1
    print(f"OK lab_id={cfg.lab_id} domain={cfg.domain} source_dir={cfg.source.dir}")
    return 0


def _orchestrator_loop(
    *,
    lab_root: Path,
    context_dir: Path,
    dry_run: bool = False,
    max_iterations: int | None = None,
    submission_dir: Path | None = None,
) -> None:
    # Indirection so tests can monkey-patch the loop body without forking.
    # In production, builds an Orchestrator from the active LabConfig and
    # runs it. The orchestrator import is deferred to avoid pulling heavy
    # transitive deps at CLI startup.
    from efferents.agents import orchestrator  # noqa: PLC0415
    cfg = lab_mod.get_config()
    # lab.yaml cadence, then EFFERENTS_CADENCE_* overrides from the daemon env.
    cadence = lab_mod.cadence_with_env(cfg.cadence)
    o = orchestrator.Orchestrator(
        lab_dir=lab_root,
        context_dir=context_dir,
        daily_cap_usd=cfg.budget.daily_cap_usd,
        total_cap_usd=cfg.budget.total_cap_usd,
        dry_run=dry_run,
        startup_message=(
            f"efferents daemon for lab_id={cfg.lab_id}\n\n"
            f"cadence: {cadence.as_kwargs()}"
        ),
        submission_dir=submission_dir if submission_dir is not None else context_dir.parent,
        **cadence.as_kwargs(),
    )
    o.run(max_iterations=max_iterations)
    # Always leave a current static artifact, including bounded/offline runs
    # that stop before the Analyst cadence fires.
    from efferents.agents.progress import write_progress  # noqa: PLC0415
    write_progress(o.paths, context_dir=context_dir)


def _init_lab_root(
    submission_dir: Path, lab_root: Path, cfg: LabConfig | None = None
) -> None:
    """Create lab/ dir + run migrations + copy provenance files.

    ``cfg`` defaults to the process-global active config; callers that manage
    several labs in one process (the dashboard, a cluster) pass it explicitly.
    """
    cfg = cfg or lab_mod.get_config()
    lab_root.mkdir(parents=True, exist_ok=True)
    (lab_root / "progress").mkdir(exist_ok=True)
    (lab_root / "papers").mkdir(exist_ok=True)

    shutil.copy2(submission_dir / "hypothesis.md", lab_root / "hypothesis.md")
    shutil.copy2(submission_dir / "lab.yaml", lab_root / "lab.yaml")

    state_json = lab_root / "state.json"
    if not state_json.exists():
        state_json.write_text("{}")

    from efferents.migrations.runner import (  # noqa: PLC0415
        apply_campaigns_migration,
        ensure_runs_table,
    )
    apply_campaigns_migration(lab_root / "runs.sqlite")
    ensure_runs_table(lab_root / "runs.sqlite", cfg)

    # The submitted, already-falsifiable hypothesis is the lab's initial
    # campaign. This gives the very first run a provenance anchor before the
    # Researcher opens follow-on campaigns.
    import sqlite3  # noqa: PLC0415
    from efferents.agents.state import campaign_insert  # noqa: PLC0415

    db = lab_root / "runs.sqlite"
    with sqlite3.connect(db) as conn:
        # WAL lets dashboard readers coexist with the daemon's writes; the
        # mode is persistent on the file, so setting it here is enough.
        conn.execute("PRAGMA journal_mode=WAL")
        n_campaigns = int(
            conn.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0]
        )
    if n_campaigns == 0:
        hypothesis_text = (submission_dir / "hypothesis.md").read_text()
        digest = hashlib.sha256(hypothesis_text.encode()).hexdigest()
        question = _markdown_section(hypothesis_text, "Claim")
        if not question:
            question = _markdown_section(hypothesis_text, "Operational restatement")
        if not question:
            question = f"Initial submitted hypothesis for {cfg.lab_id}"
        campaign_insert(
            db,
            id=f"submission-{digest[:12]}",
            lab_id=cfg.lab_id,
            question=question,
            hypothesis_path="hypothesis.md",
            hypothesis_hash=f"sha256:{digest}",
            student_id=cfg.default_student_id,
            headline_metric=cfg.metrics.headline.column,
            headline_direction=cfg.metrics.headline.direction,
        )

    context_dir = submission_dir / "context"
    context_dir.mkdir(exist_ok=True)
    research_log = context_dir / "research_log.md"
    if not research_log.exists():
        research_log.write_text(
            f"# {cfg.lab_id} research log\n\n"
            "*(empty — populate to guide the Researcher; "
            "the lab will operate from the hypothesis if left blank)*\n"
        )


def _markdown_section(text: str, heading: str) -> str:
    lines = text.splitlines()
    wanted = f"## {heading}".lower()
    out: list[str] = []
    capture = False
    for line in lines:
        if line.startswith("## "):
            if capture:
                break
            capture = line.strip().lower() == wanted
            continue
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def _cmd_start(args: argparse.Namespace) -> int:
    sub = Path(args.submission).resolve()
    try:
        cfg = LabConfig.from_submission(sub)
    except SubmissionError as e:
        print(f"validation failed: {e}", file=sys.stderr)
        return 1

    lab_root = Path(args.lab_root).resolve() if args.lab_root else (sub / "lab").resolve()

    # Load the submission's .env (if any) so the daemon — including a detached
    # child, which inherits this process's os.environ — can resolve
    # ANTHROPIC_API_KEY without it being exported in the launching shell.
    load_dotenv(sub / ".env")

    lab_mod.set_config(cfg)
    _init_lab_root(sub, lab_root, cfg=cfg)
    os.chdir(sub)

    force = getattr(args, "force", False)
    # Pidfile guard: independent of the registry, so a lost or stale record
    # can never let a second daemon loose on the same lab root.
    pidfile_pid = daemon.read_pidfile(lab_root / "daemon.pid")
    if pidfile_pid is not None and daemon.is_pid_alive(pidfile_pid) and not force:
        print(
            f"lab_root={lab_root} already has a live daemon pid={pidfile_pid} "
            f"({lab_root / 'daemon.pid'}); use --force to start anyway",
            file=sys.stderr,
        )
        return 1

    started_at = datetime.now(timezone.utc).isoformat()
    reg = Registry()
    existing = reg.get(cfg.lab_id)
    if (
        existing is not None
        and existing.status == "running"
        and daemon.is_pid_alive(existing.pid)
        and not force
    ):
        print(
            f"lab_id={cfg.lab_id} is already running as pid={existing.pid}",
            file=sys.stderr,
        )
        return 1
    # A "running" record whose pid is dead is a crash leftover: replace it.
    rec = LabRecord(
        lab_id=cfg.lab_id,
        submission_dir=str(sub),
        lab_root=str(lab_root),
        pid=os.getpid(),
        started_at=started_at,
        status="running",
    )
    reg.register(rec)
    # A halt reason belongs to the previous run; `list`/`status` would
    # otherwise show it next to a live daemon.
    daemon.clear_pidfile(lab_root / "halt_reason.txt")

    print(f"lab_id={cfg.lab_id} pid={os.getpid()} dashboard={lab_root}/progress.html")

    def loop() -> None:
        _orchestrator_loop(
            lab_root=lab_root,
            context_dir=sub / "context",
            dry_run=args.dry_run,
            max_iterations=args.max_iterations,
            submission_dir=sub,
        )

    if args.detach:
        rec.pid = daemon.daemonize_and_run(lab_root, loop)
        # Re-register from the record we built, not from a fresh `get()`: if
        # the record vanished meanwhile this restores it with the child pid.
        reg.register(rec)
        return 0

    try:
        daemon.run_foreground(lab_root, loop)
    finally:
        reg.update_status(cfg.lab_id, "stopped")
    return 0


def _halt_reason(lab_root: Path, limit: int = 60) -> str:
    halt = lab_root / "halt_reason.txt"
    if not halt.exists():
        return ""
    text = " ".join(halt.read_text().split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cmd_migrate_paper_dir(args: argparse.Namespace) -> int:
    """Move Writer output from the legacy ``lab/paper/`` to ``<submission>/paper/``.

    Refuses to overwrite: a file that exists at both locations is left in
    place and reported, so provenance is never clobbered.
    """
    sub = Path(args.submission).resolve()
    lab_root = Path(args.lab_root).resolve() if args.lab_root else (sub / "lab").resolve()
    old_dir = lab_root / "paper"
    new_dir = sub / "paper"
    if not old_dir.is_dir():
        print(f"nothing to migrate: {old_dir} does not exist")
        return 0
    new_dir.mkdir(parents=True, exist_ok=True)
    moved, collisions = 0, []
    for path in sorted(old_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(old_dir)
        target = new_dir / rel
        if target.exists():
            collisions.append(str(rel))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        moved += 1
    print(f"moved {moved} file(s) from {old_dir} to {new_dir}")
    if collisions:
        print("left in place (already present at destination):", file=sys.stderr)
        for rel in collisions:
            print(f"  {rel}", file=sys.stderr)
        return 1
    # Remove now-empty directories so the legacy location disappears cleanly.
    for d in sorted((d for d in old_dir.rglob("*") if d.is_dir()), reverse=True):
        if not any(d.iterdir()):
            d.rmdir()
    if not any(old_dir.iterdir()):
        old_dir.rmdir()
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    reg = Registry()
    records = reg.list()
    if not records:
        print("no labs registered")
        return 0
    print(f"{'LAB_ID':<24} {'STATUS':<10} {'STARTED':<25} {'SUBMISSION':<40} HALT_REASON")
    for r in records:
        status = r.status
        if status == "running" and not daemon.is_pid_alive(r.pid):
            status = "crashed"
        halt = _halt_reason(Path(r.lab_root))
        line = f"{r.lab_id:<24} {status:<10} {r.started_at:<25} {r.submission_dir:<40}"
        print(f"{line} {halt}".rstrip())
    return 0


def _resolve_lab(args: argparse.Namespace) -> tuple[str | None, Path | None, LabRecord | None]:
    """Locate a lab by --lab-id, --submission, or --lab-root.

    Returns (lab_id, lab_root, registry record). Any of them may be None; the
    lab root is derived from the directory flags so a lab remains addressable
    even when the registry has lost its record.
    """
    reg = Registry()
    lab_id = getattr(args, "lab_id", None)
    submission = getattr(args, "submission", None)
    lab_root_arg = getattr(args, "lab_root", None)

    lab_root: Path | None = None
    if lab_root_arg:
        lab_root = Path(lab_root_arg).resolve()
    elif submission:
        lab_root = (Path(submission).resolve() / "lab").resolve()

    rec = reg.get(lab_id) if lab_id else None
    if rec is None and lab_root is not None:
        for r in reg.list():
            if Path(r.lab_root).resolve() == lab_root:
                rec = r
                break
    if lab_id is None and rec is not None:
        lab_id = rec.lab_id
    if lab_id is None:
        for source in (Path(submission).resolve() if submission else None, lab_root):
            if source is None:
                continue
            try:
                lab_id = LabConfig.from_submission(source, check_paths=False).lab_id
                break
            except SubmissionError:
                continue
    if lab_root is None and rec is not None:
        lab_root = Path(rec.lab_root)
    return lab_id, lab_root, rec


def _unknown_lab(args: argparse.Namespace, lab_id: str | None, lab_root: Path | None) -> int:
    where = f"lab_id: {lab_id}" if lab_id else f"lab at {lab_root}"
    if lab_root is None:
        hint = "no registry record; pass --submission <dir> or --lab-root <dir>"
    else:
        hint = f"no registry record and no live {lab_root / 'daemon.pid'}"
    print(f"unknown {where} ({hint})", file=sys.stderr)
    return 1


def _workspace_url(lab_root: Path) -> str | None:
    """URL of a dashboard server started with `efferents serve` for this root."""
    serve_json = lab_root / "serve.json"
    if not serve_json.exists():
        return None
    try:
        info = json.loads(serve_json.read_text())
    except (OSError, ValueError):
        return None
    pid = info.get("pid")
    if isinstance(pid, int) and daemon.is_pid_alive(pid):
        return info.get("url")
    return None


def _cmd_status(args: argparse.Namespace) -> int:
    if not (args.lab_id or args.submission or args.lab_root):
        return _cmd_list(args)
    lab_id, lab_root, rec = _resolve_lab(args)
    pidfile_pid = daemon.read_pidfile(lab_root / "daemon.pid") if lab_root else None
    if rec is None and pidfile_pid is None:
        return _unknown_lab(args, lab_id, lab_root)

    if rec is not None:
        pid = rec.pid
        alive = daemon.is_pid_alive(pid)
        status = rec.status
        if rec.status == "running" and not alive:
            status = "crashed"
            Registry().update_status(rec.lab_id, "crashed")
        started_at = rec.started_at
    else:
        pid = pidfile_pid
        alive = daemon.is_pid_alive(pid)
        status = "running" if alive else "crashed"
        started_at = "unknown (registry record missing; pid from daemon.pid)"
    assert lab_root is not None

    print(f"lab_id={lab_id}")
    print(f"status={status}")
    print(f"started_at={started_at}")
    print(f"pid={pid} (alive={alive})")
    state_json = lab_root / "state.json"
    if state_json.exists():
        mtime = datetime.fromtimestamp(state_json.stat().st_mtime, tz=timezone.utc).isoformat()
        print(f"last_activity={mtime}")
        for key in ("pending_patches", "blocked_on_infrastructure"):
            count = _state_list_count(state_json, key)
            if count is not None:
                print(f"{key}={count}")
    print(f"dashboard=file://{lab_root}/progress.html")
    workspace = _workspace_url(lab_root)
    if workspace:
        print(f"workspace={workspace}")
    halt = lab_root / "halt_reason.txt"
    if halt.exists():
        print(f"halt_reason={halt.read_text().strip()}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    if not (args.lab_id or args.submission or args.lab_root):
        print("stop: pass --lab-id, --submission, or --lab-root", file=sys.stderr)
        return 2
    lab_id, lab_root, rec = _resolve_lab(args)
    pidfile_pid = daemon.read_pidfile(lab_root / "daemon.pid") if lab_root else None
    if rec is None and pidfile_pid is None:
        return _unknown_lab(args, lab_id, lab_root)

    pids = {p for p in (rec.pid if rec else None, pidfile_pid) if p}
    for pid in sorted(pids):
        if not daemon.is_pid_alive(pid):
            continue
        os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not daemon.is_pid_alive(pid):
                break
            time.sleep(0.1)
        if daemon.is_pid_alive(pid):
            os.kill(pid, signal.SIGKILL)
            print(f"warning: SIGTERM ignored, sent SIGKILL to PID {pid}", file=sys.stderr)

    if rec is not None:
        Registry().update_status(rec.lab_id, "stopped")
    if lab_root is not None and pidfile_pid is not None and not daemon.is_pid_alive(pidfile_pid):
        daemon.clear_pidfile(lab_root / "daemon.pid")
    print(f"stopped lab_id={lab_id}" if lab_id else f"stopped lab at {lab_root}")
    return 0


def _state_list_count(state_json: Path, key: str) -> int | None:
    """Length of the list mirrored under ``key`` in state.json, or None when
    the file is unreadable or the key is absent."""
    try:
        state = json.loads(state_json.read_text())
    except (OSError, ValueError):
        return None
    value = state.get(key) if isinstance(state, dict) else None
    return len(value) if isinstance(value, list) else None


def _submission_lab_root(args: argparse.Namespace) -> tuple[Path, Path]:
    sub = Path(args.submission).resolve()
    lab_root = Path(args.lab_root).resolve() if args.lab_root else sub / "lab"
    return sub, lab_root


def _ledger_patch_path(lab_root: Path, path: str) -> str:
    """Map a user-supplied patch path onto the ledger's recorded path (the
    ledger keys patches by the string the Coder wrote; accept a relative
    path, a resolved path, or just the file name)."""
    from efferents.agents.state import pending_patches  # noqa: PLC0415
    given = Path(path)
    resolved = given.resolve()
    for rec in pending_patches(lab_root):
        recorded = str(rec.get("path", ""))
        if recorded in (path, str(resolved)) or (
            recorded and Path(recorded).resolve() == resolved
        ) or (
            given.name == Path(recorded).name and given.parent == Path(".")
        ):
            return recorded
    return str(resolved)


def _patch_files(diff_path: Path) -> list[str]:
    """Paths a unified diff touches, from its ``+++ b/…`` / ``--- a/…`` headers."""
    files: list[str] = []
    try:
        lines = diff_path.read_text().splitlines()
    except OSError:
        return files
    for line in lines:
        for prefix in ("+++ b/", "--- a/"):
            if line.startswith(prefix):
                name = line[len(prefix):].split("\t")[0].strip()
                if name and name not in files:
                    files.append(name)
    return files


def _git(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *argv], cwd=str(cwd), capture_output=True, text=True, check=False,
    )


def _cmd_patch(args: argparse.Namespace) -> int:
    from efferents.agents.state import mark_patch, pending_patches  # noqa: PLC0415

    sub, lab_root = _submission_lab_root(args)
    if args.action == "list":
        pending = pending_patches(lab_root)
        if not pending:
            print("no pending patches")
            return 0
        for rec in pending:
            files = ", ".join(rec.get("files") or []) or "-"
            print(f"{rec.get('ts', '-')}  {rec.get('path')}")
            print(f"    name={rec.get('name')}  student={rec.get('student_id') or '-'}  "
                  f"files={files}")
            if rec.get("rationale_path"):
                print(f"    rationale={rec['rationale_path']}")
        return 0

    if not args.path:
        print(f"patch: {args.action} needs the patch path", file=sys.stderr)
        return 2
    recorded = _ledger_patch_path(lab_root, args.path)

    if args.action == "reject":
        if not mark_patch(lab_root, recorded, "rejected", by=args.by):
            print(f"patch: {args.path} is not in the ledger at {lab_root}", file=sys.stderr)
            return 1
        print(f"rejected {recorded}")
        return 0

    diff_path = Path(recorded)
    if not diff_path.is_file():
        print(f"patch: no such file: {diff_path}", file=sys.stderr)
        return 1
    touched = _patch_files(diff_path)
    if touched:
        dirty = _git(sub, "diff", "--name-only", "--", *touched)
        if dirty.returncode != 0:
            print(f"patch: git diff failed in {sub}: {dirty.stderr.strip()}", file=sys.stderr)
            return 1
        unstaged = [line for line in dirty.stdout.splitlines() if line.strip()]
        if unstaged:
            print(f"patch: refusing to apply; unstaged changes in {sub} to files the patch "
                  "touches:", file=sys.stderr)
            for name in unstaged:
                print(f"  {name}", file=sys.stderr)
            print("commit or stash them first", file=sys.stderr)
            return 1
    check = _git(sub, "apply", "--check", str(diff_path))
    if check.returncode != 0:
        print(f"patch: does not apply cleanly in {sub}:", file=sys.stderr)
        print((check.stderr or check.stdout).strip(), file=sys.stderr)
        return 1
    applied = _git(sub, "apply", str(diff_path))
    if applied.returncode != 0:
        print(f"patch: git apply failed in {sub}:", file=sys.stderr)
        print((applied.stderr or applied.stdout).strip(), file=sys.stderr)
        return 1
    if not mark_patch(lab_root, recorded, "applied", by=args.by):
        print(f"applied {diff_path} (not in the ledger at {lab_root}; nothing marked)")
        return 0
    print(f"applied {diff_path}")
    for name in touched:
        print(f"  {name}")
    print("the working tree is modified but not committed; run the lab's smoke command, "
          "then commit")
    return 0


def _cmd_block(args: argparse.Namespace) -> int:
    from efferents.agents.state import open_blocks, resolve_block  # noqa: PLC0415

    _sub, lab_root = _submission_lab_root(args)
    if args.action == "list":
        blocks = open_blocks(lab_root)
        if not blocks:
            print("no open blocks")
            return 0
        for rec in blocks:
            print(f"{rec.get('id')}  {rec.get('ts', '-')}  student={rec.get('student_id') or '-'}")
            print(f"    {rec.get('summary', '')}")
            if rec.get("proposed_change"):
                print(f"    proposed: {rec['proposed_change']}")
            for ev in rec.get("evidence") or []:
                print(f"    evidence: {ev}")
        return 0
    if not args.id:
        print("block: resolve needs the block id", file=sys.stderr)
        return 2
    if not resolve_block(lab_root, args.id, by=args.by):
        print(f"block: no open block {args.id} at {lab_root}", file=sys.stderr)
        return 1
    print(f"resolved {args.id} by {args.by}")
    return 0


def _cmd_steer(args: argparse.Namespace) -> int:
    from efferents import steer as steer_mod  # noqa: PLC0415

    sub = Path(args.submission).resolve()
    if args.file and args.text:
        print("steer: pass either text or --file, not both", file=sys.stderr)
        return 2
    text = args.text or ""
    if args.file:
        try:
            text = Path(args.file).read_text()
        except OSError as e:
            print(f"steer: cannot read --file: {e}", file=sys.stderr)
            return 1
    action = "pause" if args.pause else "resume" if args.resume else None
    if not text.strip() and action is None and not args.supersede:
        print("steer: give the steering text, --file <path>, --pause, --resume, "
              "or --supersede <hypothesis.md>", file=sys.stderr)
        return 2

    try:
        if args.supersede:
            if action is not None:
                print("steer: --supersede cannot be combined with --pause/--resume",
                      file=sys.stderr)
                return 2
            res = steer_mod.supersede(
                sub, args.supersede, by=args.by, note=text, lab_root=args.lab_root,
            )
            charter, ledger = res["charter"], res["steering"]
            print(f"superseded {res['old_slug']} ({res['old_hash'][:19]}...) "
                  f"-> {res['new_slug']} ({res['new_hash'][:19]}...)")
            if res["corpus_marked"] is not None:
                print(f"marked superseded_by in {res['corpus_marked']}")
            print(f"installed {sub / 'hypothesis.md'}")
        else:
            if not text.strip():
                text = f"{action} requested by {args.by}"
            charter, ledger = steer_mod.steer(
                sub, text=text, by=args.by, action=action, lab_root=args.lab_root,
            )
    except steer_mod.SteeringError as e:
        print(f"steer failed: {e}", file=sys.stderr)
        return 1
    print(f"charter={charter}")
    print(f"steering={ledger}")
    print("the daemon will pick this up on its next step")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from efferents.demo import run_demo

    try:
        run_demo(args.lab, args.out)
    except FileNotFoundError as e:
        print(f"demo failed: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_place(args: argparse.Namespace) -> int:
    from efferents.placement import extract_profile, hire, place, scan_network

    new = extract_profile(args.submission)
    network = scan_network(args.network or [])
    if not network:
        print("place: no labs found on the network; proceed and create the lab")
        return 0
    decision = place(new, network)
    print(decision.summary())
    if not new.declared:
        print("note: no explicit topic:/approach: fields declared — comparison "
              "used noisy fallback text; declare both for a reliable verdict")
    if decision.action == "join" and args.apply:
        if not args.student_id:
            print("place: --apply requires --student-id", file=sys.stderr)
            return 1
        cfg = hire(
            decision.target.root,
            student_id=args.student_id,
            focus=new.topic,
            direction=new.approach if new.declared else new.topic,
            prompted_by=f"placement:{new.lab_id}",
        )
        print(f"hired {args.student_id} into {decision.target.lab_id} ({cfg})")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    from efferents.runner import run_adapter, RunnerError
    from efferents.repo_adapter import AdapterConfigError

    try:
        run_adapter(
            args.repo,
            args.out,
            max_iters=args.max_iters,
            approved=args.approve,
        )
    except (RunnerError, AdapterConfigError, FileNotFoundError) as e:
        print(f"run failed: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from efferents.dashboard import server as dash_server

    lab_root = Path(args.lab_root).resolve()
    # `_init_lab_root` copies hypothesis.md + lab.yaml into the lab root.  The
    # lab root is a valid config source, but source.dir and config_template in
    # that copied lab.yaml are relative to the *submission* root, not lab/.
    # We don't need to run code here (serve is read-only), so skip path checks.
    connected_root: Path | None = lab_root
    try:
        cfg = LabConfig.from_submission(lab_root, check_paths=False)
    except SubmissionError:
        connected_root = None
    else:
        lab_mod.set_config(cfg)
    serve_json = lab_root / "serve.json"
    if lab_root.is_dir():
        serve_json.write_text(json.dumps({
            "pid": os.getpid(),
            "port": args.port,
            "url": f"http://localhost:{args.port}",
        }))
    extra = {}
    host = getattr(args, "host", "127.0.0.1")
    if host and host != "127.0.0.1":
        extra["host"] = host
    try:
        dash_server.serve(
            connected_root,
            port=args.port,
            open_browser=not args.no_open,
            paused_demo=getattr(args, "paused_demo", False),
            **extra,
        )
    finally:
        daemon.clear_pidfile(serve_json)
    return 0


def _cmd_public_check(args: argparse.Namespace) -> int:
    from efferents.publication import (  # noqa: PLC0415
        check_public_repository,
        format_publication_report,
    )

    report = check_public_repository(
        args.repository,
        reviewer=args.acknowledge_manual_review,
    )
    rendered = report.to_json() + "\n" if args.json else format_publication_report(report)
    print(rendered, end="")
    if args.report:
        report_path = Path(args.report).expanduser().resolve()
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report.to_json() + "\n")
        except OSError as exc:
            print(f"could not write publication report: {exc}", file=sys.stderr)
            return 1
        if not args.json:
            print(f"report={report_path}")
    return 0 if report.is_ready else 1


def _add_lab_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lab-id", default=None)
    parser.add_argument("--submission", default=None,
                        help="Submission directory (lab root defaults to <dir>/lab)")
    parser.add_argument("--lab-root", default=None,
                        help="Lab root directory; works even if the registry lost the record")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="efferents")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_validate = sub.add_parser("validate", help="Validate a submission directory")
    p_validate.add_argument("--submission", required=True)
    p_validate.set_defaults(func=_cmd_validate)

    p_start = sub.add_parser("start", help="Start the lab daemon")
    p_start.add_argument("--submission", required=True)
    p_start.add_argument("--detach", action="store_true")
    p_start.add_argument("--lab-root", default=None)
    p_start.add_argument(
        "--dry-run",
        action="store_true",
        help="Run a deterministic offline smoke proposal without LLM calls",
    )
    p_start.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Stop after N orchestrator iterations (for bounded trials)",
    )
    p_start.add_argument(
        "--force",
        action="store_true",
        help="Start even if daemon.pid or the registry reports a live daemon",
    )
    p_start.set_defaults(func=_cmd_start)

    p_status = sub.add_parser("status", help="Show lab status")
    _add_lab_selector(p_status)
    p_status.set_defaults(func=_cmd_status)

    p_stop = sub.add_parser("stop", help="Stop a running lab daemon")
    _add_lab_selector(p_stop)
    p_stop.set_defaults(func=_cmd_stop)

    p_list = sub.add_parser("list", help="List all registered labs")
    p_list.set_defaults(func=_cmd_list)

    p_migrate = sub.add_parser(
        "migrate-paper-dir",
        help="Move Writer output from the legacy lab/paper/ to <submission>/paper/",
    )
    p_migrate.add_argument("--submission", required=True)
    p_migrate.add_argument("--lab-root", default=None)
    p_migrate.set_defaults(func=_cmd_migrate_paper_dir)

    p_steer = sub.add_parser(
        "steer",
        help="Owner steering: redirect, pause/resume, or supersede the hypothesis "
             "(auditable; never rewrites evidence)",
    )
    p_steer.add_argument("--submission", required=True)
    p_steer.add_argument("text", nargs="?", default=None,
                         help="Steering text, recorded verbatim in context/popper.md")
    p_steer.add_argument("--file", default=None, help="Read the steering text from a file")
    p_steer.add_argument("--by", default="lab owner",
                         help="Who is steering (name/role; default: lab owner)")
    p_steer.add_argument("--lab-root", default=None,
                         help="Lab root directory (default: <submission>/lab)")
    p_pause = p_steer.add_mutually_exclusive_group()
    p_pause.add_argument("--pause", action="store_true",
                         help="Pause spending until `steer --resume`")
    p_pause.add_argument("--resume", action="store_true", help="Lift an owner pause")
    p_steer.add_argument(
        "--supersede", metavar="HYPOTHESIS_MD", default=None,
        help="Install a gated successor hypothesis whose `supersedes:` names the "
             "current slug; the retired corpus copy gets `superseded_by:`",
    )
    p_steer.set_defaults(func=_cmd_steer)

    p_patch = sub.add_parser(
        "patch",
        help="Owner review of Coder patches written in autonomy.coder_mode: review",
    )
    p_patch.add_argument("action", choices=("list", "apply", "reject"))
    p_patch.add_argument("path", nargs="?", default=None,
                         help="Patch path (or file name) from `patch list`")
    p_patch.add_argument("--submission", required=True)
    p_patch.add_argument("--lab-root", default=None,
                         help="Lab root directory (default: <submission>/lab)")
    p_patch.add_argument("--by", default="owner", help="Who decided (default: owner)")
    p_patch.set_defaults(func=_cmd_patch)

    p_block = sub.add_parser(
        "block",
        help="List or resolve a student's open blocked_on_infrastructure records",
    )
    p_block.add_argument("action", choices=("list", "resolve"))
    p_block.add_argument("id", nargs="?", default=None, help="Block id from `block list`")
    p_block.add_argument("--submission", required=True)
    p_block.add_argument("--lab-root", default=None,
                         help="Lab root directory (default: <submission>/lab)")
    p_block.add_argument("--by", default="owner", help="Who resolved it (default: owner)")
    p_block.set_defaults(func=_cmd_block)

    p_demo = sub.add_parser(
        "demo", help="Run an offline, no-API product demo and write journal/runs/dashboard")
    p_demo.add_argument("lab", nargs="?", default="smoke-lab",
                        help="Example lab name (default: smoke-lab) or path to a submission dir")
    p_demo.add_argument("--out", default="efferents-demo",
                        help="Output directory for demo artifacts (default: ./efferents-demo)")
    p_demo.set_defaults(func=_cmd_demo)

    p_place = sub.add_parser(
        "place",
        help="Check a proposed lab against the network: same topic + same way "
             "of thinking joins the existing lab; otherwise create a new one")
    p_place.add_argument("submission", help="Path to the proposed lab / submission dir")
    p_place.add_argument("--network", action="append", default=[],
                         help="Directory of labs to compare against (repeatable)")
    p_place.add_argument("--apply", action="store_true",
                         help="On a JOIN verdict, hire into the target lab")
    p_place.add_argument("--student-id", default=None,
                         help="Student id to register when hiring (with --apply)")
    p_place.set_defaults(func=_cmd_place)

    p_run = sub.add_parser(
        "run", help="Execute a repo adapter (efferents.yaml) as a bounded experiment loop")
    p_run.add_argument("repo", nargs="?", default="examples/repo-adapter",
                       help="Path to a repo containing efferents.yaml (default: examples/repo-adapter)")
    p_run.add_argument("--out", default="efferents-run",
                       help="Output directory for artifacts (default: ./efferents-run)")
    p_run.add_argument("--max-iters", type=int, default=None,
                       help="Cap the number of experiments")
    p_run.add_argument(
        "--approve",
        action="store_true",
        help="Authorize a plan_then_execute adapter after inspecting its plan",
    )
    p_run.set_defaults(func=_cmd_run)

    p_serve = sub.add_parser(
        "serve",
        help="Start the local lab connection, steering, and observer app",
    )
    p_serve.add_argument("--lab-root", default="lab",
                         help="Initialized lab directory (relative to cwd)")
    p_serve.add_argument("--port", type=int, default=8800)
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="Bind address (default loopback; a reverse proxy "
                              "should terminate TLS in front of anything else)")
    p_serve.add_argument("--no-open", action="store_true",
                         help="Do not auto-open the browser")
    p_serve.add_argument(
        "--paused-demo",
        action="store_true",
        help=(
            "Serve a read-only historical snapshot labelled paused; disable "
            "connection, steering, and lab execution"
        ),
    )
    p_serve.set_defaults(func=_cmd_serve)

    p_public = sub.add_parser(
        "public-check",
        help="Fail-closed preflight before making a git repository public",
    )
    p_public.add_argument(
        "repository",
        nargs="?",
        default=".",
        help="Git repository to review (default: current directory)",
    )
    p_public.add_argument(
        "--acknowledge-manual-review",
        metavar="REVIEWER",
        help=(
            "Record the named human who confirmed rights, privacy, confidentiality, "
            "export-control, and security attestations"
        ),
    )
    p_public.add_argument(
        "--report",
        help="Write the machine-readable JSON release report to this path",
    )
    p_public.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable JSON report",
    )
    p_public.set_defaults(func=_cmd_public_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
