"""Schema for the platform-shaped Writer output.

Two halves: a pydantic model for the YAML frontmatter, and a small
structural check that the body contains the five required sections,
in order, with non-empty content.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, model_validator


REQUIRED_SECTIONS_IN_ORDER: tuple[str, ...] = (
    "Motivation",
    "Methods",
    "Results",
    "Conclusion",
    "Next questions",
)


class MetricProvenance(BaseModel):
    name: str = Field(..., min_length=1)
    value: float
    delta_vs_baseline: float | None = None
    runs: list[str] = Field(..., min_length=1)
    seeds: list[int] = Field(..., min_length=1)
    comparator_name: str | None = None
    comparator_value: float | None = None
    aggregate: Literal["min", "max"] | None = None


class PaperFrontmatter(BaseModel):
    lab_id: str = Field(..., min_length=1)
    domain: str = Field(..., min_length=1)
    subdomain: str | None = None
    pi_handle: str | None = None
    campaign_id: str = Field(..., min_length=1)
    hypothesis_hash: str = Field(..., pattern=r"^sha256:[0-9a-f]{64}$")
    hypothesis_path: str = Field(..., min_length=1)
    code_repo: str | None = None
    code_sha: str | None = None
    metric_provenance: list[MetricProvenance] = Field(..., min_length=1)
    novelty_claim: str = Field(..., min_length=1)
    published_at: str = Field(..., min_length=1)
    status: Literal["preprint", "draft"]

    @model_validator(mode="after")
    def _code_pointers_paired(self) -> "PaperFrontmatter":
        if (self.code_repo is None) != (self.code_sha is None):
            raise ValueError(
                "code_repo and code_sha must both be set or both absent"
            )
        return self

    @model_validator(mode="after")
    def _novelty_claim_nontrivial(self) -> "PaperFrontmatter":
        if not self.novelty_claim.strip():
            raise ValueError("novelty_claim must be non-whitespace")
        return self


_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def structural_check(body: str) -> tuple[bool, list[str]]:
    """Return (ok, errors). Errors enumerate each violation found."""
    headers = _SECTION_RE.findall(body)
    errors: list[str] = []

    missing = [s for s in REQUIRED_SECTIONS_IN_ORDER if s not in headers]
    for s in missing:
        errors.append(f"missing required section: {s}")

    # Filter headers to just the required ones, preserving order, and check order.
    present = [h for h in headers if h in REQUIRED_SECTIONS_IN_ORDER]
    expected_order = [s for s in REQUIRED_SECTIONS_IN_ORDER if s in present]
    if present != expected_order:
        errors.append(
            f"sections out of order; got {present}, expected order {expected_order}"
        )

    # Non-empty body per required section
    sections = _split_sections(body)
    for s in REQUIRED_SECTIONS_IN_ORDER:
        if s in sections and not sections[s].strip():
            errors.append(f"section '{s}' is empty")

    return (len(errors) == 0, errors)


def _split_sections(body: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []
    for line in body.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            if current is not None:
                parts[current] = "\n".join(buf).strip()
            current = m.group(1)
            buf = []
        else:
            if current is not None:
                buf.append(line)
    if current is not None:
        parts[current] = "\n".join(buf).strip()
    return parts
