"""Researcher agent — Student↔Supervisor dialogue producing config + architectural proposals.

The orchestrator's call boundary is unchanged: ``propose()`` returns
``{proposals, architectural_proposals, consulted_topic_ids, raw}`` and persists
architectural proposals to ``lab/proposed_changes.md`` exactly as before. The
dialogue is a private implementation detail.

Internal flow (each iteration):

    Turn 0  _saturation_report(paths)                              # deterministic
    Turn 1  Supervisor brief    (Sonnet by default; Opus when streak ≥ 2)
    Turn 2  Student propose     (Sonnet, with lit_review tool)
    Turn 3  Supervisor review   (same model as the brief)

A single ``$1.00`` per-call cost guardrail aborts the review turn (Student
output is returned verbatim). A circuit-breaker bypasses the Supervisor
entirely after two consecutive empty-proposal iterations — falling back to a
single Student-only call so the queue never starves.

State (in ``lab/state.json``):
    supervisor_opus_streak      : int  iterations of consecutive saturation ≥ 1
    researcher_consecutive_empty: int  consecutive empty propose() returns

Observability:
    Each call appends one row to ``lab/researcher_dialogue.jsonl`` (no reader).

Output schema for the Coder is unchanged.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import anthropic
import yaml

from efferents.agents import librarian
from efferents.agents import popper_gate as _popper_gate
from efferents.agents.budget import (
    BudgetTracker,
    CallUsage,
    billing_model,
    model_for,
    model_for_supervisor,
)
from efferents.agents.state import (
    SEMANTIC_IDENTITY_KEYS,
    LabPaths,
    StudentStateView,
    append_jsonl,
    campaign_insert as _campaign_insert,
    campaign_open_list_for_student as _campaign_open_list_for_student,
    campaign_recently_closed_list as _campaign_recently_closed_list,
    load_proposal_base_config,
    load_state,
    notebook_append,
    notebook_tail,
    now_iso,
    open_blocks,
    parse_json_loose,
    parse_json_with_one_retry,
    proposal_config_hash,
    proposal_semantic_hash,
    queued_config_hashes,
    read_context,
    read_jsonl,
    recent_runs,
    record_blocked,
    retry_hint,
    save_state,
    semantic_config_hash,
    semantic_hash_index,
    succeeded_run_for_hash,
    succeeded_run_for_semantic_hash,
)
from efferents import lab as _lab
from efferents.lab import _COL_NAME_RE  # SQL-identifier sanitizer
from efferents.agents.prompts.loader import load_prompt
from efferents import metrics_view as mv


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")


def _campaign_metric_from_proposal(
    new_campaign: dict,
) -> tuple[str | None, str | None]:
    """Extract + validate (headline_metric, direction) from a Student's
    new_campaign declaration. Returns (None, None) when absent or invalid,
    so the campaign falls back to the LabConfig default."""
    raw_metric = new_campaign.get("headline_metric")
    metric = raw_metric.strip() if isinstance(raw_metric, str) else ""
    raw_dir = new_campaign.get("direction")
    direction = raw_dir.strip() if isinstance(raw_dir, str) else ""
    if not metric or not _COL_NAME_RE.match(metric):
        return (None, None)
    if direction not in ("min", "max"):
        return (None, None)
    return (metric, direction)

MAX_LIT_CALLS_PER_PASS = 5
PER_CALL_COST_CAP_USD = 1.00
EMPTY_FALLBACK_THRESHOLD = 2  # consecutive empty propose() returns → bypass Supervisor


# -----------------------------------------------------------------------------
# Deterministic helpers
# -----------------------------------------------------------------------------


_META_RENDER_COLS = ("run_id", "started_at", "campaign_id", "researcher_mode", "duration_seconds")


def _render_cell(value: Any) -> str:
    """Finite floats with %.4g, other values as str, '' if absent."""
    f = mv.finite(value)
    if f is not None:
        return f"{f:.4g}"
    return "" if value is None else str(value)


def _recent_run_columns(db_path) -> list[str]:
    """Lab-agnostic column order: a fixed meta subset, the configured headline
    column, the panel columns, then the remaining auto-discovered columns —
    de-duplicated, preserving first-seen order."""
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(col: str) -> None:
        if col not in seen:
            seen.add(col)
            ordered.append(col)

    for c in _META_RENDER_COLS:
        _add(c)
    _add(mv.headline().column)
    for p in mv.panels():
        _add(p.column)
    for c in mv.discover_columns(db_path):
        _add(c)
    return ordered


def _format_recent_runs(rows: list[dict[str, Any]], db_path) -> str:
    if not rows:
        return "(no runs yet)"
    cols = _recent_run_columns(db_path)
    out = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        cells = [_render_cell(r.get(c)) for c in cols]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def _read_default_config() -> str:
    p = Path("config/default.yaml")
    return p.read_text() if p.exists() else "(missing)"


# Keys the Executor stamps onto every rendered config; they identify the
# proposal, not the experiment, so they are not "overrides" worth listing
# (and they are what the semantic hash strips).
_RENDER_META_KEYS = frozenset(SEMANTIC_IDENTITY_KEYS)
_MISSING = object()


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    """Nested dict -> {"dotted.path": leaf} (the config_overrides shape)."""
    out: dict[str, Any] = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict) and v:
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _row_semantic_hash(row: dict[str, Any]) -> str | None:
    """The row's stored ``config_hash_semantic``, recomputed from
    ``config_yaml`` for rows written before the column existed."""
    h = row.get("config_hash_semantic")
    if h:
        return str(h)
    if isinstance(row.get("config_yaml"), str):
        return semantic_config_hash(row["config_yaml"])
    return None


def _tried_configs(rows: list[dict[str, Any]], base_config: dict | None) -> list[dict]:
    """Group succeeded runs by semantic config hash (identity keys stripped,
    so a renamed re-run lands in the same group); recover each group's
    overrides by diffing the stored rendered config against the lab's config
    template. Groups are returned in recency order (``rows`` is newest-first)."""
    base_flat = _flatten(base_config or {})
    try:
        headline_col = mv.headline().column
    except RuntimeError:
        headline_col = None
    groups: dict[str, dict[str, Any]] = {}
    for r in rows:
        h = _row_semantic_hash(r)
        if not h:
            continue
        g = groups.get(h)
        if g is None:
            overrides: dict[str, Any] = {}
            if isinstance(r.get("config_yaml"), str):
                try:
                    rendered = yaml.safe_load(r["config_yaml"]) or {}
                except yaml.YAMLError:
                    rendered = {}
                overrides = {
                    k: v for k, v in _flatten(rendered).items()
                    if k not in _RENDER_META_KEYS and base_flat.get(k, _MISSING) != v
                }
            g = groups[h] = {
                "hash": str(h), "overrides": overrides, "n": 0,
                "run_id": r.get("run_id"), "headline": [],
            }
        g["n"] += 1
        v = mv.finite(r.get(headline_col)) if headline_col else None
        if v is not None:
            g["headline"].append(v)
    return list(groups.values())


def _format_tried_configs(tried: list[dict]) -> str:
    """One compact line per configuration: hash prefix, overrides, headline."""
    if not tried:
        return "(no runs yet)"
    try:
        h = mv.headline()
        headline_col, direction = h.column, h.direction
    except RuntimeError:
        headline_col, direction = None, "min"
    lines = []
    for g in tried:
        digest = g["hash"].split(":")[-1][:12]
        overrides = ", ".join(f"{k}={v!r}" for k, v in g["overrides"].items()) or "(defaults)"
        if g["headline"]:
            best = min(g["headline"]) if direction == "min" else max(g["headline"])
            result = f"{headline_col}={best:.4g}"
        else:
            result = "(no headline value)"
        times = f" ×{g['n']}" if g["n"] > 1 else ""
        lines.append(f"- `{digest}`{times} | {overrides} | {result}")
    return "\n".join(lines)


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _saturation_metrics() -> list[tuple[str, str]]:
    """(column, direction) for each metric saturation is evaluated on: the
    configured headline metric plus panel metrics, de-duplicated by column."""
    h = mv.headline()
    out: list[tuple[str, str]] = [(h.column, h.direction)]
    seen = {h.column}
    for p in mv.panels():
        if p.column not in seen:
            seen.add(p.column)
            out.append((p.column, p.direction))
    return out


def _config_group_key(row: dict) -> str:
    """A stable key identifying a run's configuration, for same-config
    replication detection. Prefers an explicit config hash, then the config
    path, then the run id (which makes the run its own singleton group)."""
    return str(
        row.get("config_hash")
        or row.get("config_path")
        or row.get("run_id")
        or id(row)
    )


def _saturation_report(paths: LabPaths, *, n: int = 50) -> dict[str, Any]:
    """Detect saturated experimental axes across the configured metrics.

    Runs are grouped into buckets by ``LabConfig.metrics.bucket_axes`` (a lab
    that declares no axes is treated as a single bucket). Within each bucket the
    saturation heuristic is computed independently per metric (the headline
    metric plus panels, direction-aware):

    - per-config noise floor: mean of within-config stds where ≥2 runs share a
      configuration;
    - cross-config delta: std of best-per-config metric values;
    - top-quartile floor: std/mean of the best 25% of per-config bests (catches
      the case where the best results are clustered after many attempts).

    A metric is saturated for that bucket when:

    - same-config replication exists AND cross_std ≤ 1.5 × noise_floor; or
    - no replication AND n_configs ≥ 10 (many tries, no breakthrough); or
    - no replication AND n_configs ≥ 6 AND top-quartile floor_ratio < 0.10.

    A bucket as a whole is saturated when at least half of the configured
    metrics are saturated — single-metric saturation can be a coincidence.
    """
    try:
        metric_specs = _saturation_metrics()
        axes = _lab.get_config().metrics.bucket_axes
    except RuntimeError:
        # No active LabConfig (e.g. a bare unit test). Nothing to report on.
        return {"saturated_axes": [], "score": 0, "evidence": []}
    if not paths.runs_db.exists():
        return {"saturated_axes": [], "score": 0, "evidence": []}
    try:
        rows = recent_runs(paths.runs_db, n=n)
    except sqlite3.OperationalError:
        return {"saturated_axes": [], "score": 0, "evidence": []}

    headline_col = mv.headline().column

    def _bucket_key(row: dict) -> tuple[str, ...]:
        return ("all",) if not axes else tuple(str(row.get(a)) for a in axes)

    def _bucket_label(key: tuple[str, ...]) -> str:
        return "all runs" if not axes else " ".join(f"{a}={v}" for a, v in zip(axes, key))

    # bucket -> config_group -> metric -> [finite values]
    buckets: dict[tuple[str, ...], dict[str, dict[str, list[float]]]] = (
        defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    )
    for r in rows:
        key = _bucket_key(r)
        cg = _config_group_key(r)
        for col, _dir in metric_specs:
            v = mv.finite(r.get(col))
            if v is not None:
                buckets[key][cg][col].append(v)

    def _best(vals: list[float], direction: str) -> float:
        return min(vals) if direction == "min" else max(vals)

    def _metric_status(per_cfg, metric: str, direction: str) -> dict[str, Any]:
        vals_per_config = [d[metric] for d in per_cfg.values() if d.get(metric)]
        if len(vals_per_config) < 4:
            return {"n_configs": len(vals_per_config), "saturated": False, "rule": "n<4"}
        bests = [_best(vs, direction) for vs in vals_per_config]
        best_val = _best(bests, direction)
        cross_std = _stdev(bests)
        within_stds = [_stdev(vs) for vs in vals_per_config if len(vs) >= 2]
        seed_noise = sum(within_stds) / len(within_stds) if within_stds else None
        ranked = sorted(bests, reverse=(direction == "max"))
        top_q = ranked[: max(2, len(ranked) // 4)]
        floor_mean = sum(top_q) / len(top_q)
        floor_ratio = (_stdev(top_q) / abs(floor_mean)) if floor_mean != 0 else 0.0
        n_configs = len(vals_per_config)
        if seed_noise is not None and seed_noise > 0:
            if cross_std <= 1.5 * seed_noise:
                return {
                    "n_configs": n_configs, "saturated": True, "best": best_val,
                    "rule": f"cross_std={cross_std:.3g} ≤ 1.5×noise_floor={seed_noise:.3g}",
                }
        if n_configs >= 10:
            return {
                "n_configs": n_configs, "saturated": True, "best": best_val,
                "rule": f"n_configs={n_configs} ≥ 10 (no breakthrough)",
            }
        if n_configs >= 6 and floor_ratio < 0.10:
            return {
                "n_configs": n_configs, "saturated": True, "best": best_val,
                "rule": f"n_configs={n_configs}, floor_ratio={floor_ratio:.3f} < 0.10",
            }
        return {"n_configs": n_configs, "saturated": False, "best": best_val, "rule": ""}

    saturated_axes: list[str] = []
    evidence: list[dict[str, Any]] = []
    for key, per_cfg in buckets.items():
        per_metric = {col: _metric_status(per_cfg, col, d) for col, d in metric_specs}
        n_sat = sum(1 for s in per_metric.values() if s["saturated"])
        is_saturated = n_sat >= max(1, (len(metric_specs) + 1) // 2)
        n_configs = len([cg for cg in per_cfg if per_cfg[cg].get(headline_col)])
        n_runs = sum(len(per_cfg[cg].get(headline_col, [])) for cg in per_cfg)
        label = _bucket_label(key)

        ev = {
            "bucket": label,
            "n_configs": n_configs,
            "n_runs": n_runs,
            "per_metric": {
                col: {
                    "best": round(s["best"], 4) if s.get("best") is not None else None,
                    "saturated": s["saturated"],
                    "rule": s["rule"],
                }
                for col, s in per_metric.items()
            },
            "n_saturated_metrics": n_sat,
            "saturated": is_saturated,
        }
        evidence.append(ev)
        if is_saturated:
            # Name the saturated metrics in the axis label so the Supervisor's
            # brief can be specific about what's stuck.
            stuck = [col for col, s in per_metric.items() if s["saturated"]]
            saturated_axes.append(f"{label} on metrics {stuck}")

    score = min(len(saturated_axes), 5)
    return {"saturated_axes": saturated_axes, "score": score, "evidence": evidence}


def _reject_duplicate_proposals(
    paths: LabPaths,
    proposals: list[dict[str, Any]],
    *,
    base_config: dict | None = None,
) -> list[dict[str, Any]]:
    """The single dedup choke point: every student's proposals pass through
    ``propose()``, and this runs after they are tagged with campaign / mode /
    student (all of which the Executor renders into the config).

    A proposal is a duplicate when its rendered config already succeeded in
    the ledger, is queued or in flight, or appeared earlier in this batch —
    matched on the exact ``config_hash`` *and* on the semantic hash (identity
    keys stripped), so renaming a proposal with identical overrides does not
    get it past the gate. Ledger rows older than the ``config_hash_semantic``
    column are re-hashed from ``config_yaml`` (last ~200 rows, once per call).
    ``allow_duplicate: true`` plus a non-empty ``duplicate_reason`` (e.g. a
    reproducibility check) lets it through; every decision is written to the
    notebook so the funder can audit repeated spend.
    """
    if not proposals:
        return proposals
    if base_config is None:
        base_config = load_proposal_base_config()
    if base_config is None:
        return proposals  # no template to render against: nothing to compare
    queued = queued_config_hashes(paths.queue, base_config=base_config)
    queued_sem = queued_config_hashes(paths.queue, base_config=base_config, semantic=True)
    legacy_index: dict[str, dict[str, Any]] | None = None  # built lazily, once
    seen_batch: dict[str, str] = {}
    kept: list[dict[str, Any]] = []
    for p in proposals:
        name = str(p.get("name") or "?")
        h = proposal_config_hash(p, base_config=base_config)
        if h is None:
            kept.append(p)
            continue
        hs = proposal_semantic_hash(p, base_config=base_config) or h
        row = succeeded_run_for_hash(paths.runs_db, h)
        renamed = ""
        if row is None:
            row = succeeded_run_for_semantic_hash(paths.runs_db, hs)
            if row is None:
                if legacy_index is None:
                    legacy_index = semantic_hash_index(paths.runs_db)
                row = legacy_index.get(hs)
            if row is not None:
                renamed = " (same configuration under a different name)"
        if row is not None:
            dup_of = f"run {row.get('run_id')}{renamed}"
        elif h in queued:
            dup_of = f"queued proposal {queued[h]!r}"
        elif hs in queued_sem:
            dup_of = f"queued proposal {queued_sem[hs]!r} (same configuration under a different name)"
        elif hs in seen_batch:
            dup_of = f"proposal {seen_batch[hs]!r} in the same batch"
        else:
            seen_batch[hs] = name
            kept.append(p)
            continue
        reason = p.get("duplicate_reason")
        reason = reason.strip() if isinstance(reason, str) else ""
        if p.get("allow_duplicate") is True and reason:
            p["duplicate_of"] = dup_of
            notebook_append(
                paths.notebook,
                f"## {now_iso()} — proposal {name!r} allowed as duplicate of "
                f"{dup_of} (hash {h[:19]}): {reason}\n",
            )
            kept.append(p)
            continue
        detail = " (allow_duplicate set without duplicate_reason)" if p.get("allow_duplicate") else ""
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — proposal skipped: duplicate of {dup_of} "
            f"— {name!r} hash {h[:19]}{detail}\n",
        )
    return kept


def _record_blocked_on_infrastructure(
    paths: LabPaths, raw: Any, *, student_id: str
) -> dict[str, Any] | None:
    """Validate and persist a Student's ``blocked_on_infrastructure`` object
    (``{summary, evidence: [run_ids], proposed_change}``). Returns the stored
    record, or None when nothing valid was declared or the student already has
    an open block (one open block per student; the duplicate is noted)."""
    if not isinstance(raw, dict):
        return None
    summary = raw.get("summary")
    summary = " ".join(summary.split()) if isinstance(summary, str) else ""
    if not summary:
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — student {student_id} emitted blocked_on_infrastructure "
            "without a summary; ignored\n",
        )
        return None
    evidence = raw.get("evidence")
    evidence = [str(e) for e in evidence if e] if isinstance(evidence, list) else []
    change = raw.get("proposed_change")
    change = change if isinstance(change, str) else ""
    record = record_blocked(
        paths.root, student_id=student_id, summary=summary,
        evidence=evidence, proposed_change=change,
    )
    if record is None:
        existing = open_blocks(paths.root, student_id=student_id)
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — student {student_id} re-declared an infrastructure "
            f"block while {existing[0].get('id') if existing else '?'} is still open; "
            "ignored (one open block per student)\n",
        )
        return None
    notebook_append(
        paths.notebook,
        f"## {now_iso()} — student {student_id} BLOCKED on infrastructure: {summary}\n\n"
        f"**Evidence**: {', '.join(evidence) or '(none cited)'}\n\n"
        f"**Proposed change**: {record['proposed_change'] or '(none)'}\n\n"
        f"**Block id**: `{record['id']}` (lab/blocked.jsonl)\n",
    )
    return record


def _ensure_lit_context(
    proposals: list[dict[str, Any]], consulted: list[str]
) -> list[dict[str, Any]]:
    """Backfill ``lit_context`` on proposals that omitted it."""
    for p in proposals:
        if "lit_context" not in p or not isinstance(p["lit_context"], list):
            p["lit_context"] = list(consulted)
    return proposals


def _persist_architectural(path: Path, items: list[dict[str, Any]]) -> None:
    """Append architectural proposals to a markdown log for the Coder."""
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [f"\n## {ts}\n"]
    for it in items:
        lines.append(f"### {it.get('name', '(unnamed)')}\n")
        if it.get("principle"):
            lines.append(f"- **Principle**: {it['principle']}")
        if it.get("what"):
            lines.append(f"- **What**: {it['what']}")
        if it.get("why"):
            lines.append(f"- **Why**: {it['why']}")
        if it.get("expected_effort"):
            lines.append(f"- **Effort**: {it['expected_effort']}")
        if it.get("expected_payoff"):
            lines.append(f"- **Payoff**: {it['expected_payoff']}")
        if it.get("requires_new_file"):
            lines.append(f"- **NewFiles**: {', '.join(it.get('new_files') or []) or '(unspecified)'}")
        lines.append("")
    text = "\n".join(lines) + "\n"
    if not path.exists():
        path.write_text(
            "# Proposed architectural changes\n\n"
            "Researcher-authored ideas that require code changes (not config-only). "
            "Coder agent reads from here.\n"
        )
    with path.open("a") as f:
        f.write(text)


# -----------------------------------------------------------------------------
# Anthropic call helpers
# -----------------------------------------------------------------------------


def _simple_call(
    *,
    client: anthropic.Anthropic,
    system: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    model: str,
    agent: str,
    budget: BudgetTracker,
    max_tokens: int,
    notes: str = "",
) -> str:
    """One-shot ``messages.create`` without tools. Records spend, returns text."""
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    usage = CallUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    )
    budget.record(
        agent=agent, model=billing_model(client, model), usage=usage,
        notes=f"{notes} | stop={resp.stop_reason}".strip(" |"),
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


# -----------------------------------------------------------------------------
# Context blocks shared by Student + Supervisor
# -----------------------------------------------------------------------------


def _shared_static_block(*, vision: str, decisions: str, charter: str = "") -> str:
    charter_block = ""
    if charter.strip():
        charter_block = (
            "\n\n## Lab charter (popper.md)\n\n"
            "The lab's probed research directions, starting from the funder's "
            "initial prompt. Guidance for orienting and classifying student "
            "work — not binding rules; propose amendments when evidence says "
            "the direction should move.\n\n" + charter
        )
    return (
        "## Vision\n\n" + vision
        + "\n\n## Decisions\n\n" + decisions
        + charter_block
        + "\n\n## Default config (config/default.yaml)\n\n"
        + "Use ONLY keys that appear in this YAML when emitting `config_overrides`. "
        + "Dotted paths must match this structure exactly (e.g., `eval.centering`, "
        + "not `data.centering`).\n\n```yaml\n"
        + _read_default_config()
        + "\n```"
    )


def _format_open_blocks(blocks: list[dict[str, Any]]) -> str:
    """One line per open infrastructure block, oldest first."""
    if not blocks:
        return "(none)"
    lines = []
    for b in blocks:
        evidence = ", ".join(str(e) for e in (b.get("evidence") or [])) or "none cited"
        change = b.get("proposed_change") or "(no change proposed)"
        lines.append(
            f"- `{b.get('id', '?')}` student `{b.get('student_id', '?')}` "
            f"({str(b.get('ts', ''))[:19]}): {b.get('summary', '')} "
            f"— evidence: {evidence} — proposed change: {change}"
        )
    return "\n".join(lines)


def _shared_dynamic_block(
    *,
    research_log: str,
    runs_table: str,
    notebook: str,
    tried: str = "(no runs yet)",
    blocked: str = "(none)",
) -> str:
    return (
        "## Research log (human-curated)\n\n" + research_log
        + "\n\n## Recent runs\n\n" + runs_table
        + "\n\n## Already tried configurations\n\n"
        "Each line is one configuration that already ran, keyed by its semantic "
        "hash — the rendered config with the proposal's name, campaign, mode and "
        "student stripped (hash prefix | overrides vs. the default config | best "
        "headline). Proposals whose overrides render to one of these are rejected "
        "before they reach the queue, whatever they are named — do not re-propose "
        "them. A deliberate replication (reproducibility check) must set "
        "`allow_duplicate: true` and state `duplicate_reason`.\n\n" + tried
        + "\n\n## Open infrastructure blocks\n\n"
        "Students have declared that these cannot be tested until the executor "
        "itself is changed (the owner or the Coder must act). Do not propose "
        "configurations that work around them and do not re-declare them; "
        "propose in other directions until they are resolved.\n\n" + blocked
        + "\n\n## Lab notebook tail\n\n" + notebook
    )


def _kb_block(kb_index: str) -> str:
    return (
        "## kb cache index\n\n"
        "These topic_ids are already in lab/knowledge/kb.sqlite — calling "
        "`lit_review` with the same topic+intent returns the cached row "
        "instantly (no web search). Reuse these instead of re-querying.\n\n"
        + kb_index
    )


def _coder_log_tail(paths: LabPaths, n: int = 10) -> str:
    log = read_jsonl(paths.root / "coder_log.jsonl")[-n:]
    if not log:
        return "(no Coder attempts yet)"
    out = []
    for r in log:
        ok = "✓" if r.get("ok") else ("infeasible" if not r.get("feasible") else "✗")
        out.append(
            f"- {ok} {r.get('name','?')} | files={r.get('files_changed') or []} "
            f"| commit={r.get('commit_sha') or '-'} | err={r.get('error') or ''}"
        )
    return "\n".join(out)


# -----------------------------------------------------------------------------
# Dialogue turns
# -----------------------------------------------------------------------------


def _supervisor_brief(
    *,
    client: anthropic.Anthropic,
    paths: LabPaths,
    budget: BudgetTracker,
    sup_model: str,
    static_block: str,
    kb_index_block: str,
    dynamic_block: str,
    saturation: dict[str, Any],
    coder_log: str,
    mode: str = "refine",
    student_id: str = "primary",
) -> dict[str, Any]:
    """Turn 1: Supervisor reads state and produces an agenda JSON."""
    system_text = load_prompt("supervisor", extras={"hypothesis_body": ""})
    system = [{
        "type": "text", "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]

    from efferents.agents import coder as _coder_mod
    blockers = _coder_mod.recent_blockers(paths, student_id=student_id)
    blockers_block = (
        "\n\n## Coder blockers (do NOT re-propose these architectural names)\n\n"
        + (
            "\n".join(f"- `{b['name']}` ({b['date']}): {b['reason']}" for b in blockers)
            if blockers else "(none in the last 30 days)"
        )
    )

    try:
        recently_closed = _campaign_recently_closed_list(paths.runs_db, _lab.LAB_ID, days=7)
    except sqlite3.OperationalError:
        # campaigns table absent (pre-migration DB); skip the block.
        recently_closed = []
    closed_block = (
        "\n\n## Recently closed campaigns (last 7 days)\n\n"
        "Do NOT propose for these `campaign_id`s — they are resolved.\n\n"
        + (
            "\n".join(
                f"- `{c['id']}` (closed {c.get('closed_at','')[:19]}) — "
                f"reason: {c.get('close_reason') or '?'} — "
                f"question: {c.get('question','')[:100]}"
                for c in recently_closed
            )
            if recently_closed else "(none in the last 7 days)"
        )
    )

    # Recent findings from sibling autolabs, pulled by federation.consume.
    # See agents/federation.py. The Student should treat these as background
    # context AND as candidates to cite via `lit_context`. When the Student's
    # hypothesis would be FALSE if an external claim is wrong, it must mark
    # that claim as foundational (foundational_external field on the
    # proposal), so the lab can reproduce it before building on it.
    from efferents.agents import federation as _federation
    paper_dir = paths.root.parent / "paper"
    external_recent = _federation.recent_external_entries(
        paper_dir / "external_journal.md",
        days=14, max_n=10,
    )
    external_block = (
        "\n\n## Recent findings from sibling autolabs (last 14 days)\n\n"
        + (
            "\n".join(
                f"- `{e.get('lab_id','?')}/{e['campaign_id']}` — "
                f"{e.get('headline','(no headline)')[:120]}"
                for e in external_recent
            )
            if external_recent
            else "(none — `python -m agents.federation consume --from <path>` pulls a sibling journal)"
        )
    )

    # External claims we've cited as FOUNDATIONAL but haven't yet reproduced.
    # Discipline: a paper our hypothesis depends on must be reproduced before
    # we build on it. Reproductions live in paper/reproductions.md; per-
    # proposal dependencies live in lab/foundational_deps.jsonl.
    pending_deps = _federation.pending_foundational_deps(
        paths.root / "foundational_deps.jsonl",
        paper_dir,
        max_age_days=30,
    )
    foundational_block = (
        "\n\n## Foundational external claims pending reproduction\n\n"
        "When we cite a sibling lab's finding as a *premise* of our work, we "
        "must reproduce it BEFORE building further. Pending verifications:\n\n"
        + (
            "\n".join(
                f"- `{d['lab_id']}/{d['campaign_id']}` (status: {d['status'] or 'never attempted'}) — "
                f"cited because: {d['why'][:80]}"
                for d in pending_deps
            )
            if pending_deps else "(no unverified foundational deps — good)"
        )
    )

    extra = (
        f"<<MODE: {mode}>>\n\n"
        + "## Saturation report (deterministic)\n\n"
        + json.dumps(saturation, indent=2)
        + "\n\n## Recent Coder attempts\n\n" + coder_log
        + blockers_block
        + closed_block
        + external_block
        + foundational_block
        + "\n\n---\n\n"
        + "Emit your **brief-turn JSON** now. First character `{`. "
        "Address the saturation report. Add infeasible names to "
        "`forbidden_axes`. Do not target closed campaigns. If foundational "
        "deps are pending, surface that — the Student should propose a "
        "reproduction run before piling on further claims. ≤400 output tokens."
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": kb_index_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": extra},
        ],
    }]

    def _call(retry_msgs):
        final_msgs = messages + (retry_msgs or [])
        return _simple_call(
            client=client, system=system, messages=final_msgs,
            model=sup_model, agent="supervisor", budget=budget,
            max_tokens=2048, notes="brief" + (" (retry)" if retry_msgs else ""),
        )

    parsed, _status = parse_json_with_one_retry(
        call_fn=_call,
        must_contain='"expected_proposal_shape"',
        fallback={
            "open_questions": [],
            "forbidden_axes": [],
            "encouraged_paradigms": [],
            "expected_proposal_shape": "architectural",
            "post_mortem": "[supervisor brief failed to parse after one retry]",
            "_parse_error": True,
        },
    )
    return parsed


def _student_propose(
    *,
    client: anthropic.Anthropic,
    paths: LabPaths,
    budget: BudgetTracker,
    student_model: str,
    static_block: str,
    kb_index_block: str,
    dynamic_block: str,
    brief: dict[str, Any],
    max_tokens: int,
    mode: str = "refine",
    student_id: str = "primary",
) -> tuple[str, dict[str, Any], list[str]]:
    """Turn 2: Student takes the brief and produces proposals.

    `student_id` selects which prompt template to use (per the lab config's
    students[student_id]["prompt_overrides"]["student"], if
    present; otherwise the default agents/prompts/student.md).

    Returns (raw_text, parsed_dict_or_empty, consulted_topic_ids).
    """
    # Per-student prompt override → explicit override_path; else lab/framework default.
    override_path = None
    try:
        student = _lab.get_student(student_id)
    except KeyError:
        student = None
    if student:
        ov = (student.get("prompt_overrides") or {}).get("student")
        if ov:
            cand = Path(ov) if Path(ov).is_absolute() else Path(__file__).parent.parent / ov
            if cand.exists():
                override_path = cand
    system_text = load_prompt(
        "student", extras={"hypothesis_body": ""}, override_path=override_path
    )
    # If the student dict has a `focus`, prepend it so this student's prompt
    # carries its area-of-interest cleanly without needing a full prompt clone.
    if student and student.get("focus") and override_path is None:
        system_text = (
            f"## Your focus (student: {student_id})\n\n{student['focus']}\n\n"
            "Stay within this focus when picking proposals; tell the Supervisor "
            "explicitly when a finding from outside the focus should change the "
            "direction.\n\n---\n\n"
        ) + system_text
    system = [{
        "type": "text", "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]

    brief_block = (
        "## Supervisor brief (binding for this turn)\n\n```json\n"
        + json.dumps(brief, indent=2)
        + "\n```"
    )

    # Anthropic caps cache_control at 4 blocks. System prompt (1) + 3 cached
    # user blocks fills the budget; the brief and trailing instruction stay
    # uncached (the brief changes every iteration anyway).
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": kb_index_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": brief_block},
            {
                "type": "text",
                "text": (
                    f"<<MODE: {mode}>>\n\n"
                    "Produce 1–3 proposals consistent with the Supervisor brief. "
                    "Call `lit_review` first if entering a new conceptual area. "
                    "Output strict JSON. First character `{`."
                ),
            },
        ],
    }]

    text, consulted = librarian.run_with_lit_review_tool(
        client=client,
        system_prompt=system,
        messages=messages,
        model=student_model,
        paths=paths,
        budget=budget,
        agent="student",
        max_tokens=max_tokens,
        max_lit_calls=MAX_LIT_CALLS_PER_PASS,
    )
    # `run_with_lit_review_tool` mutated `messages` to hold the full tool-use
    # round-trip history (but NOT the final assistant text). On parse failure,
    # we append that final text + a retry hint and ask once more without
    # re-enabling the tool — the second call is purely a "fix the JSON" pass.
    try:
        parsed = parse_json_loose(text, must_contain='"proposals"')
        return text, parsed, consulted
    except json.JSONDecodeError as e:
        retry_msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": text}]},
            {"role": "user", "content": [
                {"type": "text", "text": retry_hint(str(e), '"proposals"')}
            ]},
        ]
        text2 = _simple_call(
            client=client, system=system, messages=messages + retry_msgs,
            model=student_model, agent="student", budget=budget,
            max_tokens=max_tokens, notes="propose (retry)",
        )
        try:
            parsed = parse_json_loose(text2, must_contain='"proposals"')
            return text2, parsed, consulted
        except json.JSONDecodeError:
            parsed = {"proposals": [], "architectural_proposals": [], "_parse_error": True}
            return text2, parsed, consulted


def _supervisor_review(
    *,
    client: anthropic.Anthropic,
    paths: LabPaths,
    budget: BudgetTracker,
    sup_model: str,
    static_block: str,
    student_parsed: dict[str, Any],
    saturation: dict[str, Any],
) -> dict[str, Any]:
    """Turn 3: Supervisor critiques the Student output."""
    system_text = load_prompt("supervisor", extras={"hypothesis_body": ""})
    system = [{
        "type": "text", "text": system_text,
        "cache_control": {"type": "ephemeral"},
    }]

    review_input = (
        "## Saturation report (reminder)\n\n"
        + json.dumps(saturation, indent=2)
        + "\n\n## Student output\n\n```json\n"
        + json.dumps(
            {
                "proposals": student_parsed.get("proposals", []),
                "architectural_proposals": student_parsed.get("architectural_proposals", []),
            },
            indent=2,
        )
        + "\n```\n\n---\n\n"
        + "Emit your **review-turn JSON** now. First character `{`. "
        "≤300 output tokens. Default to `approve` unless the Student violated "
        "a hard rule from the brief."
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": review_input},
        ],
    }]

    def _call(retry_msgs):
        final_msgs = messages + (retry_msgs or [])
        return _simple_call(
            client=client, system=system, messages=final_msgs,
            model=sup_model, agent="supervisor", budget=budget,
            max_tokens=800, notes="review" + (" (retry)" if retry_msgs else ""),
        )

    parsed, _status = parse_json_with_one_retry(
        call_fn=_call,
        must_contain='"verdict"',
        fallback={
            "verdict": "approve",
            "redlines": ["[review failed to parse after one retry]"],
            "revised_proposals": None,
            "_parse_error": True,
        },
    )
    return parsed


def _validate_architectural(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop architectural proposals missing required fields. The Coder's
    parser also checks Principle/What/Why/Effort/Payoff in the markdown
    persistence; a malformed proposal there silently drops fields."""
    required = ("name", "principle", "what", "why")
    return [p for p in items if all(p.get(k) for k in required)]


def _finalize(
    *,
    student_parsed: dict[str, Any],
    review: dict[str, Any],
    consulted: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Apply review verdict → final {proposals, architectural_proposals}."""
    revised = review.get("revised_proposals")
    verdict = (review.get("verdict") or "approve").lower()

    if verdict == "reject":
        return {"proposals": [], "architectural_proposals": []}

    if verdict == "revise" and isinstance(revised, dict):
        proposals = revised.get("proposals", []) or []
        arch = revised.get("architectural_proposals", []) or []
    else:
        proposals = student_parsed.get("proposals", []) or []
        arch = student_parsed.get("architectural_proposals", []) or []

    if not isinstance(proposals, list):
        proposals = []
    if not isinstance(arch, list):
        arch = []

    proposals = _ensure_lit_context(proposals, consulted)
    arch = _validate_architectural(_ensure_lit_context(arch, consulted))
    return {"proposals": proposals, "architectural_proposals": arch}


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------


def propose(
    *,
    paths: LabPaths,
    context_dir: str | Path,
    budget: BudgetTracker,
    client: anthropic.Anthropic,
    model: str | None = None,        # student override (legacy callers)
    max_tokens: int = 4096,
    mode: str = "refine",
    student_id: str | None = None,
) -> dict[str, Any]:
    """One Researcher iteration. Internally runs the Student↔Supervisor dialogue.

    Phase B: `student_id` selects which student's slice of state.json drives
    saturation streak / consecutive-empty / last_researcher_ts. Defaults to
    LabConfig.default_student_id; with one student configured, this is
    transparent (the primary student reads/writes the legacy flat keys).

    Each proposal is tagged with `student_id` so the Executor can stamp
    runs.student_id when it executes.

    Return shape mirrors the legacy contract so the orchestrator is unchanged:
        {"proposals": [...], "architectural_proposals": [...],
         "consulted_topic_ids": [...], "raw": str, ...}
    """
    if student_id is None:
        student_id = _lab.DEFAULT_STUDENT_ID

    state = load_state(paths.state)
    sstate = StudentStateView(state, student_id)
    streak = int(sstate.get("supervisor_opus_streak", 0))
    consecutive_empty = int(sstate.get("researcher_consecutive_empty", 0))

    # Build shared context once.
    ctx = read_context(context_dir)
    rows = recent_runs(paths.runs_db, n=30)
    librarian.init_kb(paths.kb_db)
    kb_index = librarian.index_for_prompt(paths.kb_db, n=40)
    static_block = _shared_static_block(
        vision=ctx.get("vision.md", ""),
        decisions=ctx.get("decisions.md", ""),
        charter=ctx.get("popper.md", ""),
    )
    base_config = load_proposal_base_config()
    dynamic_block = _shared_dynamic_block(
        research_log=ctx.get("research_log.md", ""),
        runs_table=_format_recent_runs(rows, paths.runs_db),
        notebook=notebook_tail(paths.notebook, max_chars=6000),
        tried=_format_tried_configs(_tried_configs(rows, base_config)),
        blocked=_format_open_blocks(open_blocks(paths.root)),
    )
    kb_index_block = _kb_block(kb_index)
    from efferents.agents import conference
    dynamic_block += conference.prompt_context(paths.root, _lab.get_config())

    saturation = _saturation_report(paths)
    coder_log = _coder_log_tail(paths, n=10)
    student_model = model or model_for("student") or "claude-sonnet-4-6"
    sup_model = model_for_supervisor(streak)

    spend_start = budget.spend_today()

    # --- Circuit breaker: too many empty rounds → bypass Supervisor ---
    bypass_supervisor = consecutive_empty >= EMPTY_FALLBACK_THRESHOLD
    if bypass_supervisor:
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — Researcher fallback: Supervisor bypassed after "
            f"{consecutive_empty} consecutive empty rounds.\n",
        )

    # --- Turn 1: Supervisor brief ---
    if bypass_supervisor:
        brief = {
            "open_questions": [],
            "forbidden_axes": [],
            "encouraged_paradigms": [],
            "expected_proposal_shape": "architectural",
            "post_mortem": "[bypass: supervisor disabled this iteration]",
        }
    else:
        brief = _supervisor_brief(
            client=client, paths=paths, budget=budget, sup_model=sup_model,
            static_block=static_block, kb_index_block=kb_index_block,
            dynamic_block=dynamic_block, saturation=saturation,
            coder_log=coder_log, mode=mode, student_id=student_id,
        )

    # --- Turn 2: Student propose ---
    student_text, student_parsed, consulted = _student_propose(
        client=client, paths=paths, budget=budget, student_model=student_model,
        static_block=static_block, kb_index_block=kb_index_block,
        dynamic_block=dynamic_block, brief=brief, max_tokens=max_tokens,
        mode=mode, student_id=student_id,
    )

    # --- Cost guardrail before Turn 3 ---
    conference.record_responses(
        paths.root, _lab.get_config(), student_parsed.get("conference_responses"), student_id,
    )
    spend_after_student = budget.spend_today()
    over_cap = (spend_after_student - spend_start) >= PER_CALL_COST_CAP_USD

    # --- Turn 3: Supervisor review ---
    if bypass_supervisor or over_cap:
        review = {
            "verdict": "approve",
            "redlines": [
                "[skipped: " + ("cost cap" if over_cap else "supervisor bypass") + "]"
            ],
            "revised_proposals": None,
        }
    else:
        review = _supervisor_review(
            client=client, paths=paths, budget=budget, sup_model=sup_model,
            static_block=static_block, student_parsed=student_parsed,
            saturation=saturation,
        )

    final = _finalize(
        student_parsed=student_parsed, review=review, consulted=consulted,
    )

    # --- Phase A: handle new_campaign + ≤2 cap, gate via popper. ---
    # new_campaign is sourced from the Student turn only — the Supervisor's
    # revised_proposals can rewrite proposals but cannot redact a campaign
    # declaration. The Student is the only role that opens campaigns.
    new_campaign = student_parsed.get("new_campaign")
    try:
        # Per-student cap (Phase B): each student gets MAX_OPEN_CAMPAIGNS_PER_STUDENT,
        # so Student A's open campaigns don't block Student B from opening their own.
        opens = _campaign_open_list_for_student(
            paths.runs_db, _lab.LAB_ID, student_id,
        )
    except sqlite3.OperationalError:
        # campaigns table absent (pre-migration DB); treat as zero open campaigns.
        opens = []
    new_campaign_id: str | None = None
    if new_campaign and len(opens) < _lab.MAX_OPEN_CAMPAIGNS_PER_STUDENT:
        slug = _slugify(new_campaign.get("question", "campaign"))[:48] + "-" + uuid.uuid4().hex[:6]
        # Per-student popper-corpus subdir. Primary keeps the legacy unsuffixed
        # path (popper-corpus/<slug>) so existing hypotheses don't move; siblings
        # land under popper-corpus/<student_id>/<slug>.
        if student_id == _lab.DEFAULT_STUDENT_ID:
            corpus_root = paths.root.parent / "popper-corpus"
        else:
            corpus_root = paths.root.parent / "popper-corpus" / student_id
        gate_result = _popper_gate.run_gate(
            draft_claim=new_campaign.get("draft_hypothesis", ""),
            slug=slug,
            corpus_root=corpus_root,
            client=client,
            budget=budget,
            charter_dir=Path(context_dir),
            prompted_by=f"student:{student_id}",
        )
        if gate_result.ok:
            campaign_id = "c-" + uuid.uuid4().hex[:10]
            _hm, _hd = _campaign_metric_from_proposal(new_campaign)
            if new_campaign.get("headline_metric") and _hm is None:
                notebook_append(
                    paths.notebook,
                    f"## {now_iso()} — proposed headline_metric "
                    f"{new_campaign.get('headline_metric')!r}/{new_campaign.get('direction')!r} "
                    f"was invalid; campaign falls back to the lab default.\n",
                )
            _campaign_insert(
                paths.runs_db,
                id=campaign_id,
                lab_id=_lab.LAB_ID,
                question=new_campaign.get("question", ""),
                hypothesis_path=str(gate_result.path.relative_to(paths.root.parent)),
                hypothesis_hash=gate_result.hash,
                student_id=student_id,
                headline_metric=_hm,
                headline_direction=_hd,
            )
            new_campaign_id = campaign_id
            notebook_append(
                paths.notebook,
                f"## {now_iso()} — opened campaign {campaign_id} "
                f"({new_campaign.get('question')!r}) hash={gate_result.hash[:14]}...\n",
            )
        else:
            notebook_append(
                paths.notebook,
                f"## {now_iso()} — popper-gate REJECTED draft hypothesis: "
                f"{gate_result.reason}\n",
            )

    # Route proposals: only when the student declared a new_campaign (or
    # proposals carry explicit campaign_ids). If the student emitted neither,
    # proposals are passed through as-is (backward-compatible path).
    if new_campaign is not None:
        final_proposals: list[dict] = []
        for p in final.get("proposals", []):
            if p.get("campaign_id") is None:
                if new_campaign_id is not None:
                    p["campaign_id"] = new_campaign_id
                else:
                    notebook_append(
                        paths.notebook,
                        f"## {now_iso()} — dropped untagged proposal {p.get('name')!r}\n",
                    )
                    continue
            final_proposals.append(p)
        final["proposals"] = final_proposals

    # --- Tag proposals with the mode + student_id ---
    for p in final["proposals"]:
        p["mode"] = mode
        p.setdefault("student_id", student_id)
    for p in final["architectural_proposals"]:
        p.setdefault("student_id", student_id)

    # --- Blocked on infrastructure: the Student says the executor itself
    # needs a code change before its line of work can be tested. Sourced from
    # the Student turn (like new_campaign — the Supervisor cannot redact it).
    # Recorded for the owner (lab/blocked.jsonl, notebook, state.json) and
    # surfaced to every student on the next turn.
    blocked = _record_blocked_on_infrastructure(
        paths, student_parsed.get("blocked_on_infrastructure"), student_id=student_id,
    )

    # --- Reject configurations that already ran / are queued (see
    # _reject_duplicate_proposals). Runs after tagging because campaign, mode
    # and student are rendered into the config the Executor hashes. A second
    # Student call to replace the rejected proposals would cost another full
    # turn, so rejected work is simply skipped this iteration.
    n_before = len(final["proposals"])
    final["proposals"] = _reject_duplicate_proposals(
        paths, final["proposals"], base_config=base_config,
    )
    n_duplicates_skipped = n_before - len(final["proposals"])

    # --- Persist architectural proposals (Coder reads this file) ---
    if final["architectural_proposals"]:
        from efferents.agents.coder import proposed_changes_path as _proposed_changes_path
        _persist_architectural(
            _proposed_changes_path(paths, student_id),
            final["architectural_proposals"],
        )

    # --- Persist foundational_external dependencies (Phase D reproduction
    # discipline). Each proposal that declares a foundational dependency on
    # an external paper writes one line to lab/foundational_deps.jsonl so
    # the next Supervisor brief can surface what's still unverified. The
    # reproduction itself is the Student's own follow-up — see student.md.
    deps_log = paths.root / "foundational_deps.jsonl"
    deps_ts = now_iso()
    for p in final["proposals"] + final["architectural_proposals"]:
        deps = p.get("foundational_external") or []
        if not isinstance(deps, list):
            continue
        for dep in deps:
            if not isinstance(dep, dict):
                continue
            lab_id = dep.get("lab_id")
            cid = dep.get("campaign_id")
            if not lab_id or not cid:
                continue
            append_jsonl(deps_log, {
                "ts": deps_ts,
                "lab_id": lab_id,
                "campaign_id": cid,
                "why": dep.get("why", ""),
                "proposal_name": p.get("name", "?"),
                "student_id": student_id,
            })

    # --- Update state cursors (per-student) ---
    is_empty = not (final["proposals"] or final["architectural_proposals"])
    sstate["researcher_consecutive_empty"] = (consecutive_empty + 1) if is_empty else 0
    sstate["supervisor_opus_streak"] = (
        (streak + 1) if saturation["score"] >= 1 else 0
    )
    sstate["last_researcher_ts"] = now_iso()
    save_state(paths.state, state)

    # --- Observability ---
    append_jsonl(paths.root / "researcher_dialogue.jsonl", {
        "ts": now_iso(),
        "saturation": saturation,
        "supervisor_opus_streak_before": streak,
        "supervisor_model": sup_model,
        "student_model": student_model,
        "bypass_supervisor": bypass_supervisor,
        "over_cost_cap": over_cap,
        "spend_delta_usd": round(spend_after_student - spend_start, 4),
        "brief": brief,
        "student_parse_error": bool(student_parsed.get("_parse_error")),
        "review_verdict": review.get("verdict"),
        "review_redlines": review.get("redlines"),
        "n_proposals": len(final["proposals"]),
        "n_architectural": len(final["architectural_proposals"]),
        "n_duplicates_skipped": n_duplicates_skipped,
        "blocked_on_infrastructure": blocked.get("id") if blocked else None,
    })

    return {
        "proposals": final["proposals"],
        "architectural_proposals": final["architectural_proposals"],
        "consulted_topic_ids": consulted,
        "raw": student_text,
        "supervisor_brief": brief,
        "supervisor_review": review,
        "saturation": saturation,
        "blocked_on_infrastructure": blocked,
    }
