"""Stdout-JSON contract for run subprocess capture."""
from __future__ import annotations
import json
import shlex
import sys

from efferents.exec import _extract_trailing_json, _run_and_capture


def test_extract_trailing_json_simple():
    stdout = 'epoch 1\nepoch 2\n{"run_id":"r1","metrics":{"loss":0.5}}\n'
    out = _extract_trailing_json(stdout)
    assert out == {"run_id": "r1", "metrics": {"loss": 0.5}}


def test_extract_trailing_json_multiple_objects_takes_last():
    stdout = '{"a":1}\nsome chatter\n{"b":2}\n'
    out = _extract_trailing_json(stdout)
    assert out == {"b": 2}


def test_extract_trailing_json_none_when_absent():
    assert _extract_trailing_json("just plain text") is None
    assert _extract_trailing_json("") is None


def test_extract_trailing_json_handles_nested_braces():
    stdout = 'log\n{"run_id":"r","metrics":{"loss":0.1,"nested":{"k":1}}}\n'
    out = _extract_trailing_json(stdout)
    assert out["metrics"]["nested"]["k"] == 1


def test_extract_trailing_json_malformed_returns_none():
    assert _extract_trailing_json('{"unterminated') is None


def test_run_and_capture_happy_path(tmp_path):
    payload = {"run_id": "test-1", "metrics": {"synthetic_loss": 0.42}, "elapsed_s": 0.01}
    cmd = f"echo '{json.dumps(payload)}'"
    result = _run_and_capture(cmd, timeout_s=10, cwd=str(tmp_path), env_passthrough=())
    assert result.ok is True
    assert result.metrics == {"synthetic_loss": 0.42}
    assert result.error is None


def test_run_and_capture_no_json(tmp_path):
    result = _run_and_capture("echo no-json-here", timeout_s=10, cwd=str(tmp_path), env_passthrough=())
    assert result.ok is False
    assert result.error is not None
    assert "JSON" in result.error


def test_run_and_capture_nonzero_exit(tmp_path):
    payload = {"run_id": "test-1", "metrics": {"x": 1}}
    import json as _json
    cmd = f"echo '{_json.dumps(payload)}' && exit 1"
    result = _run_and_capture(cmd, timeout_s=10, cwd=str(tmp_path), env_passthrough=())
    assert result.ok is False
    assert result.metrics == {"x": 1}


def test_run_and_capture_timeout(tmp_path):
    result = _run_and_capture("sleep 5", timeout_s=1, cwd=str(tmp_path), env_passthrough=())
    assert result.ok is False
    assert result.error is not None
    assert "timeout" in result.error.lower()


def test_run_and_capture_does_not_inherit_secrets_unless_configured(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-not-reach-experiment")
    script = (
        "import json, os; "
        "print(json.dumps({'metrics': {'secret_present': "
        "int('ANTHROPIC_API_KEY' in os.environ)}}))"
    )
    cmd = f"{shlex.quote(sys.executable)} -c {shlex.quote(script)}"

    isolated = _run_and_capture(
        cmd, timeout_s=10, cwd=str(tmp_path), env_passthrough=()
    )
    explicit = _run_and_capture(
        cmd,
        timeout_s=10,
        cwd=str(tmp_path),
        env_passthrough=("ANTHROPIC_API_KEY",),
    )

    assert isolated.metrics == {"secret_present": 0}
    assert explicit.metrics == {"secret_present": 1}


def test_run_and_capture_rejects_nonfinite_metric(tmp_path):
    result = _run_and_capture(
        "echo '{\"metrics\":{\"loss\":NaN}}'",
        timeout_s=10,
        cwd=str(tmp_path),
        env_passthrough=(),
    )
    assert result.ok is False
    assert "finite number" in (result.error or "")


def test_run_and_capture_preserves_structured_observations(tmp_path):
    payload = {
        "metrics": {"loss": 0.2},
        "observations": [
            {
                "name": "treatment-sample",
                "dimensions": {"variant": "treatment", "seed": 42},
                "metrics": {"loss": 0.2, "validity": 1.0},
                "artifacts": [{"kind": "sample", "path": "sample.png"}],
            },
            {
                "name": "control-sample",
                "dimensions": {"variant": "control", "seed": 42},
                "metrics": {"loss": 0.3, "validity": 1.0},
            },
        ],
    }
    cmd = f"echo '{json.dumps(payload)}'"
    result = _run_and_capture(
        cmd, timeout_s=10, cwd=str(tmp_path), env_passthrough=()
    )
    assert result.ok is True
    assert len(result.observations) == 2
    assert result.observations[0]["dimensions"]["variant"] == "treatment"
    assert result.observations[1]["metrics"]["loss"] == 0.3
