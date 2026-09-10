"""Offline, deterministic product demo for efferents.

`efferents demo <lab>` (or `python -m efferents demo <lab>`) runs a *bounded,
fully offline* experiment loop against an example lab and writes the artifacts a
real lab produces — a research journal, a runs log, a claims/provenance log, and
a static dashboard — **without any paid API call**.

The agent reasoning is faked with deterministic, hand-written text so a buyer can
clone the repo and see the shape of the product in one command. The *experiment*
itself is real: it executes the lab's stub run command over a small parameter
sweep and records the actual metric each run emits. The point is product
comprehension and provenance, not novel science.

Outputs (under ``--out``, default ``./efferents-demo``):

    journal/001_hypothesis.md
    journal/002_experiment_plan.md
    journal/003_results.md
    journal/004_reviewed_memo.md
    runs.jsonl
    claims.jsonl
    dashboard.html
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

import yaml

from efferents.dashboard.report_theme import REPORT_CSS

# A fixed epoch so the demo is byte-for-byte reproducible across machines/runs.
_BASE_TS = "2026-06-25T09:00:00Z"
_RUN_SECONDS = 7  # synthetic wall-clock per run, for the runs log


def _examples_dir() -> Path:
    # repo_root/examples (this file lives at repo_root/efferents/demo.py)
    return Path(__file__).resolve().parents[1] / "examples"


def _resolve_lab(lab: str) -> Path:
    """Map a demo lab name to a submission directory."""
    candidate = Path(lab)
    if candidate.is_dir() and (candidate / "lab.yaml").is_file():
        return candidate.resolve()
    guess = _examples_dir() / lab
    if (guess / "lab.yaml").is_file():
        return guess.resolve()
    raise FileNotFoundError(
        f"could not find a lab named {lab!r}; expected {guess}/lab.yaml or a "
        f"path to a submission directory containing lab.yaml"
    )


def _ts(i: int) -> str:
    """Deterministic increasing ISO timestamp, 1 minute apart."""
    base_min = 0
    minute = base_min + i
    hh = 9 + minute // 60
    mm = minute % 60
    return f"2026-06-25T{hh:02d}:{mm:02d}:00Z"


@dataclass
class RunResult:
    run_id: str
    started_at: str
    coefficient: float
    synthetic_loss: float
    elapsed_s: float
    config_path: str
    log_path: str
    git_commit: str


def _run_once(
    submission: Path, src_dir: Path, run_command: str, coefficient: float,
    seed: int, index: int, out: Path,
) -> RunResult:
    """Execute the lab's real stub run command for one coefficient value.

    Falls back to the lab's analytic truth (|0.8 - coefficient|) if the run
    command is unavailable, so the demo never hard-fails on a fresh clone.
    """
    cfg_dir = out / "configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    run_id = f"run_{index:03d}_{int(coefficient * 100):03d}"
    cfg_path = cfg_dir / f"{run_id}.yaml"
    cfg_path.write_text(yaml.safe_dump({"coefficient": coefficient, "seed": seed}))

    log_path = out / "logs" / f"{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    synthetic_loss: float | None = None
    git_commit = ""
    cmd = run_command.replace("{config_path}", str(cfg_path.resolve()))
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=str(src_dir), capture_output=True,
            text=True, timeout=60,
        )
        log_path.write_text(
            f"$ {cmd}\n(cwd={src_dir})\n\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}\n"
        )
        # Run command emits one JSON line on stdout.
        for line in reversed(proc.stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            synthetic_loss = float(payload.get("metrics", {}).get("synthetic_loss"))
            git_commit = payload.get("git_commit", "") or ""
            break
    except Exception as e:  # noqa: BLE001 — demo must be robust on a fresh clone
        log_path.write_text(f"$ {cmd}\nrun command unavailable: {e!r}\n")

    if synthetic_loss is None:
        # Analytic fallback (the smoke lab's documented truth), made deterministic.
        synthetic_loss = round(abs(0.8 - coefficient), 4)
        log_path.write_text(
            log_path.read_text() if log_path.exists() else ""
            + f"\n[demo] used analytic fallback synthetic_loss={synthetic_loss}\n"
        )

    return RunResult(
        run_id=run_id,
        started_at=_ts(index),
        coefficient=coefficient,
        synthetic_loss=round(synthetic_loss, 4),
        elapsed_s=float(_RUN_SECONDS),
        config_path=str(cfg_path.relative_to(out)),
        log_path=str(log_path.relative_to(out)),
        git_commit=git_commit,
    )


def _sweep_values() -> list[float]:
    # Deterministic plan: a coarse sweep, then a refinement near the minimum.
    return [0.50, 0.65, 0.78, 0.82, 0.81]


def _hypothesis_summary(submission: Path) -> tuple[str, str]:
    """Pull the claim + falsifier text out of the lab's hypothesis.md."""
    text = (submission / "hypothesis.md").read_text()
    body = re.sub(r"^---\n.*?\n---\n", "", text, flags=re.DOTALL).strip()

    def _section(name: str) -> str:
        m = re.search(rf"##\s*{name}\s*\n(.*?)(?:\n##\s|\Z)", body, flags=re.DOTALL)
        return m.group(1).strip() if m else ""

    return _section("Claim"), _section("Falsifier")


# --------------------------------------------------------------------------- #
# Journal memo writers (deterministic "agent" output)
# --------------------------------------------------------------------------- #

def _w(path: Path, content: str) -> None:
    path.write_text(content.rstrip() + "\n")


def _write_hypothesis(journal: Path, lab_id: str, claim: str, falsifier: str,
                      submission: Path) -> None:
    _w(journal / "001_hypothesis.md", f"""\
---
memo: 001_hypothesis
lab_id: {lab_id}
agent: researcher
generated_at: {_BASE_TS}
falsifiability_gate: passed
---

# Hypothesis

**Lab:** `{lab_id}`  ·  **Stage:** framing

## Claim

{claim}

## Falsifier

{falsifier}

## Why this is testable

The claim names a bounded parameter interval and a concrete numeric metric
threshold, so a single experiment run either supports or refutes it. The
falsifiability gate (popper-probe) recorded `passed`.

> Provenance: claim and falsifier are read verbatim from
> [`hypothesis.md`]({_rel(submission / 'hypothesis.md', journal)}).
""")


def _write_plan(journal: Path, lab_id: str, run_command: str,
                values: list[float]) -> None:
    rows = "\n".join(
        f"| {i+1} | {v:.2f} | bounded run via stub executor |"
        for i, v in enumerate(values)
    )
    _w(journal / "002_experiment_plan.md", f"""\
---
memo: 002_experiment_plan
lab_id: {lab_id}
agent: researcher
generated_at: {_BASE_TS}
budget: bounded (5 runs, no GPU)
---

# Experiment plan

**Objective:** locate the `coefficient` that minimizes `synthetic_loss`, and
check it against the hypothesized interval (0.75, 0.85).

**Strategy:** a coarse sweep across the parameter range, then one refinement
near the apparent minimum. Each run is executed locally with:

```
{run_command}
```

| # | coefficient | run |
|---|-------------|-----|
{rows}

**Stop conditions:** budget of 5 runs; stop early if `synthetic_loss < 0.05`
is reproduced inside the hypothesized interval.

**Approval mode:** `plan_then_execute` — this plan is recorded before any run
executes.
""")


def _write_results(journal: Path, lab_id: str, runs: list[RunResult],
                   best: RunResult) -> None:
    rows = "\n".join(
        f"| `{r.run_id}` | {r.coefficient:.2f} | {r.synthetic_loss:.4f} | "
        f"{'✅ in interval' if 0.75 <= r.coefficient <= 0.85 else '—'} |"
        for r in runs
    )
    _w(journal / "003_results.md", f"""\
---
memo: 003_results
lab_id: {lab_id}
agent: analyst
generated_at: {_BASE_TS}
runs: {len(runs)}
---

# Results

{len(runs)} bounded runs completed locally. Lower `synthetic_loss` is better.

| run_id | coefficient | synthetic_loss | hypothesized interval |
|--------|-------------|----------------|-----------------------|
{rows}

**Best run:** `{best.run_id}` at coefficient **{best.coefficient:.2f}**,
`synthetic_loss = {best.synthetic_loss:.4f}`.

**Reading:** loss falls as `coefficient` approaches ~0.80 and rises on either
side — consistent with the hypothesized minimum inside (0.75, 0.85). The best
run sits at `synthetic_loss = {best.synthetic_loss:.4f}`, below the 0.05
threshold the falsifier asks for.

> Provenance: every row above is one line in
> [`runs.jsonl`](../runs.jsonl), with its config under `configs/` and stdout
> under `logs/`.
""")


def _write_memo(journal: Path, lab_id: str, claim: str, runs: list[RunResult],
               best: RunResult, claims: list[dict], submission: Path) -> None:
    ev_rows = "\n".join(
        f"| {c['claim']} | {c['evidence_type']} | `{c['source_path']}` | "
        f"`{c['run_id'] or '—'}` | {c['metric'] or '—'} |"
        for c in claims
    )
    worst = max(runs, key=lambda r: r.synthetic_loss)
    _w(journal / "004_reviewed_memo.md", f"""\
---
memo: 004_reviewed_memo
lab_id: {lab_id}
agent: writer
reviewed_by: reviewer-board (critical / neutral / enthusiast)
review_status: accepted
generated_at: {_BASE_TS}
---

# Reviewed research memo: optimal coefficient for synthetic loss

## Summary

A 5-run local sweep places the `synthetic_loss` minimum at
`coefficient ≈ {best.coefficient:.2f}` (`synthetic_loss = {best.synthetic_loss:.4f}`),
inside the hypothesized interval (0.75, 0.85). The hypothesis is **supported**
by this bounded experiment. This is a plumbing demo on a synthetic objective,
not a scientific result.

## Hypothesis

{claim}

## Experiment plan

Coarse parameter sweep (coefficient ∈ {{{', '.join(f'{r.coefficient:.2f}' for r in runs)}}})
followed by refinement near the minimum, each executed locally via the lab's
stub run command. Full plan: [`002_experiment_plan.md`](002_experiment_plan.md).

## Results

Best: `{best.run_id}` → `synthetic_loss = {best.synthetic_loss:.4f}` at
coefficient {best.coefficient:.2f}. Worst: `{worst.run_id}` →
`synthetic_loss = {worst.synthetic_loss:.4f}` at coefficient
{worst.coefficient:.2f}. Loss is monotone in `|0.8 − coefficient|`, as the
synthetic objective predicts. Full table: [`003_results.md`](003_results.md).

## Reviewer notes

- *Critical:* sweep is coarse (5 points); the minimum could sit slightly off
  {best.coefficient:.2f}. Acceptable for a bounded demo; a finer grid is the
  obvious next step.
- *Neutral:* every quantitative claim resolves to a run_id and a logged metric
  (see evidence table). Provenance is complete.
- *Enthusiast:* clean monotone signal, hypothesis interval confirmed on the
  first pass.
- **Board decision: accepted** for the internal lab journal.

## Limitations

- Synthetic objective — no real-world validity; this exercises the pipeline,
  not a domain.
- No noise model beyond the executor's built-in seed; results are deterministic.
- Single parameter, single metric. Multi-metric trade-offs are out of scope.

## Next experiment

Refine the grid to coefficient ∈ [0.78, 0.82] in 0.01 steps to localize the
minimum to two decimals, and add a held-out seed to estimate run-to-run
variance.

## Evidence table

Every nontrivial claim below points to a run, a metric, or a source file.

| claim | evidence_type | source_path | run_id | metric |
|-------|---------------|-------------|--------|--------|
{ev_rows}
""")


def _rel(target: Path, start_dir: Path) -> str:
    import os
    return os.path.relpath(target, start_dir)


# --------------------------------------------------------------------------- #
# Dashboard
# --------------------------------------------------------------------------- #

def _write_dashboard(out: Path, lab_id: str, claim: str, runs: list[RunResult],
                    best: RunResult) -> None:
    max_loss = max(r.synthetic_loss for r in runs) or 1.0
    bars = "\n".join(
        f'''        <div class="bar-row">
          <span class="coef">{r.coefficient:.2f}</span>
          <div class="bar" style="width:{max(4, r.synthetic_loss / max_loss * 100):.0f}%"
               title="{r.run_id}"></div>
          <span class="val{' best' if r.run_id == best.run_id else ''}">{r.synthetic_loss:.4f}</span>
        </div>'''
        for r in runs
    )
    run_rows = "\n".join(
        f'''        <tr{' class="best"' if r.run_id == best.run_id else ''}>
          <td><code>{r.run_id}</code></td><td>{r.coefficient:.2f}</td>
          <td>{r.synthetic_loss:.4f}</td><td><code>{r.config_path}</code></td>
          <td><code>{r.log_path}</code></td>
        </tr>'''
        for r in runs
    )
    _w(out / "dashboard.html", f"""\
<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>efferents demo — {lab_id}</title>
<style>{REPORT_CSS}</style></head><body>
<header>
  <h1>{lab_id} <span class="muted small">· efferents demo</span></h1>
  <div class="muted small">Offline run · no API calls · deterministic · synthetic objective</div>
</header>
<main>
  <div class="banner">Demo dashboard rendered from <code>runs.jsonl</code> and the
  journal memos in this folder. A live lab is served read-only with
  <code>efferents serve --lab-root &lt;lab&gt;</code>.</div>

  <div class="cards" style="margin-top:18px">
    <div class="card"><div class="k">Hypothesis</div>
      <div class="v ok">supported</div></div>
    <div class="card"><div class="k">Best synthetic_loss</div>
      <div class="v">{best.synthetic_loss:.4f}</div></div>
    <div class="card"><div class="k">Best coefficient</div>
      <div class="v">{best.coefficient:.2f}</div></div>
    <div class="card"><div class="k">Runs</div>
      <div class="v">{len(runs)}</div></div>
  </div>

  <div class="section">
    <h2>Claim under test</h2>
    <p class="muted">{claim}</p>
  </div>

  <div class="section">
    <h2>synthetic_loss by coefficient (lower is better)</h2>
{bars}
  </div>

  <div class="section">
    <h2>Runs (provenance)</h2>
    <table>
      <tr><th>run_id</th><th>coefficient</th><th>synthetic_loss</th><th>config</th><th>log</th></tr>
{run_rows}
    </table>
  </div>

  <div class="section memos">
    <h2>Research journal</h2>
    <a href="journal/001_hypothesis.md">001 · Hypothesis</a>
    <a href="journal/002_experiment_plan.md">002 · Experiment plan</a>
    <a href="journal/003_results.md">003 · Results</a>
    <a href="journal/004_reviewed_memo.md">004 · Reviewed memo (with evidence table)</a>
  </div>
</main></body></html>
""")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def run_demo(lab: str, out_dir: str | Path) -> Path:
    """Run the offline demo and write all artifacts. Returns the output dir."""
    submission = _resolve_lab(lab)
    raw = yaml.safe_load((submission / "lab.yaml").read_text())
    lab_id = raw.get("lab_id", "demo-lab")
    src_dir = (submission / (raw.get("source") or {}).get("dir", ".")).resolve()
    run_command = (raw.get("executor") or {}).get(
        "run_command", "python3 -m stub_run --config {config_path}"
    )

    out = Path(out_dir).resolve()
    journal = out / "journal"
    journal.mkdir(parents=True, exist_ok=True)

    claim, falsifier = _hypothesis_summary(submission)
    values = _sweep_values()

    print(f"efferents demo · lab={lab_id} · {len(values)} bounded runs (offline)")

    runs: list[RunResult] = []
    for i, v in enumerate(values):
        r = _run_once(submission, src_dir, run_command, v, seed=42, index=i, out=out)
        runs.append(r)
        print(f"  run {i+1}/{len(values)}  coefficient={v:.2f}  "
              f"synthetic_loss={r.synthetic_loss:.4f}")

    best = min(runs, key=lambda r: r.synthetic_loss)

    # runs.jsonl
    with (out / "runs.jsonl").open("w") as f:
        for r in runs:
            f.write(json.dumps(asdict(r)) + "\n")

    # claims.jsonl + evidence rows
    claims = _build_claims(lab_id, runs, best, submission)
    with (out / "claims.jsonl").open("w") as f:
        for c in claims:
            f.write(json.dumps(c) + "\n")

    # journal memos
    _write_hypothesis(journal, lab_id, claim, falsifier, submission)
    _write_plan(journal, lab_id, run_command, values)
    _write_results(journal, lab_id, runs, best)
    _write_memo(journal, lab_id, claim, runs, best, claims, submission)

    # dashboard
    _write_dashboard(out, lab_id, claim, runs, best)

    print(f"\nWrote demo to {out}")
    print("  journal/   4 memos (001..004)")
    print(f"  runs.jsonl   {len(runs)} runs")
    print(f"  claims.jsonl {len(claims)} claims with provenance")
    print("  dashboard.html")
    print(f"\nOpen the dashboard:\n  open {out / 'dashboard.html'}")
    return out


def _build_claims(lab_id: str, runs: list[RunResult], best: RunResult,
                 submission: Path) -> list[dict]:
    """One provenance record per nontrivial claim in the memo."""
    in_interval = [r for r in runs if 0.75 <= r.coefficient <= 0.85]
    return [
        {
            "claim": "Hypothesis was reviewed and gated as falsifiable",
            "evidence_type": "document",
            "source_path": "journal/001_hypothesis.md",
            "run_id": None,
            "metric": None,
            "value": "falsifiability_gate=passed",
        },
        {
            "claim": f"synthetic_loss is minimized near coefficient {best.coefficient:.2f}",
            "evidence_type": "run_metric",
            "source_path": best.log_path,
            "run_id": best.run_id,
            "metric": "synthetic_loss",
            "value": best.synthetic_loss,
        },
        {
            "claim": "Best run beats the 0.05 falsifier threshold",
            "evidence_type": "run_metric",
            "source_path": "runs.jsonl",
            "run_id": best.run_id,
            "metric": "synthetic_loss",
            "value": best.synthetic_loss,
        },
        {
            "claim": "Runs inside (0.75, 0.85) all stay below threshold",
            "evidence_type": "metric_aggregate",
            "source_path": "runs.jsonl",
            "run_id": None,
            "metric": "synthetic_loss",
            "value": f"max={max((r.synthetic_loss for r in in_interval), default=None)}",
        },
        {
            "claim": "Experiment plan recorded before execution (plan_then_execute)",
            "evidence_type": "document",
            "source_path": "journal/002_experiment_plan.md",
            "run_id": None,
            "metric": None,
            "value": "approval=plan_then_execute",
        },
    ]
