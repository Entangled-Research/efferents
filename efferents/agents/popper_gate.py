"""Headless Popper Probe intake for the orchestrator.

Single-shot self-play: load SKILL.md as system prompt, ask the model to
play both roles (claimant + Popperian probe) and emit ONLY the
hypothesis.md contents. Subprocess-validate with popper-probe's existing
CLI. One retry on validator fail.

Popper-probe repo location: env POPPER_PROBE_REPO, default ~/Documents/popper-probe.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from efferents.agents.budget import BudgetTracker, CallUsage


def _popper_repo() -> Path:
    return Path(os.environ.get("POPPER_PROBE_REPO", str(Path.home() / "Documents/popper-probe")))


def _skill_md() -> str:
    return (_popper_repo() / "skills/intake/SKILL.md").read_text()


def _validator() -> Path:
    return _popper_repo() / "scripts/validate_hypothesis.py"


_HEADLESS_INSTRUCTION = """
HEADLESS MODE — IMPORTANT

You are running without an interactive user. Play BOTH roles yourself:
the claimant (using the draft claim below as their starting position)
AND the Popperian probe. Run Probes 1, 2, and 3 internally. Probe 0
(SoTA orientation) is skipped. Probe 4 (distinctiveness) is recorded as
flagged or substantive.

If Probe 1 or Probe 2 cannot be satisfied even after a real sharpening
attempt, emit a hypothesis.md with `falsifiability_gate: failed`,
`status: unfalsifiable`, and a `## Diagnostic` section. Otherwise emit
`falsifiability_gate: passed`, `status: active`, and the full body
sections per the schema.

Output ONLY the hypothesis.md file contents (YAML frontmatter +
markdown body). No commentary, no code fences, no preamble. Your first
character must be a literal "---" opening the frontmatter.
""".strip()



INTERACTIVE_INSTRUCTION = """
INTERACTIVE WEB MODE — IMPORTANT

You are the Popperian probe. The human you are sparring with types in a web
chat and reads your replies there. Follow the protocol above with these
adjustments:

- You have NO tools: no filesystem, no web search, no papers. Probe 0's
  paper-based pieces are unavailable; say so if the human asks. Everything
  you know about the field comes from training, hedged as the protocol says.
- Stay in ONE probe stage per turn and ask ONE question per turn. Keep each
  reply under 150 words unless you are emitting the hypothesis file.
- The human's claim will be tested by an automated experiment runner that
  reports numeric metrics per run. Push the operational restatement and the
  falsifier toward quantities such a runner could measure per run.
- When the gate is decided (passed OR failed), emit the COMPLETE
  hypothesis.md inside exactly one fenced block whose info string is
  `hypothesis.md`, then ask: "Approve, or tell me what to change?" If the
  human asks for changes, re-emit the whole corrected file the same way.
- Frontmatter must carry `slug`, `created` (today's ISO date), `status`,
  `falsifiability_gate`, and `literature_pass: none`. Leave the
  `## References` section empty (no `- path:` entries) — nothing can be
  attached from a web chat.
- Never write about files being saved; the server stores the draft.
""".strip()

_HYPOTHESIS_BLOCK_RE = __import__("re").compile(
    r"```(?:hypothesis\.md|hypothesis|markdown|md|yaml)?[ \t]*\r?\n(---\r?\n[\s\S]*?)\r?\n```",
)


def extract_hypothesis_block(text: str) -> str | None:
    """The last fenced hypothesis.md draft in a model reply, or None."""
    matches = _HYPOTHESIS_BLOCK_RE.findall(text or "")
    if not matches:
        return None
    body = matches[-1].strip()
    return body + "\n" if not body.endswith("\n") else body


def validate_hypothesis_text(text: str, scratch_dir: Path) -> tuple[bool, str]:
    """Run popper-probe's validator over an in-memory draft."""
    scratch_dir = Path(scratch_dir)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    draft = scratch_dir / "hypothesis.md"
    draft.write_text(text)
    return _validate(draft)


def frontmatter_value(text: str, key: str) -> str | None:
    """Read one scalar from a hypothesis.md frontmatter block."""
    lines = (text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        k, sep, v = line.partition(":")
        if sep and k.strip() == key:
            return v.strip().strip("'\"")
    return None

@dataclass
class GateResult:
    ok: bool
    path: Path | None
    hash: str | None
    reason: str | None  # populated on failure


CHARTER_FILENAME = "popper.md"

_CHARTER_HEADER = """\
---
document: lab charter (popper.md)
nature: living document — guidance, not rules
---

# Lab charter

This file tracks the lab's research direction as it passes through the
Popper Probe: the initial direction prompted by the lab's funder, and every
subsequent probed hypothesis that opened a new line of work.

**How students and supervisors should use it.** Read this before proposing
or prioritizing work. It orients: it says where the lab started, what design
decisions were made at each gate, and why. It does not bind: requirements
change, and when the evidence says the direction should move, amend this
file (append below — never rewrite earlier entries) rather than obey it.
Proposals that depart from the charter are legitimate; silent departures are
not. When classifying or onboarding new students, use the entries below to
decide which lines of work a student continues versus opens fresh.

## Entries
"""


def write_charter(
    context_dir: str | Path,
    *,
    initial_direction: str,
    prompted_by: str = "unrecorded",
    hypothesis_path: str | Path | None = None,
    hypothesis_hash: str | None = None,
    design_notes: str = "",
    title: str | None = None,
) -> Path:
    """Record a gate passage in the lab's charter (``context/popper.md``).

    Creates the charter on first call; afterwards appends entries only —
    earlier entries are history, not editable policy. ``initial_direction``
    is preserved verbatim so the direction as actually prompted (by the
    funder at intake, or by a student opening a campaign later) stays
    distinguishable from what the probe sharpened it into.
    """
    cd = Path(context_dir)
    cd.mkdir(parents=True, exist_ok=True)
    charter = cd / CHARTER_FILENAME
    if not charter.exists():
        charter.write_text(_CHARTER_HEADER)

    from datetime import date

    lines = [
        "",
        f"### {date.today().isoformat()} — {title or 'probed direction'}",
        "",
        f"- **Prompted by**: {prompted_by}",
        "- **Direction as prompted (verbatim)**:",
        "",
        "> " + "\n> ".join(initial_direction.strip().splitlines() or ["(empty)"]),
        "",
    ]
    if hypothesis_path is not None:
        lines.append(f"- **Gated hypothesis**: `{hypothesis_path}`"
                     + (f" ({hypothesis_hash})" if hypothesis_hash else ""))
    if design_notes.strip():
        lines += ["- **Design decisions at the gate**:", ""]
        lines += [f"  {ln}" if ln.strip() else "" for ln in design_notes.strip().splitlines()]
    with charter.open("a") as fh:
        fh.write("\n".join(lines) + "\n")
    return charter


def _hash_file(p: Path) -> str:
    return "sha256:" + hashlib.sha256(p.read_bytes()).hexdigest()


def _extract_text(response: Any) -> str:
    return "".join(block.text for block in response.content if getattr(block, "type", "text") == "text")


def _validate(path: Path) -> tuple[bool, str]:
    v = _validator()
    if not v.exists():
        return False, f"validator not found: {v} (check POPPER_PROBE_REPO)"
    try:
        proc = subprocess.run(
            [sys.executable, str(v), str(path)],
            capture_output=True, text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False, "validator timed out after 10s"
    return (proc.returncode == 0, (proc.stderr or proc.stdout).strip())


def run_gate(
    *,
    draft_claim: str,
    slug: str,
    corpus_root: Path,
    client: Any,
    budget: BudgetTracker | None = None,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 4096,
    charter_dir: str | Path | None = None,
    prompted_by: str = "unrecorded",
) -> GateResult:
    """Run single-shot self-play intake. Writes hypothesis.md on success.

    Returns GateResult with ok=True/path/hash on accept, or ok=False/reason
    on drop after one retry. When ``charter_dir`` is given, a successful gate
    also appends the probed direction to the lab charter
    (``<charter_dir>/popper.md``) so the design decision is tracked for
    future students and supervisors.
    """
    out_dir = corpus_root / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "hypothesis.md"

    system = _skill_md() + "\n\n" + _HEADLESS_INSTRUCTION

    user_first = (
        f"Draft claim to process:\n\n{draft_claim}\n\n"
        f"Emit the hypothesis.md for slug `{slug}` now."
    )
    last_errors = ""

    for attempt in (1, 2):
        if attempt == 1:
            user_msg = user_first
        else:
            user_msg = (
                f"{user_first}\n\n"
                f"Your previous output failed validate_hypothesis.py with:\n\n"
                f"{last_errors}\n\n"
                f"Emit a corrected hypothesis.md. Same output rules apply."
            )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        if budget is not None:
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
            budget.record(
                agent="popper_gate",
                model=model,
                usage=usage,
                notes=f"attempt={attempt}",
            )
        body = _extract_text(response)
        out_path.write_text(body)
        ok, errors = _validate(out_path)
        if ok:
            result = GateResult(ok=True, path=out_path, hash=_hash_file(out_path), reason=None)
            if charter_dir is not None:
                write_charter(
                    charter_dir,
                    initial_direction=draft_claim,
                    prompted_by=prompted_by,
                    hypothesis_path=out_path,
                    hypothesis_hash=result.hash,
                    design_notes=(
                        "Headless self-play gate (probes 1–3 internal); the "
                        "sharpened claim and falsifier live in the gated "
                        "hypothesis file. Interactive intakes should record "
                        "the dialogue's sharpening decisions here instead."
                    ),
                    title=f"campaign gate: {slug}",
                )
            return result
        last_errors = errors

    # A rejected draft must not linger on disk looking like a hypothesis.
    out_path.unlink(missing_ok=True)
    return GateResult(ok=False, path=None, hash=None, reason=f"validate failed: {last_errors}")
