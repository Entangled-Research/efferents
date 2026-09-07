"""Analyst agent: periodic digest writer. Reads recent runs + notebook + context,
writes a markdown digest. Notifies the user via macOS + ntfy.sh.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anthropic

from efferents import evidence as ev
from efferents import lab as _lab
from efferents import metrics_view as mv
from efferents.agents import notebook as nb
from efferents.agents.budget import BudgetExhausted, BudgetTracker, CallUsage, model_for
from efferents.agents.model_client import IMAGE_TOKEN_ESTIMATE, image_block
from efferents.agents.notify import notify_all
from efferents.agents.prompts.loader import load_prompt
from efferents.agents.state import LabPaths, load_state, notebook_append, notebook_tail, now_iso, read_context, recent_runs, save_state


def _flat_digest_epsilon() -> float:
    return _lab.get_config().metrics.flat_digest_epsilon


def group_runs_by_campaign(runs: list[dict]) -> dict[str | None, list[dict]]:
    out: dict[str | None, list[dict]] = {}
    for r in runs:
        key = r.get("campaign_id")
        out.setdefault(key, []).append(r)
    return out


def _load_campaign(db_path: Path, campaign_id: str) -> dict | None:
    """Load a single campaign row from runs.sqlite. Returns None if not found."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            row = conn.execute(
                "SELECT id, question, hypothesis_hash FROM campaigns WHERE id = ?",
                (campaign_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    finally:
        conn.close()
    return dict(row) if row else None


# Rendered subset of meta columns (companion to mv.META_COLUMNS); order matters for digest tables/narratives.
_META_RENDER_COLS = ("run_id", "started_at", "campaign_id", "researcher_mode", "duration_seconds")


def _digest_columns(db_path: Path) -> list[str]:
    """Lab-agnostic column order for digest rendering: a fixed meta subset, the
    configured headline column, the configured panel columns, then the remaining
    auto-discovered columns — de-duplicated, preserving first-seen order."""
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


def _render_cell(value: Any) -> str:
    """Render a run value: finite floats with %.4g, raw value as str, '—' if absent."""
    if value is None:
        return "—"
    fv = mv.finite(value)
    if fv is not None and isinstance(value, float):
        return f"{fv:.4g}"
    return str(value)


def _format_campaign_blocks(groups: dict[str | None, list[dict]], db_path: Path) -> str:
    """Format grouped runs as a markdown narrative of campaign blocks."""
    metric_keys = _digest_columns(db_path)
    lines: list[str] = []

    def _render_run(r: dict) -> str:
        parts = [f"- {r.get('run_id', '?')}:"]
        for k in metric_keys:
            if k == "run_id":
                continue
            v = r.get(k)
            if v is None:
                continue
            parts.append(f"{k}={_render_cell(v)}")
        return " ".join(parts)

    for campaign_id, runs in groups.items():
        if campaign_id is None:
            continue
        campaign = _load_campaign(db_path, campaign_id)
        question = campaign["question"] if campaign else "(unknown question)"
        h_hash = campaign["hypothesis_hash"] if campaign else ""
        lines.append(f"### Campaign {campaign_id} — {question}")
        if h_hash:
            lines.append(f"Hypothesis hash: {h_hash}")
        lines.append("Runs in this campaign:")
        for r in runs:
            lines.append(_render_run(r))
        lines.append("")

    # Uncampaigned runs
    if None in groups:
        lines.append("### Uncampaigned runs")
        for r in groups[None]:
            lines.append(_render_run(r))
        lines.append("")

    return "\n".join(lines).strip()


def _format_recent_runs(rows: list[dict[str, Any]], db_path: Path) -> str:
    """Compact table: the meta subset, the headline + panel columns and the
    constraint verdict — the same set the notebook entry shows. Remaining
    numeric columns are counted, not rendered."""
    if not rows:
        return "(no runs yet)"
    cols = [*_META_RENDER_COLS, *nb.compact_columns()]
    out = ["| " + " | ".join(cols) + " | constraints |",
           "|" + "|".join("---" for _ in cols) + "|---|"]
    hidden: set[str] = set()
    for r in rows:
        cells = [_render_cell(r.get(c)) for c in cols]
        out.append("| " + " | ".join(cells) + f" | {nb.constraint_status(r)} |")
        hidden.update(
            k for k, v in r.items()
            if k not in cols and k not in mv.META_COLUMNS and mv.finite(v) is not None
        )
    if hidden:
        out.append(f"\n_+{len(hidden)} more metric columns in the ledger (`runs.sqlite`)._")
    return "\n".join(out)


# --- blind qualitative review of image artifacts --------------------------------

REVIEW_IMAGES_ENV = "EFFERENTS_REVIEW_IMAGES"
REVIEW_IMAGES_DEFAULT = 6
REVIEW_MAX_IMAGE_BYTES = 4 * 1024 * 1024
# Artifact kinds reviewed first; other image artifacts fill remaining slots.
REVIEW_PREFERRED_KINDS = ("comparison_grid", "sample_grid")
_IMAGE_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def review_image_limit() -> int:
    raw = os.environ.get(REVIEW_IMAGES_ENV, "").strip()
    if not raw:
        return REVIEW_IMAGES_DEFAULT
    try:
        return max(0, int(raw))
    except ValueError:
        return REVIEW_IMAGES_DEFAULT


def _blind_label(i: int) -> str:
    """0 -> 'A', 25 -> 'Z', 26 -> 'AA', ..."""
    label = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def _load_json_list(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        value = json.loads(raw)
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _observation_label(observation: dict) -> str:
    name = observation.get("name")
    if name:
        return str(name)
    dims = observation.get("dimensions") or {}
    return ", ".join(f"{k}={v}" for k, v in dims.items()) if dims else "—"


def collect_review_images(rows: list[dict[str, Any]], *, limit: int | None = None) -> list[dict[str, Any]]:
    """Up to ``limit`` image artifacts from ``rows`` (latest first), preferring
    ``REVIEW_PREFERRED_KINDS`` in order. Missing, non-image and oversized
    files are skipped. Each record: run_id, observation, kind, path, media_type."""
    limit = review_image_limit() if limit is None else limit
    if limit <= 0:
        return []
    candidates: list[tuple[int, int, dict[str, Any]]] = []
    seen: set[str] = set()
    for recency, row in enumerate(rows):
        run_id = str(row.get("run_id", "?"))
        sources: list[tuple[str, list]] = [("—", _load_json_list(row.get("artifacts_json")))]
        for obs in _load_json_list(row.get("observations_json")):
            if isinstance(obs, dict):
                sources.append((_observation_label(obs), list(obs.get("artifacts") or [])))
        for obs_label, artifacts in sources:
            for artifact in artifacts:
                if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
                    continue
                if artifact.get("missing") or artifact.get("skipped_large"):
                    continue
                path = Path(artifact["path"])
                media_type = _IMAGE_MEDIA_TYPES.get(path.suffix.lower())
                if media_type is None or str(path) in seen:
                    continue
                try:
                    if not path.is_file() or path.stat().st_size > REVIEW_MAX_IMAGE_BYTES:
                        continue
                except OSError:
                    continue
                seen.add(str(path))
                kind = str(artifact.get("kind") or "artifact")
                priority = (
                    REVIEW_PREFERRED_KINDS.index(kind)
                    if kind in REVIEW_PREFERRED_KINDS else len(REVIEW_PREFERRED_KINDS)
                )
                candidates.append((priority, recency, {
                    "run_id": run_id, "observation": obs_label, "kind": kind,
                    "path": str(path), "media_type": media_type,
                }))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return [record for _, _, record in candidates[:limit]]


def _review_content(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """User-message blocks: blinded captions + image blocks, nothing about runs."""
    labels = [_blind_label(i) for i in range(len(images))]
    blocks: list[dict[str, Any]] = [{
        "type": "text",
        "text": f"{len(images)} images follow, labelled " + ", ".join(labels) + ".",
    }]
    for label, record in zip(labels, images):
        blocks.append({"type": "text", "text": f"Image {label}:"})
        blocks.append(image_block(Path(record["path"]).read_bytes(), record["media_type"]))
    blocks.append({"type": "text", "text": "Rank the images per the rubric and return the JSON object only."})
    return blocks


def _parse_review(text: str) -> tuple[list[str], dict[str, str]] | None:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{"):] if "{" in body else body
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("ranking"), list):
        return None
    ranking = [str(x).strip().upper().removeprefix("IMAGE ").strip() for x in data["ranking"]]
    notes_raw = data.get("notes") if isinstance(data.get("notes"), dict) else {}
    notes = {str(k).strip().upper().removeprefix("IMAGE ").strip(): str(v) for k, v in notes_raw.items()}
    return ranking, notes


def _review_section(images: list[dict[str, Any]], ranking: list[str], notes: dict[str, str]) -> str:
    """Un-blind: label -> run/observation, in rank order, unranked labels last."""
    by_label = {_blind_label(i): rec for i, rec in enumerate(images)}
    order = [lb for lb in ranking if lb in by_label]
    order += [lb for lb in by_label if lb not in order]
    lines = [
        "## Qualitative review (blind)", "",
        f"{len(images)} image artifact(s) ranked by a reviewer shown only letter labels "
        "(no run ids, variants or metrics). Un-blinded here.", "",
        "| rank | image | run | observation | kind | note |", "|---|---|---|---|---|---|",
    ]
    for rank, label in enumerate(order, 1):
        rec = by_label[label]
        note = notes.get(label, "—").replace("|", "\\|")
        rank_cell = str(rank) if label in ranking else "unranked"
        lines.append(
            f"| {rank_cell} | {label} | {rec['run_id']} | {rec['observation']} | {rec['kind']} | {note} |"
        )
    return "\n".join(lines)


def blind_image_review(
    *,
    rows: list[dict[str, Any]],
    budget: BudgetTracker,
    client: Any,
    model: str,
    max_tokens: int = 1024,
    limit: int | None = None,
) -> str | None:
    """One extra model call per digest at most: rank recent image artifacts
    blind and return the un-blinded markdown section, or None when there is
    nothing to review (no images, disabled, or budget exhausted)."""
    images = collect_review_images(rows, limit=limit)
    if not images:
        return None
    content = _review_content(images)
    system_prompt = load_prompt("analyst_review")
    try:
        budget.reserve(
            model, max_tokens, len(images) * IMAGE_TOKEN_ESTIMATE + len(system_prompt) // 3
        )
    except BudgetExhausted:
        return None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": content}],
        )
    except BudgetExhausted:
        return None
    except Exception as exc:  # noqa: BLE001 - an optional review must not sink the digest
        return (
            "## Qualitative review (blind)\n\n"
            f"Skipped: review call failed ({type(exc).__name__}: {exc})."
        )
    budget.record(agent="analyst", model=model, usage=CallUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    ))
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    parsed = _parse_review(text)
    if parsed is None:
        return _review_section(images, [], {}) + "\n\n(Reviewer returned no parseable ranking.)"
    ranking, notes = parsed
    return _review_section(images, ranking, notes)


def _fmt_stat(x: float | None) -> str:
    return "—" if x is None else f"{x:.4g}"


def _evidence_section(rows: list[dict[str, Any]], cfg=None) -> str:
    """Deterministic (no-LLM) evidence block: per-bucket medians of the headline
    and panel columns, seed-paired arm deltas when a comparison axis is
    observed, the falsifier table, and the verdict line."""
    cfg = cfg or _lab.get_config()
    cols: list[str] = []
    for c in (cfg.metrics.headline.column, *(p.column for p in cfg.metrics.panels)):
        if c not in cols:
            cols.append(c)
    summary = ev.bucket_summary(rows, cfg)
    lines = ["## Evidence (computed)", ""]
    if not summary:
        lines.append("(no runs yet)")
    else:
        axes = ", ".join(cfg.metrics.bucket_axes) or "all runs"
        lines += [f"Buckets by {axes}; cells are median (n).", "",
                  "| bucket | runs | " + " | ".join(cols) + " |",
                  "|---|---|" + "|".join("---" for _ in cols) + "|"]
        for label, entry in summary.items():
            cells = []
            for c in cols:
                st = entry["columns"].get(c)
                cells.append(f"{_fmt_stat(st['median'])} (n={st['n']})" if st and st["n"] else "—")
            lines.append(f"| {label} | {entry['n']} | " + " | ".join(cells) + " |")
        paired = [(label, m, st) for label, e in summary.items() for m, st in e["paired"].items()]
        if paired:
            first, second = paired[0][2]["arms"]
            lines += ["", f"Seed-paired deltas ({first} − {second}); 95% bootstrap CI of the median.", "",
                      "| bucket | column | n | median | 95% CI |", "|---|---|---|---|---|"]
            for label, m, st in paired:
                ci = st["ci95"]
                ci_s = f"[{_fmt_stat(ci[0])}, {_fmt_stat(ci[1])}]" if ci else "—"
                lines.append(f"| {label} | {m} | {st['n']} | {_fmt_stat(st['median'])} | {ci_s} |")
    results = ev.evaluate_falsifiers(rows, cfg)
    lines += ["", "### Falsifiers", ""]
    if results:
        lines += ["| id | status | rule | detail |", "|---|---|---|---|"]
        for r in results:
            lines.append(f"| {r['id']} | {r['status']} | {r['description']} | {r['detail']} |")
    else:
        lines.append("No falsifiers declared in lab.yaml (`falsifiers:`).")
    lines += ["", f"**Verdict:** {ev.verdict(results)}"]
    return "\n".join(lines)


def _budget_snapshot(budget: BudgetTracker) -> str:
    today = budget.spend_today()
    total = budget.spend_total()
    cap = budget.daily_cap
    cache = budget.cache_stats(50)
    hit = cache["cache_read_share"] * 100
    return (
        f"Today: ${today:.2f} of ${cap:.2f} cap. "
        f"Total: ${total:.2f}. "
        f"Cache read share (last 50 calls): {hit:.0f}%."
    )


def _build_messages(
    *,
    vision: str,
    decisions: str,
    research_log: str,
    recent_runs_table: str,
    notebook: str,
    budget_snapshot: str,
    campaign_blocks: str = "",
    evidence_section: str = "",
    qualitative_review: str = "",
) -> list[dict[str, Any]]:
    static_block = "## Vision\n\n" + vision + "\n\n## Decisions\n\n" + decisions
    campaign_section = ("\n\n## Runs grouped by campaign\n\n" + campaign_blocks) if campaign_blocks else ""
    evidence_block = (
        "\n\n## Evidence summary (computed, deterministic)\n\n" + evidence_section
        + "\n\nGround the digest in the falsifier statuses and verdict above; "
        "the headline column alone does not settle the hypothesis."
    ) if evidence_section else ""
    review_block = ("\n\n" + qualitative_review) if qualitative_review else ""
    dynamic_block = (
        "## Research log\n\n" + research_log
        + evidence_block
        + campaign_section
        + "\n\n## Recent runs\n\n" + recent_runs_table
        + review_block
        + "\n\n## Lab notebook tail\n\n" + notebook
        + "\n\n## Budget snapshot\n\n" + budget_snapshot
    )
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic_block, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "Write the digest now per your system-prompt format."},
            ],
        }
    ]


def write_digest(
    *,
    paths: LabPaths,
    context_dir: str | Path,
    budget: BudgetTracker,
    client: anthropic.Anthropic,
    model: str | None = None,
    max_tokens: int = 2048,
    n_recent: int = 50,
    notify: bool = True,
) -> dict[str, Any]:
    ctx = read_context(context_dir)
    rows = recent_runs(paths.runs_db, n=n_recent)
    system_prompt = load_prompt("analyst")

    chosen = model or model_for("analyst")
    if chosen is None:
        raise RuntimeError("No model configured for Analyst")

    groups = group_runs_by_campaign(rows)
    campaign_blocks = _format_campaign_blocks(groups, paths.runs_db)
    evidence_section = _evidence_section(rows)
    # Blind review runs first (at most one extra call) so the narrative can cite it.
    review_section = blind_image_review(rows=rows, budget=budget, client=client, model=chosen) or ""

    messages = _build_messages(
        vision=ctx.get("vision.md", ""),
        decisions=ctx.get("decisions.md", ""),
        research_log=ctx.get("research_log.md", ""),
        recent_runs_table=_format_recent_runs(rows, paths.runs_db),
        notebook=notebook_tail(paths.notebook, max_chars=8000),
        budget_snapshot=_budget_snapshot(budget),
        campaign_blocks=campaign_blocks,
        evidence_section=evidence_section,
        qualitative_review=review_section,
    )

    resp = client.messages.create(
        model=chosen,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        messages=messages,
    )

    usage = CallUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    )
    budget.record(agent="analyst", model=chosen, usage=usage)

    narrative = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    text = evidence_section + (("\n\n" + review_section) if review_section else "") + "\n\n" + narrative
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")
    digest_path = paths.digests_dir / f"{ts}.md"
    digest_path.write_text(text)

    notebook_append(paths.notebook, f"## {ts} — digest\n\nWrote `{digest_path}`.\n")

    notified = {}
    if notify:
        # Push the TL;DR (first ~300 chars after the heading) to the phone.
        tl = _extract_tldr(text)
        notified = notify_all(
            title=f"{_lab.get_config().lab_id} digest",
            message=f"{tl}\n\nFull: {digest_path}",
        )

    best = mv.best_run(rows)
    best_headline = mv.headline_value(best) if best else None
    state = load_state(paths.state)
    state = update_flat_digest_counter(state, current_best_headline=best_headline)
    save_state(paths.state, state)

    # Best-effort progress dashboard refresh. Never let a rendering bug kill a digest.
    try:
        from efferents.agents.progress import write_progress
        write_progress(paths, context_dir=context_dir)
    except Exception as exc:
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — progress.html refresh FAILED: {type(exc).__name__}: {exc}\n",
        )

    return {"path": str(digest_path), "tokens": (usage.input_tokens, usage.output_tokens), "notify": notified}


def update_flat_digest_counter(
    state: dict, *, current_best_headline: float | None, epsilon: float | None = None
) -> dict:
    """Return a new state dict with `digests_without_improvement` and
    `last_digest_best_headline` updated based on this digest's best headline value.

    Direction-aware: improvement is decided by ``mv.improved`` using the active
    lab's headline direction ('min' -> a decrease beyond epsilon improves;
    'max' -> an increase beyond epsilon improves). An improvement resets the
    counter; otherwise it increments.

    epsilon: absolute improvement threshold. Defaults to
    ``_flat_digest_epsilon()`` (reads from the active LabConfig) when not
    supplied explicitly.

    Reads the legacy ``last_digest_best_w1`` key as a fallback for the previous
    value so pre-existing state.json files are preserved across the rename.
    """
    if epsilon is None:
        epsilon = _flat_digest_epsilon()
    direction = _lab.get_config().metrics.headline.direction
    prev = state.get("last_digest_best_headline", state.get("last_digest_best_w1"))
    out = dict(state)
    if current_best_headline is None:
        out.setdefault("digests_without_improvement", out.get("digests_without_improvement", 0))
        return out
    if mv.improved(prev, current_best_headline, direction=direction, epsilon=epsilon):
        out["digests_without_improvement"] = 0
    else:
        out["digests_without_improvement"] = int(out.get("digests_without_improvement", 0)) + 1
    out["last_digest_best_headline"] = current_best_headline
    return out


def _extract_tldr(digest: str) -> str:
    lines = digest.splitlines()
    out = []
    in_tldr = False
    for line in lines:
        if line.strip().lower().startswith("## tl;dr"):
            in_tldr = True
            continue
        if in_tldr and line.strip().startswith("## "):
            break
        if in_tldr:
            out.append(line)
    text = "\n".join(out).strip()
    return text[:300] if text else digest[:300]
