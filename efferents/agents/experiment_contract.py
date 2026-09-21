"""A bounded, auditable experiment contract with no external intake dependency."""
from __future__ import annotations

import hashlib
from pathlib import Path

import yaml

from efferents.agents.popper_gate import GateResult
from efferents.lab import LabConfig


def write_contract(*, claim: str, slug: str, root: Path, cfg: LabConfig) -> GateResult:
    if not isinstance(claim, str) or not claim.strip() or len(claim) > 12000:
        return GateResult(False, None, None, "A concrete claim is required (1–12000 characters).")
    if not cfg.falsifiers:
        return GateResult(False, None, None, "Configure a measurable falsifier before opening a campaign.")
    measurement = f"Measure {cfg.metrics.headline.column}; direction: {cfg.metrics.headline.direction}."
    conditions = "\n".join(f"- {f.id}: {f.description}" for f in cfg.falsifiers)
    body = ("---\n" + yaml.safe_dump({"slug": slug, "validation": "lightweight", "status": "active"})
            + f"---\n\n## Claim\n\n{claim.strip()}\n\n## Measurement\n\n{measurement}\n\n"
            + f"## Stop condition\n\n{conditions}\n\n"
            + "This contract checks structure and configured measurements. It is not a scientific endorsement.\n")
    path = root / slug / "hypothesis.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return GateResult(True, path, "sha256:" + hashlib.sha256(body.encode()).hexdigest(), None)
