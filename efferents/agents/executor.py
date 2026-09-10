"""Executor agent: render a proposal's config, run it, persist + log.

The execute() signature and return shape are preserved for the
orchestrator's downstream consumers. Internals route through
efferents.exec._execute_run + _persist_run_result, and the notebook
formatter renders dynamic columns from RunResult.metrics — no domain-
specific references survive.
"""
from __future__ import annotations

import copy
import math
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from efferents import lab as _lab
from efferents.agents.state import LabPaths, notebook_append, now_iso
from efferents.exec import _execute_run, _persist_run_result, RunResult


def _record_reproduction_outcome(
    *,
    paper_dir: Path,
    proposal: dict[str, Any],
    run_id: str,
    result: RunResult,
) -> dict[str, Any] | None:
    """Turn a reproduction run into a verified or failed receipt."""
    reproduction = proposal.get("reproduction_of")
    if not isinstance(reproduction, dict):
        return None

    from efferents.agents.federation import (
        DEFAULT_REPRO_TOLERANCE,
        record_reproduction,
    )

    lab_id = reproduction.get("lab_id")
    campaign_id = reproduction.get("campaign_id")
    metric = reproduction.get("claimed_metric")
    claimed_raw = reproduction.get("claimed_value")
    tolerance_raw = reproduction.get("tolerance", DEFAULT_REPRO_TOLERANCE)
    if not all(isinstance(v, str) and v for v in (lab_id, campaign_id, metric)):
        return None

    observed_raw = (result.metrics or {}).get(metric) if result.ok else None
    try:
        claimed = float(claimed_raw)
        tolerance = float(tolerance_raw)
        observed = float(observed_raw)
        valid = all(math.isfinite(v) for v in (claimed, tolerance, observed))
        valid = valid and tolerance >= 0
    except (TypeError, ValueError):
        claimed, tolerance, observed, valid = 0.0, 0.0, 0.0, False

    within = valid and abs(observed - claimed) <= tolerance * max(abs(claimed), 1e-9)
    status = "verified" if within else "failed"
    notes = (
        f"metric={metric}; relative_tolerance={tolerance:g}"
        if valid
        else f"metric={metric}; invalid or missing reproduction measurement"
    )
    record_reproduction(
        paper_dir,
        lab_id=lab_id,
        campaign_id=campaign_id,
        status=status,
        claimed=f"{metric}={claimed_raw}",
        observed=(f"{metric}={observed_raw}" if observed_raw is not None else None),
        run_id=run_id,
        notes=notes,
    )
    return {
        "lab_id": lab_id,
        "campaign_id": campaign_id,
        "status": status,
        "metric": metric,
        "claimed": claimed_raw,
        "observed": observed_raw,
        "tolerance": tolerance_raw,
    }


def load_default_config(path: str | Path) -> dict[str, Any]:
    with Path(path).open() as f:
        return yaml.safe_load(f)


def apply_overrides(cfg: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Apply dotted-path overrides ('training.batch_size' -> 64) to a nested dict."""
    out = copy.deepcopy(cfg)
    for path, value in overrides.items():
        keys = path.split(".")
        cursor = out
        for k in keys[:-1]:
            if k not in cursor or not isinstance(cursor[k], dict):
                cursor[k] = {}
            cursor = cursor[k]
        cursor[keys[-1]] = value
    return out


def execute(
    *,
    paths: LabPaths,
    proposal: dict[str, Any],
    base_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one proposal end-to-end. Returns {ok, name, rows | error}."""
    name = proposal.get("name", "unnamed")

    # Verification is an execution precondition, not a prompt suggestion.
    # Check it before loading config, writing files, or invoking lab code.
    from efferents.agents.federation import foundational_dependency_violations
    violations = foundational_dependency_violations(
        paths.root.parent / "paper", proposal
    )
    if violations:
        detail = "; ".join(
            f"{v.get('lab_id', '?')}/{v.get('campaign_id', '?')}: "
            f"{v['reason']} (status={v.get('status') or 'none'})"
            for v in violations
        )
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — blocked unverified dependency for {name}\n\n"
            f"{detail}\n",
        )
        return {
            "ok": False,
            "blocked": True,
            "name": name,
            "error": f"foundational dependency gate: {detail}",
            "violations": violations,
            "duration_seconds": 0.0,
        }

    cfg = _lab.get_config()
    overrides = dict(proposal.get("config_overrides", {}) or {})
    if proposal.get("campaign_id"):
        overrides["run.campaign_id"] = proposal["campaign_id"]
    if proposal.get("mode"):
        overrides["run.researcher_mode"] = proposal["mode"]
    if proposal.get("student_id"):
        overrides["run.student_id"] = proposal["student_id"]

    base = base_config or load_default_config(cfg.executor.config_template)
    rendered = apply_overrides(base, overrides)
    rendered.setdefault("run", {})["name"] = name

    run_id = uuid.uuid4().hex
    config_dir = paths.root / "configs"
    config_dir.mkdir(exist_ok=True)
    # Resolve to an absolute path: the run command executes with cwd =
    # source.dir, which is generally NOT the daemon's cwd, so a relative
    # config path would not resolve for the subprocess.
    config_path = (config_dir / f"run_{run_id}.yaml").resolve()
    config_yaml = yaml.safe_dump(rendered, sort_keys=True)
    config_path.write_text(config_yaml)

    started = now_iso()
    t0 = time.monotonic()
    result = _execute_run(
        config_path,
        smoke=bool(proposal.get("use_smoke_command")),
    )
    duration = time.monotonic() - t0

    logs_dir = paths.root / "logs"
    logs_dir.mkdir(exist_ok=True)
    stdout_path = (logs_dir / f"run_{run_id}.stdout.log").resolve()
    stderr_path = (logs_dir / f"run_{run_id}.stderr.log").resolve()
    stdout_path.write_text(result.stdout)
    stderr_path.write_text(result.stderr)

    seed_raw = rendered.get("seed")
    if seed_raw is None and isinstance(rendered.get("run"), dict):
        seed_raw = rendered["run"].get("seed")
    try:
        seed = int(seed_raw) if seed_raw is not None else None
    except (TypeError, ValueError):
        seed = None

    _persist_run_result(
        result,
        run_id,
        config_path,
        db_path=paths.runs_db,
        proposal=proposal,
        config_yaml=config_yaml,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        duration_seconds=duration,
        started_at=started,
        seed=seed,
    )

    reproduction = _record_reproduction_outcome(
        paper_dir=paths.root.parent / "paper",
        proposal=proposal,
        run_id=run_id,
        result=result,
    )

    notebook_append(
        paths.notebook,
        _format_outcome(
            name=name,
            hypothesis=proposal.get("hypothesis", ""),
            expected=proposal.get("expected", ""),
            overrides=overrides,
            result=result,
            duration=duration,
            started=started,
        ),
    )

    if result.ok:
        row: dict[str, Any] = {"run_id": run_id, "name": name}
        if result.metrics:
            row.update(result.metrics)
        if result.git_commit:
            row["git_commit"] = result.git_commit
        if reproduction is not None:
            row["reproduction"] = reproduction
        row["status"] = "succeeded"
        row["campaign_id"] = proposal.get("campaign_id")
        return {"ok": True, "name": name, "rows": [row], "duration_seconds": duration}

    err_tail = (result.stderr or "")[-200:]
    return {
        "ok": False,
        "name": name,
        "error": result.error or err_tail or "run failed",
        "traceback": "",
        "duration_seconds": duration,
    }


def _format_outcome(
    *,
    name: str,
    hypothesis: str,
    expected: str,
    overrides: dict[str, Any],
    result: RunResult,
    duration: float,
    started: str,
) -> str:
    lines = [
        f"## {started} — {name}",
        "",
        f"**Hypothesis**: {hypothesis}",
        "",
        f"**Expected**: {expected}",
        "",
        f"**Overrides**: `{overrides}`",
        "",
        f"**Duration**: {duration:.1f}s",
        "",
    ]
    if result.metrics:
        cols = list(result.metrics.keys())
        lines.append("| " + " | ".join(cols) + " |")
        lines.append("|" + "|".join("---" for _ in cols) + "|")

        def _fmt(v: Any) -> str:
            if isinstance(v, float):
                return f"{v:.4g}"
            if isinstance(v, int):
                return str(v)
            return str(v)

        lines.append("| " + " | ".join(_fmt(result.metrics[c]) for c in cols) + " |")
    else:
        lines.append(f"**Error**: {result.error or 'no metrics emitted'}")
        if result.stderr:
            tail = result.stderr[-1024:]
            lines.append("")
            lines.append("```")
            lines.append(tail)
            lines.append("```")
    return "\n".join(lines)
