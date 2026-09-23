"""Auditable journal influence, distinct from delivery and independent replication."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from efferents.agents.federation import parse_journal_entries, reproduction_status
from efferents.journal.reviews import is_publication, review_scores


def received_publications(lab_root: Path) -> dict[str, dict]:
    """Accepted papers durably received by this lab, including terminal subscriptions."""
    from efferents.agents.conference import _rows
    publications = {}
    for paper in (lab_root.parent / "paper", lab_root / "paper"):
        path = paper / "external_journal.md"
        if not path.is_file():
            continue
        for entry in parse_journal_entries(path.read_text()):
            if not entry.get("lab_id"):
                continue
            metadata = {}
            for line in entry["body"].splitlines():
                for key in ("Journal", "Domain"):
                    if line.startswith(f"**{key}**:"):
                        metadata[key.lower()] = line.split(":", 1)[1].strip()
            row = {**entry, **metadata, "id": f"journal:{entry['lab_id']}:{entry['campaign_id']}",
                   "kind": "publication", "publication_status": "accepted",
                   "review_scores": review_scores(entry["body"]),
                   "journal": metadata.get("journal") or "External journal"}
            if is_publication(row):
                publications[row["id"]] = row
    for row in _rows(lab_root / "conference" / "inbox.jsonl"):
        if is_publication(row) and isinstance(row.get("id"), str):
            publications[row["id"]] = row
    return publications


def record_execution(lab_root: Path, proposal: dict, outcome: dict) -> int:
    """Record declared influence only after a successful execution of that proposal.

    Unknown IDs and mere deliveries cannot become use records. A reproduction is
    labeled as an attempt, never upgraded here to corroboration or verification.
    """
    if not outcome.get("ok"):
        return 0
    from efferents.agents.conference import _append, _locked, _rows
    from efferents.agents.state import notebook_append
    publications = received_publications(lab_root)
    references = proposal.get("external_citations") or []
    references = list(references) if isinstance(references, list) else []
    deps = proposal.get("foundational_external") or []
    for dep in deps if isinstance(deps, list) else []:
        if isinstance(dep, dict):
            source = next((p for p in publications.values()
                           if (p.get("lab_id"), p.get("campaign_id")) ==
                           (dep.get("lab_id"), dep.get("campaign_id"))), None)
            if source:
                references.append({"publication_id": source["id"], "why": dep.get("why", ""),
                                   "foundational": True})
    runs = [str(row["run_id"]) for row in outcome.get("rows", [])
            if isinstance(row, dict) and row.get("run_id")]
    if not runs:
        return 0
    count = 0
    with _locked(lab_root):
        log = lab_root / "journal_uses.jsonl"
        seen = {row.get("id") for row in _rows(log)}
        for ref in references:
            if not isinstance(ref, dict):
                continue
            if not isinstance(ref.get("publication_id"), str):
                continue
            source = publications.get(ref.get("publication_id"))
            why = ref.get("why")
            if source is None or not isinstance(why, str) or not why.strip():
                continue
            source_id = source["id"]
            identifier = hashlib.sha256(json.dumps([source_id, runs], sort_keys=True).encode()).hexdigest()
            if identifier in seen:
                continue
            reproduction = proposal.get("reproduction_of") or {}
            is_attempt = isinstance(reproduction, dict) and (
                reproduction.get("lab_id"), reproduction.get("campaign_id")) == (
                source["lab_id"], source["campaign_id"])
            row = {"id": identifier, "ts": datetime.now(timezone.utc).isoformat(),
                   "publication_id": source_id, "lab_id": source["lab_id"],
                   "campaign_id": source["campaign_id"], "journal": source["journal"],
                   "domain": source.get("domain"), "source_sha256": hashlib.sha256(source["body"].encode()).hexdigest(),
                   "local_campaign_id": proposal.get("campaign_id"),
                   "student_id": proposal.get("student_id", "primary"),
                   "proposal_name": proposal.get("name"), "run_ids": runs,
                   "use_kind": "replication_attempt" if is_attempt else "method_or_design",
                   "why": why.strip()[:2000], "foundational": bool(ref.get("foundational")),
                   "reproduction_status": reproduction_status(lab_root.parent / "paper",
                       lab_id=source["lab_id"], campaign_id=source["campaign_id"]) or "unverified"}
            _append(log, row)
            notebook_append(lab_root / "lab_notebook.md", (
                f"## {row['ts']} — Journal use: {source_id}\n\n"
                f"Journal: {row['journal']}; proposal: {row['proposal_name']}; "
                f"runs: {', '.join(runs)}; use: {row['use_kind']}; "
                f"replication: {row['reproduction_status']}.\n\n{row['why']}\n"))
            seen.add(identifier)
            count += 1
    return count


def campaign_citations(lab_root: Path, campaign_id: str) -> list[dict]:
    from efferents.agents.conference import _rows
    return [row for row in _rows(lab_root / "journal_uses.jsonl")
            if row.get("local_campaign_id") == campaign_id]


def citation_markdown(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["", "## External journal citations", "",
             "These citations record declared influence on completed runs. Receipt of a paper alone "
             "is not use, and use is not independent corroboration.", ""]
    for row in citations:
        lines.extend([
            f"- Publication `{row['publication_id']}` — {row['journal']}; "
            f"source lab `{row['lab_id']}`, campaign `{row['campaign_id']}`.",
            f"  Use: {row['use_kind']}; runs: {', '.join(row['run_ids'])}; "
            f"replication status at use: {row['reproduction_status']}.",
            f"  Reason: {row['why']}",
            f"  Source snapshot SHA-256: `{row['source_sha256']}`.",
        ])
    return "\n".join(lines) + "\n"
