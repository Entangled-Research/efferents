"""Durable, provenance-bearing context inboxes for ideas in one lab.

An idea receives a source at its next research checkpoint. Receipt, declared
influence on a completed experiment, and replication are separate records.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml

from efferents.agents.state import now_iso, recent_runs
from efferents.journal.provenance import received_publications
from efferents.steer import read_steering


def _id(kind: str, value: str) -> str:
    return hashlib.sha256(f"{kind}:{value}".encode()).hexdigest()


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


@contextmanager
def _lock(lab_root: Path):
    path = lab_root / "context_queue.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _queue_path(lab_root: Path, student_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", student_id):
        raise ValueError("invalid student ID")
    return lab_root / "context_queues" / f"{student_id}.jsonl"


def _sources(lab_root: Path, student_id: str) -> list[dict]:
    sources: list[dict] = []
    # Only successful runs can become research evidence. Bound the prompt feed,
    # while leaving the complete run ledger authoritative for older results.
    try:
        successful_runs = recent_runs(lab_root / "runs.sqlite", n=100)
    except sqlite3.OperationalError:
        successful_runs = []
    for run in reversed(successful_runs):
        run_id = run.get("run_id")
        if not run_id:
            continue
        origin = run.get("student_id") or "primary"
        kind = "own_result" if origin == student_id else "sibling_result"
        metrics = {k: v for k, v in run.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)
                   and k not in {"exit_code", "seed", "duration_seconds", "duplicate_arm_artifacts"}}
        try:
            config = yaml.safe_load(run.get("config_yaml") or "") or {}
            run_name = str(config.get("run", {}).get("name") or "") if isinstance(config, dict) else ""
        except (ValueError, TypeError, yaml.YAMLError):
            run_name = ""
        sources.append({"id": _id("run", str(run_id)), "kind": kind,
                        "source": {"lab": "local", "student_id": origin,
                                   "run_id": str(run_id)},
                        "summary": f"Successful run {run_id} ({run_name[:120]}); measurements: {json.dumps(metrics, sort_keys=True)[:900]}",
                        "measured_at": run.get("started_at")})
    for steer in read_steering(lab_root):
        if not steer.get("text"):
            continue
        fingerprint = json.dumps([steer.get("ts"), steer.get("by"), steer.get("text")])
        sources.append({"id": _id("steering", fingerprint), "kind": "owner_steering",
                        "source": {"by": steer.get("by"), "ts": steer.get("ts")},
                        "summary": str(steer["text"])[:1200]})
    # This reader applies the accepted-publication gate and therefore excludes
    # raw measurements, unreviewed talks, and direct peer messages.
    for publication in received_publications(lab_root).values():
        source_id = publication.get("id")
        if not source_id:
            continue
        sources.append({"id": _id("publication", str(source_id)),
                        "kind": "accepted_publication",
                        "source": {"publication_id": source_id,
                                   "lab_id": publication.get("lab_id"),
                                   "campaign_id": publication.get("campaign_id")},
                        "summary": str(publication.get("body") or publication.get("headline") or "")[:1200]})
    return sources


def _select_context(rows: list[dict], max_items: int) -> list[dict]:
    """Keep a recent, balanced view so one busy idea cannot bury siblings."""
    selected: list[dict] = []
    seen: set[str] = set()
    quotas = {"own_result": 4, "sibling_result": 4,
              "owner_steering": 2, "accepted_publication": 2}
    for kind, quota in quotas.items():
        for row in (r for r in reversed(rows) if r.get("kind") == kind):
            if len([r for r in selected if r.get("kind") == kind]) >= quota:
                break
            if row.get("id") not in seen:
                selected.append(row)
                seen.add(row["id"])
    for row in reversed(rows):
        if len(selected) >= max_items:
            break
        if row.get("id") not in seen:
            selected.append(row)
            seen.add(row["id"])
    return selected[:max_items]


def checkpoint(lab_root: Path, student_id: str, *, max_items: int = 12) -> tuple[str, list[str]]:
    """Deliver newly available context and return a bounded prompt plus IDs.

    Delivery is durable and idempotent. The caller should attach the returned
    IDs to proposals only when the agent explicitly declares influence.
    """
    queue = _queue_path(lab_root, student_id)
    with _lock(lab_root):
        existing = _rows(queue)
        seen = {row.get("id") for row in existing}
        for source in _sources(lab_root, student_id):
            if source["id"] in seen:
                continue
            _append(queue, {**source, "received_at": now_iso()})
            existing.append(source)
            seen.add(source["id"])
        selected = _select_context(existing, max_items)
        _append(lab_root / "context_checkpoints.jsonl",
                {"at": now_iso(), "student_id": student_id,
                 "context_ids": [row["id"] for row in selected]})
    if not selected:
        return "", []
    lines = ["\n\n## This idea's context queue", "Each item records receipt only. Declare a "
             "context ID in `context_uses` on a proposal only if it shaped the proposed "
             "experiment, as `context_uses: [{context_id: <ID>, why: <reason>}]`. "
             "To test a sibling finding independently, declare `replication_of` "
             "with that context ID. A replication attempt is not verification.\n"]
    for item in selected:
        lines.append(f"- [{item['id']}] {item['kind']} source={json.dumps(item['source'], sort_keys=True)}: "
                     f"{item['summary']}")
    return "\n".join(lines), [item["id"] for item in selected]


def record_uses(lab_root: Path, proposal: dict, outcome: dict) -> int:
    """Record declared influence only for successful measured executions."""
    if not outcome.get("ok"):
        return 0
    student_id = proposal.get("student_id") or "primary"
    queue = _queue_path(lab_root, student_id)
    declared = proposal.get("context_uses")
    if not isinstance(declared, list):
        return 0
    run_ids = [str(row["run_id"]) for row in outcome.get("rows", [])
               if isinstance(row, dict) and row.get("run_id")]
    if not run_ids:
        return 0
    with _lock(lab_root):
        known = {row.get("id"): row for row in _rows(queue)}
        log = lab_root / "context_uses.jsonl"
        seen = {row.get("id") for row in _rows(log)}
        count = 0
        for item in declared:
            if not isinstance(item, dict) or item.get("context_id") not in known:
                continue
            context_id = item["context_id"]
            why = item.get("why")
            if not isinstance(why, str) or not why.strip():
                continue
            source = known[context_id]
            replication = proposal.get("replication_of") == context_id
            identifier = _id("use", json.dumps([context_id, run_ids], sort_keys=True))
            if identifier in seen:
                continue
            _append(log, {"id": identifier, "at": now_iso(), "student_id": student_id,
                          "context_id": context_id, "source": source.get("source"),
                          "source_kind": source.get("kind"), "run_ids": run_ids,
                          "use_kind": "replication_attempt" if replication else "influence",
                          "why": why.strip()[:1000]})
            seen.add(identifier)
            count += 1
    return count
