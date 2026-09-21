"""Create runnable starter contracts and perform bounded, model-free trials."""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
from pathlib import Path

import yaml

from efferents.lab import LabConfig
from efferents.starter_catalog import DOCUMENTED

TEMPLATE_TITLES = {"evacuation": "Congestion-aware evacuation", "integration": "Numerical integration"}


def suggest_lab_id(*, idea: str = "", goal: str = "", approach: str = "",
                   starter: str = "auto", name: str = "", taken: set[str] | None = None) -> str:
    """Derive a readable lab_id from the owner's own words, unique among local labs.

    Precedence: explicit name, then idea, approach, goal, then the starter title.
    The result matches the lab_id grammar and is deduplicated with -2, -3, ...
    """
    import re
    from efferents.registry import Registry

    source = next((text for text in (name, idea, approach, goal) if text and text.strip()), "")
    if not source:
        source = TEMPLATE_TITLES.get(starter) or DOCUMENTED.get(starter, {}).get("title") or starter
    words = [w for w in re.sub(r"[^a-z0-9]+", " ", source.lower()).split() if w]
    slug = ""
    for word in words[:6]:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > 48:
            break
        slug = candidate
    if not slug:
        slug = "lab"
    if taken is None:
        taken = {record.lab_id for record in Registry().list()}
    unique, counter = slug, 2
    while unique in taken:
        unique, counter = f"{slug}-{counter}", counter + 1
    return unique

TEMPLATES = {"evacuation": "starter-evacuation-lab", "integration": "starter-integration-lab"}
TEMPLATES.update({name: "starter-documented-lab" for name in DOCUMENTED})


def create_lab(destination: Path, *, starter: str = "auto", idea: str = "",
               goal: str = "", approach: str = "", exchange: bool = False,
               name: str = "") -> dict:
    """Infer reversible choices, preserve the owner's idea, record every default."""
    for field, value, limit in (("idea", idea, 4000), ("goal", goal, 160), ("approach", approach, 160),
                                ("name", name, 128)):
        if not isinstance(value, str) or len(value) > limit or "\x00" in value:
            raise ValueError(f"{field} must be text of at most {limit} characters")
    if starter == "auto":
        words = idea.lower()
        starter = next((name for name, keys in (
            ("vehicle", ("vehicle", "driverless", "cruise", "following")),
            ("active-learning", ("labels", "learning", "classification", "banknote")),
            ("orbit", ("planet", "orbit", "physics", "verlet")),
            ("integration", ("integrat", "quadrature", "simpson", "calculus")),
            ("evacuation", ("evacuat", "congestion", "rerout", "stable routes")),
        ) if any(word in words for word in keys)), "coloring")
    if starter not in TEMPLATES:
        raise ValueError("Choose a documented starter, or connect an existing lab for another domain.")
    if destination.exists():
        raise ValueError(f"Destination already exists: {destination}")
    source = Path(__file__).parent / "templates" / TEMPLATES[starter]
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "artifacts", "lab"))
    cfg_path = destination / "lab.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    if starter in DOCUMENTED:
        spec = DOCUMENTED[starter]
        raw.update(domain=spec["domain"], approach=spec["approach"])
        (destination / "hypothesis.md").write_text(
            f"---\nslug: {starter}\nvalidation: lightweight\nstatus: active\n---\n\n"
            f"## Claim\n\n{spec['claim']}\n\n## Measurement\n\n{spec['measurement']}\n\n"
            f"## Stop condition\n\n{spec['stop']}\n")
    raw["lab_id"] = suggest_lab_id(idea=idea, goal=goal, approach=approach, starter=starter, name=name)
    raw["hypothesis_validation"] = "lightweight"
    raw["peer_review"] = {**(raw.get("peer_review") or {}), "enabled": True}
    # Generated ideas share one explicit trusted-host resource-owner pool.
    # The router still requires topic relevance and executor compatibility.
    raw["routing"] = {"pool": "local-onboarding", "owner": "local-owner", "accept_students": True}
    raw["research_goal"] = goal.strip()
    raw["approach"] = approach.strip() or raw["approach"]
    trial_config_path = destination / "configs" / "default.yaml"
    trial_config = yaml.safe_load(trial_config_path.read_text())
    if starter in DOCUMENTED:
        trial_config["experiment"] = starter
    direction = (idea + " " + approach).lower()
    if starter == "evacuation":
        if any(word in direction for word in ("frequent", "quick", "responsive")):
            trial_config["candidate"]["reroute_interval"] = 1
            if not approach.strip():
                raw["approach"] = "frequent-route-replanning"
        elif any(word in direction for word in ("conservative", "stable", "less", "slow")):
            trial_config["candidate"]["reroute_interval"] = 8
            if not approach.strip():
                raw["approach"] = "stable-route-replanning"
        elif any(word in direction for word in ("avoid", "penalt", "occupancy", "congestion")):
            trial_config["candidate"]["congestion_weight"] = 6.0
            if not approach.strip():
                raw["approach"] = "higher-congestion-penalty"
    elif "trapezoid" in direction:
        trial_config["candidate"] = "trapezoid"
        if not approach.strip():
            raw["approach"] = "composite-trapezoid"
        hypothesis = destination / "hypothesis.md"
        hypothesis.write_text(hypothesis.read_text().replace("Composite Simpson", "Composite trapezoid"))
    trial_config_path.write_text(yaml.safe_dump(trial_config, sort_keys=False))
    raw["conference"] = {"enabled": exchange, "venue": "private-event", "interval_minutes": 2,
                         "interdisciplinary_every": 3}
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    if starter == "evacuation":
        (destination / "hypothesis.md").write_text(
            "---\nslug: congestion-aware-evacuation\nvalidation: lightweight\nstatus: active\n---\n\n"
            "## Claim\n\nCongestion-aware replanning reduces median evacuation time by at least 10% "
            "across 12 paired seeds while preserving at least 95% completion.\n\n"
            "## Measurement\n\nCompare static and congestion-aware routing on the same seeded layouts. "
            "Measure evacuation_improvement_pct and candidate_completion_rate.\n\n"
            "## Stop condition\n\nAfter 12 distinct seeds, median improvement below 10% or any completion "
            "rate below 95% fails the claim. This synthetic simulator does not establish real-world safety.\n"
        )
        corpus = destination / "popper-corpus" / "congestion-aware-evacuation" / "hypothesis.md"
        corpus.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destination / "hypothesis.md", corpus)
    context = destination / "context"
    context.mkdir(exist_ok=True)
    (context / "research_log.md").write_text(
        f"# Owner direction\n\n{idea.strip() or 'Explore the starter experiment and its limitations.'}\n\n"
        f"Shared goal: {goal.strip() or 'Independent research'}\nApproach: {raw['approach']}\n\n"
        "The initial executable experiment is the starter contract. Treat broader ideas as future work, "
        "not as results already supported by this experiment.\n"
    )
    decisions = {"lab_id": raw["lab_id"], "starter": starter, "idea": idea.strip(),
                 "goal": goal.strip(), "approach": raw["approach"], "validation": "lightweight",
                 "daily_cap_usd": 1, "total_cap_usd": 2, "coder_mode": "review",
                 "exchange": exchange, "trial_runs": 3, "submission": str(destination)}
    decisions["experiment_config"] = trial_config
    (context / "onboarding.json").write_text(json.dumps(decisions, indent=2) + "\n")
    # Validate the complete contract now, before any experiment is launched.
    LabConfig.from_submission(destination)
    return decisions


def trial(submission: Path, *, runs: int = 3, student_id: str | None = None) -> dict:
    """Run distinct seeds through the ordinary evidence pipeline, without an LLM."""
    if type(runs) is not int or not 1 <= runs <= 12:
        raise ValueError("runs must be between 1 and 12")
    from efferents import daemon, lab as lab_mod
    from efferents.agents.executor import execute
    from efferents.agents.state import lab_paths, init_lab, campaign_open_list, notebook_append, now_iso
    from efferents.agents.progress import write_progress
    from efferents.agents.conference import attend
    from efferents.cli import _init_lab_root
    from efferents.registry import Registry, LabRecord

    cfg = LabConfig.from_submission(submission)
    student_id = student_id or cfg.default_student_id
    if student_id not in {student["id"] for student in cfg.students}:
        raise ValueError("Unknown idea/student track")
    lab_mod.set_config(cfg)
    root = submission / "lab"
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".trial.lock"
    import fcntl
    with lock.open("w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("A trial is already running for this lab") from exc
        pid = daemon.read_pidfile(root / "daemon.pid")
        if pid and daemon.is_pid_alive(pid):
            raise ValueError("Stop the lab before starting a separate trial")
        from efferents.steer import owner_paused, read_steering
        def paused():
            actions = [r for r in read_steering(root) if r.get("action") in {"pause", "resume"}]
            return actions[-1]["action"] == "pause" if actions else bool(owner_paused(root))
        if paused():
            raise ValueError("The owner paused this lab. Resume it before running a trial.")
        _init_lab_root(submission, root)
        Registry().register(LabRecord(cfg.lab_id, str(submission), str(root), os.getpid(), now_iso(), "running"))
        paths = lab_paths(root)
        init_lab(paths)
        campaigns = [campaign for campaign in campaign_open_list(root / "runs.sqlite", cfg.lab_id)
                     if campaign.get("student_id") == student_id]
        outcomes = []
        with sqlite3.connect(root / "runs.sqlite") as conn:
            next_seed = int(conn.execute("SELECT COALESCE(MAX(seed),-1)+1 FROM runs").fetchone()[0])
        from efferents.event import sync, exchange
        def interrupt(signum, frame):
            raise InterruptedError("Trial stopped by owner")
        previous = {sig: signal.signal(sig, interrupt) for sig in (signal.SIGTERM, signal.SIGINT)}
        daemon.write_pidfile(root / "daemon.pid", os.getpid())
        try:
            sync(submission, runtime_status="running", quiet=True)
            for seed in range(next_seed, next_seed + runs):
                if paused():
                    notebook_append(paths.notebook, f"## {now_iso()} — trial stopped at owner pause\n")
                    break
                outcomes.append(execute(paths=paths, proposal={
                    "name": f"trial-seed-{seed}", "config_overrides": {"seed": seed, "run.seed": seed},
                    "campaign_id": campaigns[0]["id"] if campaigns else None,
                    "student_id": student_id,
                }))
                if not outcomes[-1].get("ok"):
                    (root / "halt_reason.txt").write_text(str(outcomes[-1].get("error") or "Experiment failed"))
                    break
                sync(submission, runtime_status="running", quiet=True)
                exchange(submission)
            attend(cfg=cfg, lab_root=root)
            notebook_append(paths.notebook, f"## {now_iso()} — bounded trial\n\n"
                            f"{len(outcomes)} real experiments; no model calls.\n")
            write_progress(paths, context_dir=submission / "context")
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
            daemon.clear_pidfile(root / "daemon.pid")
            Registry().update_status(cfg.lab_id, "stopped")
            sync(submission, runtime_status="stopped", quiet=True)
        return {"ok": all(o.get("ok") for o in outcomes), "runs": len(outcomes), "lab_id": cfg.lab_id,
                "outcomes": outcomes}
