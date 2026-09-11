"""Lab placement — one lab per (topic, way-of-thinking) on a network.

Network policy: two labs MAY share a topic, but no two labs may attack the
same topic the same way — that is redundant. When a newcomer's proposed lab
matches an existing lab on both axes, the newcomer's agents are **hired**
into the existing research group as a new student (the framework's
multi-student machinery gives them their own campaigns, popper-corpus subdir,
and charter attribution) instead of founding a duplicate lab. A genuinely
different topic — or the same topic thought about differently — proceeds as
usual and founds a new lab.

Comparison is deterministic (declared-field token similarity) so it works
offline; the thresholds leave a documented gray zone that a network operator
can adjudicate with an LLM or a human editor. Labs SHOULD declare explicit
``topic:`` and ``approach:`` fields in lab.yaml / efferents.yaml; without
them the profile falls back to domain/goal/charter text, which is noisier.

CLI:
    efferents place <submission> --network <dir> [--network <dir> ...]
    efferents place <submission> --network <dir> --apply --student-id s2
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from efferents.agents.popper_gate import write_charter

# Similarity above `same`: axes match. Below `distinct`: axes differ.
# Between the two: gray zone — flagged for adjudication, treated as distinct
# (founding a lab is reversible by a later merge; a wrong forced-merge is not).
TOPIC_SAME, TOPIC_DISTINCT = 0.5, 0.25
APPROACH_SAME, APPROACH_DISTINCT = 0.5, 0.25

_STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it of on or over than that "
    "the their this to under via we with without you your maximize minimize "
    "improve tuning tune using use new novel lab".split()
)


@dataclass
class LabProfile:
    lab_id: str
    root: Path
    topic: str
    approach: str
    declared: bool  # explicit topic:/approach: fields vs noisy fallback


@dataclass
class Placement:
    action: str                      # "create" | "join"
    target: LabProfile | None        # the hiring lab when action == "join"
    siblings: list[str] = field(default_factory=list)   # same topic, different thinking
    gray_zone: list[str] = field(default_factory=list)  # needs adjudication
    scores: dict[str, tuple[float, float]] = field(default_factory=dict)

    def summary(self) -> str:
        lines = []
        for lab, (t, a) in sorted(self.scores.items()):
            lines.append(f"  vs {lab}: topic {t:.2f}, approach {a:.2f}")
        if self.action == "join":
            head = (f"JOIN {self.target.lab_id}: same topic, same way of "
                    f"thinking — redundant as a new lab; hire in as a student")
        else:
            head = "CREATE a new lab" + (
                f" (siblings on the same topic: {', '.join(self.siblings)})"
                if self.siblings else "")
        if self.gray_zone:
            head += f"\n  gray zone (adjudicate): {', '.join(self.gray_zone)}"
        return head + ("\n" + "\n".join(lines) if lines else "")


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z][a-z0-9\-]+", text.lower())
    return frozenset(w.rstrip("s") for w in words if w not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def extract_profile(lab_dir: str | Path) -> LabProfile:
    """Profile a lab (or lab submission) directory for placement."""
    root = Path(lab_dir).resolve()
    raw: dict = {}
    for name in ("lab.yaml", "efferents.yaml"):
        cfg = root / name
        if cfg.is_file():
            raw = yaml.safe_load(cfg.read_text()) or {}
            break
    topic = str(raw.get("topic", "") or "").strip()
    approach = str(raw.get("approach", "") or "").strip()
    declared = bool(topic and approach)

    if not topic:
        parts = [str(raw.get("domain", "") or ""), str(raw.get("goal", "") or "")]
        hyp = root / "hypothesis.md"
        if hyp.is_file():
            parts.append(hyp.read_text()[:2000])
        topic = " ".join(p for p in parts if p)
    if not approach:
        parts = [str(raw.get("goal", "") or "")]
        charter = root / "context" / "popper.md"
        if charter.is_file():
            parts.append(charter.read_text()[:2000])
        approach = " ".join(p for p in parts if p)

    return LabProfile(
        lab_id=str(raw.get("lab_id") or root.name),
        root=root,
        topic=topic,
        approach=approach,
        declared=declared,
    )


def scan_network(dirs: list[str | Path]) -> list[LabProfile]:
    """Every child directory (or the directory itself) holding a lab config."""
    profiles = []
    for d in dirs:
        d = Path(d)
        candidates = [d] + sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []
        for c in candidates:
            if (c / "lab.yaml").is_file() or (c / "efferents.yaml").is_file():
                profiles.append(extract_profile(c))
    return profiles


def classify(new: LabProfile, existing: LabProfile) -> tuple[str, float, float]:
    """-> (label, topic_score, approach_score); label in
    {redundant, sibling, distinct, gray}."""
    t = _jaccard(_tokens(new.topic), _tokens(existing.topic))
    a = _jaccard(_tokens(new.approach), _tokens(existing.approach))
    if t >= TOPIC_SAME and a >= APPROACH_SAME:
        return "redundant", t, a
    if t >= TOPIC_SAME and a <= APPROACH_DISTINCT:
        return "sibling", t, a
    if t <= TOPIC_DISTINCT:
        return "distinct", t, a
    return "gray", t, a


def place(new: LabProfile, network: list[LabProfile]) -> Placement:
    decision = Placement(action="create", target=None)
    best: tuple[float, LabProfile] | None = None
    for existing in network:
        if existing.root == new.root:
            continue
        label, t, a = classify(new, existing)
        decision.scores[existing.lab_id] = (t, a)
        if label == "redundant" and (best is None or t + a > best[0]):
            best = (t + a, existing)
        elif label == "sibling":
            decision.siblings.append(existing.lab_id)
        elif label == "gray":
            decision.gray_zone.append(existing.lab_id)
    if best is not None:
        decision.action = "join"
        decision.target = best[1]
    return decision


def hire(
    target_lab_dir: str | Path,
    *,
    student_id: str,
    focus: str,
    direction: str,
    prompted_by: str,
) -> Path:
    """Hire a newcomer into an existing lab as a student.

    Appends a ``students:`` roster entry to the lab's lab.yaml (text-level
    insertion so existing comments survive) and records the newcomer's
    direction verbatim in the lab charter. Their campaigns then run under
    their own student_id with the standard multi-student machinery.
    """
    root = Path(target_lab_dir).resolve()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", student_id):
        raise ValueError("student_id must match [a-z][a-z0-9_-]*")
    cfg_path = root / "lab.yaml"
    if not cfg_path.is_file():
        raise FileNotFoundError(f"{cfg_path} not found — can only hire into lab.yaml labs")
    text = cfg_path.read_text()
    existing = yaml.safe_load(text) or {}
    roster = existing.get("students") or [
        {"id": "primary", "handle": None, "focus": "", "prompt_overrides": {}}
    ]
    if any(s.get("id") == student_id for s in roster):
        raise ValueError(f"student id {student_id!r} already on the roster of {root.name}")

    block = yaml.safe_dump({"students": roster + [
        {"id": student_id, "handle": None, "focus": focus, "prompt_overrides": {}}
    ]}, sort_keys=False)
    if re.search(r"^students:", text, re.M):
        text = re.sub(r"^students:.*?(?=^[A-Za-z_][A-Za-z0-9_-]*:|\Z)",
                      lambda _: block, text, count=1, flags=re.M | re.DOTALL)
    else:
        text = text.rstrip("\n") + "\n" + block
    parsed = yaml.safe_load(text)
    ids = [s["id"] for s in parsed.get("students", [])]
    if student_id not in ids:
        raise RuntimeError("roster insertion failed validation; lab.yaml left unchanged")
    # Keep the original default track even if the newcomer is inserted first.
    if "default_student_id" not in parsed:
        text += f"\ndefault_student_id: {json.dumps(roster[0]['id'])}\n"
    temporary = cfg_path.with_suffix(".yaml.tmp")
    temporary.write_text(text)
    temporary.replace(cfg_path)

    write_charter(
        root / "context",
        initial_direction=direction,
        prompted_by=prompted_by,
        design_notes=(
            f"Placement: {prompted_by} joined as student `{student_id}`. "
            "Preserve this student's distinct hypothesis and approach."
        ),
        title=f"hired: {student_id}",
    )
    return cfg_path
