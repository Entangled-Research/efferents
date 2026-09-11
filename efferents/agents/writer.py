"""The Writer agent: composes agent-readable Phase A paper artifacts (Markdown)
for closed campaigns, runs them through the peer-review board, and commits the
accepted bundle.

The live entry point is `write_phase_a_paper`, driven by the orchestrator
(`efferents start` -> Orchestrator -> writer.write_phase_a_paper). It:
  1. mechanically gates on novelty + headline-metric gain (should_publish),
  2. composes the paper (Sonnet, via compose_paper) -> paper/<campaign_id>.md,
  3. (if peer review enabled) runs the 3-reviewer board + rebuttal + decision,
  4. writes side-cars, appends to journal.md / rejected.md, auto-commits on
     accept, and closes the campaign.

Reads campaign runs from `lab/runs.sqlite`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml as _yaml

from efferents import lab as _lab
from efferents.schemas.paper_frontmatter import (
    PaperFrontmatter,
    REQUIRED_SECTIONS_IN_ORDER,
    structural_check,
)


# ----------------------- novelty / gain gate -----------------------

@dataclass
class GateInputs:
    primary_metric_name: str
    baseline_value: float
    candidate_value: float
    novelty_claim: str
    existing_lab_claims: list[str] = field(default_factory=list)
    refutation_of_corroborated: str | None = None
    direction: str = "min"


def should_publish(
    inputs: GateInputs, *, gain_threshold: float = 0.05
) -> tuple[bool, str]:
    """Apply the novelty + significant-gain gate.

    Pass conditions (either is sufficient to satisfy the gain half):
      - candidate_value strictly better than baseline by at least
        gain_threshold (relative; direction is honored: "min" for
        lower-is-better metrics, "max" for higher-is-better metrics).
      - refutation_of_corroborated is set (refuting a previously-
        corroborated claim is publishable without gain).

    Novelty must always pass: non-empty stripped claim, not a duplicate
    of existing lab claims (case-insensitive exact match).
    """
    nov = inputs.novelty_claim.strip()
    if not nov:
        return (False, "novelty_claim is empty")
    if any(nov.lower() == c.strip().lower() for c in inputs.existing_lab_claims):
        return (False, f"novelty_claim duplicates existing lab claim: {nov!r}")

    if inputs.refutation_of_corroborated:
        return (True, "refutation path")

    if inputs.baseline_value <= 0:
        return (False, "non-positive baseline_value; cannot compute relative gain")
    if inputs.direction == "max":
        rel = (inputs.candidate_value - inputs.baseline_value) / inputs.baseline_value
    else:
        rel = (inputs.baseline_value - inputs.candidate_value) / inputs.baseline_value
    if rel < gain_threshold:
        return (False, f"insufficient gain: {rel:.3%} < {gain_threshold:.1%}")

    return (True, f"gain={rel:.1%}, novelty OK")


# ----------------------- platform-shaped paper artifact -----------------------

_WRITER_SYSTEM = """You are the Writer agent for an autonomous research lab.
You produce agent-readable paper artifacts (Markdown) for OTHER agents to read.
Output ONLY the body Markdown — five sections in this exact order:

""" + "\n".join(f"## {s}" for s in REQUIRED_SECTIONS_IN_ORDER) + """

Methods must be detailed enough that another lab's Researcher can draft
a recreation config WITHOUT consulting the source repo. Use inline code
blocks where the canonical implementation is non-obvious.

No frontmatter — the caller adds that. No code fences around the
output. Begin with the literal line "## Motivation"."""


def _resolve_code_sha() -> str | None:
    """Return git's current HEAD short SHA, or None if not in a repo."""
    import subprocess
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return out.decode().strip() or None
    except Exception:
        return None


def _best_metric(runs: list[dict], col: str, direction: str) -> float | None:
    """Return the best value of `col` across runs. min when direction=='min',
    max otherwise. None when no run carries the column."""
    vals = [r[col] for r in runs if r.get(col) is not None]
    if not vals:
        return None
    return min(vals) if direction == "min" else max(vals)


def _resolve_campaign_metric(
    campaign: dict, *, default: tuple[str, str]
) -> tuple[str, str]:
    """Resolve (metric, direction) for a campaign, preferring the
    campaign-declared values and falling back to `default` when null."""
    metric = campaign.get("headline_metric") or default[0]
    direction = campaign.get("headline_direction") or default[1]
    if direction not in ("min", "max"):
        direction = default[1]
    return metric, direction


def compose_paper(
    *,
    client: Any,
    campaign: dict,
    metric_provenance: list[dict],
    novelty_claim: str,
    code_sha: str | None,
    code_repo: str | None,
    budget: Any = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 8192,
) -> str:
    """Produce a complete platform-shaped paper artifact.

    Returns the artifact as a string (YAML frontmatter + body).
    Raises ValueError if the body fails structural check or the
    frontmatter fails pydantic validation.
    """
    user = (
        f"Campaign: {campaign['id']} — {campaign['question']}\n"
        f"Hypothesis file: {campaign['hypothesis_path']}\n"
        f"Hypothesis hash: {campaign['hypothesis_hash']}\n"
        f"Metrics: {metric_provenance}\n"
        f"Novelty: {novelty_claim}\n"
        f"Write the paper body now."
    )
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_WRITER_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    if budget is not None:
        from efferents.agents.budget import CallUsage, billing_model
        usage = CallUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_input_tokens=(
                getattr(response.usage, "cache_creation_input_tokens", 0) or 0
            ),
            cache_read_input_tokens=(
                getattr(response.usage, "cache_read_input_tokens", 0) or 0
            ),
        )
        budget.record(agent="writer", model=billing_model(client, model), usage=usage, notes="compose paper")
    body = "".join(b.text for b in response.content).strip()
    ok, errors = structural_check(body)
    if not ok:
        raise ValueError("Writer body failed structural check: " + "; ".join(errors))

    fm = PaperFrontmatter(
        lab_id=_lab.LAB_ID,
        domain=_lab.DOMAIN,
        subdomain=_lab.SUBDOMAIN,
        pi_handle=_lab.PI_HANDLE,
        campaign_id=campaign["id"],
        hypothesis_hash=campaign["hypothesis_hash"],
        hypothesis_path=campaign["hypothesis_path"],
        code_repo=code_repo,
        code_sha=code_sha,
        metric_provenance=metric_provenance,
        novelty_claim=novelty_claim,
        published_at=_date.today().isoformat(),
        status="preprint",
    )
    fm_yaml = _yaml.safe_dump(fm.model_dump(), sort_keys=False).strip()
    return f"---\n{fm_yaml}\n---\n\n{body}\n"


# ----------------------- Phase A entry point -----------------------

def write_phase_a_paper(
    paths: "WriterPaths",
    campaign: dict,
    client: Any,
    *,
    gain_threshold: float = 0.05,
    model: str = "claude-sonnet-4-6",
    budget: Any = None,
) -> str | None:
    """Gate-check, compose, peer-review, and commit a paper for a campaign.

    Pipeline:
      1. Mechanical pre-gate: novelty + ≥`gain_threshold` metric improvement
         (agents/writer.py:should_publish). If it fails, log and return None.
      2. Compose the paper artifact (Sonnet via compose_paper) and write to
         paper/<campaign_id>.md.
      3. If peer review is disabled (LabConfig.peer_review_enabled), return here (legacy
         publish-on-mechanical-gate behavior).
      4. Otherwise, run the 3-reviewer board (critical/neutral/enthusiast,
         in parallel) + one-shot rebuttal + decide().
      5. Write side-cars (reviews.md, rebuttal.md). Append to journal.md
         on accept; append to rejected.md on reject.
      6. On accept: auto-commit the bundle (paper + reviews + rebuttal +
         journal.md). Close the campaign with reason "published".
      7. On reject: close the campaign with reason "rejected_by_review".

    Returns the paper artifact string regardless of accept/reject (so callers
    can introspect what was composed), or None if the mechanical gate
    rejected the campaign before composition.
    """
    import sqlite3 as _sqlite3

    db = paths.runs_db

    def _load_campaign_runs(campaign_id: str) -> list[dict]:
        if not db.exists():
            return []
        conn = _sqlite3.connect(db)
        conn.row_factory = _sqlite3.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
            status_clause = (
                " AND status = 'succeeded'" if "status" in cols else ""
            )
            rows = conn.execute(
                "SELECT * FROM runs WHERE campaign_id = ?"
                + status_clause
                + " ORDER BY started_at ASC",
                (campaign_id,),
            ).fetchall()
        except _sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _load_other_runs(campaign_id: str) -> list[dict]:
        if not db.exists():
            return []
        conn = _sqlite3.connect(db)
        conn.row_factory = _sqlite3.Row
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(runs)")}
            status_clause = (
                " AND status = 'succeeded'" if "status" in cols else ""
            )
            rows = conn.execute(
                "SELECT * FROM runs WHERE (campaign_id != ? OR campaign_id IS NULL)"
                + status_clause
                + " ORDER BY started_at ASC",
                (campaign_id,),
            ).fetchall()
        except _sqlite3.OperationalError:
            return []
        finally:
            conn.close()
        return [dict(r) for r in rows]

    def _load_existing_claims() -> list[str]:
        """Stub: return novelty_claim strings from prior paper frontmatter files."""
        paper_dir = paths.paper
        if not paper_dir.exists():
            return []
        claims: list[str] = []
        for md_file in paper_dir.glob("*.md"):
            try:
                text = md_file.read_text()
                if not text.startswith("---"):
                    continue
                _, fm_yaml, _ = text.split("---", 2)
                fm = _yaml.safe_load(fm_yaml)
                if isinstance(fm, dict) and fm.get("novelty_claim"):
                    claims.append(str(fm["novelty_claim"]))
            except Exception:
                continue
        return claims

    campaign_id = campaign["id"]
    campaign_runs = _load_campaign_runs(campaign_id)
    other_runs = _load_other_runs(campaign_id)

    from efferents import lab as _lab_cfg  # local import; cfg may be unset in unit tests
    try:
        _cfg = _lab_cfg.get_config()
        _default = (_cfg.metrics.headline.column, _cfg.metrics.headline.direction)
    except RuntimeError:
        # No active LabConfig (e.g. a bare unit test). Defer to whatever the
        # campaign itself declares; a null metric makes the gate a safe no-op.
        _default = (campaign.get("headline_metric"), "min")
    metric, direction = _resolve_campaign_metric(campaign, default=_default)

    candidate_value = _best_metric(campaign_runs, metric, direction)
    baseline_value = _best_metric(other_runs, metric, direction)

    # If no campaign runs, nothing to publish.
    if candidate_value is None:
        return None

    # A relative-gain claim requires a measured comparator. Never synthesize
    # one merely to make a first campaign publishable.
    if baseline_value is None:
        try:
            with paths.notebook.open("a") as f:
                f.write(
                    f"\n### Writer gate: skipped {campaign_id}\n\n"
                    f"No successful baseline run exists for `{metric}`. "
                    "Queue or identify a comparator before publication.\n"
                )
        except Exception:
            pass
        return None

    existing_claims = _load_existing_claims()
    novelty_claim = campaign.get("question", "").strip() or campaign_id

    gate_inputs = GateInputs(
        primary_metric_name=metric,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        novelty_claim=novelty_claim,
        existing_lab_claims=existing_claims,
        refutation_of_corroborated=campaign.get("refutation_of_corroborated"),
        direction=direction,
    )

    ok, reason = should_publish(gate_inputs, gain_threshold=gain_threshold)

    if not ok:
        # Log to notebook and return None.
        notebook = paths.notebook
        msg = (
            f"\n### Writer gate: skipped {campaign_id}\n\n"
            f"Gate rejected: {reason}\n"
            f"candidate {metric}={candidate_value:.4f}, baseline={baseline_value:.4f}\n"
        )
        try:
            with notebook.open("a") as f:
                f.write(msg)
        except Exception:
            pass
        return None

    # Build metric_provenance from campaign runs.
    runs_by_seed: dict[int, list[float]] = {}
    run_ids: list[str] = []
    for r in campaign_runs:
        if r.get(metric) is None:
            continue
        seed = r.get("seed", 0) or 0
        runs_by_seed.setdefault(seed, []).append(r[metric])
        run_ids.append(r["run_id"])

    metric_provenance = [
        {
            "name": metric,
            "value": candidate_value,
            "delta_vs_baseline": candidate_value - baseline_value,
            "runs": run_ids or [campaign_id],
            "seeds": list(runs_by_seed.keys()) or [0],
        }
    ]

    # Resolve real git SHA for paper metadata, or set both None if unavailable.
    sha = _resolve_code_sha() if _lab.CODE_REPO else None
    repo = _lab.CODE_REPO if sha else None  # if we can't get a SHA, set neither

    artifact = compose_paper(
        client=client,
        campaign=campaign,
        metric_provenance=metric_provenance,
        novelty_claim=novelty_claim,
        code_sha=sha,
        code_repo=repo,
        budget=budget,
        model=model,
    )

    # Write artifact to paper/<campaign_id>.md.
    paper_dir = paths.paper
    paper_dir.mkdir(parents=True, exist_ok=True)
    out_path = paper_dir / f"{campaign_id}.md"
    out_path.write_text(artifact)

    # If peer review is disabled, we're done — legacy publish-on-gate path.
    if not _lab.PEER_REVIEW_ENABLED:
        return artifact

    # ------------------------------------------------------------------
    # Peer review pipeline: 3 reviewers (parallel) → rebuttal → decide
    # ------------------------------------------------------------------
    from concurrent.futures import ThreadPoolExecutor

    from efferents.agents import journal as _journal
    from efferents.agents import rebuttal as _rebuttal
    from efferents.agents import reviewer as _reviewer
    from efferents.agents.state import (
        campaign_close as _campaign_close,
        notebook_append,
        now_iso,
    )

    if budget is None:
        from efferents.agents.budget import BudgetTracker
        budget = BudgetTracker(paths.budget, daily_cap_usd=10000.0)

    delta_pct = (
        100.0 * (baseline_value - candidate_value) / baseline_value
        if direction == "min" and baseline_value != 0.0
        else (100.0 * (candidate_value - baseline_value) / baseline_value
              if baseline_value != 0.0 else 0.0)
    )
    headline = (
        f"{novelty_claim} — {metric} {candidate_value:.3f} vs baseline "
        f"{baseline_value:.3f} ({delta_pct:+.1f}%)"
    )

    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            futures = {
                p: ex.submit(
                    _reviewer.review,
                    paper_path=out_path, persona=p,
                    client=client, budget=budget,
                )
                for p in _reviewer.PERSONAS
            }
            reviews = [futures[p].result() for p in _reviewer.PERSONAS]
    except Exception as e:
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — peer-review board FAILED for {campaign_id}: "
            f"{type(e).__name__}: {e}. Campaign left open.\n",
        )
        return artifact

    try:
        rebuttal_text = _rebuttal.write_rebuttal(
            paper_path=out_path, reviews=reviews,
            client=client, budget=budget,
        )
    except Exception as e:
        rebuttal_text = f"## Rebuttal\n\n(rebuttal failed: {type(e).__name__}: {e})\n"

    decision = _reviewer.decide(reviews)

    _journal.write_reviews_file(
        paper_dir / f"{campaign_id}.reviews.md",
        campaign_id=campaign_id, reviews=reviews, decision=decision,
    )
    _journal.write_rebuttal_file(
        paper_dir / f"{campaign_id}.rebuttal.md",
        campaign_id=campaign_id, rebuttal_text=rebuttal_text,
    )

    student_id = campaign.get("student_id") or "primary"

    if decision["accept"]:
        _journal.append_journal(
            paper_dir / "journal.md",
            campaign_id=campaign_id, headline=headline, decision=decision,
            student_id=student_id,
        )
        try:
            sha = _journal.auto_commit_paper(
                repo_root=paths.paper.parent,
                campaign_id=campaign_id, headline=headline, decision=decision,
            )
            commit_msg = f"committed as {sha}" if sha else "commit failed"
        except Exception as e:
            commit_msg = f"commit raised: {type(e).__name__}: {e}"
        try:
            _campaign_close(paths.runs_db, campaign_id, reason="published")
        except Exception:
            pass  # already closed, or no campaigns table; non-fatal
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — Paper ACCEPTED: {campaign_id} "
            f"(mean={decision['mean_score']:.1f}, min={decision['min_score']}); "
            f"{commit_msg}.\n",
        )
    else:
        _journal.append_rejected(
            paper_dir / "rejected.md",
            campaign_id=campaign_id, headline=headline, decision=decision,
            student_id=student_id,
        )
        try:
            _campaign_close(paths.runs_db, campaign_id, reason="rejected_by_review")
        except Exception:
            pass
        notebook_append(
            paths.notebook,
            f"## {now_iso()} — Paper REJECTED: {campaign_id}; {decision['reason']}.\n",
        )

    return artifact


@dataclass(frozen=True)
class WriterPaths:
    lab: Path
    runs_db: Path
    notebook: Path
    digests_dir: Path
    state: Path
    budget: Path
    context: Path
    kb_db: Path
    paper: Path
    paper_sections: Path
    paper_notes: Path
    findings_log: Path
    results_section: Path
    refs_bib: Path
    related_section: Path
    reports_weekly: Path


def writer_paths(
    *, lab: str | Path, paper: str | Path, reports: str | Path, context: str | Path
) -> WriterPaths:
    lab = Path(lab)
    paper = Path(paper)
    reports = Path(reports)
    context = Path(context)
    return WriterPaths(
        lab=lab,
        runs_db=lab / "runs.sqlite",
        notebook=lab / "lab_notebook.md",
        digests_dir=lab / "digests",
        state=lab / "state.json",
        budget=lab / "budget.jsonl",
        context=context,
        kb_db=lab / "knowledge" / "kb.sqlite",
        paper=paper,
        paper_sections=paper / "sections",
        paper_notes=paper / "notes.md",
        findings_log=paper / "sections" / "05_findings_log.tex",
        results_section=paper / "sections" / "03_results.tex",
        refs_bib=paper / "refs.bib",
        related_section=paper / "sections" / "02_related.tex",
        reports_weekly=reports / "weekly",
    )
