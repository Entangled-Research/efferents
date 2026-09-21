from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from efferents.lab import LabConfig
from efferents.onboarding import create_lab
from efferents.starter_catalog import DOCUMENTED


TEMPLATE = Path(__file__).resolve().parents[1] / "efferents" / "templates" / "starter-documented-lab"


def run_once(submission: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "run_experiment.py", "--config", str(submission / "configs" / "default.yaml")],
        cwd=submission / "src", capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_frozen_data_ships_with_the_template():
    manifest = json.loads((TEMPLATE / "data" / "manifest.json").read_text())
    for source in manifest["sources"]:
        assert (TEMPLATE / "data" / source["file"]).is_file(), source["file"]


@pytest.mark.parametrize("starter", sorted(DOCUMENTED))
def test_each_documented_starter_is_real_fast_and_deterministic(tmp_path, starter):
    submission = tmp_path / starter
    decisions = create_lab(submission, starter=starter)
    assert decisions["starter"] == starter
    cfg = LabConfig.from_submission(submission)
    assert cfg.domain == DOCUMENTED[starter]["domain"]
    assert yaml.safe_load((submission / "configs" / "default.yaml").read_text())["experiment"] == starter

    started = time.monotonic()
    first = run_once(submission)
    elapsed = time.monotonic() - started
    second = run_once(submission)

    assert elapsed < 30
    assert first["metrics"] == second["metrics"]
    assert first["metrics"]["valid"] == 1
    assert {"improvement", "baseline", "candidate"} <= set(first["metrics"])
    artifacts = {item["kind"]: Path(item["path"]) for item in first["artifacts"]}
    assert artifacts["provenance"].is_file()
    assert artifacts[starter].suffix == ".svg"
