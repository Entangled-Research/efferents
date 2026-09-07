"""Verdict surface: read_verdict, /api/verdict, workspace panels, summary line."""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.request
from pathlib import Path

import pytest

from efferents import lab as lab_mod
from efferents.dashboard import reader, server
from efferents.lab import (
    Budget, Evidence, Executor, Falsifier, Headline, LabConfig, Metrics, Panel, Source,
)

STATIC = Path(__file__).resolve().parents[1] / "efferents" / "dashboard" / "static"


def make_cfg(tmp_path, *, falsifiers=()):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "c.yaml").touch()
    return LabConfig(
        lab_id="verdict-lab", domain="test", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(run_command="echo {config_path}", smoke_command=None,
                          config_template=src / "c.yaml"),
        metrics=Metrics(headline=Headline(column="e_w1", direction="min"),
                        panels=(Panel(column="delta_e_w1", label="Delta"),),
                        bucket_axes=("raw_q",)),
        budget=Budget(),
        evidence=Evidence(comparison_axis="role",
                          comparison_order=("quantum", "classical")),
        falsifiers=tuple(falsifiers),
    )


FALSIFIERS = (
    # bucket 8 deltas are all positive -> fires
    Falsifier(id="F1", description="quantum worse at q=8", kind="aggregate",
              column="delta_e_w1", agg="median", op=">", value=0.0, bucket=8),
    # bucket 16 deltas are all negative -> survives
    Falsifier(id="F2", description="quantum worse at q=16", kind="aggregate",
              column="delta_e_w1", agg="median", op=">", value=0.0, bucket=16),
    # min_n too high for either bucket -> insufficient
    Falsifier(id="F3", description="no separation anywhere", kind="paired",
              metric="e_w1", ci95_excludes_zero=False, bucket="all", min_n=10),
)


def write_ledger(lab_root: Path) -> None:
    conn = sqlite3.connect(lab_root / "runs.sqlite")
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, status TEXT, "
        "raw_q INTEGER, seed INTEGER, e_w1 REAL, delta_e_w1 REAL, observations_json TEXT)"
    )
    deltas_by_q = {8: [0.1, 0.3, 0.2], 16: [-0.2, -0.1, -0.3, -0.4]}
    rows = []
    for q, deltas in deltas_by_q.items():
        for seed, d in enumerate(deltas):
            obs = [
                {"name": "quantum", "dimensions": {"role": "quantum", "seed": seed},
                 "metrics": {"e_w1": 1.0 + d}},
                {"name": "classical", "dimensions": {"role": "classical", "seed": seed},
                 "metrics": {"e_w1": 1.0}},
            ]
            rows.append((f"r-{q}-{seed}", f"2026-09-01T00:{seed:02}:00", "succeeded",
                         q, seed, 1.0 + d, d, json.dumps(obs)))
    # A failed run with a wild delta must not reach the evidence functions.
    rows.append(("r-failed", "2026-09-02T00:00:00", "failed", 8, 9, 5.0, 4.0, "[]"))
    conn.executemany("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def test_read_verdict_without_falsifiers_reports_buckets_and_pairs(tmp_path):
    write_ledger(tmp_path)
    payload = reader.read_verdict(tmp_path, cfg=make_cfg(tmp_path))

    assert payload["verdict"] == "undecided"
    assert payload["falsifiers"] == []
    assert payload["line"] == "verdict: undecided · no falsifiers"
    assert payload["n_runs"] == 7  # succeeded runs only
    assert payload["axes"] == ["raw_q"]
    assert [b["label"] for b in payload["buckets"]] == ["raw_q=8", "raw_q=16"]
    b16 = payload["buckets"][1]
    assert b16["n"] == 4
    assert b16["columns"]["delta_e_w1"]["median"] == -0.25
    assert b16["arms"]["classical"]["e_w1"]["median"] == 1.0
    assert b16["paired"]["e_w1"]["arms"] == ["quantum", "classical"]
    pair = next(p for p in payload["paired"] if p["bucket"] == "raw_q=16")
    assert pair["metric"] == "e_w1" and pair["n"] == 4
    assert pair["ci95"] is not None and pair["ci95"][1] < 0
    json.dumps(payload)  # JSON-serializable end to end


def test_read_verdict_evaluates_declared_falsifiers(tmp_path):
    write_ledger(tmp_path)
    payload = reader.read_verdict(tmp_path, cfg=make_cfg(tmp_path, falsifiers=FALSIFIERS))

    assert payload["verdict"] == "falsified"
    assert payload["line"] == "verdict: falsified · F1 fired · F3 insufficient"
    by_id = {f["id"]: f for f in payload["falsifiers"]}
    assert by_id["F1"]["status"] == "fired"
    assert by_id["F1"]["bucket"] == "8"
    assert by_id["F1"]["rule"] == "median(delta_e_w1) > 0"
    assert "raw_q=8" in by_id["F1"]["detail"]
    assert by_id["F2"]["status"] == "survived"
    assert by_id["F3"]["status"] == "insufficient_data"
    assert by_id["F3"]["rule"] == "95% CI of median(Δe_w1) includes zero"


def test_read_verdict_tolerates_missing_ledger(tmp_path):
    payload = reader.read_verdict(tmp_path, cfg=make_cfg(tmp_path, falsifiers=FALSIFIERS))
    assert payload["n_runs"] == 0 and payload["buckets"] == []
    assert payload["verdict"] == "undecided"
    assert {f["status"] for f in payload["falsifiers"]} == {"insufficient_data"}


def test_read_summary_carries_verdict_line(tmp_path):
    write_ledger(tmp_path)
    summary = reader.read_summary(tmp_path, make_cfg(tmp_path, falsifiers=FALSIFIERS))
    assert summary["verdict"] == {
        "status": "falsified",
        "line": "verdict: falsified · F1 fired · F3 insufficient",
    }
    bare = reader.read_summary(tmp_path, make_cfg(tmp_path))
    assert bare["verdict"] == {"status": "undecided",
                               "line": "verdict: undecided · no falsifiers"}


@pytest.fixture
def verdict_server(tmp_path):
    write_ledger(tmp_path)
    lab_mod.set_config(make_cfg(tmp_path, falsifiers=FALSIFIERS))
    httpd = server.make_server(tmp_path, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def test_api_verdict_returns_json(verdict_server):
    with urllib.request.urlopen(f"http://127.0.0.1:{verdict_server}/api/verdict") as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"] == "application/json"
        body = json.loads(resp.read())
    assert body["verdict"] == "falsified"
    assert [f["id"] for f in body["falsifiers"]] == ["F1", "F2", "F3"]
    assert body["n_runs"] == 7


def test_api_verdict_is_empty_when_disconnected():
    httpd = server.make_server(None, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{httpd.server_address[1]}/api/verdict"
        ) as resp:
            body = json.loads(resp.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert body["verdict"] == "undecided" and body["falsifiers"] == []


def test_workspace_renders_verdict_and_bucket_panels(verdict_server):
    with urllib.request.urlopen(f"http://127.0.0.1:{verdict_server}/") as resp:
        html = resp.read().decode()
    js = (STATIC / "dashboard.js").read_text()
    css = (STATIC / "dashboard.css").read_text()

    assert 'id="verdict-line"' in html
    assert '<table id="falsifiers">' in html
    assert '<table id="buckets">' in html
    assert "<th scope=\"col\">Rule · detail</th>" in html
    # Renderer is wired to the route and paints ids, status, verdict line.
    assert '["/api/verdict", renderVerdict]' in js
    assert "${esc(f.id)}" in js and "status-${esc(f.status)}" in js
    assert 'data?.line || "verdict: undecided"' in js
    assert "No falsifiers declared in lab.yaml" in js
    assert "formatCI(p.ci95)" in js
    # Portfolio rail shows the compact verdict line.
    assert "lab.verdict?.line" in js
    # Only theme tokens; falsification uses the terracotta warning colour.
    assert "td.status-fired {\n  color: var(--terracotta);" in css
    verdict_css = (
        css.split("/* --------------------------------------------------------------- verdict */")[1]
        .split("/* ------------------------------------------------------ papers, activity */")[0]
        .replace("#falsifiers", "").replace("#buckets", "")
    )
    assert "#" not in verdict_css
