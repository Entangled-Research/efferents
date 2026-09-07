"""Shared file/DB I/O for the agent loop.

Layout under lab/:
    runs.sqlite       per-run results (schema defined by the lab's executor)
    queue.jsonl       Researcher -> Executor handoff (one JSON proposal per line)
    lab_notebook.md   running narrative, agent-only writes, append-only
    digests/          Analyst summaries, one timestamped markdown per write
    budget.jsonl      per-call spend records (one JSON per line)
    state.json        misc orchestrator state (last_digest_at, last_run_id, ...)
    knowledge/        librarian-managed lit-review cache
        kb.sqlite     topics + papers tables (see agents/librarian.py)
    librarian_log.jsonl  per-call librarian record (parallel to coder_log.jsonl)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LabPaths:
    root: Path
    runs_db: Path
    queue: Path
    inflight: Path
    notebook: Path
    digests_dir: Path
    budget: Path
    state: Path
    knowledge_dir: Path
    kb_db: Path
    librarian_log: Path


def lab_paths(root: str | Path = "lab") -> LabPaths:
    r = Path(root)
    return LabPaths(
        root=r,
        runs_db=r / "runs.sqlite",
        queue=r / "queue.jsonl",
        inflight=r / "inflight.json",
        notebook=r / "lab_notebook.md",
        digests_dir=r / "digests",
        budget=r / "budget.jsonl",
        state=r / "state.json",
        knowledge_dir=r / "knowledge",
        kb_db=r / "knowledge" / "kb.sqlite",
        librarian_log=r / "librarian_log.jsonl",
    )


def init_lab(paths: LabPaths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.digests_dir.mkdir(parents=True, exist_ok=True)
    paths.knowledge_dir.mkdir(parents=True, exist_ok=True)
    if not paths.notebook.exists():
        paths.notebook.write_text(
            "# Lab notebook\n\n"
            "Agent-only, append-only running narrative. "
            "Each run appends one entry; each digest appends a pointer.\n\n"
            f"Initialized {datetime.now(timezone.utc).isoformat()}.\n\n"
        )
    if not paths.queue.exists():
        paths.queue.touch()
    if paths.inflight.exists():
        # Recover a proposal claimed before an abrupt process exit. Put it
        # back at the front before the loop resumes.
        claimed = paths.inflight.read_text().strip()
        if claimed:
            queued = paths.queue.read_text()
            _atomic_write_text(
                paths.queue,
                claimed + "\n" + queued,
            )
        paths.inflight.unlink()
    if not paths.budget.exists():
        paths.budget.touch()
    if not paths.state.exists():
        paths.state.write_text(json.dumps({}))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def recent_runs(db_path: Path, n: int = 30) -> list[dict[str, Any]]:
    # SELECT * lets the schema be lab-defined (per LabConfig.metrics + Phase A's
    # base meta columns). Downstream consumers use r.get(...) for lab-specific
    # columns and tolerate absence — see researcher.py:_saturation_report.
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        where = "WHERE status = 'succeeded'" if "status" in cols else ""
        rows = conn.execute(
            f"SELECT * FROM runs {where} ORDER BY started_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def runs_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        where = " WHERE status = 'succeeded'" if "status" in cols else ""
        return int(conn.execute(f"SELECT COUNT(*) FROM runs{where}").fetchone()[0])
    finally:
        conn.close()


def notebook_tail(path: Path, max_chars: int = 8000) -> str:
    if not path.exists():
        return ""
    text = path.read_text()
    if len(text) <= max_chars:
        return text
    return "...[truncated head]...\n" + text[-max_chars:]


def notebook_append(path: Path, entry: str) -> None:
    with path.open("a") as f:
        f.write(entry.rstrip() + "\n\n")


def queue_push(path: Path, proposal: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(proposal) + "\n")


def queue_pop(path: Path) -> dict[str, Any] | None:
    """Claim the first line and persist it in ``inflight.json``.

    The caller must call ``queue_ack`` after recording an outcome or
    ``queue_requeue_inflight`` when execution raises.
    """
    if not path.exists():
        return None
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    if not lines:
        return None
    head, rest = lines[0], lines[1:]
    proposal = json.loads(head)
    inflight = path.with_name("inflight.json")
    _atomic_write_text(inflight, json.dumps(proposal) + "\n")
    _atomic_write_text(path, "\n".join(rest) + ("\n" if rest else ""))
    return proposal


def queue_ack(path: Path) -> None:
    inflight = path.with_name("inflight.json")
    try:
        inflight.unlink()
    except FileNotFoundError:
        pass


def queue_requeue_inflight(path: Path) -> None:
    inflight = path.with_name("inflight.json")
    if not inflight.exists():
        return
    claimed = inflight.read_text().strip()
    if claimed:
        _atomic_write_text(path, claimed + "\n" + path.read_text())
    inflight.unlink()


def queue_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for ln in path.read_text().splitlines() if ln.strip())


# ---------- Proposal deduplication ----------
#
# Students in one lab re-propose configurations that already ran. The ledger
# keys each run by ``config_hash`` (exec._persist_run_result), so a proposal
# can be checked before it reaches the queue by rendering it exactly the way
# the Executor will and hashing the same YAML.


def load_proposal_base_config() -> dict[str, Any] | None:
    """The active lab's config template as a dict, or None when no LabConfig
    is active or the template cannot be read (dedup is then skipped)."""
    from efferents import lab as _lab  # noqa: PLC0415 - lab imports state lazily
    from efferents.agents.executor import load_default_config  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    try:
        template = _lab.get_config().executor.config_template
    except RuntimeError:
        return None
    try:
        return load_default_config(template) or {}
    except (OSError, yaml.YAMLError):
        return None


def render_proposal_config(
    proposal: dict[str, Any], *, base_config: dict[str, Any] | None = None
) -> str | None:
    """The YAML the Executor would write for ``proposal`` — same override
    composition, ``run.name`` stamp and sorted dump as ``executor.execute``.
    Returns None when the base config is unavailable."""
    from efferents.agents.executor import apply_overrides  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    if base_config is None:
        base_config = load_proposal_base_config()
        if base_config is None:
            return None
    overrides = dict(proposal.get("config_overrides", {}) or {})
    if proposal.get("campaign_id"):
        overrides["run.campaign_id"] = proposal["campaign_id"]
    if proposal.get("mode"):
        overrides["run.researcher_mode"] = proposal["mode"]
    if proposal.get("student_id"):
        overrides["run.student_id"] = proposal["student_id"]
    rendered = apply_overrides(base_config, overrides)
    rendered.setdefault("run", {})["name"] = proposal.get("name", "unnamed")
    return yaml.safe_dump(rendered, sort_keys=True)


def proposal_config_hash(
    proposal: dict[str, Any], *, base_config: dict[str, Any] | None = None
) -> str | None:
    """``runs.config_hash`` for the config ``proposal`` would render to,
    computed as ``exec._persist_run_result`` does (``sha256:`` + hex digest of
    the YAML). The proposal name is part of the rendered config, so a renamed
    re-proposal is a different hash; the researcher's "already tried" prompt
    block covers that case. Returns None when the base config is unavailable.
    """
    import hashlib  # noqa: PLC0415
    config_yaml = render_proposal_config(proposal, base_config=base_config)
    if config_yaml is None:
        return None
    return "sha256:" + hashlib.sha256(config_yaml.encode()).hexdigest()


# Keys the Executor stamps onto a rendered config that identify the proposal
# rather than the experiment. Stripping them before hashing gives the
# *semantic* hash: two proposals with identical overrides but different
# names / campaigns / students / notes collide on it.
SEMANTIC_IDENTITY_KEYS: tuple[str, ...] = (
    "run.name", "run.campaign_id", "run.researcher_mode", "run.student_id",
    "logging.notes",
)


def semantic_config_hash(config_yaml: str) -> str | None:
    """``sha256:`` digest of ``config_yaml`` with ``SEMANTIC_IDENTITY_KEYS``
    removed (re-dumped sorted). Persisted as ``runs.config_hash_semantic``
    and used by the Researcher's dedup gate. None for unparseable YAML."""
    import hashlib  # noqa: PLC0415
    import yaml  # noqa: PLC0415
    try:
        rendered = yaml.safe_load(config_yaml)
    except yaml.YAMLError:
        return None
    if not isinstance(rendered, dict):
        return None
    for dotted in SEMANTIC_IDENTITY_KEYS:
        *parents, leaf = dotted.split(".")
        chain: list[tuple[dict, str]] = []  # (container, key) down to the leaf's parent
        cursor: Any = rendered
        for k in parents:
            nxt = cursor.get(k) if isinstance(cursor, dict) else None
            if not isinstance(nxt, dict):
                cursor = None
                break
            chain.append((cursor, k))
            cursor = nxt
        if isinstance(cursor, dict):
            cursor.pop(leaf, None)
        # Prune parents left (or found) empty, so `logging: {notes: x}` and a
        # config with no `logging` section at all hash the same.
        for container, k in reversed(chain):
            if isinstance(container.get(k), dict) and not container[k]:
                del container[k]
            else:
                break
    text = yaml.safe_dump(rendered, sort_keys=True)
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


def proposal_semantic_hash(
    proposal: dict[str, Any], *, base_config: dict[str, Any] | None = None
) -> str | None:
    """Semantic hash for the config ``proposal`` would render to (see
    ``semantic_config_hash``). None when the base config is unavailable."""
    config_yaml = render_proposal_config(proposal, base_config=base_config)
    if config_yaml is None:
        return None
    return semantic_config_hash(config_yaml)


def succeeded_run_for_semantic_hash(
    db_path: Path, semantic_hash: str
) -> dict[str, Any] | None:
    """The most recent succeeded ledger row whose ``config_hash_semantic``
    column equals ``semantic_hash``. Rows written before the column existed
    are covered by ``semantic_hash_index`` instead."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "config_hash_semantic" not in cols:
            return None
        where = "config_hash_semantic = ?"
        if "status" in cols:
            where += " AND status = 'succeeded'"
        row = conn.execute(
            f"SELECT * FROM runs WHERE {where} ORDER BY started_at DESC LIMIT 1",
            (semantic_hash,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return dict(row) if row is not None else None


def semantic_hash_index(db_path: Path, *, n: int = 200) -> dict[str, dict[str, Any]]:
    """``{semantic_hash: row}`` for the last ``n`` succeeded rows that have no
    ``config_hash_semantic`` value (pre-column rows), recomputed from their
    stored ``config_yaml``. Callers compute this once per step and reuse it;
    the most recent row wins per hash."""
    if not db_path.exists():
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "config_yaml" not in cols:
            return {}
        clauses = ["config_yaml IS NOT NULL"]
        if "config_hash_semantic" in cols:
            clauses.append("config_hash_semantic IS NULL")
        if "status" in cols:
            clauses.append("status = 'succeeded'")
        rows = conn.execute(
            f"SELECT * FROM runs WHERE {' AND '.join(clauses)} "
            f"ORDER BY started_at DESC LIMIT ?",
            (n,),
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        h = semantic_config_hash(row["config_yaml"])
        if h is not None:
            out.setdefault(h, dict(row))
    return out


def succeeded_run_for_hash(db_path: Path, config_hash: str) -> dict[str, Any] | None:
    """The most recent succeeded ledger row with ``config_hash``, or None.
    Failed attempts do not count: retrying a crashed config is legitimate."""
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        if "config_hash" not in cols:
            return None
        where = "config_hash = ?"
        if "status" in cols:
            where += " AND status = 'succeeded'"
        row = conn.execute(
            f"SELECT * FROM runs WHERE {where} ORDER BY started_at DESC LIMIT 1",
            (config_hash,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return dict(row) if row is not None else None


def queued_config_hashes(
    queue_path: Path,
    *,
    base_config: dict[str, Any] | None = None,
    semantic: bool = False,
) -> dict[str, str]:
    """``{config_hash: proposal name}`` for proposals waiting in the queue or
    claimed in ``inflight.json`` — work that is about to run and must not be
    proposed a second time. ``semantic=True`` keys by the semantic hash."""
    hash_fn = proposal_semantic_hash if semantic else proposal_config_hash
    out: dict[str, str] = {}
    for path in (queue_path, queue_path.with_name("inflight.json")):
        try:
            queued = read_jsonl(path)
        except json.JSONDecodeError:
            continue
        for proposal in queued:
            if not isinstance(proposal, dict):
                continue
            h = hash_fn(proposal, base_config=base_config)
            if h is not None:
                out.setdefault(h, str(proposal.get("name") or "?"))
    return out


def read_context(context_dir: str | Path = "context") -> dict[str, str]:
    """Load human-curated context files used by Researcher / Analyst prompts."""
    cd = Path(context_dir)
    out: dict[str, str] = {}
    for name in ("vision.md", "decisions.md", "research_log.md", "popper.md"):
        p = cd / name
        if p.exists():
            out[name] = p.read_text()
    return out


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text() or "{}")
    except json.JSONDecodeError:
        return {}


def save_state(path: Path, state: dict[str, Any]) -> None:
    # Mirror keys derived from file-backed ledgers beside state.json. Agents
    # load state at the start of a step and save it at the end; refreshing
    # here keeps the mirrors current even when a ledger changed in between
    # (a block recorded mid-step, a patch registered while the orchestrator
    # holds a stale dict). The ledgers stay the source of truth.
    _refresh_mirrors(path.parent, state)
    _atomic_write_text(path, json.dumps(state, indent=2))


def _refresh_mirrors(lab_root: Path, state: dict[str, Any]) -> None:
    if blocked_path(lab_root).exists():
        state["blocked_on_infrastructure"] = open_blocks(lab_root)
    if _patch_ledger(lab_root).exists():
        state["pending_patches"] = pending_patches(lab_root)


def _atomic_write_text(path: Path, text: str) -> None:
    """Replace a small state file atomically on the same filesystem."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class StudentStateView:
    """Read/write view onto a per-student slice of state.json (Phase B).

    For the DEFAULT_STUDENT_ID, reads/writes go to the top-level state dict
    (backward-compat with Phase A's flat layout). For other students, reads
    /writes are nested under state["students"][<student_id>].

    The view is backed by `state` — modifications through this view mutate
    the underlying dict in place. Caller still needs to save_state() to
    persist.
    """

    __slots__ = ("_state", "_student_id", "_flat")

    def __init__(self, state: dict[str, Any], student_id: str):
        # Lazy import to avoid a circular dependency at module-load time.
        from efferents.lab import DEFAULT_STUDENT_ID  # noqa: PLC0415
        self._state = state
        self._student_id = student_id
        self._flat = (student_id == DEFAULT_STUDENT_ID)

    @property
    def scope(self) -> dict[str, Any]:
        """The actual dict where this student's cursors live."""
        if self._flat:
            return self._state
        students = self._state.setdefault("students", {})
        return students.setdefault(self._student_id, {})

    def get(self, key: str, default: Any = None) -> Any:
        return self.scope.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.scope[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.scope[key] = value

    def __contains__(self, key: str) -> bool:
        return key in self.scope

    def update(self, mapping: dict[str, Any]) -> None:
        self.scope.update(mapping)


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]


def retry_hint(parse_error: str, must_contain: str | None) -> str:
    """Standard nudge appended to a retry call after a JSON parse failure.

    Used by `parse_json_with_one_retry`; exposed so tests + tool-use loops
    can compose the same hint into their own message stream.
    """
    hint = (
        f"Your previous output failed JSON parsing: `{parse_error}`. "
        "Emit STRICT JSON now. First character must be `{`. No prose, no "
        "code fences."
    )
    if must_contain:
        hint += f" The JSON must contain {must_contain}."
    return hint


def parse_json_with_one_retry(
    *,
    call_fn,
    must_contain: str | None = None,
    fallback: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    """Call once; on JSON parse failure, retry once with the parse error fed
    back into the prompt. Returns (parsed_dict, status) where status is one
    of "ok" (first try parsed), "retried" (second try parsed), or "failed"
    (both attempts failed; returns ``fallback or {}``).

    `call_fn(retry_messages)` is the API-invoker closure:
      - On the first call, `retry_messages` is None.
      - On the retry, `retry_messages` is a list of message dicts the caller
        should APPEND to its existing message history before re-invoking the
        model. Conventionally these are an assistant turn echoing the failed
        text and a user turn carrying ``retry_hint(error, must_contain)``.
    Caller composes the actual API request inside `call_fn`; this helper
    only owns parse + retry-shape decisions.
    """
    text = call_fn(None)
    try:
        return parse_json_loose(text, must_contain=must_contain), "ok"
    except json.JSONDecodeError as e:
        retry_messages = [
            {"role": "assistant", "content": [{"type": "text", "text": text}]},
            {"role": "user", "content": [
                {"type": "text", "text": retry_hint(str(e), must_contain)}
            ]},
        ]
        text2 = call_fn(retry_messages)
        try:
            return parse_json_loose(text2, must_contain=must_contain), "retried"
        except json.JSONDecodeError:
            return (fallback or {}), "failed"


def parse_json_loose(text: str, *, must_contain: str | None = None) -> dict[str, Any]:
    """Robust JSON extraction. Handles raw JSON, fenced ```json ... ``` blocks,
    and prose+JSON. If `must_contain` is given, only balanced {...} substrings
    containing that key are considered (e.g. '"proposals"' or '"edits"').
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    starts = [i for i, c in enumerate(text) if c == "{"]
    for start in starts:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : i + 1]
                    if must_contain is None or must_contain in candidate:
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
                    break
    raise json.JSONDecodeError(
        f"no parseable JSON object{f' containing {must_contain!r}' if must_contain else ''} found",
        text,
        0,
    )


# ---------- Campaigns (Phase A) ----------

def campaign_insert(
    db_path: Path,
    *,
    id: str,
    lab_id: str,
    question: str,
    hypothesis_path: str,
    hypothesis_hash: str,
    opened_at: str | None = None,
    student_id: str = "primary",
    headline_metric: str | None = None,
    headline_direction: str | None = None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        # Detect which optional columns the campaigns table has. Pre-migration
        # DBs (old tests / fresh-without-migration) lack student_id and the
        # v0.1.3 metric columns; build the INSERT from whatever is present so
        # the caller never blows up.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
        names = ["id", "lab_id", "question", "hypothesis_path", "hypothesis_hash", "opened_at"]
        values = [id, lab_id, question, hypothesis_path, hypothesis_hash, opened_at or now_iso()]
        if "student_id" in cols:
            names.append("student_id")
            values.append(student_id)
        if "headline_metric" in cols:
            names.append("headline_metric")
            values.append(headline_metric)
        if "headline_direction" in cols:
            names.append("headline_direction")
            values.append(headline_direction)
        placeholders = ", ".join("?" for _ in names)
        conn.execute(
            f"INSERT INTO campaigns ({', '.join(names)}) VALUES ({placeholders})",
            values,
        )
        conn.commit()
    finally:
        conn.close()


def campaign_close(db_path: Path, id: str, *, reason: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE campaigns SET closed_at = ?, close_reason = ? WHERE id = ? AND closed_at IS NULL",
            (now_iso(), reason, id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise KeyError(f"campaign {id!r} not found or already closed")
    finally:
        conn.close()


def campaign_open_list(db_path: Path, lab_id: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE lab_id = ? AND closed_at IS NULL",
            (lab_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def campaign_open_list_for_student(
    db_path: Path, lab_id: str, student_id: str
) -> list[dict[str, Any]]:
    """Open campaigns owned by a specific student. Used to enforce the
    per-student cap (LabConfig.max_open_campaigns_per_student).

    Falls back to all-lab-open if the campaigns table lacks the student_id
    column (pre-Phase-B migration). The fallback is conservative — it
    counts another student's open campaigns against this student's quota,
    which is fine for single-student labs."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(campaigns)").fetchall()}
        if "student_id" in cols:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE lab_id = ? AND student_id = ? AND closed_at IS NULL",
                (lab_id, student_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM campaigns WHERE lab_id = ? AND closed_at IS NULL",
                (lab_id,),
            ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def campaign_recently_closed_list(
    db_path: Path, lab_id: str, *, days: int = 7
) -> list[dict[str, Any]]:
    """Campaigns closed within the last `days` days. Used by the Researcher's
    Supervisor brief to surface closures (stale / published / rejected_by_review)
    so the Student stops proposing for them."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT * FROM campaigns
            WHERE lab_id = ? AND closed_at IS NOT NULL AND closed_at >= ?
            ORDER BY closed_at DESC
            """,
            (lab_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def campaign_stale_open(
    db_path: Path, lab_id: str, *, hours: float = 48.0
) -> list[dict[str, Any]]:
    """Open campaigns where the most recent associated run (or, if no runs,
    `opened_at`) is older than `hours`."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT c.* FROM campaigns c
            LEFT JOIN runs r ON r.campaign_id = c.id
            WHERE c.lab_id = ? AND c.closed_at IS NULL
            GROUP BY c.id
            HAVING COALESCE(MAX(r.started_at), c.opened_at) < ?
            """,
            (lab_id, cutoff),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# ---------- Blocked on infrastructure ----------
#
# A student whose experiment cannot be made valid by config knobs alone can
# declare that the executor itself needs a code change. Records live in
# lab/blocked.jsonl (one JSON per line, rewritten in place when resolved) and
# the open set is mirrored into state.json["blocked_on_infrastructure"] for
# the dashboard / status views.

BLOCKED_FILE = "blocked.jsonl"


def blocked_path(lab_root: Path) -> Path:
    return Path(lab_root) / BLOCKED_FILE


def _sync_blocked_state(lab_root: Path) -> None:
    state_path = Path(lab_root) / "state.json"
    state = load_state(state_path)
    state["blocked_on_infrastructure"] = open_blocks(lab_root)
    save_state(state_path, state)


def open_blocks(lab_root: Path, *, student_id: str | None = None) -> list[dict[str, Any]]:
    """Unresolved blocks, oldest first; optionally for one student."""
    try:
        records = read_jsonl(blocked_path(lab_root))
    except json.JSONDecodeError:
        return []
    return [
        r for r in records
        if isinstance(r, dict) and not r.get("resolved")
        and (student_id is None or r.get("student_id") == student_id)
    ]


def record_blocked(
    lab_root: Path,
    *,
    student_id: str,
    summary: str,
    evidence: list[str] | None = None,
    proposed_change: str = "",
    ts: str | None = None,
) -> dict[str, Any] | None:
    """Append one open block for ``student_id``. At most one open block per
    student: returns None (and writes nothing) while one is already open."""
    import uuid  # noqa: PLC0415
    if open_blocks(lab_root, student_id=student_id):
        return None
    record = {
        "id": "blk-" + uuid.uuid4().hex[:8],
        "ts": ts or now_iso(),
        "student_id": student_id,
        "summary": " ".join(str(summary).split())[:500],
        "evidence": [str(e) for e in (evidence or []) if e][:20],
        "proposed_change": " ".join(str(proposed_change or "").split())[:1000],
        "resolved": None,
    }
    append_jsonl(blocked_path(lab_root), record)
    _sync_blocked_state(lab_root)
    return record


def resolve_block(
    lab_root: Path, block_id: str, *, by: str = "owner", ts: str | None = None
) -> bool:
    """Mark ``block_id`` resolved (``resolved: <ts>``, ``resolved_by``) by
    rewriting the ledger in place. ``by`` is ``owner`` or ``coder``. Returns
    False when no open block has that id."""
    path = blocked_path(lab_root)
    records = read_jsonl(path)
    hit = False
    for r in records:
        if isinstance(r, dict) and r.get("id") == block_id and not r.get("resolved"):
            r["resolved"] = ts or now_iso()
            r["resolved_by"] = by
            hit = True
    if not hit:
        return False
    _atomic_write_text(path, "".join(json.dumps(r) + "\n" for r in records))
    _sync_blocked_state(lab_root)
    return True


# ---------- Coder patches awaiting owner review ----------
#
# In ``autonomy.coder_mode: review`` the Coder writes a unified diff plus a
# rationale under lab/patches/ instead of editing source.dir. The ledger at
# lab/patches/patches.jsonl tracks each patch's status (pending / applied /
# rejected); the pending set is mirrored into state.json["pending_patches"].

PATCHES_DIR = "patches"
PATCH_LEDGER = "patches.jsonl"
PATCH_STATUSES = ("pending", "applied", "rejected")


def patches_dir(lab_root: Path) -> Path:
    return Path(lab_root) / PATCHES_DIR


def _patch_ledger(lab_root: Path) -> Path:
    return patches_dir(lab_root) / PATCH_LEDGER


def _sync_patch_state(lab_root: Path) -> None:
    state_path = Path(lab_root) / "state.json"
    state = load_state(state_path)
    state["pending_patches"] = pending_patches(lab_root)
    save_state(state_path, state)


def pending_patches(lab_root: Path) -> list[dict[str, Any]]:
    """Patches still awaiting the owner's decision, oldest first."""
    try:
        records = read_jsonl(_patch_ledger(lab_root))
    except json.JSONDecodeError:
        return []
    return [r for r in records if isinstance(r, dict) and r.get("status") == "pending"]


def register_patch(
    lab_root: Path,
    *,
    path: str | Path,
    rationale_path: str | Path,
    name: str,
    student_id: str | None = None,
    blocked_id: str | None = None,
    files: list[str] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Record a freshly written patch as ``pending``."""
    record = {
        "ts": ts or now_iso(),
        "path": str(path),
        "rationale_path": str(rationale_path),
        "name": name,
        "student_id": student_id,
        "blocked_id": blocked_id,
        "files": list(files or []),
        "status": "pending",
        "status_ts": None,
        "status_by": None,
    }
    patches_dir(lab_root).mkdir(parents=True, exist_ok=True)
    append_jsonl(_patch_ledger(lab_root), record)
    _sync_patch_state(lab_root)
    return record


def mark_patch(
    lab_root: Path, path: str | Path, status: str, *, by: str = "owner"
) -> bool:
    """Set a patch's status (``applied`` / ``rejected`` / ``pending``) by
    rewriting the ledger in place. When a patch that addressed a block is
    applied, the block is closed as well. Returns False for an unknown path."""
    if status not in PATCH_STATUSES:
        raise ValueError(f"status must be one of {PATCH_STATUSES}, got {status!r}")
    ledger = _patch_ledger(lab_root)
    records = read_jsonl(ledger)
    target = str(path)
    hit: dict[str, Any] | None = None
    for r in records:
        if isinstance(r, dict) and r.get("path") == target:
            r["status"] = status
            r["status_ts"] = now_iso()
            r["status_by"] = by
            hit = r
    if hit is None:
        return False
    _atomic_write_text(ledger, "".join(json.dumps(r) + "\n" for r in records))
    _sync_patch_state(lab_root)
    if status == "applied" and hit.get("blocked_id"):
        resolve_block(lab_root, hit["blocked_id"], by=by)
    return True
