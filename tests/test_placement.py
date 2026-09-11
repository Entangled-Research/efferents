"""Placement: one lab per (topic, way of thinking); redundant -> hire."""
from __future__ import annotations

from pathlib import Path

import yaml

from efferents.placement import (
    classify, extract_profile, hire, place, scan_network,
)


def _mklab(root: Path, lab_id: str, topic: str, approach: str,
           students: bool = False) -> Path:
    d = root / lab_id
    d.mkdir(parents=True)
    cfg = {"lab_id": lab_id, "topic": topic, "approach": approach}
    body = yaml.safe_dump(cfg)
    if students:
        body += "students:\n- id: primary\n  handle: null\n  focus: ''\n  prompt_overrides: {}\n"
    (d / "lab.yaml").write_text(body)
    return d


TOPIC_EW = "tipping point early warning for climate time series"
APPROACH_STAT = "rolling statistical indicators: lag-1 autocorrelation and variance thresholds"
APPROACH_NN = "learned neural detectors trained end to end on simulated transitions"


def test_classify_redundant_sibling_distinct(tmp_path):
    a = extract_profile(_mklab(tmp_path, "a", TOPIC_EW, APPROACH_STAT))
    b = extract_profile(_mklab(tmp_path, "b", TOPIC_EW, APPROACH_STAT + " with detrending"))
    c = extract_profile(_mklab(tmp_path, "c", TOPIC_EW, APPROACH_NN))
    d = extract_profile(_mklab(tmp_path, "d",
                               "protein folding energy landscapes",
                               "diffusion models over torsion angles"))
    assert classify(a, b)[0] == "redundant"   # same topic, same thinking
    assert classify(a, c)[0] == "sibling"     # same topic, different thinking
    assert classify(a, d)[0] == "distinct"    # different everything


def test_place_joins_redundant_and_allows_siblings(tmp_path):
    _mklab(tmp_path / "net", "existing-stat", TOPIC_EW, APPROACH_STAT)
    _mklab(tmp_path / "net", "existing-nn", TOPIC_EW, APPROACH_NN)
    new = extract_profile(
        _mklab(tmp_path, "newcomer", TOPIC_EW, APPROACH_STAT + " on ocean data"))
    network = scan_network([tmp_path / "net"])
    assert {p.lab_id for p in network} == {"existing-stat", "existing-nn"}

    decision = place(new, network)
    assert decision.action == "join"
    assert decision.target.lab_id == "existing-stat"
    assert "existing-nn" in decision.siblings
    assert "JOIN" in decision.summary()


def test_place_creates_when_topic_is_new(tmp_path):
    _mklab(tmp_path / "net", "existing-stat", TOPIC_EW, APPROACH_STAT)
    new = extract_profile(_mklab(tmp_path, "newcomer",
                                 "coral reef acoustic monitoring",
                                 "self supervised audio embeddings"))
    decision = place(new, scan_network([tmp_path / "net"]))
    assert decision.action == "create"
    assert decision.siblings == []


def test_hire_appends_roster_and_charter(tmp_path):
    target = _mklab(tmp_path, "existing-stat", TOPIC_EW, APPROACH_STAT, students=True)
    cfg = hire(
        target,
        student_id="s2",
        focus="ocean tipping series",
        direction="Apply AC1 alarms to ocean transport series.",
        prompted_by="placement:newcomer-lab",
    )
    parsed = yaml.safe_load(cfg.read_text())
    ids = [s["id"] for s in parsed["students"]]
    assert ids == ["s2", "primary"] or ids == ["primary", "s2"]
    charter = (target / "context" / "popper.md").read_text()
    assert "hired: s2" in charter
    assert "> Apply AC1 alarms to ocean transport series." in charter
    assert "placement:newcomer-lab" in charter
    # Hiring the same id twice is rejected.
    try:
        hire(target, student_id="s2", focus="x", direction="y", prompted_by="z")
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate student id should raise")


def test_hire_creates_roster_when_absent(tmp_path):
    target = _mklab(tmp_path, "no-roster", TOPIC_EW, APPROACH_STAT)
    hire(target, student_id="s1", focus="f", direction="d", prompted_by="p")
    parsed = yaml.safe_load((target / "lab.yaml").read_text())
    assert [s["id"] for s in parsed["students"]] == ["primary", "s1"]
    assert parsed["default_student_id"] == "primary"
    # Original declared fields survive the text-level insertion.
    assert parsed["topic"] == TOPIC_EW
