"""Model-led hypothesis refinement with an internal measurable-contract check.

The historical module and corpus names remain compatible with existing labs.
No external intake skill, checkout or validator is required.
"""
from __future__ import annotations

import hashlib
import re

import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from efferents.agents.budget import BudgetTracker, CallUsage, billing_model, model_for


def _skill_md() -> str:
    """The framework's own research-contract instructions, kept inline."""
    return """Refine the owner's research objective into a bounded, falsifiable experiment.
Preserve the objective and instructions. Resolve routine methodological choices yourself;
record assumptions, baselines, compute bounds and uncertainty. Ask the human only when
an unclear objective, conflicting instruction or missing essential resource blocks work.
Do not run a ritual interview or require a separate hypothesis approval.
Never invent measurements, citations, literature checks or scientific acceptance.
A passed gate means the proposed experiment is testable, not that the claim is true.
Emit YAML frontmatter with slug, created (ISO date), status: active,
falsifiability_gate: passed, literature_pass: none. Use these exact headings,
with descriptions in the section bodies, never appended to the headings:
## Original framing
## Operational restatement
## Falsifier(s)
## Test design
## Auxiliary assumptions
## Distinctiveness
## References
## Intake log
Preserve the owner's words in Original framing. In Operational restatement,
specify intervention, baseline, scope, numeric metric and threshold. State a
measured outcome that would refute the claim under Falsifier(s). In Test design,
name dataset, split, seeds, controls, resource and stop bounds. Label inferred
choices as Auxiliary assumptions. Name competing explanations in Distinctiveness.
Use only verified sources in References (otherwise leave it empty). Record
assumptions and decisions in Intake log.
If no measurable contract can be formed, use status: unfalsifiable,
falsifiability_gate: failed and a nonempty ## Diagnostic explaining the blocker.
""".strip()


_HEADLESS_INSTRUCTION = """Work autonomously from the supplied draft and instructions.
Internally critique the claim, controls and failure criterion before writing the contract.
Output ONLY hypothesis.md (YAML frontmatter and Markdown), without fences or preamble.
Do not manufacture missing owner objectives; emit a failed diagnostic if they block work.
""".strip()

INTERACTIVE_INSTRUCTION = """You are helping the owner scope an experiment in a web chat.
When the objective is clear, infer reversible defaults and emit the complete contract
inside one fenced block labelled hypothesis.md. Briefly state your assumptions.
Ask at most one focused question only if a missing objective or instruction blocks work.
Do not ask for routine approval or walk through named probe stages. The owner can edit
or redirect the draft. Do not claim you browsed, saved files or ran experiments.
Never ask for a private/public placement choice; publication authorization is separate.
""".strip()

_HYPOTHESIS_BLOCK_RE = re.compile(
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
    """Check a draft with the internal measurable-contract validator."""
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

This file tracks the lab's research direction as the model refines its
experiment contracts: the initial direction prompted by the lab's funder, and every
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
    """Validate structure, not scientific truth; semantic critique is model-led."""
    text = path.read_text()
    match = re.match(r"\A---\s*\n(.*?)\n---(?:\n|$)", text, re.S)
    if not match:
        return False, "ERROR: hypothesis requires YAML frontmatter"
    try:
        front = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return False, f"ERROR: invalid YAML: {exc}"
    if not isinstance(front, dict):
        return False, "ERROR: frontmatter must be a mapping"
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", str(front.get("slug", ""))):
        return False, "ERROR: a lowercase hypothesis slug is required"
    gate = front.get("falsifiability_gate")
    if gate not in {"passed", "failed"}:
        return False, "ERROR: falsifiability_gate must be passed or failed"
    sections = ("Operational restatement", "Falsifier(s)", "Test design") if gate == "passed" else ("Diagnostic",)
    for section in sections:
        part = re.search(r"(?ims)^## " + re.escape(section) + r"[ \t]*\n(.*?)(?=^## |\Z)", text)
        if not part or not part.group(1).strip():
            return False, f"ERROR: nonempty ## {section} is required"
    if gate == "passed" and front.get("status") != "active":
        return False, "ERROR: a passed contract must have status: active"
    return True, "OK: internal contract check passed (no scientific verdict)"


def run_gate(
    *,
    draft_claim: str,
    slug: str,
    corpus_root: Path,
    client: Any,
    budget: BudgetTracker | None = None,
    model: str | None = None,
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
    from efferents.agents.model_client import configured_model, default_text_output_tokens

    model = model or model_for("supervisor") or configured_model()
    max_tokens = default_text_output_tokens(model, ordinary=max_tokens)
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
                model=billing_model(client, model),
                usage=usage,
                notes=f"attempt={attempt}",
            )
        body = _extract_text(response)
        out_path.write_text(body)
        ok, errors = _validate(out_path)
        if ok and frontmatter_value(body, "falsifiability_gate") != "passed":
            out_path.unlink(missing_ok=True)
            return GateResult(ok=False, path=None, hash=None, reason=body)
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
                        "Automatic model-led refinement; the "
                        "sharpened claim and falsifier live in the gated "
                        "hypothesis file. Interactive intakes should record "
                        "the model's refinement decisions here instead."
                    ),
                    title=f"campaign gate: {slug}",
                )
            return result
        last_errors = errors

    # A rejected draft must not linger on disk looking like a hypothesis.
    out_path.unlink(missing_ok=True)
    return GateResult(ok=False, path=None, hash=None, reason=f"validate failed: {last_errors}")
