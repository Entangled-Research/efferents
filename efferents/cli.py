"""efferents CLI entry point.

  efferents validate --submission <dir>
  efferents start    --submission <dir> [--detach] [--lab-root <path>]
  efferents status   [--lab-id <id>]
  efferents stop     --lab-id <id>
  efferents list
  efferents public-check [repository]

The `main(argv=None)` entry point is exposed for tests; pyproject.toml
console_scripts will point at `efferents.cli:main` (Task 16).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
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
) -> None:
    # Indirection so tests can monkey-patch the loop body without forking.
    # In production, builds an Orchestrator from the active LabConfig and
    # runs it. The orchestrator import is deferred to avoid pulling heavy
    # transitive deps at CLI startup.
    from efferents.agents import orchestrator  # noqa: PLC0415
    cfg = lab_mod.get_config()
    o = orchestrator.Orchestrator(
        lab_dir=lab_root,
        context_dir=context_dir,
        daily_cap_usd=cfg.budget.daily_cap_usd,
        dry_run=dry_run,
        startup_message=f"efferents daemon for lab_id={cfg.lab_id}",
    )
    o.run(max_iterations=max_iterations)
    # Always leave a current static artifact, including bounded/offline runs
    # that stop before the Analyst cadence fires.
    from efferents.agents.progress import write_progress  # noqa: PLC0415
    write_progress(o.paths, context_dir=context_dir)


def _init_lab_root(submission_dir: Path, lab_root: Path) -> None:
    """Create lab/ dir + run migrations + copy provenance files."""
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
    ensure_runs_table(lab_root / "runs.sqlite", lab_mod.get_config())

    # The submitted, already-falsifiable hypothesis is the lab's initial
    # campaign. This gives the very first run a provenance anchor before the
    # Researcher opens follow-on campaigns.
    import sqlite3  # noqa: PLC0415
    from efferents.agents.state import campaign_insert  # noqa: PLC0415

    db = lab_root / "runs.sqlite"
    with sqlite3.connect(db) as conn:
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
            question = f"Initial submitted hypothesis for {lab_mod.get_config().lab_id}"
        cfg = lab_mod.get_config()
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
        cfg = lab_mod.get_config()
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
    _init_lab_root(sub, lab_root)
    os.chdir(sub)

    started_at = datetime.now(timezone.utc).isoformat()
    reg = Registry()
    existing = reg.get(cfg.lab_id)
    if (
        existing is not None
        and existing.status == "running"
        and daemon.is_pid_alive(existing.pid)
    ):
        print(
            f"lab_id={cfg.lab_id} is already running as pid={existing.pid}",
            file=sys.stderr,
        )
        return 1
    reg.register(LabRecord(
        lab_id=cfg.lab_id,
        submission_dir=str(sub),
        lab_root=str(lab_root),
        pid=os.getpid(),
        started_at=started_at,
        status="running",
    ))

    print(f"lab_id={cfg.lab_id} pid={os.getpid()} dashboard={lab_root}/progress.html")

    loop = lambda: _orchestrator_loop(
        lab_root=lab_root,
        context_dir=sub / "context",
        dry_run=args.dry_run,
        max_iterations=args.max_iterations,
    )

    if args.detach:
        child_pid = daemon.daemonize_and_run(lab_root, loop)
        rec = reg.get(cfg.lab_id)
        if rec is not None:
            rec.pid = child_pid
            reg.register(rec)  # idempotent replace
        return 0

    try:
        daemon.run_foreground(lab_root, loop)
    finally:
        reg.update_status(cfg.lab_id, "stopped")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    reg = Registry()
    records = reg.list()
    if not records:
        print("no labs registered")
        return 0
    print(f"{'LAB_ID':<24} {'STATUS':<10} {'STARTED':<25} SUBMISSION")
    for r in records:
        status = r.status
        if status == "running" and not daemon.is_pid_alive(r.pid):
            status = "crashed"
        print(f"{r.lab_id:<24} {status:<10} {r.started_at:<25} {r.submission_dir}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    reg = Registry()
    if args.lab_id is None:
        return _cmd_list(args)
    rec = reg.get(args.lab_id)
    if rec is None:
        print(f"unknown lab_id: {args.lab_id}", file=sys.stderr)
        return 1

    alive = daemon.is_pid_alive(rec.pid)
    status = rec.status
    if rec.status == "running" and not alive:
        status = "crashed"
        reg.update_status(args.lab_id, "crashed")

    lab_root = Path(rec.lab_root)
    print(f"lab_id={rec.lab_id}")
    print(f"status={status}")
    print(f"started_at={rec.started_at}")
    print(f"pid={rec.pid} (alive={alive})")
    state_json = lab_root / "state.json"
    if state_json.exists():
        mtime = datetime.fromtimestamp(state_json.stat().st_mtime, tz=timezone.utc).isoformat()
        print(f"last_activity={mtime}")
    print(f"dashboard=file://{lab_root}/progress.html")
    halt = lab_root / "halt_reason.txt"
    if halt.exists():
        print(f"halt_reason={halt.read_text().strip()}")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    reg = Registry()
    rec = reg.get(args.lab_id)
    if rec is None:
        print(f"unknown lab_id: {args.lab_id}", file=sys.stderr)
        return 1

    if daemon.is_pid_alive(rec.pid):
        os.kill(rec.pid, signal.SIGTERM)
        for _ in range(100):
            if not daemon.is_pid_alive(rec.pid):
                break
            time.sleep(0.1)
        if daemon.is_pid_alive(rec.pid):
            os.kill(rec.pid, signal.SIGKILL)
            print(f"warning: SIGTERM ignored, sent SIGKILL to PID {rec.pid}", file=sys.stderr)

    reg.update_status(args.lab_id, "stopped")
    print(f"stopped lab_id={args.lab_id}")
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
    dash_server.serve(
        connected_root,
        port=args.port,
        open_browser=not args.no_open,
        paused_demo=getattr(args, "paused_demo", False),
    )
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
    p_start.set_defaults(func=_cmd_start)

    p_status = sub.add_parser("status", help="Show lab status")
    p_status.add_argument("--lab-id", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_stop = sub.add_parser("stop", help="Stop a running lab daemon")
    p_stop.add_argument("--lab-id", required=True)
    p_stop.set_defaults(func=_cmd_stop)

    p_list = sub.add_parser("list", help="List all registered labs")
    p_list.set_defaults(func=_cmd_list)

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
