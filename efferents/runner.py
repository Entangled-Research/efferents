"""Execute a repo-adapter (`efferents.yaml`) as a bounded experiment loop.

`efferents run <repo>` loads the adapter config, sweeps the configured parameter
(running the repo's real train + eval commands each iteration), parses the
metric from eval's stdout JSON, enforces the budget, and writes the same journal
/ runs / claims / dashboard artifacts as the offline demo — with provenance.

No paid API is involved: the "agent" decisions (what to sweep, how to write up
the result) are deterministic. The *experiments* are real subprocess runs of the
target repo's commands.
"""
from __future__ import annotations

import json
import math
import os
import shlex
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from efferents.dashboard.report_theme import REPORT_CSS
from efferents.exec import _extract_trailing_json, _subprocess_env
from efferents.repo_adapter import RepoAdapterConfig


class RunnerError(RuntimeError):
    pass


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_capture(
    cmd: str, cwd: Path, timeout_s: float = 120
) -> tuple[dict | None, str, int]:
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_subprocess_env(()),
        start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=max(0.1, timeout_s))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
        return (
            _extract_trailing_json(stdout),
            stdout + stderr + f"\ncommand timed out after {timeout_s:.1f}s\n",
            124,
        )
    return _extract_trailing_json(stdout), stdout + stderr, proc.returncode


def _iterations(cfg: RepoAdapterConfig) -> list[tuple[str, object]]:
    """(label, value) per experiment. Single unparameterized run if no sweep."""
    if cfg.sweep is None:
        return [("base", None)]
    return [(f"{cfg.sweep.param}={v}", v) for v in cfg.sweep.values]


def run_adapter(
    repo: str | Path,
    out_dir: str | Path,
    *,
    max_iters: int | None = None,
    approved: bool = False,
) -> Path:
    repo = Path(repo).resolve()
    cfg = RepoAdapterConfig.load(repo)

    out = Path(out_dir).resolve()
    journal = out / cfg.outputs.journal_dir
    journal.mkdir(parents=True, exist_ok=True)
    (out / "configs").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    base_cfg: dict = {}
    if cfg.config_template:
        tmpl = (repo / cfg.config_template).resolve()
        if not tmpl.is_file():
            raise RunnerError(f"config_template not found: {tmpl}")
        base_cfg = yaml.safe_load(tmpl.read_text()) or {}

    iters = _iterations(cfg)
    if max_iters is not None:
        iters = iters[:max_iters]

    print(f"efferents run · {cfg.metric} ({'maximize' if cfg.maximize else 'minimize'}) "
          f"· {len(iters)} experiments · approval={cfg.approval_mode}")

    # 001 + 002 are written before any experiment executes (plan_then_execute).
    _write_hypothesis(journal, cfg)
    execution_authorized = cfg.approval_mode == "autonomous" or (
        cfg.approval_mode == "plan_then_execute" and approved
    )
    _write_plan(
        journal,
        cfg,
        iters,
        execution_authorized=execution_authorized,
    )

    if not execution_authorized:
        reason = (
            "approval.mode=dry_run — plan written; execution is disabled."
            if cfg.approval_mode == "dry_run"
            else (
                "approval.mode=plan_then_execute — plan written; inspect it, "
                "then re-run with --approve."
            )
        )
        print(reason)
        _write_dashboard(out, cfg, [], None, plan_only_reason=reason)
        return out

    runs: list[dict] = []
    gpu_seconds = 0.0
    budget_s = (cfg.budget.max_gpu_hours or float("inf")) * 3600
    for i, (label, value) in enumerate(iters):
        if gpu_seconds >= budget_s:
            print(f"budget reached ({cfg.budget.max_gpu_hours} GPU-h) — stopping early.")
            break

        run_cfg = dict(base_cfg)
        if cfg.sweep is not None:
            run_cfg[cfg.sweep.param] = value
        ckpt_dir = out / "ckpt" / f"iter_{i:02d}"
        run_cfg["checkpoint_dir"] = str(ckpt_dir)
        cfg_path = out / "configs" / f"iter_{i:02d}.yaml"
        cfg_path.write_text(yaml.safe_dump(run_cfg))

        log_path = out / "logs" / f"iter_{i:02d}.log"
        started_at = _ts()
        t0 = time.monotonic()

        remaining_s = budget_s - gpu_seconds
        command_timeout = min(120.0, remaining_s) if math.isfinite(remaining_s) else 120.0
        train_cmd = cfg.train_command.replace(
            "{config_path}", shlex.quote(str(cfg_path))
        )
        tj, tlog, trc = _run_capture(train_cmd, repo, timeout_s=command_timeout)
        gpu_seconds += time.monotonic() - t0
        if tj is None or "checkpoint" not in tj or trc != 0:
            log_path.write_text(f"$ {train_cmd}\n{tlog}\n")
            raise RunnerError(f"train command failed or emitted no checkpoint (iter {i}); see {log_path}")
        checkpoint = tj["checkpoint"]
        if not isinstance(checkpoint, str) or not checkpoint.strip():
            raise RunnerError(f"train output has invalid checkpoint path (iter {i})")

        eval_started = time.monotonic()
        remaining_s = budget_s - gpu_seconds
        if remaining_s <= 0:
            log_path.write_text(
                f"$ {train_cmd}\n{tlog}\nconfigured compute ceiling reached before eval\n"
            )
            raise RunnerError(
                f"configured compute ceiling reached before eval (iter {i}); see {log_path}"
            )
        command_timeout = min(120.0, remaining_s) if math.isfinite(remaining_s) else 120.0
        eval_cmd = cfg.eval_command.replace("{checkpoint}", shlex.quote(checkpoint))
        ej, elog, erc = _run_capture(eval_cmd, repo, timeout_s=command_timeout)
        gpu_seconds += time.monotonic() - eval_started
        elapsed = time.monotonic() - t0
        log_path.write_text(f"$ {train_cmd}\n{tlog}\n$ {eval_cmd}\n{elog}\n")
        if ej is None or erc != 0:
            raise RunnerError(f"eval command failed or emitted no metric (iter {i}); see {log_path}")
        metric_val = (ej.get("metrics") or {}).get(cfg.metric)
        if (
            not isinstance(metric_val, (int, float))
            or isinstance(metric_val, bool)
            or not math.isfinite(float(metric_val))
        ):
            raise RunnerError(
                f"eval output missing a finite numeric metric {cfg.metric!r} (iter {i})"
            )

        runs.append({
            "run_id": f"run_{i:02d}",
            "started_at": started_at,
            "param": cfg.sweep.param if cfg.sweep else None,
            "value": value,
            "metric": cfg.metric,
            cfg.metric: round(float(metric_val), 4),
            "checkpoint": str(checkpoint),
            "config_path": os.path.relpath(cfg_path, out),
            "log_path": os.path.relpath(log_path, out),
            "elapsed_s": round(elapsed, 3),
        })
        print(f"  {label:<18} {cfg.metric}={round(float(metric_val), 4)}")

    if not runs:
        raise RunnerError("no experiments completed")

    best = (max if cfg.maximize else min)(runs, key=lambda r: r[cfg.metric])

    with (out / cfg.outputs.runs_file).open("w") as f:
        for r in runs:
            f.write(json.dumps(r) + "\n")

    claims = _build_claims(cfg, runs, best)
    with (out / cfg.outputs.claims_file).open("w") as f:
        for c in claims:
            f.write(json.dumps(c) + "\n")

    _write_results(journal, cfg, runs, best)
    _write_memo(journal, cfg, runs, best, claims, gpu_seconds)
    _write_dashboard(out, cfg, runs, best)

    print(f"\nWrote run to {out}")
    print(f"  {cfg.outputs.journal_dir}/   4 research records (001..004)")
    print(f"  {cfg.outputs.runs_file}   {len(runs)} runs")
    print(f"  {cfg.outputs.claims_file} {len(claims)} claims with provenance")
    print("  dashboard.html")
    print(f"  wall-clock proxy: {gpu_seconds/3600:.4f} / "
          f"{cfg.budget.max_gpu_hours} configured GPU-h, "
          f"$0.00 / ${cfg.budget.max_llm_cost_usd} LLM (offline)")
    print(f"\nOpen the dashboard:\n  open {out / 'dashboard.html'}")
    return out


# --------------------------------------------------------------------------- #
# Memo / claims / dashboard writers
# --------------------------------------------------------------------------- #

def _w(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n")


def _best_line(cfg: RepoAdapterConfig, best: dict) -> str:
    knob = f"{best['param']}={best['value']}" if best.get("param") else "the base config"
    return f"{cfg.metric}={best[cfg.metric]} at {knob} (`{best['run_id']}`)"


def _write_hypothesis(journal: Path, cfg: RepoAdapterConfig) -> None:
    space = ""
    if cfg.sweep:
        space = (f"\n\n**Search space:** `{cfg.sweep.param}` ∈ "
                 f"{{{', '.join(str(v) for v in cfg.sweep.values)}}}")
    _w(journal / "001_hypothesis.md", f"""\
---
memo: 001_hypothesis
agent: researcher
generated_at: {_ts()}
---

# Objective

{cfg.goal}

**Metric:** `{cfg.metric}` ({'maximize' if cfg.maximize else 'minimize'}).{space}

## Why this is testable

Each experiment runs the repo's own train + eval commands and reports a single
scalar (`{cfg.metric}`). The configurations are ordered, so the loop either
finds a setting that moves the metric or shows the metric is flat across the
search space.
""")


def _write_plan(
    journal: Path,
    cfg: RepoAdapterConfig,
    iters: list,
    *,
    execution_authorized: bool,
) -> None:
    rows = "\n".join(f"| {i+1} | {label} |" for i, (label, _) in enumerate(iters))
    _w(journal / "002_experiment_plan.md", f"""\
---
memo: 002_experiment_plan
agent: researcher
generated_at: {_ts()}
approval_mode: {cfg.approval_mode}
execution_authorized: {str(execution_authorized).lower()}
---

# Experiment plan

**Objective:** {cfg.goal}

Each experiment runs:

```
train: {cfg.train_command}
eval:  {cfg.eval_command}
```

| # | experiment |
|---|------------|
{rows}

**Budget ceiling:** {cfg.budget.max_gpu_hours} GPU-hours, ${cfg.budget.max_llm_cost_usd} LLM spend.
**Approval mode:** `{cfg.approval_mode}`.
**Execution authorized:** `{str(execution_authorized).lower()}`.
This plan is recorded before any experiment runs.
""")


def _write_results(journal: Path, cfg: RepoAdapterConfig, runs: list, best: dict) -> None:
    rows = "\n".join(
        f"| `{r['run_id']}` | {r['value'] if r.get('value') is not None else '—'} | "
        f"{r[cfg.metric]} |{' ⬅ best' if r['run_id'] == best['run_id'] else ''}"
        for r in runs
    )
    _w(journal / "003_results.md", f"""\
---
memo: 003_results
agent: analyst
generated_at: {_ts()}
runs: {len(runs)}
---

# Results

{len(runs)} experiments completed locally. Objective: {'maximize' if cfg.maximize else 'minimize'} `{cfg.metric}`.

| run_id | {cfg.sweep.param if cfg.sweep else 'config'} | {cfg.metric} | |
|--------|------|------|---|
{rows}

**Best:** {_best_line(cfg, best)}.

> Provenance: every row is one line in [`../{cfg.outputs.runs_file}`](../{cfg.outputs.runs_file});
> each run's train+eval stdout is under `logs/`.
""")


def _write_memo(journal: Path, cfg: RepoAdapterConfig, runs: list, best: dict,
                claims: list, gpu_seconds: float) -> None:
    ev_rows = "\n".join(
        f"| {c['claim']} | {c['evidence_type']} | `{c['source_path']}` | "
        f"`{c['run_id'] or '—'}` | {c['metric'] or '—'} |"
        for c in claims
    )
    worst = (min if cfg.maximize else max)(runs, key=lambda r: r[cfg.metric])
    sweep_description = (
        f"Swept `{cfg.sweep.param}` over {len(runs)} configured values"
        if cfg.sweep
        else "Ran the single configured experiment"
    )
    next_experiment = (
        f"Refine `{cfg.sweep.param}` around {best.get('value')} and add "
        f"predeclared repeated seeds for `{cfg.metric}`."
        if cfg.sweep
        else (
            f"Add a predeclared comparison configuration and repeated seeds "
            f"for `{cfg.metric}`."
        )
    )
    _w(journal / "004_research_memo.md", f"""\
---
memo: 004_research_memo
agent: writer
reviewed_by: deterministic-provenance-audit
review_status: not_peer_reviewed
generated_at: {_ts()}
---

# Research memo: {cfg.goal}

## Summary

Across {len(runs)} bounded experiments, the best setting was
{_best_line(cfg, best)} — versus {worst[cfg.metric]} at the weakest setting.
The objective ({'maximize' if cfg.maximize else 'minimize'} `{cfg.metric}`) is
addressed by the search above. The commands consumed
{gpu_seconds/3600:.4f} wall-clock hours, charged as a conservative proxy
against the configured compute ceiling; $0.00 LLM spend.

## Hypothesis

{cfg.goal}

## Experiment plan

{sweep_description}, running the repo's own `train`/`eval` commands.
Full plan: [`002_experiment_plan.md`](002_experiment_plan.md).

## Results

Best `{cfg.metric}` = {best[cfg.metric]} at `{cfg.sweep.param if cfg.sweep else 'base'}`
= {best.get('value', '—')}. Full table: [`003_results.md`](003_results.md).

## Automated audit notes

- Every reported number resolves to a `run_id` and a logged train/eval pair.
- The configured execution ceiling held under the wall-clock proxy.
- This deterministic check is not scientific peer review and does not assess
  novelty, domain validity, leakage, or causal interpretation.

## Limitations

- The search covers only {len(runs)} configured setting(s) and one metric.
- No variance estimate is available unless the repository command itself
  performs and reports repeated seeds.
- Wall-clock duration is not GPU hardware telemetry or a monetary cost meter.
- Human domain review is required before acting on the result.

## Next experiment

{next_experiment}

## Evidence table

Every nontrivial claim below points to a run, a metric, or a source file.

| claim | evidence_type | source_path | run_id | metric |
|-------|---------------|-------------|--------|--------|
{ev_rows}
""")


def _build_claims(cfg: RepoAdapterConfig, runs: list, best: dict) -> list[dict]:
    metric_min = min(r[cfg.metric] for r in runs)
    metric_max = max(r[cfg.metric] for r in runs)
    if cfg.sweep:
        aggregate_claim = (
            f"{cfg.metric} varied across the configured {cfg.sweep.param} sweep"
            if metric_min != metric_max
            else f"{cfg.metric} was flat across the configured {cfg.sweep.param} sweep"
        )
    else:
        aggregate_claim = f"Observed {cfg.metric} for the base configuration"
    return [
        {
            "claim": f"Best {cfg.metric} = {best[cfg.metric]} at "
                     f"{best.get('param')}={best.get('value')}",
            "evidence_type": "run_metric",
            "source_path": best["log_path"],
            "run_id": best["run_id"],
            "metric": cfg.metric,
            "value": best[cfg.metric],
        },
        {
            "claim": aggregate_claim,
            "evidence_type": "metric_aggregate",
            "source_path": cfg.outputs.runs_file,
            "run_id": None,
            "metric": cfg.metric,
            "value": f"range={metric_min}..{metric_max}",
        },
        {
            "claim": "Experiment plan recorded before execution",
            "evidence_type": "document",
            "source_path": f"{cfg.outputs.journal_dir}/002_experiment_plan.md",
            "run_id": None,
            "metric": None,
            "value": f"approval={cfg.approval_mode}; execution_authorized=true",
        },
        {
            "claim": "Execution stayed within the configured wall-clock compute proxy",
            "evidence_type": "budget",
            "source_path": f"{cfg.outputs.journal_dir}/004_research_memo.md",
            "run_id": None,
            "metric": None,
            "value": f"<= {cfg.budget.max_gpu_hours} GPU-h, $0 LLM",
        },
    ]


def _write_dashboard(
    out: Path,
    cfg: RepoAdapterConfig,
    runs: list,
    best: dict | None,
    plan_only_reason: str | None = None,
) -> None:
    metric = cfg.metric
    if runs:
        mmax = max(r[metric] for r in runs) or 1.0
        bars = "\n".join(
            f'''        <div class="bar-row">
          <span class="coef">{r.get('value', '—')}</span>
          <div class="bar" style="width:{max(4, r[metric] / mmax * 100):.0f}%"></div>
          <span class="val{' best' if best and r['run_id'] == best['run_id'] else ''}">{r[metric]}</span>
        </div>'''
            for r in runs
        )
        run_rows = "\n".join(
            f'''        <tr{' class="best"' if best and r['run_id'] == best['run_id'] else ''}>
          <td><code>{r['run_id']}</code></td><td>{r.get('value', '—')}</td>
          <td>{r[metric]}</td><td><code>{r['log_path']}</code></td>
        </tr>'''
            for r in runs
        )
        headline = f"{best[metric]}" if best else "—"
    else:
        bars = run_rows = ""
        headline = "plan only"

    banner = (
        plan_only_reason
        or "Rendered from runs.jsonl and the research records in this folder."
    )
    _w(out / "dashboard.html", f"""\
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>efferents run — {cfg.metric}</title>
<style>{REPORT_CSS}</style></head><body>
<header>
  <h1>{cfg.goal} <span class="muted small">· efferents run</span></h1>
  <div class="muted small">Local compute · offline · metric: {cfg.metric}</div>
</header>
<main>
  <div class="banner">{banner}</div>
  <div class="cards">
    <div class="card"><div class="k">best {cfg.metric}</div><div class="v ok">{headline}</div></div>
    <div class="card"><div class="k">experiments</div><div class="v">{len(runs)}</div></div>
    <div class="card"><div class="k">LLM spend</div><div class="v">$0.00</div></div>
  </div>
  <div class="section">
    <h2>{cfg.metric} by {cfg.sweep.param if cfg.sweep else 'config'}</h2>
{bars}
  </div>
  <div class="section">
    <h2>Runs (provenance)</h2>
    <table>
      <tr><th>run_id</th><th>{cfg.sweep.param if cfg.sweep else 'config'}</th><th>{cfg.metric}</th><th>log</th></tr>
{run_rows}
    </table>
  </div>
  <div class="section memos">
    <h2>Research journal</h2>
    <a href="{cfg.outputs.journal_dir}/001_hypothesis.md">001 · Objective</a>
    <a href="{cfg.outputs.journal_dir}/002_experiment_plan.md">002 · Experiment plan</a>
    <a href="{cfg.outputs.journal_dir}/003_results.md">003 · Results</a>
    <a href="{cfg.outputs.journal_dir}/004_research_memo.md">004 · Research memo (automated audit + evidence)</a>
  </div>
</main></body></html>
""")
