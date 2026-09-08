"""Lab-scoped dashboard routes: many labs, many viewers, no shared selection."""
from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from efferents.dashboard import server
from efferents.dashboard.control import ControlContext

SAMPLE = Path(__file__).parent / "fixtures" / "sample_submission"


def _submission(root: Path, lab_id: str, domain: str) -> Path:
    submission = root / lab_id
    shutil.copytree(SAMPLE, submission)
    (submission / "README.md").write_text(f"# {lab_id}\n")
    raw = yaml.safe_load((submission / "lab.yaml").read_text())
    raw["lab_id"] = lab_id
    raw["domain"] = domain
    (submission / "lab.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    return submission


@pytest.fixture
def two_labs(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    control = ControlContext()
    first = _submission(tmp_path, "alpha-lab", "climate")
    second = _submission(tmp_path, "beta-lab", "biology")
    control.connect(str(first))
    control.connect(str(second))
    httpd = server.make_server(None, port=0, control=control, read_ttl_s=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port, control, first, second
    httpd.shutdown()
    httpd.server_close()


def _request(port, path, *, method="GET", payload=None, csrf=None):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    if csrf is not None:
        headers["X-Efferents-CSRF"] = csrf
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read() or b"{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:  # stdlib send_error() writes HTML
            return exc.code, {"error": raw.decode(errors="replace")}


def test_scoped_reads_do_not_depend_on_selection(two_labs):
    port, control, *_ = two_labs
    # The default lab is the last connected one; scoped reads ignore it.
    assert control.snapshot().cfg.lab_id == "beta-lab"
    status, state = _request(port, "/api/labs/alpha-lab/state")
    assert status == 200 and state["lab_id"] == "alpha-lab" and state["domain"] == "climate"
    status, state = _request(port, "/api/labs/beta-lab/state")
    assert status == 200 and state["lab_id"] == "beta-lab"
    status, info = _request(port, "/api/labs/alpha-lab/control")
    assert status == 200 and info["lab_id"] == "alpha-lab" and info["connected"] is True
    for kind in ("runs", "papers", "activity", "evidence", "verdict"):
        status, _ = _request(port, f"/api/labs/alpha-lab/{kind}")
        assert status == 200, kind
    # Legacy unscoped routes still serve the default lab.
    _, legacy = _request(port, "/api/state")
    assert legacy["lab_id"] == "beta-lab"
    assert control.snapshot().cfg.lab_id == "beta-lab"


def test_unknown_lab_and_unknown_view_are_404(two_labs):
    port, *_ = two_labs
    assert _request(port, "/api/labs/nope/state")[0] == 404
    status, _ = _request(port, "/api/labs/alpha-lab/secrets")
    assert status == 404


def test_scoped_steer_and_pause_write_the_ledger(two_labs):
    port, control, first, _second = two_labs
    _, session = _request(port, "/api/control")
    csrf = session["csrf_token"]
    status, result = _request(
        port, "/api/labs/alpha-lab/steer", method="POST",
        payload={"message": "Focus on the coastal buckets."}, csrf=csrf,
    )
    assert status == 200 and result["ok"] is True
    status, info = _request(
        port, "/api/labs/alpha-lab/pause", method="POST",
        payload={"reason": "review"}, csrf=csrf,
    )
    assert status == 200 and info["queued"] == "pause"
    records = [json.loads(line) for line in (first / "lab" / "steering.jsonl").read_text().splitlines()]
    assert [r.get("action") for r in records] == [None, "pause"]
    assert records[0]["text"] == "Focus on the coastal buckets."
    # Nothing leaked into the other lab.
    assert not (two_labs[3] / "lab" / "steering.jsonl").exists()


def test_scoped_mutation_requires_csrf(two_labs):
    port, *_ = two_labs
    status, body = _request(
        port, "/api/labs/alpha-lab/steer", method="POST", payload={"message": "x"},
    )
    assert status == 403 and "control token" in body["error"]


def test_portfolio_marks_default_and_lists_both(two_labs):
    port, *_ = two_labs
    _, portfolio = _request(port, "/api/labs")
    ids = [lab["lab_id"] for lab in portfolio["labs"]]
    assert ids == ["alpha-lab", "beta-lab"]
    assert [lab["selected"] for lab in portfolio["labs"]] == [False, True]
    assert all(lab["owner_id"] is None for lab in portfolio["labs"])


def test_lab_catalog_reloads_when_lab_yaml_changes(two_labs):
    port, control, first, _ = two_labs
    lab = control.labs.resolve("alpha-lab")
    assert lab.cfg.domain == "climate"
    raw = yaml.safe_load((first / "lab.yaml").read_text())
    raw["domain"] = "oceanography"
    import os
    import time
    (first / "lab.yaml").write_text(yaml.safe_dump(raw, sort_keys=False))
    os.utime(first / "lab.yaml", (time.time() + 5, time.time() + 5))
    assert control.labs.resolve("alpha-lab").cfg.domain == "oceanography"


def test_owner_json_is_surfaced(two_labs):
    port, control, first, _ = two_labs
    (first / "owner.json").write_text(json.dumps(
        {"owner_id": "o1", "owner_name": "Ada", "track": "noise"}
    ))
    control.labs.invalidate("alpha-lab")
    _, info = _request(port, "/api/labs/alpha-lab/control")
    assert info["owner_id"] == "o1" and info["owner_name"] == "Ada" and info["track"] == "noise"
