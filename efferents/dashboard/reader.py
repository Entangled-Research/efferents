"""Read a lab directory into plain dicts for the dashboard endpoints.

Read-only. Tolerant of missing files (a stopped or just-initialized lab still
renders). No HTTP knowledge — pure file/db reads, so it is testable without a
socket.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from efferents import daemon
from efferents import evidence
from efferents import lab as lab_mod
from efferents.agents import state as state_mod
from efferents.journal.feed import render_feed
from efferents import metrics_view

if TYPE_CHECKING:
    from efferents.lab import LabConfig

_ACTIVITY_BODY_PREVIEW = 300
_EVIDENCE_RUN_LIMIT = 120
ARTIFACT_CONTENT_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def read_state(lab_root: Path, cfg: "LabConfig | None" = None) -> dict:
    lab_root = Path(lab_root)
    cfg = cfg or lab_mod.get_config()
    pid = daemon.read_pidfile(lab_root / "daemon.pid")
    running = pid is not None and daemon.is_pid_alive(pid)
    return {
        "lab_id": cfg.lab_id,
        "domain": cfg.domain,
        "status": "running" if running else "stopped",
        "budget": {
            "spent": _budget_spent(lab_root / "budget.jsonl"),
            "cap": cfg.budget.daily_cap_usd,
        },
        "hypothesis": _current_hypothesis(lab_root, cfg.lab_id),
    }


def read_runs(
    lab_root: Path, n: int = 30, cfg: "LabConfig | None" = None
) -> dict:
    lab_root = Path(lab_root)
    cfg = cfg or lab_mod.get_config()
    column = cfg.metrics.headline.column
    direction = cfg.metrics.headline.direction
    def _finite(x):
        return x if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None

    db = lab_root / "runs.sqlite"
    rows = state_mod.recent_runs(db, n) if db.exists() else []
    runs = []
    for row in rows:
        failures = metrics_view.constraint_failures(row, cfg=cfg)
        runs.append({
            "run_id": row.get("run_id"),
            "started_at": row.get("started_at"),
            "value": _finite(row.get(column)),
            "eligible": not failures,
            "constraint_failures": failures,
        })
    series = [
        {"started_at": r["started_at"], "value": r["value"]}
        for r in reversed(runs)
        if (
            r["value"] is not None
            and r["started_at"] is not None
            and r["eligible"]
        )
    ]
    best_row = metrics_view.best_run_from_db(db, cfg=cfg)
    history = {
        "total": state_mod.runs_count(db),
        "best": (
            metrics_view.finite(best_row.get(column))
            if best_row is not None
            else None
        ),
        "best_run_id": best_row.get("run_id") if best_row is not None else None,
    }
    return {"headline": {"column": column, "direction": direction},
            "runs": runs, "series": series, "history": history}


def _json_list(value) -> list:
    if isinstance(value, list):
        return value
    if not isinstance(value, str) or not value.strip():
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _artifact_path(
    value: object, lab_root: Path, cfg: "LabConfig"
) -> Path | None:
    """Resolve an artifact record (or bare path) to a readable image file.

    A record prefers its per-run copy (``path``) and falls back to the
    executor's original ``source_path`` for ledgers written before copies
    were kept.
    """
    if isinstance(value, dict):
        for key in ("path", "source_path"):
            found = _artifact_path(value.get(key), lab_root, cfg)
            if found is not None:
                return found
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = Path(value).expanduser()
    roots = (Path(lab_root).resolve(), Path(cfg.source.dir).resolve())
    candidates = (raw,) if raw.is_absolute() else tuple(root / raw for root in roots)
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_file()
            and resolved.suffix.lower() in ARTIFACT_CONTENT_TYPES
            and any(resolved == root or root in resolved.parents for root in roots)
        ):
            return resolved
    return None


def _artifact_token(run_id: str, path: Path) -> str:
    payload = f"{run_id}\0{path}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _panel_specs(cfg: "LabConfig") -> list[dict]:
    configured = {panel.column: panel for panel in cfg.metrics.panels}
    columns = [cfg.metrics.headline.column, *configured]
    specs = []
    seen = set()
    for column in columns:
        if column in seen:
            continue
        seen.add(column)
        panel = configured.get(column)
        specs.append({
            "column": column,
            "label": panel.label if panel else column,
            "direction": panel.direction if panel else cfg.metrics.headline.direction,
            "target": panel.target if panel else None,
        })
    return specs


def _json_dict(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _deployment_evidence(
    lab_root: Path,
    cfg: "LabConfig",
    panels: list[dict],
    catalog: dict[str, Path],
) -> list[dict]:
    root = Path(lab_root) / "deployments"
    if not root.is_dir():
        return []
    images = sorted(
        (
            path for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in ARTIFACT_CONTENT_TYPES
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[:12]
    records = []
    for image in images:
        relative = image.relative_to(root)
        deployment_dir = root / relative.parts[0]
        manifest = _json_dict(deployment_dir / "manifest.json")
        metrics_doc = _json_dict(image.parent / "metrics.json")
        if not metrics_doc:
            metrics_paths = list(deployment_dir.rglob("metrics.json"))
            metrics_doc = _json_dict(metrics_paths[0]) if metrics_paths else {}
        merged_metrics = {}
        if isinstance(manifest.get("metrics"), dict):
            merged_metrics.update(manifest["metrics"])
        if isinstance(metrics_doc.get("metrics"), dict):
            merged_metrics.update(metrics_doc["metrics"])
        metrics = {
            panel["column"]: metrics_view.finite(merged_metrics.get(panel["column"]))
            for panel in panels
        }
        failures = metrics_view.constraint_failures(merged_metrics, cfg=cfg)
        run_id = str(manifest.get("source_run_id") or f"deployment:{relative}")
        token = _artifact_token(f"{run_id}:{relative}", image.resolve())
        catalog[token] = image.resolve()
        dimensions = {}
        for key, value in manifest.items():
            if (
                len(dimensions) < 6
                and isinstance(value, (str, int, float, bool))
                and key not in {
                    "contract_version", "lab_id", "promoted_at", "source_run_id"
                }
                and "path" not in key
                and "command" not in key
                and len(str(value)) <= 48
            ):
                dimensions[key] = value
        records.append({
            "run_id": run_id,
            "started_at": manifest.get("promoted_at"),
            "name": " / ".join(relative.with_suffix("").parts),
            "eligible": not failures,
            "constraint_failures": failures,
            "dimensions": dimensions,
            "metrics": metrics,
            "artifacts": [{
                "kind": "deployment_image",
                "token": token,
                "url": f"/api/artifacts/{token}",
            }],
        })
    return records


def _evidence_payload(
    lab_root: Path, cfg: "LabConfig"
) -> tuple[dict, dict[str, Path]]:
    lab_root = Path(lab_root)
    rows = _ledger_rows(lab_root)
    panels = _panel_specs(cfg)
    catalog: dict[str, Path] = {}
    records: list[dict] = []

    for row in rows:
        run_id = str(row.get("run_id") or "unnamed-run")
        observations = [
            item for item in _json_list(row.get("observations_json"))
            if isinstance(item, dict)
        ]
        if not observations and _json_list(row.get("artifacts_json")):
            observations = [{
                "name": run_id,
                "dimensions": {},
                "metrics": {},
                "artifacts": _json_list(row.get("artifacts_json")),
            }]

        failures = metrics_view.constraint_failures(row, cfg=cfg)
        for observation in observations:
            dimensions = observation.get("dimensions")
            dimensions = dimensions if isinstance(dimensions, dict) else {}
            observed_metrics = observation.get("metrics")
            observed_metrics = observed_metrics if isinstance(observed_metrics, dict) else {}
            metrics = {
                panel["column"]: metrics_view.finite(
                    observed_metrics.get(panel["column"], row.get(panel["column"]))
                )
                for panel in panels
            }
            artifacts = observation.get("artifacts")
            artifacts = artifacts if isinstance(artifacts, list) else []
            if not artifacts:
                artifacts = _json_list(row.get("artifacts_json"))

            visual_artifacts = []
            seen_paths: set[Path] = set()
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                path = _artifact_path(artifact, lab_root, cfg)
                if path is None or path in seen_paths:
                    continue
                seen_paths.add(path)
                token = _artifact_token(run_id, path)
                catalog[token] = path
                visual_artifacts.append({
                    "kind": str(artifact.get("kind") or "image"),
                    "token": token,
                    "url": f"/api/artifacts/{token}",
                })

            if visual_artifacts:
                records.append({
                    "run_id": run_id,
                    "started_at": row.get("started_at"),
                    "name": str(observation.get("name") or "visual evidence"),
                    "eligible": not failures,
                    "constraint_failures": failures,
                    "dimensions": dimensions,
                    "metrics": metrics,
                    "artifacts": visual_artifacts,
                })

    constraints = [{
        "column": constraint.column,
        "op": constraint.op,
        "value": constraint.value,
        "label": constraint.label or constraint.column,
    } for constraint in cfg.metrics.constraints]
    records = _deployment_evidence(lab_root, cfg, panels, catalog) + records
    return ({
        "panels": panels,
        "constraints": constraints,
        "comparison": {
            "axis": cfg.evidence.comparison_axis,
            "labels": dict(cfg.evidence.comparison_labels),
            "order": list(cfg.evidence.comparison_order),
        },
        "records": records,
        "artifact_count": sum(len(record["artifacts"]) for record in records),
    }, catalog)


def read_evidence(lab_root: Path, cfg: "LabConfig | None" = None) -> dict:
    """Return lab-declared visual observations without assuming a domain."""
    cfg = cfg or lab_mod.get_config()
    return _evidence_payload(Path(lab_root), cfg)[0]


def _ledger_rows(lab_root: Path) -> list[dict]:
    """Succeeded runs, newest first, bounded like the evidence surface."""
    db = Path(lab_root) / "runs.sqlite"
    return state_mod.recent_runs(db, _EVIDENCE_RUN_LIMIT) if db.exists() else []


def _rule_text(rule) -> str:
    if rule.kind == "paired":
        name = rule.column or f"\u0394{rule.metric}"
        wanted = "excludes" if rule.ci95_excludes_zero else "includes"
        return f"95% CI of median({name}) {wanted} zero"
    arg = rule.column if rule.threshold is None else f"{rule.column}, {rule.threshold:g}"
    return f"{rule.agg}({arg}) {rule.op} {rule.value:g}"


def _falsifier_results(rows: list[dict], cfg: "LabConfig") -> list[dict]:
    rules = {rule.id: rule for rule in cfg.falsifiers}
    results = []
    for result in evidence.evaluate_falsifiers(rows, cfg):
        rule = rules[result["id"]]
        results.append({
            **result,
            "kind": rule.kind,
            "bucket": str(rule.bucket),
            "min_n": rule.min_n,
            "rule": _rule_text(rule),
        })
    return results


_SHORT_STATUS = {"insufficient_data": "insufficient"}


def _verdict_line(status: str, falsifiers: list[dict]) -> str:
    if not falsifiers:
        return f"verdict: {status} · no falsifiers"
    notes = [
        f"{f['id']} {_SHORT_STATUS.get(f['status'], f['status'])}"
        for f in falsifiers if f["status"] != "survived"
    ]
    return " · ".join([f"verdict: {status}", *notes])


def _verdict(rows: list[dict], cfg: "LabConfig") -> tuple[str, list[dict]]:
    falsifiers = _falsifier_results(rows, cfg)
    status = evidence.verdict(falsifiers) if falsifiers else "undecided"
    return status, falsifiers


def _bucket_entries(rows: list[dict], cfg: "LabConfig") -> tuple[list[dict], list[dict]]:
    buckets: list[dict] = []
    paired: list[dict] = []
    for label, entry in evidence.bucket_summary(rows, cfg).items():
        entry_paired = {
            metric: {**stats, "ci95": list(stats["ci95"]) if stats["ci95"] else None,
                     "arms": list(stats["arms"])}
            for metric, stats in entry["paired"].items()
        }
        buckets.append({
            "label": label,
            "key": list(entry["key"]),
            "n": entry["n"],
            "columns": entry["columns"],
            "arms": entry["arms"],
            "paired": entry_paired,
        })
        paired.extend(
            {"bucket": label, "metric": metric, **stats}
            for metric, stats in entry_paired.items()
        )
    return buckets, paired


def read_verdict(lab_root: Path, cfg: "LabConfig | None" = None) -> dict:
    """Deterministic evidence over succeeded runs: per-bucket aggregates,
    seed-paired deltas, every declared falsifier, and the resulting verdict."""
    cfg = cfg or lab_mod.get_config()
    rows = _ledger_rows(lab_root)
    status, falsifiers = _verdict(rows, cfg)
    buckets, paired = _bucket_entries(rows, cfg)
    return {
        "verdict": status,
        "line": _verdict_line(status, falsifiers),
        "n_runs": len(rows),
        "axes": list(cfg.metrics.bucket_axes),
        "comparison": {
            "axis": cfg.evidence.comparison_axis,
            "labels": dict(cfg.evidence.comparison_labels),
        },
        "columns": [
            {"column": panel["column"], "label": panel["label"]}
            for panel in _panel_specs(cfg)
        ],
        "buckets": buckets,
        "paired": paired,
        "falsifiers": falsifiers,
    }


def resolve_artifact(
    lab_root: Path, token: str, cfg: "LabConfig | None" = None
) -> Path | None:
    """Resolve an opaque artifact token from the selected lab's evidence set."""
    cfg = cfg or lab_mod.get_config()
    return _evidence_payload(Path(lab_root), cfg)[1].get(token)


def read_papers(lab_root: Path) -> list[dict]:
    lab_root = Path(lab_root)
    paths: list[Path] = []
    seen: set[str] = set()
    for name in ("paper", "papers"):  # writer uses 'paper'; CLI pre-creates 'papers'
        d = lab_root / name
        if d.exists():
            for p in sorted(d.glob("*.md")):
                if p.name not in seen:
                    seen.add(p.name)
                    paths.append(p)
    return [c.model_dump() for c in render_feed(paths)]


def read_activity(lab_root: Path, n: int = 20) -> list[dict]:
    nb = Path(lab_root) / "lab_notebook.md"
    if not nb.exists():
        return []
    text = nb.read_text()
    entries: list[dict] = []
    for block in text.split("\n## "):
        block = block.lstrip("# ").rstrip()
        if not block:
            continue
        head, _, body = block.partition("\n")
        timestamp, sep, title = head.partition(" — ")
        if not sep:
            continue
        entries.append({
            "timestamp": timestamp.strip(),
            "title": title.strip(),
            "body": body.strip()[:_ACTIVITY_BODY_PREVIEW],
        })
    entries.reverse()
    return entries[:n]


def read_summary(lab_root: Path, cfg: "LabConfig") -> dict:
    """Return the compact, evidence-backed state used by the lab portfolio rail."""
    state = read_state(lab_root, cfg=cfg)
    run_data = read_runs(lab_root, n=60, cfg=cfg)
    papers = read_papers(lab_root)
    activity = read_activity(lab_root, n=1)
    best_row = metrics_view.best_run_from_db(lab_root / "runs.sqlite", cfg=cfg)
    best = (
        metrics_view.finite(best_row.get(cfg.metrics.headline.column))
        if best_row is not None
        else None
    )
    latest_run = run_data["runs"][0] if run_data["runs"] else {}
    last_activity = (
        activity[0].get("timestamp")
        if activity
        else latest_run.get("started_at")
    )
    verdict, falsifiers = _verdict(_ledger_rows(lab_root), cfg)
    return {
        "status": state["status"],
        "budget": state["budget"],
        "headline": {
            **run_data["headline"],
            "best": best,
            "latest": latest_run.get("value"),
            "observations": state_mod.runs_count(lab_root / "runs.sqlite"),
        },
        "papers": len(papers),
        "last_activity": last_activity,
        "hypothesis": state["hypothesis"],
        "verdict": {"status": verdict, "line": _verdict_line(verdict, falsifiers)},
    }


def _budget_spent(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = 0.0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total += float(json.loads(line).get("cost_usd", 0.0))
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return total


def _current_hypothesis(lab_root: Path, lab_id: str) -> dict:
    question = ""
    student = ""
    db = lab_root / "runs.sqlite"
    if db.exists():
        try:
            campaigns = state_mod.campaign_open_list(db, lab_id)
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            campaigns = []
        if campaigns:
            latest = max(campaigns, key=lambda c: c.get("opened_at", ""))
            question = latest.get("question", "") or ""
            student = latest.get("student_id", "") or ""
    hyp_md = lab_root / "hypothesis.md"
    claim = falsifier = ""
    if hyp_md.exists():
        text = hyp_md.read_text()
        claim = _section(text, "Claim") or _section(text, "Operational restatement")
        falsifier = _section(text, "Falsifier") or _section(text, "Falsifier(s)")
    return {"question": question, "claim": claim,
            "falsifier": falsifier, "student": student}


def _section(markdown: str, name: str) -> str:
    """Return the text under a `## {name}` heading, up to the next `## ` heading."""
    lines = markdown.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("## "):
            if capturing:
                break
            capturing = line.strip()[3:].strip().lower() == name.lower()
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()
