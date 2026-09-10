"""Cross-lab federation — consume sibling labs' journal entries into ours.

Phase D groundwork toward the multi-lab vision: other people's autolabs run
this codebase (or compatible forks), produce their own `paper/journal.md`
files with `Lab:` / `Code:` / `Campaign:` provenance, and we ingest them into
our `paper/external_journal.md`. Our Researcher then has visibility into
sibling labs' findings as it forms hypotheses.

Today: one-shot CLI for manual sync. No auto-pull yet (Phase D.future).

Usage:
    # Pull a sibling lab's journal (must be a path to paper/journal.md):
    python -m agents.federation consume --from /path/to/other-lab/paper/journal.md

    # Or:
    python -m agents.federation consume --from /path/to/other-lab/paper/journal.md \\
        --out paper/external_journal.md --our-lab-id my-lab

The consumer:
- Parses the source journal by `## YYYY-MM-DD HH:MM UTC — <campaign_id>` headers.
- Skips entries whose `**Lab**:` matches our own (no self-reimport via a
  sibling who happens to also pull from us).
- Dedups by `(lab_id, campaign_id)` against entries already in out_path.
- Appends new entries verbatim, plus an `**Imported**:` line stamping source
  + timestamp.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

EXTERNAL_HEADER = (
    "# External journal — findings from sibling autolabs\n\n"
    "Append-only. Imports via `python -m agents.federation consume --from <path>`.\n"
    "Each entry preserves the originating lab's text verbatim and stamps an\n"
    "`**Imported**:` line marking when + from where. Dedup is by\n"
    "(lab_id, campaign_id).\n\n"
    "<!-- ENTRIES BELOW -->\n"
)
EXTERNAL_SENTINEL = "<!-- ENTRIES BELOW -->"

REPRODUCTIONS_HEADER = (
    "# Reproductions of external work\n\n"
    "Append-only. Each entry records our lab's attempt to reproduce a\n"
    "finding from `paper/external_journal.md` BEFORE building on it.\n"
    "Discipline mirrors academic practice: a paper we cite as foundational\n"
    "to one of our hypotheses must be reproduced first (or the citation\n"
    "is unverified and downstream work is conditional).\n\n"
    "Status legend:\n"
    "- `verified`  — our run reproduced the claim within tolerance\n"
    "- `failed`    — our run did NOT reproduce; downstream work that cites\n"
    "                this claim should be retracted or marked conditional\n"
    "- `pending`   — reproduction proposal queued but not yet executed\n\n"
    "<!-- ENTRIES BELOW -->\n"
)
REPRODUCTIONS_SENTINEL = "<!-- ENTRIES BELOW -->"

VALID_REPRODUCTION_STATUS = ("verified", "failed", "pending")
DEFAULT_REPRO_TOLERANCE = 0.20   # 20% relative error — typical eval-noise band

# A header line looks like:  ## 2026-05-26 14:32 UTC — c-aa11
_ENTRY_HEADER_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC) [—-] (\S+)\s*$"
)
_BUNDLE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_bundle_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _BUNDLE_ID_RE.fullmatch(value):
        raise ValueError(f"unsafe {label} in bundle: {value!r}")
    return value


def _safe_bundle_relpath(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"unsafe file path in bundle manifest: {value!r}")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"unsafe file path in bundle manifest: {value!r}")
    return Path(*path.parts)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_journal_entries(text: str) -> list[dict[str, Any]]:
    """Parse paper/journal.md (or compatible) into a list of entry dicts.

    Each entry has: {ts, campaign_id, lab_id, student_id, headline, body}.
    `body` is the full entry markdown (header + all fields) preserved verbatim
    — federation imports re-emit `body` so the source lab's formatting is kept.
    Fields are best-effort: missing ones come out as None / empty string.
    """
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    def _flush():
        if current is not None:
            current["body"] = "\n".join(body_lines).rstrip()
            entries.append(current)

    for line in text.splitlines():
        m = _ENTRY_HEADER_RE.match(line)
        if m:
            _flush()
            current = {
                "ts": m.group(1),
                "campaign_id": m.group(2),
                "lab_id": None,
                "student_id": None,
                "headline": None,
            }
            body_lines = [line]
            continue
        if current is None:
            continue  # pre-header preamble (file header etc.)
        body_lines.append(line)
        # Extract well-known fields opportunistically.
        s = line.strip()
        if s.startswith("**Lab**:"):
            current["lab_id"] = s[len("**Lab**:"):].strip()
        elif s.startswith("**Student**:"):
            current["student_id"] = s[len("**Student**:"):].strip()
        elif s.startswith("**Headline**:"):
            current["headline"] = s[len("**Headline**:"):].strip()
    _flush()
    return entries


def recent_external_entries(
    external_path: Path,
    *,
    days: int = 14,
    max_n: int = 10,
) -> list[dict[str, Any]]:
    """Return the most recent N entries from paper/external_journal.md
    (default: last 14 days, capped at 10). Used by the Researcher's
    Supervisor brief to surface sibling-lab findings.

    Returns [] if the file is missing. Entries are sorted newest-first
    by ts (which is `YYYY-MM-DD HH:MM UTC` — lex-sortable as-is).
    """
    if not external_path.exists():
        return []
    entries = parse_journal_entries(external_path.read_text())
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    fresh = [e for e in entries if (e.get("ts") or "") >= cutoff]
    fresh.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return fresh[:max_n]


# ---------------------------------------------------------------------------
# Reproduction ledger (paper/reproductions.md)
# ---------------------------------------------------------------------------

# A header line looks like:  ## 2026-05-27 09:00 UTC — other-lab/c-aa11
_REPRO_HEADER_RE = re.compile(
    r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC) [—-] (\S+)/(\S+)\s*$"
)


def _parse_reproductions(text: str) -> list[dict[str, Any]]:
    """Parse paper/reproductions.md into entries:
    [{ts, lab_id, campaign_id, status, claimed, observed, body}]."""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body_lines: list[str] = []

    def _flush():
        if current is not None:
            current["body"] = "\n".join(body_lines).rstrip()
            entries.append(current)

    for line in text.splitlines():
        m = _REPRO_HEADER_RE.match(line)
        if m:
            _flush()
            current = {
                "ts": m.group(1),
                "lab_id": m.group(2),
                "campaign_id": m.group(3),
                "status": None,
                "claimed": None,
                "observed": None,
            }
            body_lines = [line]
            continue
        if current is None:
            continue
        body_lines.append(line)
        s = line.strip()
        if s.startswith("**Status**:"):
            current["status"] = s[len("**Status**:"):].strip()
        elif s.startswith("**Claimed**:"):
            current["claimed"] = s[len("**Claimed**:"):].strip()
        elif s.startswith("**Observed**:"):
            current["observed"] = s[len("**Observed**:"):].strip()
    _flush()
    return entries


def reproductions_path(paper_dir: Path) -> Path:
    return Path(paper_dir) / "reproductions.md"


def is_reproduced(
    paper_dir: Path, *, lab_id: str, campaign_id: str
) -> bool:
    """True iff the latest reproduction status is ``verified``.

    A later failed reproduction revokes an earlier verification.  Using the
    latest status makes the execution gate fail closed when evidence changes.
    """
    return reproduction_status(
        paper_dir, lab_id=lab_id, campaign_id=campaign_id
    ) == "verified"


def reproduction_status(
    paper_dir: Path, *, lab_id: str, campaign_id: str
) -> str | None:
    """Latest status for this (lab, campaign), or None if no attempts logged.
    Returns one of 'verified' / 'failed' / 'pending' / None.

    Entries in reproductions.md are stored newest-first (sentinel insertion
    pushes older entries down), so the FIRST matching entry is the latest."""
    path = reproductions_path(paper_dir)
    if not path.exists():
        return None
    matching = [
        e for e in _parse_reproductions(path.read_text())
        if e.get("lab_id") == lab_id and e.get("campaign_id") == campaign_id
    ]
    return matching[0]["status"] if matching else None


def foundational_dependency_violations(
    paper_dir: Path,
    proposal: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return dependencies that make ``proposal`` unsafe to execute.

    A proposal may use a sibling paper as a foundational premise only when
    the latest local reproduction record for that paper is ``verified``.
    Reproduction proposals may target their own still-unverified dependency,
    but that narrow exception does not waive any other dependency.

    Malformed dependency declarations fail closed.  Each returned item has a
    compact ``reason`` suitable for the append-only lab notebook.
    """
    deps = proposal.get("foundational_external")
    if deps is None:
        return []
    if not isinstance(deps, list):
        return [{"reason": "foundational_external must be a list"}]

    reproduction = proposal.get("reproduction_of")
    reproduction_key: tuple[str, str] | None = None
    if isinstance(reproduction, dict):
        r_lab = reproduction.get("lab_id")
        r_campaign = reproduction.get("campaign_id")
        if isinstance(r_lab, str) and isinstance(r_campaign, str):
            reproduction_key = (r_lab, r_campaign)

    violations: list[dict[str, Any]] = []
    for index, dep in enumerate(deps):
        if not isinstance(dep, dict):
            violations.append({
                "index": index,
                "reason": "dependency must be an object",
            })
            continue
        lab_id = dep.get("lab_id")
        campaign_id = dep.get("campaign_id")
        if not isinstance(lab_id, str) or not lab_id or not isinstance(campaign_id, str) or not campaign_id:
            violations.append({
                "index": index,
                "reason": "dependency requires non-empty lab_id and campaign_id",
            })
            continue
        key = (lab_id, campaign_id)
        if reproduction_key == key:
            continue
        status = reproduction_status(
            paper_dir, lab_id=lab_id, campaign_id=campaign_id
        )
        if status != "verified":
            violations.append({
                "index": index,
                "lab_id": lab_id,
                "campaign_id": campaign_id,
                "status": status,
                "reason": "latest reproduction is not verified",
            })
    return violations


def record_reproduction(
    paper_dir: Path,
    *,
    lab_id: str,
    campaign_id: str,
    status: str,
    claimed: str | None = None,
    observed: str | None = None,
    run_id: str | None = None,
    notes: str | None = None,
) -> None:
    """Append a reproduction-attempt entry to paper/reproductions.md."""
    if status not in VALID_REPRODUCTION_STATUS:
        raise ValueError(
            f"status must be one of {VALID_REPRODUCTION_STATUS}; got {status!r}"
        )
    path = reproductions_path(paper_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(REPRODUCTIONS_HEADER)
    lines = [
        f"\n## {_now_str()} — {lab_id}/{campaign_id}",
        f"**Status**: {status}",
    ]
    if claimed:
        lines.append(f"**Claimed**: {claimed}")
    if observed:
        lines.append(f"**Observed**: {observed}")
    if run_id:
        lines.append(f"**Run**: `{run_id}`")
    if notes:
        lines.append(f"**Notes**: {notes}")
    entry = "\n".join(lines) + "\n"

    content = path.read_text()
    if REPRODUCTIONS_SENTINEL in content:
        marker_end = content.index(REPRODUCTIONS_SENTINEL) + len(REPRODUCTIONS_SENTINEL)
        new_content = content[:marker_end] + "\n" + entry + content[marker_end:]
    else:
        new_content = content.rstrip() + "\n" + entry
    path.write_text(new_content)


def pending_foundational_deps(
    deps_log: Path,
    paper_dir: Path,
    *,
    max_age_days: int = 30,
) -> list[dict[str, Any]]:
    """Walk `lab/foundational_deps.jsonl` (the per-proposal dependency log) and
    return entries whose (lab_id, campaign_id) has NO `verified` status in
    `paper/reproductions.md` yet. Filtered by entry age.

    Each returned dict: {lab_id, campaign_id, why, ts, proposal_name, status}.
    `status` is whatever reproductions.md says (could be 'failed', 'pending',
    or None for 'never attempted').
    """
    if not deps_log.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    pending: list[dict[str, Any]] = []
    # Dedup by (lab_id, campaign_id) — show each once.
    seen: set[tuple[str, str]] = set()
    for line in deps_log.read_text().splitlines():
        if not line.strip():
            continue
        try:
            rec = __import__("json").loads(line)
        except Exception:
            continue
        try:
            ts = datetime.fromisoformat(rec.get("ts", ""))
        except ValueError:
            continue
        if ts < cutoff:
            continue
        lab_id = rec.get("lab_id")
        cid = rec.get("campaign_id")
        if not lab_id or not cid:
            continue
        if (lab_id, cid) in seen:
            continue
        seen.add((lab_id, cid))
        if is_reproduced(paper_dir, lab_id=lab_id, campaign_id=cid):
            continue
        pending.append({
            "lab_id": lab_id,
            "campaign_id": cid,
            "why": rec.get("why", ""),
            "ts": rec.get("ts", ""),
            "proposal_name": rec.get("proposal_name", ""),
            "status": reproduction_status(paper_dir, lab_id=lab_id, campaign_id=cid),
        })
    return pending


# ---------------------------------------------------------------------------
# Bundle export + import (D.5)
# ---------------------------------------------------------------------------
# Self-contained tarball for sibling-lab consumption. The bundle carries the
# paper, reviews, rebuttal, hypothesis, journal entry, the canonical
# experiment recipe (config_yaml from the best run), and a manifest
# with provenance hashes. A sibling lab `import`s this and can both cite
# the work AND reproduce it — closing the loop on D.4's reproduction
# discipline.

MANIFEST_VERSION = 1
BUNDLE_REL_PATHS = {
    "paper": "paper/paper.md",
    "reviews": "paper/reviews.md",
    "rebuttal": "paper/rebuttal.md",
    "journal": "journal_entry.md",
    "hypothesis": "hypothesis.md",
    "recipe": "recipe.yaml",
    "metrics": "metric_provenance.json",
}


def _sha256_of(content: bytes | str) -> str:
    import hashlib
    if isinstance(content, str):
        content = content.encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _campaign_row(db: Path, campaign_id: str) -> dict[str, Any] | None:
    import sqlite3
    if not db.exists():
        return None
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM campaigns WHERE id = ?", (campaign_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _campaign_runs(db: Path, campaign_id: str, metric: str | None = None, direction: str = "min") -> list[dict[str, Any]]:
    """All runs for this campaign, ordered best-first by metric+direction.

    *direction* controls sort order: "min" means lower is better (ASC),
    "max" means higher is better (DESC).  If *metric* is None (or the column
    doesn't exist in the runs table) the query falls back to ordering by
    started_at and returns all rows.
    """
    import sqlite3
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        # Detect available columns so we can order by metric only when present.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)").fetchall()}
        status_clause = " AND status = 'succeeded'" if "status" in cols else ""
        if metric and metric in cols:
            order = "ASC" if direction == "min" else "DESC"
            rows = conn.execute(
                f"""SELECT * FROM runs
                   WHERE campaign_id = ?{status_clause} AND {metric} IS NOT NULL
                   ORDER BY {metric} {order}""",
                (campaign_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                f"""SELECT * FROM runs
                   WHERE campaign_id = ?{status_clause}
                   ORDER BY started_at ASC""",
                (campaign_id,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _journal_entry_block(journal_path: Path, campaign_id: str) -> str | None:
    """Extract one campaign's entry block from paper/journal.md verbatim."""
    if not journal_path.exists():
        return None
    for e in parse_journal_entries(journal_path.read_text()):
        if e.get("campaign_id") == campaign_id:
            return e.get("body")
    return None


def export_paper_bundle(
    *,
    campaign_id: str,
    db: Path,
    paper_dir: Path,
    popper_corpus_root: Path | None = None,
    out_path: Path | None = None,
    lab_id: str | None = None,
    code_repo: str | None = None,
    code_sha: str | None = None,
) -> dict[str, Any]:
    """Export an accepted campaign as a federation tarball.

    Bundle layout (inside the .tar.gz):
        manifest.json
        paper/paper.md           (renamed from <campaign_id>.md for stable paths)
        paper/reviews.md
        paper/rebuttal.md
        journal_entry.md         (this campaign's block from paper/journal.md)
        hypothesis.md            (from popper-corpus/<student_id>/<slug>/)
        recipe.yaml              (config_yaml of the best run by campaign metric)
        metric_provenance.json   (run_id, <metric>, seed for each campaign run)

    Missing pieces (no hypothesis file, no runs, etc.) are skipped silently;
    the manifest's `files` list reflects what's actually present. Returns
    telemetry: {bundle_path, manifest, files, n_runs}.
    """
    import json as _json
    import tarfile
    import tempfile

    if lab_id is None or code_repo is None or code_sha is None:
        # Reuse the same resolver journal.py uses for entry stamping so
        # exported manifests and journal entries see the same lab metadata.
        from efferents.agents.journal import _lab_metadata
        d_lab_id, d_repo, d_sha = _lab_metadata()
        lab_id = lab_id or d_lab_id
        code_repo = code_repo or d_repo
        code_sha = code_sha or d_sha
    lab_id = _safe_bundle_id(lab_id or "unknown-lab", "lab_id")
    campaign_id = _safe_bundle_id(campaign_id, "campaign_id")

    paper_dir = Path(paper_dir)
    db = Path(db)
    popper_corpus_root = (
        Path(popper_corpus_root)
        if popper_corpus_root is not None
        else paper_dir.parent / "popper-corpus"
    )

    # Sources on disk
    paper_path = paper_dir / f"{campaign_id}.md"
    reviews_path = paper_dir / f"{campaign_id}.reviews.md"
    rebuttal_path = paper_dir / f"{campaign_id}.rebuttal.md"
    journal_path = paper_dir / "journal.md"

    if not paper_path.exists():
        raise FileNotFoundError(
            f"paper artifact not found: {paper_path}. "
            "Export is only supported for campaigns that have a paper.md "
            "(i.e., they passed the mechanical should_publish gate)."
        )

    campaign_row = _campaign_row(db, campaign_id) or {}
    student_id = campaign_row.get("student_id") or "primary"
    hypothesis_hash_db = campaign_row.get("hypothesis_hash") or ""
    hypothesis_path_str = campaign_row.get("hypothesis_path") or ""

    # Resolve which metric column and direction to use: campaign row first,
    # then lab config fallback. With neither, metric is None and _campaign_runs
    # falls back to ordering by started_at.
    try:
        from efferents import lab as _lab_cfg
        _cfg = _lab_cfg.get_config()
        _default = (_cfg.metrics.headline.column, _cfg.metrics.headline.direction)
    except (RuntimeError, AttributeError):
        _default = (None, "min")
    metric = campaign_row.get("headline_metric") or _default[0]
    direction = campaign_row.get("headline_direction") or _default[1]

    # Hypothesis lookup. hypothesis_path is stored relative to the repo root
    # (paths.root.parent / hypothesis_path), as written by the popper-gate.
    repo_root = paper_dir.parent
    hyp_path = None
    if hypothesis_path_str:
        candidate = (repo_root / hypothesis_path_str).resolve()
        try:
            candidate.relative_to(popper_corpus_root.resolve())
        except ValueError:
            candidate = None
        if candidate is not None and candidate.is_file():
            hyp_path = candidate

    journal_entry_md = _journal_entry_block(journal_path, campaign_id)
    runs = _campaign_runs(db, campaign_id, metric=metric, direction=direction)
    candidate_run = runs[0] if runs else None
    recipe_yaml = candidate_run.get("config_yaml") if candidate_run else None

    # Parse the journal entry to surface the score for the manifest.
    decision_block: dict[str, Any] = {}
    if journal_entry_md:
        # The body starts with the header line "## ...". parse_journal_entries
        # surfaces lab_id/student_id/headline; for scores, we don't need them
        # parsed into structure — the importer can read journal_entry.md
        # directly. Keep the manifest minimal here.
        for line in journal_entry_md.splitlines():
            s = line.strip()
            if s.startswith("**Headline**:"):
                decision_block["headline"] = s[len("**Headline**:"):].strip()
            elif s.startswith("**Scores**:"):
                decision_block["scores_line"] = s[len("**Scores**:"):].strip()

    if out_path is None:
        bundles_dir = paper_dir / "bundles"
        bundles_dir.mkdir(parents=True, exist_ok=True)
        out_path = bundles_dir / f"{lab_id}-{campaign_id}.tar.gz"
    else:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Stage files into a tmp dir, write manifest, tar it up.
    files_in_bundle: list[str] = []
    metric_provenance: list[dict[str, Any]] = [
        {"run_id": r.get("run_id"), metric: r.get(metric),
         "seed": r.get("seed"), "model": r.get("model"),
         "direction": direction}
        for r in runs
    ]

    with tempfile.TemporaryDirectory() as td:
        staging = Path(td) / "bundle"
        staging.mkdir()
        (staging / "paper").mkdir()

        # Paper + side-cars
        (staging / BUNDLE_REL_PATHS["paper"]).write_text(paper_path.read_text())
        files_in_bundle.append(BUNDLE_REL_PATHS["paper"])

        if reviews_path.exists():
            (staging / BUNDLE_REL_PATHS["reviews"]).write_text(reviews_path.read_text())
            files_in_bundle.append(BUNDLE_REL_PATHS["reviews"])
        if rebuttal_path.exists():
            (staging / BUNDLE_REL_PATHS["rebuttal"]).write_text(rebuttal_path.read_text())
            files_in_bundle.append(BUNDLE_REL_PATHS["rebuttal"])

        if journal_entry_md:
            (staging / BUNDLE_REL_PATHS["journal"]).write_text(journal_entry_md + "\n")
            files_in_bundle.append(BUNDLE_REL_PATHS["journal"])

        hypothesis_hash_computed: str | None = None
        if hyp_path and hyp_path.exists():
            hyp_text = hyp_path.read_text()
            (staging / BUNDLE_REL_PATHS["hypothesis"]).write_text(hyp_text)
            files_in_bundle.append(BUNDLE_REL_PATHS["hypothesis"])
            hypothesis_hash_computed = _sha256_of(hyp_text)

        if recipe_yaml:
            (staging / BUNDLE_REL_PATHS["recipe"]).write_text(recipe_yaml)
            files_in_bundle.append(BUNDLE_REL_PATHS["recipe"])

        (staging / BUNDLE_REL_PATHS["metrics"]).write_text(
            _json.dumps(metric_provenance, indent=2)
        )
        files_in_bundle.append(BUNDLE_REL_PATHS["metrics"])

        # The manifest carries the *recomputed* hypothesis hash (so import
        # verification compares apples-to-apples). The db-stored hash is
        # surfaced as `hypothesis_hash_recorded` for audit — if it doesn't
        # match what we just computed, the popper-corpus drifted from the
        # campaigns row and the importer can decide what to do.
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "lab_id": lab_id,
            "campaign_id": campaign_id,
            "student_id": student_id,
            "code_repo": code_repo,
            "code_sha": code_sha,
            "hypothesis_hash": hypothesis_hash_computed,
            "hypothesis_hash_recorded": hypothesis_hash_db or None,
            "exported_at": _now_str(),
            "headline": decision_block.get("headline"),
            "scores_line": decision_block.get("scores_line"),
            "n_runs": len(runs),
            "primary_metric": (
                {"name": metric, "value": runs[0].get(metric),
                 "run_id": runs[0]["run_id"]}
                if runs else None
            ),
            "files": files_in_bundle,
        }
        (staging / "manifest.json").write_text(_json.dumps(manifest, indent=2))
        files_in_bundle_sorted = sorted(set(files_in_bundle) | {"manifest.json"})

        with tarfile.open(out_path, "w:gz") as tar:
            for rel in files_in_bundle_sorted:
                tar.add(staging / rel, arcname=rel)

    return {
        "bundle_path": str(out_path),
        "manifest": manifest,
        "files": files_in_bundle_sorted,
        "n_runs": len(runs),
    }


def import_paper_bundle(
    *,
    bundle_path: Path,
    paper_dir: Path,
    our_lab_id: str | None = None,
    verify_hash: bool = True,
) -> dict[str, Any]:
    """Extract a federation bundle into paper/external/<lab_id>/<campaign_id>/.

    Steps:
      1. Open tarball, extract to a tmp dir.
      2. Read manifest.json.
      3. If verify_hash and the bundle includes hypothesis.md + a recorded
         hash in the manifest, recompute and compare. Raise on mismatch.
      4. Copy all bundled files into paper/external/<lab_id>/<campaign_id>/.
      5. Append the journal_entry.md to paper/external_journal.md via
         consume_external_journal (dedup applied).

    Returns telemetry: {lab_id, campaign_id, target_dir, hash_ok,
    n_consumed_added, n_consumed_dup, files_placed}.
    """
    import json as _json
    import tarfile
    import tempfile

    bundle_path = Path(bundle_path)
    paper_dir = Path(paper_dir)

    if not bundle_path.exists():
        raise FileNotFoundError(f"bundle not found: {bundle_path}")

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        with tarfile.open(bundle_path, "r:*") as tar:
            # Safe extract: reject any member with absolute or escaping path.
            members = tar.getmembers()
            for m in members:
                normalized = m.name.replace("\\", "/")
                member_path = PurePosixPath(normalized)
                if member_path.is_absolute() or ".." in member_path.parts:
                    raise ValueError(f"unsafe path in bundle: {m.name!r}")
            # filter="data" (Python 3.12+) is the safest extraction policy —
            # strips ownership, rejects special files (devices, fifos), and
            # blocks path traversal. Our check above is defense-in-depth.
            tar.extractall(td_path, filter="data")

        manifest_path = td_path / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("bundle is missing manifest.json")
        manifest = _json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("bundle manifest must be a JSON object")

        lab_id = _safe_bundle_id(manifest.get("lab_id") or "unknown-lab", "lab_id")
        campaign_id = _safe_bundle_id(
            manifest.get("campaign_id") or "unknown-campaign", "campaign_id"
        )
        manifest_files = manifest.get("files", [])
        if not isinstance(manifest_files, list):
            raise ValueError("bundle manifest 'files' must be a list")

        # Hash verification: recompute sha256 of bundled hypothesis.md and
        # compare to manifest.hypothesis_hash. If verify_hash is True and
        # both sides are present, mismatch raises.
        hash_ok: bool | None = None
        hyp_in_bundle = td_path / BUNDLE_REL_PATHS["hypothesis"]
        recorded_hash = manifest.get("hypothesis_hash")
        if verify_hash and recorded_hash and hyp_in_bundle.exists():
            actual = _sha256_of(hyp_in_bundle.read_text())
            hash_ok = (actual == recorded_hash)
            if not hash_ok:
                raise ValueError(
                    f"hypothesis hash mismatch for {lab_id}/{campaign_id}: "
                    f"manifest={recorded_hash}, recomputed={actual}"
                )
        elif recorded_hash and hyp_in_bundle.exists():
            hash_ok = (_sha256_of(hyp_in_bundle.read_text()) == recorded_hash)

        # Place files at paper/external/<lab_id>/<campaign_id>/<rel>
        target_dir = paper_dir / "external" / lab_id / campaign_id
        target_dir.mkdir(parents=True, exist_ok=True)
        files_placed: list[str] = []
        for raw_rel in manifest_files + ["manifest.json"]:
            rel = _safe_bundle_relpath(raw_rel)
            src = (td_path / rel).resolve()
            try:
                src.relative_to(td_path.resolve())
            except ValueError as e:
                raise ValueError(f"bundle source escapes extraction root: {raw_rel!r}") from e
            if not src.is_file():
                continue
            dst = (target_dir / rel).resolve()
            try:
                dst.relative_to(target_dir.resolve())
            except ValueError as e:
                raise ValueError(f"bundle target escapes import root: {raw_rel!r}") from e
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            files_placed.append(rel.as_posix())

        # Consume the journal entry → paper/external_journal.md (dedup by
        # (lab_id, campaign_id) handled by consume_external_journal).
        journal_consume: dict[str, Any] = {"n_added": 0, "n_skipped_dup": 0,
                                            "n_skipped_self": 0, "n_source": 0}
        journal_entry_path = target_dir / BUNDLE_REL_PATHS["journal"]
        if journal_entry_path.exists():
            journal_consume = consume_external_journal(
                source=journal_entry_path,
                out_path=paper_dir / "external_journal.md",
                our_lab_id=our_lab_id,
            )

    return {
        "lab_id": lab_id,
        "campaign_id": campaign_id,
        "target_dir": str(target_dir),
        "hash_ok": hash_ok,
        "n_consumed_added": journal_consume.get("n_added", 0),
        "n_consumed_dup": journal_consume.get("n_skipped_dup", 0),
        "files_placed": files_placed,
        "manifest": manifest,
    }


def consume_external_journal(
    *,
    source: Path,
    out_path: Path,
    our_lab_id: str | None = None,
) -> dict[str, Any]:
    """Pull sibling-lab journal entries into our `paper/external_journal.md`.

    Returns telemetry:
        {n_source, n_skipped_self, n_skipped_dup, n_added, source, out}
    """
    if our_lab_id is None:
        from efferents import lab as _lab
        our_lab_id = _lab.LAB_ID

    source = Path(source)
    out_path = Path(out_path)

    if not source.exists():
        raise FileNotFoundError(f"source journal not found: {source}")

    src_entries = parse_journal_entries(source.read_text())

    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        out_path.write_text(EXTERNAL_HEADER)

    existing_entries = parse_journal_entries(out_path.read_text())
    existing_keys = {(e.get("lab_id"), e["campaign_id"]) for e in existing_entries}

    new_blocks: list[str] = []
    skipped_self = 0
    skipped_dup = 0
    for e in src_entries:
        if e.get("lab_id") == our_lab_id:
            skipped_self += 1
            continue
        key = (e.get("lab_id"), e["campaign_id"])
        if key in existing_keys:
            skipped_dup += 1
            continue
        # Append the source's body verbatim + an Imported: marker so the
        # provenance of THIS import is recoverable later.
        block = e["body"].rstrip() + f"\n**Imported**: {_now_str()} from {source}"
        new_blocks.append(block)

    if new_blocks:
        content = out_path.read_text()
        if EXTERNAL_SENTINEL in content:
            marker_end = content.index(EXTERNAL_SENTINEL) + len(EXTERNAL_SENTINEL)
            insertion = "\n\n" + "\n\n".join(new_blocks) + "\n"
            new_content = content[:marker_end] + insertion + content[marker_end:]
        else:
            new_content = content.rstrip() + "\n\n" + "\n\n".join(new_blocks) + "\n"
        out_path.write_text(new_content)

    return {
        "n_source": len(src_entries),
        "n_skipped_self": skipped_self,
        "n_skipped_dup": skipped_dup,
        "n_added": len(new_blocks),
        "source": str(source),
        "out": str(out_path),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agents.federation",
        description="Federation tools: consume sibling-lab journals; "
                    "export/import paper bundles.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # consume
    p_consume = sub.add_parser(
        "consume", help="Pull sibling-lab journal entries into paper/external_journal.md"
    )
    p_consume.add_argument(
        "--from", dest="source", required=True, type=Path,
        help="Path to the sibling lab's paper/journal.md",
    )
    p_consume.add_argument(
        "--out", dest="out", default=Path("paper/external_journal.md"), type=Path,
        help="Where to append external entries (default: paper/external_journal.md)",
    )
    p_consume.add_argument(
        "--our-lab-id", dest="our_lab_id", default=None,
        help="Our lab_id (defaults to LabConfig.lab_id). Used to skip "
             "self-imports if a sibling lab happens to re-export our entries.",
    )

    # export
    p_export = sub.add_parser(
        "export", help="Package a campaign as a federation bundle (.tar.gz)"
    )
    p_export.add_argument(
        "--campaign", dest="campaign_id", required=True,
        help="campaign_id to export (e.g. c-aa11)",
    )
    p_export.add_argument(
        "--db", dest="db", default=Path("lab/runs.sqlite"), type=Path,
        help="Path to runs.sqlite (default: lab/runs.sqlite)",
    )
    p_export.add_argument(
        "--paper-dir", dest="paper_dir", default=Path("paper"), type=Path,
        help="paper/ directory (default: paper)",
    )
    p_export.add_argument(
        "--out", dest="out", default=None, type=Path,
        help="Output .tar.gz path "
             "(default: paper/bundles/<lab_id>-<campaign_id>.tar.gz)",
    )

    # import_  (avoid Python keyword)
    p_import = sub.add_parser(
        "import", help="Ingest a federation bundle (.tar.gz)"
    )
    p_import.add_argument(
        "bundle", type=Path, help="Path to the bundle .tar.gz to import",
    )
    p_import.add_argument(
        "--paper-dir", dest="paper_dir", default=Path("paper"), type=Path,
        help="Our paper/ directory (default: paper)",
    )
    p_import.add_argument(
        "--our-lab-id", dest="our_lab_id", default=None,
        help="Our lab_id (defaults to LabConfig.lab_id)",
    )
    p_import.add_argument(
        "--no-verify-hash", dest="verify_hash", action="store_false", default=True,
        help="Skip hypothesis-hash verification (NOT recommended)",
    )

    args = parser.parse_args(argv)

    if args.cmd == "consume":
        result = consume_external_journal(
            source=args.source, out_path=args.out, our_lab_id=args.our_lab_id,
        )
        print(
            f"federation/consume: {result['n_added']} added, "
            f"{result['n_skipped_dup']} dup, {result['n_skipped_self']} self-skip "
            f"(out of {result['n_source']} source entries) → {result['out']}"
        )
        return 0

    if args.cmd == "export":
        result = export_paper_bundle(
            campaign_id=args.campaign_id,
            db=args.db, paper_dir=args.paper_dir, out_path=args.out,
        )
        manifest = result["manifest"]
        print(
            f"federation/export: {result['bundle_path']}\n"
            f"  lab={manifest['lab_id']} campaign={manifest['campaign_id']} "
            f"student={manifest['student_id']}\n"
            f"  code={manifest.get('code_repo')}@{manifest.get('code_sha')}\n"
            f"  hypothesis_hash={manifest.get('hypothesis_hash') or '(none)'}\n"
            f"  files: {len(result['files'])} bundled, {result['n_runs']} runs"
        )
        return 0

    if args.cmd == "import":
        result = import_paper_bundle(
            bundle_path=args.bundle,
            paper_dir=args.paper_dir,
            our_lab_id=args.our_lab_id,
            verify_hash=args.verify_hash,
        )
        hash_str = (
            "ok" if result["hash_ok"] is True
            else ("(skipped)" if result["hash_ok"] is None else "MISMATCH")
        )
        print(
            f"federation/import: {result['lab_id']}/{result['campaign_id']}\n"
            f"  placed {len(result['files_placed'])} files at {result['target_dir']}\n"
            f"  hypothesis_hash: {hash_str}\n"
            f"  external_journal: {result['n_consumed_added']} added, "
            f"{result['n_consumed_dup']} dup"
        )
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(_main())
