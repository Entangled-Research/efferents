"""Reviewer agent — peer-review board for paper artifacts.

Each campaign that clears the mechanical `should_publish` gate (novelty +
≥5% metric gain; agents/writer.py) is submitted to a 3-reviewer board
before it can be accepted into the journal. The three personas are:

    critical    — adversarial; looks for confounds, weak baselines, p-hacking,
                  alternative mechanisms. Score-ceiling 6 unless airtight.
    neutral     — balanced; is the claim supported, methodology reproducible,
                  contribution clear.
    enthusiast  — constructive; takes the claim seriously, suggests
                  strengthenings; ceiling 9 (no 10s without exceptional case).

Each reviewer scores 1–10 (OpenReview-style; see the prompts) and surfaces
strengths / weaknesses / questions. `decide()` aggregates scores against
the configured LabConfig peer-review thresholds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import anthropic

from efferents.agents.budget import BudgetTracker, CallUsage, model_for
from efferents.agents.prompts.loader import load_prompt
from efferents.agents.state import parse_json_with_one_retry

Persona = Literal["critical", "neutral", "enthusiast"]
PERSONAS: tuple[Persona, ...] = ("critical", "neutral", "enthusiast")


@dataclass
class Review:
    persona: str
    score: int
    summary: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    raw_md: str = ""

    def to_markdown(self) -> str:
        """Render as a markdown block for the per-paper reviews.md side-car."""
        lines = [
            f"### Reviewer: {self.persona} — score {self.score}/10",
            "",
            f"**Summary**: {self.summary}",
            "",
            "**Strengths**:",
        ]
        lines.extend(f"- {s}" for s in self.strengths) if self.strengths else lines.append("- (none flagged)")
        lines.append("")
        lines.append("**Weaknesses**:")
        lines.extend(f"- {w}" for w in self.weaknesses) if self.weaknesses else lines.append("- (none flagged)")
        lines.append("")
        lines.append("**Questions for rebuttal**:")
        lines.extend(f"- {q}" for q in self.questions) if self.questions else lines.append("- (none)")
        lines.append("")
        return "\n".join(lines)


def _prompt_for(persona: Persona) -> str:
    return load_prompt(f"reviewer_{persona}")


def review(
    *,
    paper_path: Path,
    persona: Persona,
    client: anthropic.Anthropic,
    budget: BudgetTracker,
    model: str | None = None,
    max_tokens: int = 2048,
) -> Review:
    """Single peer review of one paper artifact by one persona."""
    if persona not in PERSONAS:
        raise ValueError(f"persona must be one of {PERSONAS}; got {persona!r}")
    chosen = model or model_for("reviewer")
    if chosen is None:
        raise RuntimeError("No model configured for Reviewer")

    paper_md = paper_path.read_text()
    system = [{
        "type": "text", "text": _prompt_for(persona),
        "cache_control": {"type": "ephemeral"},
    }]
    user_block = (
        "## Paper under review\n\n"
        + paper_md
        + "\n\n---\n\nReview this paper now. Emit strict JSON per your "
        "system prompt format. First character `{`. No prose, no fences."
    )
    messages = [{"role": "user", "content": [{"type": "text", "text": user_block}]}]

    def _call(retry_msgs):
        final_msgs = messages + (retry_msgs or [])
        resp = client.messages.create(
            model=chosen, max_tokens=max_tokens, system=system, messages=final_msgs,
        )
        usage = CallUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        budget.record(
            agent="reviewer", model=chosen, usage=usage,
            notes=f"persona={persona}" + (" (retry)" if retry_msgs else ""),
        )
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")

    parsed, status = parse_json_with_one_retry(
        call_fn=_call,
        must_contain='"score"',
        fallback={
            "score": 5,
            "summary": f"[{persona} reviewer failed to parse after one retry]",
            "strengths": [],
            "weaknesses": ["review JSON did not parse — treat with skepticism"],
            "questions": [],
            "_parse_error": True,
        },
    )

    # Coerce types defensively.
    score = parsed.get("score", 5)
    try:
        score_int = int(score)
    except (TypeError, ValueError):
        score_int = 5
    score_int = max(1, min(10, score_int))

    def _as_str_list(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x) for x in v if x]
        return []

    rev = Review(
        persona=persona,
        score=score_int,
        summary=str(parsed.get("summary") or "(no summary)"),
        strengths=_as_str_list(parsed.get("strengths")),
        weaknesses=_as_str_list(parsed.get("weaknesses")),
        questions=_as_str_list(parsed.get("questions")),
        raw_md="",
    )
    rev.raw_md = rev.to_markdown()
    return rev


def decide(
    reviews: list[Review],
    *,
    accept_mean: float | None = None,
    accept_min: int | None = None,
) -> dict[str, Any]:
    """Aggregate three reviews → accept/reject. Pure-Python, no API.

    Defaults pull from efferents.lab (PEER_REVIEW_ACCEPT_*). Pass explicit
    values for tests."""
    if accept_mean is None or accept_min is None:
        from efferents import lab as _lab  # local import to avoid circulars
        accept_mean = accept_mean if accept_mean is not None else _lab.PEER_REVIEW_ACCEPT_MEAN_THRESHOLD
        accept_min = accept_min if accept_min is not None else _lab.PEER_REVIEW_ACCEPT_MIN_THRESHOLD

    if not reviews:
        return {
            "accept": False,
            "mean_score": 0.0,
            "min_score": 0,
            "reason": "no reviews",
            "per_persona": {},
        }

    scores = [r.score for r in reviews]
    mean = sum(scores) / len(scores)
    mn = min(scores)
    accept = mean >= accept_mean and mn >= accept_min
    reason = (
        f"mean={mean:.2f}, min={mn} — "
        + (
            f"accept (≥ {accept_mean:.1f} mean and ≥ {accept_min} min)"
            if accept
            else f"reject (need mean ≥ {accept_mean:.1f} AND min ≥ {accept_min})"
        )
    )
    return {
        "accept": accept,
        "mean_score": round(mean, 2),
        "min_score": mn,
        "reason": reason,
        "per_persona": {r.persona: r.score for r in reviews},
    }
