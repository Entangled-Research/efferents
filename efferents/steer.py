"""Owner steering: auditable redirects, pauses, and hypothesis supersession.

The funder of a lab may redirect it mid-run without editing evidence. Every
steering act leaves two traces:

  * a verbatim, append-only entry in the lab charter (``context/popper.md``,
    via :func:`efferents.agents.popper_gate.write_charter`), which the
    Researcher already reads as guidance; and
  * a queued record in ``<lab_root>/steering.jsonl`` that the daemon
    acknowledges on its next step (notebook line, ``state.json["steering"]``,
    and — for ``pause`` / ``resume`` / ``supersede`` — the matching halt or
    campaign bookkeeping).

Nothing here rewrites ``hypothesis.md`` bodies, the run ledger, or earlier
charter entries. Supersession only adds a ``superseded_by`` frontmatter key
to the retired corpus file and installs the successor as the submission's
current hypothesis.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from efferents import lab as _lab
from efferents.agents.popper_gate import write_charter
from efferents.agents.state import (
    campaign_insert,
    campaign_open_list,
    load_state,
    notebook_append,
    now_iso,
    save_state,
)
from efferents.lab import SubmissionError, _parse_hypothesis

STEERING_FILENAME = "steering.jsonl"
HALT_KIND = "owner"
_ACTIONS = ("pause", "resume", "supersede")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


class SteeringError(ValueError):
    """Raised when a steering request cannot be honoured safely."""


# --- steering ledger ---------------------------------------------------------

def steering_path(lab_root: str | Path) -> Path:
    return Path(lab_root) / STEERING_FILENAME


def read_steering(lab_root: str | Path) -> list[dict[str, Any]]:
    path = steering_path(lab_root)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a corrupt line must not wedge the daemon
        if isinstance(rec, dict):
            out.append(rec)
    return out


def pending_steering(lab_root: str | Path) -> list[dict[str, Any]]:
    return [r for r in read_steering(lab_root) if r.get("ack") is None]


def record_steering(
    lab_root: str | Path,
    *,
    text: str,
    by: str,
    action: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Append one queued steering record and return it."""
    if action is not None and action not in _ACTIONS:
        raise SteeringError(f"unknown steering action {action!r}")
    rec: dict[str, Any] = {"ts": now_iso(), "by": by, "text": text}
    if action is not None:
        rec["action"] = action
    rec.update(extra)
    rec["ack"] = None
    path = steering_path(lab_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def _ack(lab_root: str | Path, ts_values: set[str]) -> None:
    """Set ``ack`` on the named records. Only the ack field changes."""
    records = read_steering(lab_root)
    stamp = now_iso()
    for rec in records:
        if rec.get("ts") in ts_values and rec.get("ack") is None:
            rec["ack"] = stamp
    path = steering_path(lab_root)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in records))
    os.replace(tmp, path)


# --- owner-facing entry points (CLI) -----------------------------------------

def _lab_root_for(submission_dir: Path, lab_root: str | Path | None) -> Path:
    return Path(lab_root).resolve() if lab_root else (submission_dir / "lab").resolve()


def steer(
    submission_dir: str | Path,
    *,
    text: str,
    by: str = "lab owner",
    action: str | None = None,
    lab_root: str | Path | None = None,
) -> tuple[Path, Path]:
    """Record an owner redirect (optionally a pause/resume).

    Returns ``(charter_path, steering_path)``.
    """
    sub = Path(submission_dir).resolve()
    text = text.strip()
    if not text:
        raise SteeringError("steering text must not be empty")
    root = _lab_root_for(sub, lab_root)
    title = "owner steering" + (f": {action}" if action else "")
    charter = write_charter(
        sub / "context", initial_direction=text, prompted_by=by, title=title,
    )
    record_steering(root, text=text, by=by, action=action)
    return charter, steering_path(root)


def _hash_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def _markdown_section(text: str, heading: str) -> str:
    wanted = f"## {heading}".lower()
    out: list[str] = []
    capture = False
    for line in text.splitlines():
        if line.startswith("## "):
            if capture:
                break
            capture = line.strip().lower() == wanted
            continue
        if capture:
            out.append(line)
    return "\n".join(out).strip()


def hypothesis_question(text: str, fallback: str) -> str:
    return (
        _markdown_section(text, "Claim")
        or _markdown_section(text, "Operational restatement")
        or fallback
    )


def _corpus_file(submission_dir: Path, slug: str) -> Path | None:
    """The corpus copy of ``slug``: popper-corpus/<slug>/ or a per-student
    subdir. None when the corpus does not hold it."""
    corpus = submission_dir / "popper-corpus"
    direct = corpus / slug / "hypothesis.md"
    if direct.is_file():
        return direct
    if corpus.is_dir():
        for cand in sorted(corpus.glob(f"*/{slug}/hypothesis.md")):
            return cand
    return None


def mark_superseded(path: Path, successor: str) -> None:
    """Add ``superseded_by: <successor>`` to the frontmatter of ``path``.

    Frontmatter only; the body is left byte-for-byte intact. Idempotent when
    the file already names the same successor; refuses to overwrite a
    different one (that would erase history).
    """
    text = path.read_text()
    m = _FRONTMATTER_RE.match(text)
    if m is None:
        raise SteeringError(f"{path} has no YAML frontmatter to mark")
    fm = m.group(1)
    existing = re.search(r"^superseded_by:\s*(.+?)\s*$", fm, re.MULTILINE)
    if existing is not None:
        if existing.group(1).strip("'\"") == successor:
            return
        raise SteeringError(
            f"{path} is already superseded_by={existing.group(1)!r}; refusing to overwrite"
        )
    new_fm = fm + f"\nsuperseded_by: {successor}"
    path.write_text(text[: m.start(1)] + new_fm + text[m.end(1):])


def supersede(
    submission_dir: str | Path,
    new_hypothesis: str | Path,
    *,
    by: str = "lab owner",
    note: str = "",
    lab_root: str | Path | None = None,
) -> dict[str, Any]:
    """Retire the submission's current hypothesis in favour of a gated successor.

    Steps: validate the successor (gate passed, ``supersedes`` names the
    current slug), mark the retired corpus file ``superseded_by``, install
    the successor as ``<submission>/hypothesis.md``, append a charter entry
    carrying both hashes, and queue a ``supersede`` steering record.
    """
    sub = Path(submission_dir).resolve()
    new_path = Path(new_hypothesis).resolve()
    current_path = sub / "hypothesis.md"
    root = _lab_root_for(sub, lab_root)

    try:
        old_fm = _parse_hypothesis(current_path)
    except SubmissionError as e:
        raise SteeringError(f"current hypothesis is not runnable: {e}") from e
    old_slug = old_fm.get("slug")
    if not old_slug:
        raise SteeringError(f"{current_path} has no slug; cannot be superseded by name")
    if not new_path.is_file():
        raise SteeringError(f"new hypothesis not found: {new_path}")
    if new_path == current_path.resolve():
        raise SteeringError("the new hypothesis must be a different file from the current one")
    try:
        new_fm = _parse_hypothesis(new_path)
    except SubmissionError as e:
        raise SteeringError(f"new hypothesis rejected: {e}") from e
    new_slug = new_fm.get("slug")
    if not new_slug or new_slug == old_slug:
        raise SteeringError(
            f"new hypothesis must carry its own slug (current is {old_slug!r})"
        )
    if new_fm.get("supersedes") != old_slug:
        raise SteeringError(
            f"new hypothesis has supersedes={new_fm.get('supersedes')!r}; "
            f"expected the current slug {old_slug!r}"
        )

    old_text = current_path.read_text()
    new_text = new_path.read_text()
    old_hash, new_hash = _hash_text(old_text), _hash_text(new_text)
    if old_hash == new_hash:
        raise SteeringError("new hypothesis is byte-identical to the current one")

    corpus_old = _corpus_file(sub, old_slug)
    if corpus_old is not None:
        mark_superseded(corpus_old, new_slug)
    shutil.copy2(new_path, current_path)
    if root.is_dir():
        # Provenance copy `efferents start` makes; keep `serve` consistent.
        shutil.copy2(new_path, root / "hypothesis.md")

    try:
        hyp_rel = str(current_path.relative_to(sub))
    except ValueError:
        hyp_rel = str(current_path)
    direction = note.strip() or f"Supersede `{old_slug}` with `{new_slug}`."
    notes = [
        f"Retired: `{old_slug}` ({old_hash})"
        + (f", corpus copy `{corpus_old.relative_to(sub)}` marked superseded_by."
           if corpus_old is not None else "; no corpus copy found to mark."),
        f"Successor: `{new_slug}` ({new_hash}) from `{new_path}`.",
        "Evidence gathered under the retired hypothesis stays in the ledger; "
        "its open campaign continues until it closes on its own terms.",
    ]
    charter = write_charter(
        sub / "context",
        initial_direction=direction,
        prompted_by=by,
        hypothesis_path=hyp_rel,
        hypothesis_hash=new_hash,
        design_notes="\n".join(notes),
        title=f"owner supersession: {old_slug} -> {new_slug}",
    )
    rec = record_steering(
        root,
        text=direction,
        by=by,
        action="supersede",
        old_slug=old_slug,
        new_slug=new_slug,
        old_hash=old_hash,
        new_hash=new_hash,
        hypothesis_path=hyp_rel,
        question=hypothesis_question(new_text, f"Superseding hypothesis {new_slug}"),
    )
    return {
        "charter": charter,
        "steering": steering_path(root),
        "old_slug": old_slug,
        "new_slug": new_slug,
        "old_hash": old_hash,
        "new_hash": new_hash,
        "corpus_marked": corpus_old,
        "record": rec,
    }


# --- daemon side -------------------------------------------------------------

def owner_paused(lab_root: str | Path) -> str | None:
    """The owner's halt reason when the lab is paused by steering, else None."""
    state = load_state(Path(lab_root) / "state.json")
    reason = str(state.get("halt_reason") or "")
    if state.get("status") == "paused" and reason.startswith(f"{HALT_KIND}:"):
        return reason
    return None


def _open_successor_campaign(orch: Any, rec: dict[str, Any]) -> str | None:
    """Open the successor's campaign on its hash unless one already exists.
    The retired hypothesis's campaign is left open — it closes on its own."""
    new_hash = rec.get("new_hash") or ""
    digest = new_hash.split(":", 1)[-1]
    if not digest:
        return None
    cid = f"submission-{digest[:12]}"
    db = orch.paths.runs_db
    if any(c.get("hypothesis_hash") == new_hash for c in campaign_open_list(db, _lab.LAB_ID)):
        return None
    headline_metric = headline_direction = None
    try:
        headline = _lab.get_config().metrics.headline
        headline_metric, headline_direction = headline.column, headline.direction
    except RuntimeError:
        pass
    try:
        campaign_insert(
            db,
            id=cid,
            lab_id=_lab.LAB_ID,
            question=str(rec.get("question") or f"Superseding hypothesis {rec.get('new_slug')}"),
            hypothesis_path=str(rec.get("hypothesis_path") or "hypothesis.md"),
            hypothesis_hash=new_hash,
            student_id=_lab.DEFAULT_STUDENT_ID,
            headline_metric=headline_metric,
            headline_direction=headline_direction,
        )
    except Exception as e:  # e.g. id already present from an earlier ack attempt
        notebook_append(
            orch.paths.notebook,
            f"## {now_iso()} — could not open successor campaign {cid}: {type(e).__name__}: {e}\n",
        )
        return None
    return cid


def apply_pending(orch: Any) -> list[dict[str, Any]]:
    """Acknowledge queued steering records against a running orchestrator.

    ``orch`` needs ``paths`` (LabPaths), ``_halt(kind, reason)`` and
    ``_resume(note)``. Each record gets a notebook line and becomes
    ``state.json["steering"]``; ``pause``/``resume`` toggle the same halt
    machinery budget exhaustion uses (kind ``owner``); ``supersede`` opens the
    successor's campaign. Returns the records processed.
    """
    root = orch.paths.root
    pending = pending_steering(root)
    if not pending:
        return []
    for rec in pending:
        action = rec.get("action")
        text = str(rec.get("text") or "").strip()
        by = rec.get("by") or "lab owner"
        label = f"owner steering ({action})" if action else "owner steering"
        notebook_append(orch.paths.notebook, f"## {rec.get('ts')} — {label}: {text}\n")
        if action == "pause":
            orch._halt(HALT_KIND, f"{by}: {text}")
        elif action == "resume":
            if owner_paused(root):
                orch._resume(f"owner steering by {by}: {text}")
        elif action == "supersede":
            cid = _open_successor_campaign(orch, rec)
            if cid:
                notebook_append(
                    orch.paths.notebook,
                    f"## {now_iso()} — opened campaign {cid} for superseding hypothesis "
                    f"{rec.get('new_slug')!r} hash={str(rec.get('new_hash'))[:14]}...\n",
                )
        state = load_state(orch.paths.state)
        state["steering"] = {
            k: rec.get(k) for k in ("ts", "by", "text", "action") if rec.get(k) is not None
        }
        save_state(orch.paths.state, state)
    _ack(root, {r["ts"] for r in pending if r.get("ts")})
    return pending


def step_hook(orch: Any, *, idle_seconds: float = 60.0) -> bool:
    """Orchestrator step prologue. Applies pending steering; returns True when
    the lab is paused by its owner (the caller should skip the step)."""
    apply_pending(orch)
    reason = owner_paused(orch.paths.root)
    if reason is None:
        return False
    halt_file = orch.paths.root / "halt_reason.txt"
    if not halt_file.exists():  # `efferents start` clears it; keep status honest
        halt_file.write_text(reason + "\n")
    orch._interruptible_sleep(idle_seconds)
    return True
