"""Compact per-run entries for ``lab/lab_notebook.md``.

The notebook tail is the context the Researcher reads every iteration. A real
lab emits dozens of numeric metric columns per run; rendering all of them
after every run floods that tail with tables. Each run therefore gets a
compact entry: the headline metric, the configured panel columns, constraint
pass/fail, and a pointer to the ledger for the rest. The full metric set is
never lost — it lives in ``runs.sqlite`` and the run's ``raw_metrics_json``.
"""
from __future__ import annotations

from typing import Any, Mapping

from efferents import lab as _lab
from efferents import metrics_view as mv


def compact_columns(cfg=None) -> list[str]:
    """Headline column followed by the configured panel columns, de-duplicated."""
    cfg = cfg or _lab.get_config()
    cols: list[str] = []
    for c in (cfg.metrics.headline.column, *(p.column for p in cfg.metrics.panels)):
        if c not in cols:
            cols.append(c)
    return cols


def render_value(value: Any) -> str:
    """Finite floats with %.4g; everything else as str; '—' when absent."""
    if value is None:
        return "—"
    fv = mv.finite(value)
    if fv is not None and isinstance(value, float):
        return f"{fv:.4g}"
    return str(value)


def constraint_status(metrics: Mapping[str, Any], cfg=None) -> str:
    """'pass', 'FAIL — <detail>', or 'n/a (none configured)'."""
    cfg = cfg or _lab.get_config()
    if not cfg.metrics.constraints:
        return "n/a (none configured)"
    failures = mv.constraint_failures(dict(metrics), cfg=cfg)
    return "pass" if not failures else "FAIL — " + "; ".join(failures)


def render_metrics_summary(metrics: Mapping[str, Any], cfg=None) -> str:
    """Markdown block: a one-row table of the compact columns, the constraint
    verdict, and a ``+N more metrics in ledger`` note for the hidden columns."""
    cfg = cfg or _lab.get_config()
    cols = compact_columns(cfg)
    lines = [
        "| " + " | ".join(cols) + " |",
        "|" + "|".join("---" for _ in cols) + "|",
        "| " + " | ".join(render_value(metrics.get(c)) for c in cols) + " |",
        "",
        f"**Constraints**: {constraint_status(metrics, cfg)}",
    ]
    hidden = [k for k in metrics if k not in cols]
    if hidden:
        lines.append(f"_+{len(hidden)} more metrics in ledger (`runs.sqlite`)._")
    return "\n".join(lines)


def format_run_entry(
    *,
    name: str,
    hypothesis: str,
    expected: str,
    overrides: dict[str, Any],
    result: Any,
    duration: float,
    started: str,
) -> str:
    """Compact notebook entry for one executed run.

    Keyword-compatible with ``executor._format_outcome`` so the orchestrator
    can install it as the per-run renderer; ``result`` is an ``exec.RunResult``.
    """
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
        lines.append(render_metrics_summary(result.metrics))
    else:
        lines.append(f"**Error**: {result.error or 'no metrics emitted'}")
        if result.stderr:
            lines += ["", "```", result.stderr[-1024:], "```"]
    return "\n".join(lines)


def install_compact_run_entries(executor_module: Any) -> None:
    """Make ``executor.execute`` append compact entries instead of full tables.

    The executor renders its notebook entry through the module-level
    ``_format_outcome`` hook; swapping that hook keeps the executor's
    persistence path untouched while un-flooding ``notebook_tail()``.
    """
    if hasattr(executor_module, "_format_outcome"):
        executor_module._format_outcome = format_run_entry
