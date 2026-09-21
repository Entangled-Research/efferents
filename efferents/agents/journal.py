"""Journal + rejected-paper file management.

Pure-Python file IO. Called by the writer's peer-review pipeline after
`reviewer.decide(...)` returns.

Three responsibilities:
1. `write_reviews_file` — paper/<id>.reviews.md  (the 3 reviews + decision)
2. `write_rebuttal_file` — paper/<id>.rebuttal.md  (the Student's response)
3. `append_journal` / `append_rejected` — paper/journal.md (accepted, newest-on-top)
   and paper/rejected.md (rejected, append-only).

The journal uses a sentinel `<!-- ENTRIES BELOW -->` marker so we can insert
new entries at the top of the entry list without reading-and-rewriting the
whole file. Rejected.md is plain append-only (chronological).
"""
from __future__ import annotations

import subprocess
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from efferents.agents.reviewer import Review

JOURNAL_HEADER = (
    "# Lab journal — accepted findings\n\n"
    "Append-only. Each entry is one paper accepted by the 3-reviewer board.\n"
    "Newest at the top — `head -60 paper/journal.md` is the recent state.\n\n"
    "<!-- ENTRIES BELOW -->\n"
)
REJECTED_HEADER = (
    "# Lab journal — rejected submissions\n\n"
    "Append-only. Each entry is one paper that failed the peer-review gate.\n"
    "Keeps a public record so the Student can learn from rejected work.\n"
    "Chronological (newest at the bottom).\n\n"
)
ENTRY_SENTINEL = "<!-- ENTRIES BELOW -->"


def write_reviews_file(
    out_path: Path,
    *,
    campaign_id: str,
    reviews: list[Review],
    decision: dict[str, Any],
) -> None:
    """Write `paper/<id>.reviews.md` with the decision header + 3 reviews."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    accept_str = "ACCEPT" if decision.get("accept") else "REJECT"
    lines = [
        f"# Reviews — {campaign_id}",
        "",
        "## Decision",
        "",
        f"**Verdict**: **{accept_str}**",
        f"**Mean score**: {decision.get('mean_score', 0.0):.2f}",
        f"**Min score**: {decision.get('min_score', 0)}",
        f"**Reason**: {decision.get('reason', '?')}",
        "",
        "| persona | score |",
        "| --- | --- |",
    ]
    for r in reviews:
        lines.append(f"| {r.persona} | {r.score} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    for r in reviews:
        lines.append(r.to_markdown() if r.raw_md else r.to_markdown())
        lines.append("---")
        lines.append("")
    out_path.write_text("\n".join(lines).rstrip() + "\n")
    out_path.with_suffix(".json").write_text(json.dumps({
        "campaign_id": campaign_id, "decision": decision,
        "reviews": [asdict(review) for review in reviews],
    }, indent=2) + "\n")


def write_rebuttal_file(out_path: Path, *, campaign_id: str, rebuttal_text: str) -> None:
    """Write `paper/<id>.rebuttal.md`. Adds a one-line frontmatter so the
    decision pipeline can identify the rebuttal independently of the paper."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = rebuttal_text.strip() or "## Rebuttal\n\n(no rebuttal produced)"
    if not body.lstrip().startswith("## Rebuttal"):
        body = "## Rebuttal\n\n" + body
    header = f"<!-- campaign: {campaign_id} -->\n\n"
    out_path.write_text(header + body + "\n")


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _lab_metadata() -> tuple[str, str | None, str | None]:
    """Return (lab_id, code_repo, code_sha_short). Federation-ready provenance
    for every journal entry: which lab produced this finding and at what code
    revision. code_repo / code_sha may be None if unresolvable (no git, no
    CODE_REPO configured)."""
    from efferents import lab as _lab
    lab_id = _lab.LAB_ID
    code_repo = _lab.CODE_REPO if getattr(_lab, "CODE_REPO", None) else None
    code_sha: str | None = None
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        )
        code_sha = out.decode().strip() or None
    except Exception:
        pass
    return lab_id, code_repo, code_sha


def _code_line(code_repo: str | None, code_sha: str | None) -> str | None:
    if code_repo and code_sha:
        return f"**Code**: {code_repo}@{code_sha}"
    if code_sha:
        return f"**Code**: @{code_sha}"
    return None


def append_journal(
    journal_path: Path,
    *,
    campaign_id: str,
    headline: str,
    decision: dict[str, Any],
    paper_filename: str | None = None,
    student_id: str = "primary",
    lab_id: str | None = None,
    code_repo: str | None = None,
    code_sha: str | None = None,
) -> None:
    """Insert a new accepted-paper entry at the top of the entry list.

    Federation-ready provenance: each entry stamps `**Lab**: <lab_id>` and
    optionally `**Code**: <repo>@<sha>` so other labs (or future-us reading
    a federation-synced journal) can identify origin and code-revision.

    If `lab_id` / `code_repo` / `code_sha` are not supplied, they default
    from the active LabConfig + git HEAD.

    Creates the file with header if missing. Entry format:

        ## 2026-05-26 14:32 UTC — <campaign_id>
        **Lab**: <lab-id>
        **Student**: primary
        **Headline**: <headline>
        **Scores**: critical=6, neutral=7, enthusiast=8 (mean=7.0)
        **Code**: https://github.com/.../lab@abc1234
        **Paper**: [c-aa11.md](c-aa11.md) · ...
    """
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    if not journal_path.exists():
        journal_path.write_text(JOURNAL_HEADER)

    if lab_id is None or code_repo is None or code_sha is None:
        d_lab_id, d_repo, d_sha = _lab_metadata()
        lab_id = lab_id or d_lab_id
        code_repo = code_repo or d_repo
        code_sha = code_sha or d_sha

    per_persona = decision.get("per_persona") or {}
    scores_str = ", ".join(f"{k}={v}" for k, v in per_persona.items())
    if scores_str:
        scores_str += f" (mean={decision.get('mean_score', 0.0):.1f})"
    else:
        scores_str = f"mean={decision.get('mean_score', 0.0):.1f}"

    paper_filename = paper_filename or f"{campaign_id}.md"
    lines = [
        f"\n## {_now_str()} — {campaign_id}",
        f"**Lab**: {lab_id}",
        f"**Student**: {student_id}",
        f"**Headline**: {headline}",
        f"**Scores**: {scores_str}",
    ]
    code_line = _code_line(code_repo, code_sha)
    if code_line:
        lines.append(code_line)
    lines.append(
        f"**Paper**: [{paper_filename}]({paper_filename})"
        f" · **Reviews**: [{campaign_id}.reviews.md]({campaign_id}.reviews.md)"
        f" · **Rebuttal**: [{campaign_id}.rebuttal.md]({campaign_id}.rebuttal.md)"
    )
    entry = "\n".join(lines) + "\n"

    content = journal_path.read_text()
    if ENTRY_SENTINEL in content:
        # Insert entry right after the sentinel line.
        marker_end = content.index(ENTRY_SENTINEL) + len(ENTRY_SENTINEL)
        new_content = content[:marker_end] + "\n" + entry + content[marker_end:]
    else:
        # No sentinel (legacy or hand-edited file) — append at end.
        new_content = content.rstrip() + "\n" + entry
    journal_path.write_text(new_content)


def append_rejected(
    rejected_path: Path,
    *,
    campaign_id: str,
    headline: str,
    decision: dict[str, Any],
    paper_filename: str | None = None,
    student_id: str = "primary",
    lab_id: str | None = None,
    code_repo: str | None = None,
    code_sha: str | None = None,
) -> None:
    """Append an entry to paper/rejected.md (chronological, newest at end).

    Federation-ready: same Lab/Code stamps as append_journal so external
    consumers can attribute rejections back to their lab of origin too."""
    rejected_path.parent.mkdir(parents=True, exist_ok=True)
    if not rejected_path.exists():
        rejected_path.write_text(REJECTED_HEADER)

    if lab_id is None or code_repo is None or code_sha is None:
        d_lab_id, d_repo, d_sha = _lab_metadata()
        lab_id = lab_id or d_lab_id
        code_repo = code_repo or d_repo
        code_sha = code_sha or d_sha

    per_persona = decision.get("per_persona") or {}
    scores_str = ", ".join(f"{k}={v}" for k, v in per_persona.items())
    if scores_str:
        scores_str += f" (mean={decision.get('mean_score', 0.0):.1f})"
    else:
        scores_str = f"mean={decision.get('mean_score', 0.0):.1f}"
    paper_filename = paper_filename or f"{campaign_id}.md"
    lines = [
        f"\n## {_now_str()} — {campaign_id}",
        f"**Lab**: {lab_id}",
        f"**Student**: {student_id}",
        f"**Headline**: {headline}",
        f"**Scores**: {scores_str}",
        f"**Reason**: {decision.get('reason', '?')}",
    ]
    code_line = _code_line(code_repo, code_sha)
    if code_line:
        lines.append(code_line)
    lines.append(f"**Paper**: [{paper_filename}]({paper_filename})")
    entry = "\n".join(lines) + "\n"
    with rejected_path.open("a") as f:
        f.write(entry)


def auto_commit_paper(
    *,
    repo_root: Path,
    campaign_id: str,
    headline: str,
    decision: dict[str, Any],
    extra_files: list[str] | None = None,
) -> str | None:
    """Stage + commit the accepted paper bundle. Returns the short SHA on
    success, None on failure. Mirrors agents/coder.py:_git_commit shape.

    Files staged:
        paper/<id>.md
        paper/<id>.reviews.md
        paper/<id>.rebuttal.md
        paper/journal.md
      plus anything in `extra_files` (e.g., paper/rejected.md if an earlier
      pass left it dirty — opportunistic mop-up).

    Failure to commit is non-fatal (the artifacts stay on disk); the caller
    should log and continue.
    """
    files = [
        f"paper/{campaign_id}.md",
        f"paper/{campaign_id}.reviews.md",
        f"paper/{campaign_id}.reviews.json",
        f"paper/{campaign_id}.rebuttal.md",
        "paper/journal.md",
    ]
    if extra_files:
        files.extend(extra_files)

    per_persona = decision.get("per_persona") or {}
    scores_str = ", ".join(f"{k}={v}" for k, v in per_persona.items())
    co_authors = ["critical-reviewer", "neutral-reviewer", "optimistic-reviewer", "Student"]
    msg = (
        f"paper({campaign_id}): {headline}\n\n"
        f"Accepted by peer review (mean={decision.get('mean_score', 0.0):.1f}, "
        f"min={decision.get('min_score', 0)}; {scores_str}).\n\n"
        "Reviews + rebuttal committed alongside the paper.\n\n"
        + "".join(
            f"Co-Authored-By: {who} <noreply@anthropic.com>\n"
            for who in co_authors
        )
    ).rstrip("\n")

    relative_files = list(dict.fromkeys(files))
    try:
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=str(repo_root),
            capture_output=True,
        )
        if staged.returncode != 0:
            return None
        subprocess.run(
            ["git", "add", "--", *relative_files],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
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
        return sha or None
    except subprocess.CalledProcessError:
        subprocess.run(
            ["git", "restore", "--staged", "--", *relative_files],
            cwd=str(repo_root),
            capture_output=True,
        )
        return None
