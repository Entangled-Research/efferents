"""Read-only graph evidence from opted-in local conference ledgers."""
from __future__ import annotations

from pathlib import Path

from efferents.agents.conference import _rows, _talks, exchange_enabled
from efferents.journal.reviews import is_publication
from efferents.lab import LabConfig
from efferents.registry import Registry
from efferents.journals import journal_for_domain


def network_evidence() -> dict:
    findings, observations = {}, []
    for record in Registry().list():
        submission = Path(record.submission_dir).resolve()
        root = Path(record.lab_root).resolve()
        if not root.is_relative_to(submission):
            continue
        try:
            cfg = LabConfig.from_submission(submission, check_paths=False)
            if not exchange_enabled(root, cfg):
                continue
            talks = _talks(cfg, submission, root)
            for talk in talks:
                findings[talk["id"]] = {**talk, "body": talk["body"][:4000]}
            for talk in _rows(root / "conference" / "inbox.jsonl", submission)[-100:]:
                if not is_publication(talk):
                    continue
                observations.append({"source": talk["lab_id"], "target": cfg.lab_id,
                    "kind": "observation", "finding_id": talk["id"],
                    "track": talk.get("track", "field"), "at": talk.get("received_at"),
                    "meaning": "Received into the lab's research inbox; not a replication"})
                findings.setdefault(talk["id"], {**talk, "body": talk["body"][:4000]})
        except (OSError, ValueError):
            continue
    routed = [{**row, "journal": journal_for_domain(row["domain"]),
               "routing_status": "accepted journal publication"}
              for row in list(findings.values())[-150:]]
    return {"findings": routed, "observations": observations[-200:]}
