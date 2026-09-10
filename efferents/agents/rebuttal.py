"""Rebuttal agent — Student's one-shot response to the 3-reviewer board.

Called after `reviewer.review(...)` returns three reviews for a paper that
cleared the mechanical `should_publish` gate. The rebuttal is the author's
*only* response (one-shot system — no revise-and-resubmit). It addresses
each reviewer's `questions` and `weaknesses`, acknowledges valid
criticism, and defends where appropriate. It does NOT promise future
experiments — those would never run since the editor decides immediately.

The rebuttal lands at `paper/<campaign_id>.rebuttal.md` and is included
verbatim in the decision context (when peer-review pipeline composes
the final accept/reject decision).
"""
from __future__ import annotations

from pathlib import Path

import anthropic

from efferents.agents.budget import BudgetTracker, CallUsage, model_for
from efferents.agents.prompts.loader import load_prompt
from efferents.agents.reviewer import Review


def _reviews_block(reviews: list[Review]) -> str:
    """Render the three reviews into a single markdown block fed to the
    rebuttal-writer's user message."""
    parts = []
    for r in reviews:
        parts.append(r.to_markdown() if r.raw_md == "" else r.raw_md)
        parts.append("---")
    return "\n".join(parts).rstrip("-\n ")


def write_rebuttal(
    *,
    paper_path: Path,
    reviews: list[Review],
    client: anthropic.Anthropic,
    budget: BudgetTracker,
    model: str | None = None,
    max_tokens: int = 2048,
) -> str:
    """One-shot rebuttal composition. Returns the rebuttal markdown.

    The Student does NOT have the lit_review tool here — the rebuttal is
    purely a defensive/explanatory document grounded in what already exists
    in the paper + reviews.
    """
    chosen = model or model_for("rebuttal")
    if chosen is None:
        raise RuntimeError("No model configured for Rebuttal")

    paper_md = paper_path.read_text()
    system = [{
        "type": "text", "text": load_prompt("rebuttal"),
        "cache_control": {"type": "ephemeral"},
    }]

    user_block = (
        "## Your paper\n\n"
        + paper_md
        + "\n\n---\n\n## The three reviews\n\n"
        + _reviews_block(reviews)
        + "\n\n---\n\nWrite the rebuttal now. Markdown body only. "
        "No preamble; start with a `## Rebuttal` heading."
    )

    resp = client.messages.create(
        model=chosen,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": [{"type": "text", "text": user_block}]}],
    )
    usage = CallUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    )
    budget.record(
        agent="rebuttal", model=chosen, usage=usage,
        notes=f"{len(reviews)} reviews",
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return text or "## Rebuttal\n\n(no rebuttal produced)\n"
