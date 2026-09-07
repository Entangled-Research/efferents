"""Deterministic evidence aggregation over the run ledger.

Pure functions that turn the flat run rows the Analyst already loads into
bucket-level aggregates (by ``metrics.bucket_axes``), per-arm summaries and
seed-paired deltas (by ``evidence.comparison.axis``), and a verdict on the
lab's declared ``falsifiers``. Nothing here knows what a column means; the
lab names the columns, arms and rules in ``lab.yaml``.

Bad data never raises: values that are not finite numbers are skipped, and a
rule that cannot be decided reports ``insufficient_data``.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from typing import Any

from efferents import metrics_view as mv

BOOTSTRAP_RESAMPLES = 2000
AGGREGATES = ("median", "mean", "min", "max", "count", "frac_ge", "frac_le")
OPERATORS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}
_EMPTY_STATS = {"n": 0, "median": None, "mean": None, "min": None, "max": None}


def _observations(row: dict) -> list[dict]:
    raw = row.get("observations_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(raw, list):
        return []
    return [o for o in raw if isinstance(o, dict)]


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return dict(_EMPTY_STATS)
    return {
        "n": len(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "min": min(values),
        "max": max(values),
    }


def _sortable(value: Any) -> tuple:
    fv = mv.finite(value)
    return (0, fv, "") if fv is not None else (1, 0.0, str(value))


def bucket_label(key: tuple, axes: tuple[str, ...]) -> str:
    return ", ".join(f"{a}={v}" for a, v in zip(axes, key)) if axes else "all"


def _group_by_bucket(rows: list[dict], axes: tuple[str, ...]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(r.get(a) for a in axes), []).append(r)
    return dict(sorted(groups.items(), key=lambda kv: tuple(_sortable(v) for v in kv[0])))


def bootstrap_median_ci(
    values: list[float], *, resamples: int = BOOTSTRAP_RESAMPLES, seed: int = 0
) -> tuple[float, float] | None:
    """Percentile 95% CI of the median via seeded bootstrap (pure Python).
    None when fewer than two values."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    medians = sorted(
        statistics.median([rng.choice(values) for _ in range(n)]) for _ in range(resamples)
    )
    lo = medians[max(0, math.ceil(0.025 * resamples) - 1)]
    hi = medians[min(resamples - 1, math.ceil(0.975 * resamples) - 1)]
    return (lo, hi)


def _arm_order(cfg, arms: set[str]) -> list[str]:
    preferred = [a for a in cfg.evidence.comparison_order if a in arms]
    return preferred + sorted(a for a in arms if a not in preferred)


def _arm_metrics(rows: list[dict], axis: str) -> dict[str, dict[str, list[float]]]:
    """{arm: {metric: [finite values]}} across every observation carrying ``axis``."""
    out: dict[str, dict[str, list[float]]] = {}
    for r in rows:
        for obs in _observations(r):
            dims = obs.get("dimensions")
            metrics = obs.get("metrics")
            if not isinstance(dims, dict) or not isinstance(metrics, dict) or axis not in dims:
                continue
            arm = out.setdefault(str(dims[axis]), {})
            for m, v in metrics.items():
                fv = mv.finite(v)
                if fv is not None:
                    arm.setdefault(str(m), []).append(fv)
    return out


def paired_deltas(
    rows: list[dict], axis: str, first: str, second: str
) -> dict[str, list[float]]:
    """Per metric, ``first - second`` deltas for each (run, seed) that carries
    both arms. The seed comes from the observation's dimensions, falling back
    to the run row's ``seed`` column."""
    out: dict[str, list[float]] = {}
    for r in rows:
        per_seed: dict[Any, dict[str, dict[str, float]]] = {}
        for obs in _observations(r):
            dims = obs.get("dimensions")
            metrics = obs.get("metrics")
            if not isinstance(dims, dict) or not isinstance(metrics, dict):
                continue
            arm = str(dims.get(axis)) if axis in dims else None
            if arm not in (first, second):
                continue
            seed = dims.get("seed", r.get("seed"))
            slot = per_seed.setdefault(seed, {}).setdefault(arm, {})
            for m, v in metrics.items():
                fv = mv.finite(v)
                if fv is not None:
                    slot.setdefault(str(m), fv)
        for arms in per_seed.values():
            a, b = arms.get(first, {}), arms.get(second, {})
            for m in a.keys() & b.keys():
                out.setdefault(m, []).append(a[m] - b[m])
    return out


def _numeric_columns(rows: list[dict], excluded: set[str]) -> list[str]:
    cols: list[str] = []
    for r in rows:
        for k, v in r.items():
            if k not in excluded and k not in cols and mv.finite(v) is not None:
                cols.append(k)
    return cols


def bucket_summary(rows: list[dict], cfg) -> dict[str, dict[str, Any]]:
    """Per bucket (label -> entry): ``n``, ``columns`` {col: n/median/mean/min/max},
    and when a comparison axis is configured and observed, ``arms``
    {arm: {metric: stats}} plus ``paired`` {metric: stats + ci95 + arms}."""
    axes = tuple(cfg.metrics.bucket_axes)
    axis = cfg.evidence.comparison_axis
    excluded = set(mv.META_COLUMNS) | set(axes) | {"seed"}
    out: dict[str, dict[str, Any]] = {}
    for key, group in _group_by_bucket(rows, axes).items():
        entry: dict[str, Any] = {
            "key": key,
            "n": len(group),
            "columns": {
                col: _stats([v for r in group if (v := mv.finite(r.get(col))) is not None])
                for col in _numeric_columns(group, excluded)
            },
            "arms": {},
            "paired": {},
        }
        if axis:
            arms = _arm_metrics(group, axis)
            order = _arm_order(cfg, set(arms))
            entry["arms"] = {
                a: {m: _stats(vs) for m, vs in arms[a].items()} for a in order
            }
            if len(order) >= 2:
                entry["paired"] = {
                    m: {**_stats(vs), "ci95": bootstrap_median_ci(vs),
                        "arms": (order[0], order[1])}
                    for m, vs in paired_deltas(group, axis, order[0], order[1]).items()
                }
        out[bucket_label(key, axes)] = entry
    return out


# ---------------------------------------------------------------------------
# Falsifiers
# ---------------------------------------------------------------------------

def _paired_values(rows: list[dict], rule, cfg) -> list[float]:
    """Seed-paired differences: the flat per-run ``rule.column`` when the lab
    persists a delta itself, else ``rule.metric`` differenced across the two
    comparison arms of each (run, seed)."""
    if rule.column:
        return [v for r in rows if (v := mv.finite(r.get(rule.column))) is not None]
    axis = cfg.evidence.comparison_axis
    if not axis:
        return []
    order = _arm_order(cfg, set(_arm_metrics(rows, axis)))
    if len(order) < 2:
        return []
    return paired_deltas(rows, axis, order[0], order[1]).get(rule.metric, [])


def _aggregate(agg: str, values: list[float], threshold: float | None) -> float:
    if agg == "count":
        return float(len(values))
    if agg in ("frac_ge", "frac_le"):
        t = threshold if threshold is not None else 0.0
        hits = [v >= t if agg == "frac_ge" else v <= t for v in values]
        return sum(hits) / len(values)
    fn = {"median": statistics.median, "mean": statistics.fmean, "min": min, "max": max}[agg]
    return float(fn(values))


def _fmt(x: float | None) -> str:
    return "—" if x is None else f"{x:.4g}"


def _judge(rule, group: list[dict], label: str, cfg) -> tuple[str | None, str]:
    """(fired: bool | None, detail). None means insufficient data."""
    if rule.kind == "paired":
        values = _paired_values(group, rule, cfg)
        if len(values) < rule.min_n:
            return None, f"{label}: n={len(values)} < min_n={rule.min_n}"
        ci = bootstrap_median_ci(values)
        excludes = ci is not None and (ci[0] > 0 or ci[1] < 0)
        name = rule.column or f"\u0394{rule.metric}"
        detail = (
            f"{label}: median({name})={_fmt(statistics.median(values))} "
            f"95% CI [{_fmt(ci[0])}, {_fmt(ci[1])}] n={len(values)} "
            f"({'excludes' if excludes else 'includes'} zero)"
        )
        return excludes == rule.ci95_excludes_zero, detail
    values = [v for r in group if (v := mv.finite(r.get(rule.column))) is not None]
    if len(values) < rule.min_n:
        return None, f"{label}: n={len(values)} < min_n={rule.min_n}"
    stat = _aggregate(rule.agg, values, rule.threshold)
    arg = rule.column if rule.threshold is None else f"{rule.column}, {rule.threshold:g}"
    detail = f"{label}: {rule.agg}({arg})={_fmt(stat)} {rule.op} {rule.value:g}? n={len(values)}"
    return OPERATORS[rule.op](stat, rule.value), detail


def _bucket_matches(key: tuple, wanted: Any) -> bool:
    if not key:
        return False
    actual = key[0]
    fa, fw = mv.finite(actual), mv.finite(wanted)
    return fa == fw if fa is not None and fw is not None else str(actual) == str(wanted)


def evaluate_falsifier(rule, rows: list[dict], cfg) -> dict[str, Any]:
    axes = tuple(cfg.metrics.bucket_axes)
    groups = _group_by_bucket(rows, axes)
    if rule.bucket not in ("any", "all"):
        rows_in = [r for k, g in groups.items() if _bucket_matches(k, rule.bucket) for r in g]
        groups = {(rule.bucket,): rows_in}
        labels = {(rule.bucket,): f"{axes[0]}={rule.bucket}" if axes else "all"}
    else:
        labels = {k: bucket_label(k, axes) for k in groups}

    judged = [_judge(rule, g, labels[k], cfg) for k, g in groups.items()]
    detail = "; ".join(d for _, d in judged) or "no runs"
    decided = [f for f, _ in judged if f is not None]
    if not decided:
        status = "insufficient_data"
    elif rule.bucket == "all":
        if not all(decided):
            status = "survived"
        elif len(decided) == len(judged):
            status = "fired"
        else:
            status = "insufficient_data"
    else:  # "any" or a single named bucket
        status = "fired" if any(decided) else "survived"
    return {"id": rule.id, "status": status, "detail": detail, "description": rule.description}


def evaluate_falsifiers(rows: list[dict], cfg) -> list[dict[str, Any]]:
    """Evaluate every configured falsifier; never raises on bad rows."""
    results = []
    for rule in cfg.falsifiers:
        try:
            results.append(evaluate_falsifier(rule, rows, cfg))
        except Exception as exc:  # defensive: malformed ledger data must not kill a digest
            results.append({
                "id": rule.id, "status": "insufficient_data",
                "detail": f"evaluation error: {type(exc).__name__}: {exc}",
                "description": rule.description,
            })
    return results


def verdict(results: list[dict[str, Any]]) -> str:
    """falsified if any rule fired; survives if every rule survived; else undecided."""
    statuses = [r["status"] for r in results]
    if any(s == "fired" for s in statuses):
        return "falsified"
    if statuses and all(s == "survived" for s in statuses):
        return "survives"
    return "undecided"
