from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from efferents.agents import conference
from efferents.dashboard import server
from efferents.dashboard.control import ControlContext
from efferents.lab import LabConfig, SubmissionError
from efferents.onboarding import create_lab
from efferents.registry import Registry


def run_trial(submission, env, runs=3):
    result = subprocess.run([sys.executable, "-m", "efferents", "trial", "--submission", str(submission),
                             "--runs", str(runs)], capture_output=True, text=True, env=env, timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout


def test_two_domains_real_evidence_shared_goal_and_observation(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    paths = [tmp_path / name for name in ("responsive", "stable", "numerical")]
    create_lab(paths[0], idea="Frequent rerouting", goal="Reduce congestion", exchange=True)
    create_lab(paths[1], idea="Stable routes", goal="Reduce congestion", exchange=True)
    create_lab(paths[2], idea="Numerical integration", exchange=True)
    for path in paths:
        run_trial(path, env)
    config_a = yaml.safe_load((paths[0] / "configs/default.yaml").read_text())
    config_b = yaml.safe_load((paths[1] / "configs/default.yaml").read_text())
    assert config_a["candidate"]["reroute_interval"] != config_b["candidate"]["reroute_interval"]
    numerical = LabConfig.from_submission(paths[2])
    with sqlite3.connect(paths[2] / "lab/runs.sqlite") as conn:
        rows = conn.execute("SELECT seed,candidate_error FROM runs ORDER BY seed").fetchall()
    assert [row[0] for row in rows] == [0, 1, 2]
    assert rows[0][1] < 1e-14  # Simpson integrates the cubic exactly.
    assert max(row[1] for row in rows) < 0.0001
    control = ControlContext()
    assert control.observe_peers()["received"] == 0
    graph = control.portfolio()
    assert len(graph["labs"]) == 3
    assert graph["findings"] == []  # Raw trials are private, not journal publications.
    assert graph["observations"] == []
    assert any(row["goal"] == "Reduce congestion" for row in graph["labs"])
    assert conference.prompt_context(paths[2] / "lab", numerical) == ""
    assert len(Registry().list()) == 3
    from efferents.dashboard.reader import _current_hypothesis
    assert _current_hypothesis(paths[2] / "lab", numerical.lab_id)["falsifier"]
    run_trial(paths[2], env, runs=1)
    with sqlite3.connect(paths[2] / "lab/runs.sqlite") as conn:
        assert [r[0] for r in conn.execute("SELECT seed FROM runs ORDER BY seed")] == [0, 1, 2, 3]
    from efferents.steer import record_steering
    from efferents.onboarding import trial
    record_steering(paths[2] / "lab", text="Pause spending", by="owner", action="pause")
    with pytest.raises(ValueError, match="owner paused"):
        trial(paths[2], runs=1)


def test_lightweight_contract_needs_measurement_and_stop_condition(tmp_path):
    create_lab(tmp_path / "lab", starter="integration")
    hypothesis = tmp_path / "lab/hypothesis.md"
    assert LabConfig.from_submission(tmp_path / "lab").hypothesis_validation == "lightweight"
    hypothesis.write_text(hypothesis.read_text().replace("## Measurement", "## Unspecified"))
    with pytest.raises(SubmissionError, match="Measurement"):
        LabConfig.from_submission(tmp_path / "lab")


def test_password_and_csrf_protect_onboarding(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    monkeypatch.setenv("EFFERENTS_DASHBOARD_PASSWORD_HASH", hashlib.sha256(b"test-password").hexdigest())
    httpd = server.make_server(None, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{httpd.server_port}"
    headers = {"Authorization": "Basic " + base64.b64encode(b"organizer:test-password").decode()}
    try:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(origin + "/api/labs")
        assert exc.value.code == 401
        with urllib.request.urlopen(urllib.request.Request(origin + "/api/control", headers=headers)) as response:
            token = json.load(response)["csrf_token"]
        request = urllib.request.Request(origin + "/api/onboard", headers={**headers, "Content-Type": "application/json"},
                                         data=b'{"confirmed":true,"starter":"integration"}')
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(request)
        assert exc.value.code == 403
        request.add_header("X-Efferents-CSRF", token)
        with urllib.request.urlopen(request) as response:
            created = json.load(response)
        assert created["decisions"]["starter"] == "integration"
        assert not list((Path(created["submission_dir"]) / "lab/artifacts").glob("*"))
    finally:
        httpd.shutdown()
        httpd.server_close()
