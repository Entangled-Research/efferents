"""Coder agent: takes an architectural proposal and implements it as a code change.

Workflow per call:
    1. Pick the highest-priority pending proposal from lab/proposed_changes.md
       (skipping anything in lab/coder_log.jsonl as either succeeded or
       previously-failed-with-same-content).
    2. Read full contents of source.dir/**/*.py + config_template (per LabConfig).
    3. Ask Opus 4.7 for a JSON edit plan.
    4. Snapshot touched files (in-memory).
    5. Apply edits.
    6. Run smoke test using the lab's configured smoke command (from LabConfig).
    7. If smoke passes: git commit, log success.
    8. If smoke fails: restore snapshot, log failure.

Append-only log at lab/coder_log.jsonl with attempt outcomes. The Researcher
reads recent failures to avoid re-proposing them.

``autonomy.coder_mode: review`` replaces steps 4–8: the edit plan is applied
in memory only, written as a unified diff plus a rationale under
lab/patches/, and registered in state.json["pending_patches"] for the owner
to apply or reject. source.dir is left untouched.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import anthropic

from efferents import lab as _lab
from efferents.agents import librarian
from efferents.agents.budget import BudgetTracker, CallUsage, model_for
from efferents.agents.prompts.loader import load_prompt
from efferents.agents.state import (
    LabPaths,
    append_jsonl,
    notebook_append,
    now_iso,
    open_blocks,
    parse_json_loose,
    patches_dir,
    read_jsonl,
    register_patch,
    retry_hint,
)
from efferents.exec import _subprocess_env

MAX_LIT_CALLS_PER_PASS = 3
CODER_MODES = ("auto", "review")


def _coder_mode(cfg=None) -> str:
    """``autonomy.coder_mode`` — ``auto`` applies and commits edits (legacy
    behaviour), ``review`` only writes a patch for the owner. Read defensively:
    ``LabConfig.Autonomy`` may not declare the field yet, in which case the
    mode is ``auto``. Any other value is treated as ``review`` — a typo must
    not widen the Coder's write permission."""
    cfg = cfg or _lab.get_config()
    raw = getattr(getattr(cfg, "autonomy", None), "coder_mode", "auto")
    mode = str(raw or "auto").strip().lower()
    return mode if mode == "auto" else "review"


def _target_globs() -> list[str]:
    """Patterns from LabConfig.source.allowed_patterns are RELATIVE to source.dir;
    we prepend the absolute source dir to each so callers get absolute globs."""
    cfg = _lab.get_config()
    src = str(cfg.source.dir).rstrip("/")
    out = [f"{src}/{pat}" for pat in cfg.source.allowed_patterns]
    out.append(str(cfg.executor.config_template))
    return out


def _new_file_path_re():
    cfg = _lab.get_config()
    src = re.escape(str(cfg.source.dir).rstrip("/"))
    return re.compile(rf"^{src}/[A-Za-z_][A-Za-z0-9_]*\.py$")


def _matches_allowed_pattern(relative_path: Path) -> bool:
    """Match a source-relative path against the lab's configured patterns."""
    posix = relative_path.as_posix()
    for pattern in _lab.get_config().source.allowed_patterns:
        # ``fnmatch`` treats ``**/`` as requiring a slash, while users expect
        # the common glob meaning where it also includes source-root files.
        if fnmatch(posix, pattern):
            return True
        if pattern.startswith("**/") and fnmatch(posix, pattern[3:]):
            return True
    return False


def _resolve_coder_path(
    file_path: str,
    repo_root: Path,
    *,
    new_file: bool = False,
) -> Path:
    """Resolve and validate one model-proposed path.

    Canonical resolution blocks ``..`` and symlink escapes. Existing edits may
    target the configured template or a source file matching
    ``source.allowed_patterns``. New files are restricted further to one
    Python module directly under ``source.dir``.
    """
    if not file_path or "\x00" in file_path:
        raise ValueError(f"invalid coder path: {file_path!r}")

    raw = Path(file_path)
    candidate = raw if raw.is_absolute() else Path(repo_root) / raw
    resolved = candidate.resolve(strict=False)
    cfg = _lab.get_config()
    source_dir = cfg.source.dir.resolve()
    config_template = cfg.executor.config_template.resolve()

    if resolved == config_template and not new_file:
        return resolved
    try:
        relative = resolved.relative_to(source_dir)
    except ValueError as e:
        raise ValueError(f"path escapes source.dir: {file_path!r}") from e

    if new_file:
        if (
            relative.parent != Path(".")
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", relative.name)
        ):
            raise ValueError(
                f"new_file path {file_path!r} must be "
                f"{source_dir}/<name>.py (no nested dirs)"
            )
    elif not _matches_allowed_pattern(relative):
        raise ValueError(
            f"path {file_path!r} does not match source.allowed_patterns"
        )
    return resolved


def _smoke_command(config_path: Path) -> str:
    cfg = _lab.get_config()
    template = cfg.executor.smoke_command or cfg.executor.run_command
    return template.format(config_path=str(config_path))


def _smoke_timeout() -> int:
    return _lab.get_config().executor.smoke_timeout_s


# -----------------------------------------------------------------------------
# Per-student backlog file paths (Phase B)
# -----------------------------------------------------------------------------
# The primary student keeps using the legacy unsuffixed filenames so the
# running lab's existing proposed_changes.md / coder_blockers.md continue
# to work untouched. Sibling students live in suffixed files.

def proposed_changes_path(paths: LabPaths, student_id: str) -> Path:
    """lab/proposed_changes.md for primary, lab/proposed_changes_<id>.md for others."""
    from efferents.lab import DEFAULT_STUDENT_ID
    if student_id == DEFAULT_STUDENT_ID:
        return paths.root / "proposed_changes.md"
    return paths.root / f"proposed_changes_{student_id}.md"


@dataclass
class Edit:
    file_path: str
    old_string: str
    new_string: str


@dataclass
class NewFile:
    file_path: str
    content: str


# <source.dir>/<name>.py only — no nested dirs, no other top-levels.
# Use _new_file_path_re() to get the compiled pattern from LabConfig.
MAX_NEW_FILES_PER_CALL = 1


@dataclass
class CoderResult:
    ok: bool
    name: str
    summary: str | None = None
    files_changed: list[str] | None = None
    commit_sha: str | None = None
    error: str | None = None
    smoke_stderr: str | None = None
    feasible: bool = True
    patch_path: str | None = None  # review mode: the diff awaiting the owner


# -----------------------------------------------------------------------------
# Reading the architectural-proposals backlog
# -----------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^### (.+)$", re.MULTILINE)


def parse_proposed_changes(path: Path) -> list[dict[str, str]]:
    """Parse lab/proposed_changes.md into a list of {name, principle, what, why, payoff} dicts."""
    if not path.exists():
        return []
    text = path.read_text()
    proposals: list[dict[str, str]] = []
    chunks = re.split(r"^### ", text, flags=re.MULTILINE)
    for chunk in chunks[1:]:  # skip header
        if not chunk.strip():
            continue
        first_line, rest = chunk.split("\n", 1) if "\n" in chunk else (chunk, "")
        name = first_line.strip()
        prop: dict[str, str] = {"name": name, "raw": rest.strip()}
        for field in ("Principle", "What", "Why", "Effort", "Payoff"):
            m = re.search(rf"\*\*{field}\*\*:\s*(.+?)(?=\n-\s|\n\n|\Z)", rest, re.DOTALL)
            if m:
                prop[field.lower()] = m.group(1).strip()
        proposals.append(prop)
    return proposals


def select_pending_proposal(
    *,
    paths: LabPaths,
    student_id: str | None = None,
) -> dict[str, str] | None:
    """Pick the next proposal to try from this student's backlog. Strategy:
    most-recent unique-named entry in the student's proposed_changes file
    that hasn't already been attempted (success or fail) per coder_log.jsonl.

    The coder_log is shared across students (one ledger for the whole lab);
    the backlog files are per-student. A proposal `name` is treated as
    globally unique — if Student A and Student B happen to propose the same
    `name`, the second attempt is skipped (intentional dedup).
    """
    from efferents.lab import DEFAULT_STUDENT_ID
    sid = student_id or DEFAULT_STUDENT_ID
    proposals = parse_proposed_changes(proposed_changes_path(paths, sid))
    if not proposals:
        return None

    log_path = paths.root / "coder_log.jsonl"
    log = read_jsonl(log_path)
    attempted = {r.get("name") for r in log}

    # Walk in REVERSE so most-recent (most-strongly re-requested) names come first.
    seen: set[str] = set()
    for prop in reversed(proposals):
        if prop["name"] in seen:
            continue
        seen.add(prop["name"])
        if prop["name"] in attempted:
            continue
        # Stamp the student so the caller knows which slice this came from.
        prop.setdefault("student_id", sid)
        return prop
    return None


# -----------------------------------------------------------------------------
# Reading source files for the Coder's context
# -----------------------------------------------------------------------------


def gather_source(repo_root: Path, globs: list[str] | None = None) -> dict[str, str]:
    if globs is None:
        globs = _target_globs()
    out: dict[str, str] = {}
    for pattern in globs:
        # Absolute glob patterns are matched directly; relative ones against repo_root.
        p_pattern = Path(pattern)
        if p_pattern.is_absolute():
            # glob on absolute path: use parent as base and filename as pattern
            # For absolute patterns we glob from root
            for match in sorted(glob.glob(pattern, recursive=True)):
                p = Path(match)
                if p.is_file():
                    try:
                        resolved = _resolve_coder_path(str(p), repo_root)
                    except ValueError:
                        continue
                    out[str(resolved)] = resolved.read_text()
        else:
            for p in sorted(repo_root.glob(pattern)):
                if p.is_file():
                    try:
                        resolved = _resolve_coder_path(str(p), repo_root)
                    except ValueError:
                        continue
                    out[str(resolved)] = resolved.read_text()
    return out


# -----------------------------------------------------------------------------
# Anthropic call -> structured edit plan
# -----------------------------------------------------------------------------


def _build_messages(
    *,
    proposal: dict[str, str],
    source: dict[str, str],
    coder_log_tail: str,
) -> list[dict[str, Any]]:
    files_block = "\n\n".join(
        f"### {path}\n```{ 'yaml' if path.endswith('.yaml') else 'python' }\n{content}\n```"
        for path, content in source.items()
    )
    proposal_block = f"## Proposal\n\n```\n{json.dumps(proposal, indent=2)}\n```"
    log_block = f"## Recent Coder attempts (avoid repeating losing approaches)\n\n{coder_log_tail or '(none)'}"
    static = files_block + "\n\n" + log_block
    dynamic = proposal_block + "\n\nReturn your JSON edit plan now."
    return [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": static, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic},
            ],
        }
    ]


def get_edit_plan(
    *,
    proposal: dict[str, str],
    source: dict[str, str],
    coder_log_tail: str,
    paths: LabPaths,
    budget: BudgetTracker,
    client: anthropic.Anthropic,
    model: str | None = None,
    max_tokens: int = 12288,
) -> dict[str, Any]:
    chosen = model or model_for("coder") or "claude-opus-4-7"
    system_prompt_text = load_prompt("coder")
    system_prompt = [{
        "type": "text", "text": system_prompt_text,
        "cache_control": {"type": "ephemeral"},
    }]
    messages = _build_messages(proposal=proposal, source=source, coder_log_tail=coder_log_tail)
    text, _consulted = librarian.run_with_lit_review_tool(
        client=client,
        system_prompt=system_prompt,
        messages=messages,
        model=chosen,
        paths=paths,
        budget=budget,
        agent="coder",
        max_tokens=max_tokens,
        max_lit_calls=MAX_LIT_CALLS_PER_PASS,
    )
    # On parse failure, retry once with the error fed back into the same
    # conversation. The retry is a no-tool follow-up (we already paid for any
    # lit_review calls); it just asks the model to fix its JSON.
    try:
        return parse_json_loose(text, must_contain='"edits"')
    except json.JSONDecodeError as e:
        retry_msgs = [
            {"role": "assistant", "content": [{"type": "text", "text": text}]},
            {"role": "user", "content": [
                {"type": "text", "text": retry_hint(str(e), '"edits"')}
            ]},
        ]
        resp = client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages + retry_msgs,
        )
        usage = CallUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        budget.record(agent="coder", model=chosen, usage=usage, notes="edit-plan retry")
        text2 = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        # If the retry ALSO fails, raise; the outer caller in implement_proposal
        # already catches that and logs it as a plan failure.
        return parse_json_loose(text2, must_contain='"edits"')


# -----------------------------------------------------------------------------
# File snapshot / restore
# -----------------------------------------------------------------------------


def _snapshot(file_paths: list[str], repo_root: Path) -> dict[str, str | None]:
    snap: dict[str, str | None] = {}
    for fp in file_paths:
        p = repo_root / fp
        snap[fp] = p.read_text() if p.exists() else None
    return snap


def _restore(snapshot: dict[str, str | None], repo_root: Path) -> None:
    for fp, content in snapshot.items():
        p = repo_root / fp
        if content is None:
            if p.exists():
                p.unlink()
        else:
            p.write_text(content)


def _replaced(text: str, e: Edit) -> str:
    """``text`` after one edit; raises ValueError when the edit is not
    uniquely applicable."""
    if e.old_string == e.new_string:
        raise ValueError(f"old_string == new_string in {e.file_path}")
    if e.old_string not in text:
        raise ValueError(
            f"old_string not found in {e.file_path} "
            f"(first 80 chars: {e.old_string[:80]!r})"
        )
    if text.count(e.old_string) > 1:
        raise ValueError(
            f"old_string appears {text.count(e.old_string)} times in {e.file_path}; "
            "needs more context to be unique"
        )
    return text.replace(e.old_string, e.new_string, 1)


def _apply_edits(edits: list[Edit], repo_root: Path) -> None:
    """Raises on any edit failure (caller must restore snapshot)."""
    for e in edits:
        p = repo_root / e.file_path
        if not p.exists():
            raise FileNotFoundError(f"target file does not exist: {e.file_path}")
        p.write_text(_replaced(p.read_text(), e))


def _preview_changes(
    edits: list[Edit], new_files: list[NewFile], repo_root: Path
) -> dict[str, tuple[str | None, str]]:
    """``{file_path: (content before, content after)}`` for the plan, computed
    in memory with the same validation as ``_write_new_files`` +
    ``_apply_edits`` (edits apply sequentially, so a later edit sees an
    earlier one). ``before`` is None for a new file. Nothing is written."""
    out: dict[str, tuple[str | None, str]] = {}
    for nf in new_files:
        if (repo_root / nf.file_path).exists():
            raise FileExistsError(
                f"new_file {nf.file_path} already exists; use an edit instead"
            )
        out[nf.file_path] = (None, nf.content)
    for e in edits:
        if e.file_path in out:
            before, current = out[e.file_path]
        else:
            p = repo_root / e.file_path
            if not p.exists():
                raise FileNotFoundError(f"target file does not exist: {e.file_path}")
            before = current = p.read_text()
        out[e.file_path] = (before, _replaced(current, e))
    return out


def _extract_new_files(plan: dict[str, Any], repo_root: Path | None = None) -> list[NewFile]:
    """Validate and extract optional ``new_files`` from a Coder edit plan.

    Restricts to ``<source.dir>/<name>.py`` (no nested dirs) and enforces a
    per-call cap. Raises ValueError on any malformed entry.
    """
    raw = plan.get("new_files") or []
    if not isinstance(raw, list):
        raise ValueError(f"new_files must be a list, got {type(raw).__name__}")
    if len(raw) > MAX_NEW_FILES_PER_CALL:
        raise ValueError(
            f"new_files has {len(raw)} entries; cap is {MAX_NEW_FILES_PER_CALL}/call"
        )
    repo_root = repo_root or Path.cwd()
    out: list[NewFile] = []
    for nf in raw:
        if not isinstance(nf, dict):
            raise ValueError(f"new_files entry must be dict, got {type(nf).__name__}")
        fp = nf.get("file_path", "")
        content = nf.get("content", "")
        if not isinstance(fp, str):
            raise ValueError(f"new_file path must be a string, got {type(fp).__name__}")
        resolved = _resolve_coder_path(fp, repo_root, new_file=True)
        if not isinstance(content, str) or not content.strip():
            raise ValueError(f"new_file {fp} has empty content")
        out.append(NewFile(file_path=str(resolved), content=content))
    return out


def _write_new_files(new_files: list[NewFile], repo_root: Path) -> None:
    """Write each new file. Caller must snapshot the paths first so that
    restore-on-failure deletes the file (snapshot records None for
    didn't-exist, _restore unlinks files where the snapshot is None)."""
    for nf in new_files:
        p = repo_root / nf.file_path
        if p.exists():
            raise FileExistsError(
                f"new_file {nf.file_path} already exists; use an edit instead"
            )
        p.write_text(nf.content)


# -----------------------------------------------------------------------------
# Smoke test
# -----------------------------------------------------------------------------


def run_smoke(repo_root: Path, config_path: Path | None = None) -> tuple[bool, str]:
    """Run the lab's smoke command end-to-end. Returns (ok, combined_output).

    config_path defaults to LabConfig.executor.config_template.
    shell=True because run_command is a shell template, not an argv list.
    cwd is set to source.dir so relative paths in run_command resolve correctly.
    """
    cfg = _lab.get_config()
    if config_path is None:
        config_path = cfg.executor.config_template
    timeout = _smoke_timeout()
    try:
        proc = subprocess.Popen(
            _smoke_command(config_path),
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cfg.source.dir),
            env=_subprocess_env(cfg.executor.env_passthrough),
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
        output = (stdout or "") + "\n--- stderr ---\n" + (stderr or "")
        return False, f"smoke timed out after {timeout}s\n{output[-3500:]}"
    output = (stdout or "") + "\n--- stderr ---\n" + (stderr or "")
    return proc.returncode == 0, output[-4000:]  # tail to avoid huge strings


# -----------------------------------------------------------------------------
# Git commit
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Coder blockers — Researcher-facing summary of infeasible architectural proposals
# -----------------------------------------------------------------------------

BLOCKERS_FILE = "coder_blockers.md"
BLOCKERS_HEADER = (
    "# Coder blockers\n\n"
    "Architectural proposals the Coder declared infeasible. The Researcher "
    "(Supervisor brief) reads recent entries and treats listed `name`s as "
    "forbidden. Entries older than 30 days are filtered out at read time.\n\n"
)
BLOCKERS_RETENTION_DAYS = 30
_BLOCKER_LINE_RE = re.compile(r"^- `([^`]+)` \((\d{4}-\d{2}-\d{2})\): (.+)$")


def _blockers_path(paths: LabPaths, student_id: str | None = None) -> Path:
    """lab/coder_blockers.md for primary (legacy), lab/coder_blockers_<id>.md for others."""
    from efferents.lab import DEFAULT_STUDENT_ID
    sid = student_id or DEFAULT_STUDENT_ID
    if sid == DEFAULT_STUDENT_ID:
        return paths.root / BLOCKERS_FILE
    return paths.root / f"coder_blockers_{sid}.md"


def record_blocker(
    paths: LabPaths,
    *,
    name: str,
    reason: str,
    student_id: str | None = None,
) -> None:
    """Append an entry to the student's blockers file. Creates with header on
    first call. Caps `reason` at 200 chars and strips newlines."""
    path = _blockers_path(paths, student_id)
    if not path.exists():
        path.write_text(BLOCKERS_HEADER)
    reason_clean = " ".join((reason or "").split())[:200] or "(no reason given)"
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with path.open("a") as f:
        f.write(f"- `{name}` ({date}): {reason_clean}\n")


def recent_blockers(
    paths: LabPaths,
    *,
    days: int = BLOCKERS_RETENTION_DAYS,
    student_id: str | None = None,
) -> list[dict[str, str]]:
    """Return blocker entries for this student younger than `days` days.
    Each entry is {name, date, reason}. Returns [] if the file is missing."""
    path = _blockers_path(paths, student_id)
    if not path.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, str]] = []
    for line in path.read_text().splitlines():
        m = _BLOCKER_LINE_RE.match(line.strip())
        if not m:
            continue
        name, date_str, reason = m.group(1), m.group(2), m.group(3)
        try:
            entry_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if entry_dt < cutoff:
            continue
        out.append({"name": name, "date": date_str, "reason": reason})
    return out


def _git_commit(repo_root: Path, summary: str, name: str, files: list[str]) -> str | None:
    repo_root = repo_root.resolve()
    relative_files: list[str] = []
    try:
        # Never fold a user's pre-staged work into an autonomous commit.
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_root),
            capture_output=True,
        )
        if staged.returncode != 0:
            return None
        for file_path in files:
            resolved = Path(file_path).resolve()
            try:
                relative_files.append(str(resolved.relative_to(repo_root)))
            except ValueError:
                return None
        subprocess.run(
            ["git", "add", "--", *relative_files],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
        msg = (
            f"feat(coder): {name}\n\n{summary}\n\n"
            "Implemented autonomously by the Coder agent.\n\n"
            "Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
        )
        subprocess.run(
            ["git", "commit", "-q", "--only", "-m", msg, "--", *relative_files],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
        )
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return sha
    except subprocess.CalledProcessError:
        if relative_files:
            subprocess.run(
                ["git", "restore", "--staged", "--", *relative_files],
                cwd=str(repo_root),
                capture_output=True,
            )
        return None


# -----------------------------------------------------------------------------
# Review mode: unified diff + rationale for the owner, nothing applied
# -----------------------------------------------------------------------------


def _patch_relpath(file_path: str, repo_root: Path) -> str:
    """Path as it appears in the diff header: relative to the repo root when
    the file lives inside it (``git apply -p1`` from the root), else the
    absolute path without its leading slash."""
    resolved = Path(file_path).resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix().lstrip("/")


def _unified_diff(preview: dict[str, tuple[str | None, str]], repo_root: Path) -> str:
    chunks = []
    for file_path in sorted(preview):
        before, after = preview[file_path]
        rel = _patch_relpath(file_path, repo_root)
        chunks.append("".join(difflib.unified_diff(
            (before or "").splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="/dev/null" if before is None else f"a/{rel}",
            tofile=f"b/{rel}",
        )))
    return "".join(chunks)


def _write_review_patch(
    *,
    paths: LabPaths,
    proposal: dict[str, str],
    plan: dict[str, Any],
    preview: dict[str, tuple[str | None, str]],
    repo_root: Path,
) -> tuple[Path, Path]:
    """Write ``<lab_root>/patches/<ts>-<slug>.diff`` + ``.md`` and register
    the patch as pending. Returns (diff_path, rationale_path)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9-]+", "-", proposal["name"].lower()).strip("-")[:48] or "patch"
    pdir = patches_dir(paths.root)
    pdir.mkdir(parents=True, exist_ok=True)
    diff_path = pdir / f"{ts}-{slug}.diff"
    n = 1
    while diff_path.exists():
        diff_path = pdir / f"{ts}-{slug}-{n}.diff"
        n += 1
    md_path = diff_path.with_suffix(".md")
    diff_path.write_text(_unified_diff(preview, repo_root))

    student_id = proposal.get("student_id")
    # The block this patch answers: the student's open block (at most one).
    block = next(iter(open_blocks(paths.root, student_id=student_id)), None) if student_id else None
    files = sorted(preview)
    cfg = _lab.get_config()
    smoke = cfg.executor.smoke_command or cfg.executor.run_command
    md = [
        f"# Coder patch awaiting owner review: {proposal['name']}",
        "",
        f"- **Written**: {ts}",
        f"- **Student**: {student_id or '(unknown)'}",
        f"- **Diff**: `{diff_path}`",
        f"- **Files**: {', '.join(f'`{_patch_relpath(f, repo_root)}`' for f in files)}",
        "",
        "## What it fixes",
        "",
        f"**Summary**: {plan.get('summary') or '(none)'}",
        "",
        f"**Rationale**: {plan.get('rationale') or '(none)'}",
        "",
        f"**Proposal — what**: {proposal.get('what') or '(none)'}",
        "",
        f"**Proposal — why**: {proposal.get('why') or '(none)'}",
        "",
        "## Infrastructure block addressed",
        "",
        (
            f"`{block['id']}` — {block['summary']} (evidence: "
            f"{', '.join(block.get('evidence') or []) or 'none cited'})"
            if block else "(no open blocked_on_infrastructure record for this student)"
        ),
        "",
        "## Tests run",
        "",
        "None — review mode does not execute the lab's smoke command and does "
        "not touch source.dir. After applying, run:",
        "",
        f"    {smoke.format(config_path=str(cfg.executor.config_template))}",
        "",
        f"Smoke expectation from the plan: {plan.get('verifies_change') or '(none stated)'}",
        "",
        "## Apply / reject",
        "",
        f"    git apply {diff_path}",
        "",
        "then mark it via `efferents.agents.state.mark_patch(lab_root, path, "
        "'applied' | 'rejected')`.",
        "",
    ]
    md_path.write_text("\n".join(md))
    register_patch(
        paths.root, path=diff_path, rationale_path=md_path, name=proposal["name"],
        student_id=student_id, blocked_id=block["id"] if block else None,
        files=[_patch_relpath(f, repo_root) for f in files],
    )
    notebook_append(
        paths.notebook,
        f"## {now_iso()} — Coder patch awaiting owner review: {diff_path}\n",
    )
    return diff_path, md_path


# -----------------------------------------------------------------------------
# Top-level entrypoint
# -----------------------------------------------------------------------------


def implement_proposal(
    *,
    proposal: dict[str, str],
    paths: LabPaths,
    budget: BudgetTracker,
    client: anthropic.Anthropic,
    repo_root: Path | None = None,
    mode: str | None = None,
) -> CoderResult:
    """``mode`` overrides ``autonomy.coder_mode`` (``auto`` | ``review``)."""
    repo_root = repo_root or Path.cwd()
    mode = _coder_mode() if mode is None else ("auto" if mode == "auto" else "review")
    started = now_iso()
    t0 = time.monotonic()

    source = gather_source(repo_root)
    log = read_jsonl(paths.root / "coder_log.jsonl")
    log_tail = json.dumps(log[-10:], indent=2)

    try:
        plan = get_edit_plan(
            proposal=proposal,
            source=source,
            coder_log_tail=log_tail,
            paths=paths,
            budget=budget,
            client=client,
        )
    except Exception as e:
        result = CoderResult(ok=False, name=proposal["name"], error=f"plan failed: {e}")
        _log(paths, started, proposal, result, duration_seconds=time.monotonic() - t0)
        return result

    if not plan.get("feasible", True):
        result = CoderResult(
            ok=False, feasible=False, name=proposal["name"],
            summary=plan.get("summary"),
            error=f"declared infeasible: {plan.get('summary', '')}",
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        # Surface the infeasibility to the Researcher's next pass so the
        # same architectural idea doesn't get re-proposed.
        record_blocker(
            paths,
            name=proposal["name"],
            reason=plan.get("summary") or "(no summary)",
            student_id=proposal.get("student_id"),
        )
        return result

    edits_raw = plan.get("edits", [])
    has_new_files = bool(plan.get("new_files"))
    if not edits_raw and not has_new_files:
        result = CoderResult(ok=False, name=proposal["name"], error="empty edits list")
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    # Filter to known Edit fields — model sometimes adds extras (e.g.,
    # 'verifies_change') that belong at the plan level.
    _edit_fields = {"file_path", "old_string", "new_string"}
    try:
        edits = [
            Edit(**{k: v for k, v in e.items() if k in _edit_fields})
            for e in edits_raw
        ]
        for edit in edits:
            edit.file_path = str(_resolve_coder_path(edit.file_path, repo_root))
    except (TypeError, ValueError) as e:
        result = CoderResult(
            ok=False, name=proposal["name"], error=f"edits invalid: {e}"
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    try:
        new_files = _extract_new_files(plan, repo_root)
    except ValueError as e:
        result = CoderResult(ok=False, name=proposal["name"], error=f"new_files invalid: {e}")
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    edit_paths = {e.file_path for e in edits}
    new_paths = {nf.file_path for nf in new_files}
    file_paths = sorted(edit_paths | new_paths)

    # Validate the plan in memory first (same checks as the on-disk apply), so
    # review mode never writes to source.dir and auto mode only touches files
    # once the whole plan is known to apply.
    try:
        preview = _preview_changes(edits, new_files, repo_root)
    except Exception as apply_err:
        # Single retry with explicit error feedback — give the model one chance
        # to fix its own non-unique-old_string or whitespace mismatch.
        try:
            retry_proposal = dict(proposal)
            retry_proposal["_apply_error"] = str(apply_err)
            retry_proposal["_retry_note"] = (
                "Your previous edit plan failed: " + str(apply_err) +
                ". Emit a NEW edit plan with old_strings that are byte-exact "
                "and uniquely findable. Add more surrounding context to "
                "disambiguate if a snippet appears multiple times."
            )
            plan2 = get_edit_plan(
                proposal=retry_proposal,
                source=gather_source(repo_root),
                coder_log_tail=log_tail,
                paths=paths,
                budget=budget,
                client=client,
            )
            edits2 = [
                Edit(**{k: v for k, v in e.items() if k in _edit_fields})
                for e in plan2.get("edits", [])
            ]
            for edit in edits2:
                edit.file_path = str(_resolve_coder_path(edit.file_path, repo_root))
            new_files2 = _extract_new_files(plan2, repo_root)
            if not edits2 and not new_files2:
                raise ValueError("retry returned empty edits")
            preview = _preview_changes(edits2, new_files2, repo_root)
            edits = edits2
            new_files = new_files2
            file_paths = sorted(preview)
            plan = plan2
        except Exception as retry_err:
            result = CoderResult(
                ok=False, name=proposal["name"],
                error=f"apply failed (after retry): first={apply_err} retry={retry_err}",
            )
            _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
            return result

    if mode == "review":
        diff_path, _md_path = _write_review_patch(
            paths=paths, proposal=proposal, plan=plan, preview=preview,
            repo_root=repo_root,
        )
        # ok=False: nothing landed in source.dir. The orchestrator's "code
        # committed" notification must not fire; `patch_path` says what did.
        result = CoderResult(
            ok=False,
            name=proposal["name"],
            summary=plan.get("summary"),
            files_changed=file_paths,
            error=f"patch awaiting owner review: {diff_path}",
            patch_path=str(diff_path),
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    snapshot = _snapshot(file_paths, repo_root)
    try:
        _write_new_files(new_files, repo_root)
        _apply_edits(edits, repo_root)
    except Exception as apply_err:  # the preview passed; a race on disk
        _restore(snapshot, repo_root)
        result = CoderResult(
            ok=False, name=proposal["name"], error=f"apply failed: {apply_err}",
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    smoke_ok, smoke_out = run_smoke(repo_root)
    if not smoke_ok:
        _restore(snapshot, repo_root)
        result = CoderResult(
            ok=False,
            name=proposal["name"],
            summary=plan.get("summary"),
            files_changed=file_paths,
            error="smoke test failed",
            smoke_stderr=smoke_out,
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result

    sha = _git_commit(repo_root, plan.get("summary", ""), proposal["name"], file_paths)
    if sha is None:
        _restore(snapshot, repo_root)
        result = CoderResult(
            ok=False,
            name=proposal["name"],
            summary=plan.get("summary"),
            files_changed=file_paths,
            error=(
                "git commit refused or failed; the repository must have a "
                "clean index and all changed files must be inside it"
            ),
        )
        _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
        return result
    result = CoderResult(
        ok=True,
        name=proposal["name"],
        summary=plan.get("summary"),
        files_changed=file_paths,
        commit_sha=sha,
    )
    _log(paths, started, proposal, result, plan=plan, duration_seconds=time.monotonic() - t0)
    return result


def _log(
    paths: LabPaths,
    started: str,
    proposal: dict[str, str],
    result: CoderResult,
    *,
    plan: dict[str, Any] | None = None,
    duration_seconds: float = 0.0,
) -> None:
    record = {
        "started_at": started,
        "ended_at": now_iso(),
        "name": result.name,
        "ok": result.ok,
        "feasible": result.feasible,
        "summary": result.summary,
        "files_changed": result.files_changed,
        "commit_sha": result.commit_sha,
        "patch_path": result.patch_path,
        "error": result.error,
        "smoke_stderr": (result.smoke_stderr[-1500:] if result.smoke_stderr else None),
        "duration_seconds": duration_seconds,
        "edits_count": (
            len(plan.get("edits", []) or []) + len(plan.get("new_files", []) or [])
        ) if plan else 0,
    }
    append_jsonl(paths.root / "coder_log.jsonl", record)

    status = "✓" if result.ok else ("⏸" if result.patch_path else "✗")
    notebook_append(
        paths.notebook,
        f"## {now_iso()} — Coder {status} {result.name}\n\n"
        f"**Summary**: {result.summary or '(none)'}\n\n"
        f"**Files**: `{result.files_changed or []}`\n\n"
        + (f"**Commit**: `{result.commit_sha}`\n\n" if result.commit_sha else "")
        + (f"**Patch**: `{result.patch_path}`\n\n" if result.patch_path else "")
        + (f"**Error**: {result.error}\n\n" if result.error and not result.patch_path else "")
    )
