"""A terminal daemon's last heartbeat must describe its completed lifecycle."""
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest

from efferents import lab
from efferents.cli import _orchestrator_loop


@pytest.mark.parametrize("crash,offline", [(False, False), (True, False), (False, True)])
def test_final_terminal_heartbeat_preserves_evidence_and_exit_status(tmp_path, monkeypatch, crash, offline):
    sub = tmp_path / "submission"
    shutil.copytree(Path(__file__).parent / "fixtures" / "sample_submission", sub)
    lab.set_config(lab.LabConfig.from_submission(sub))
    root = sub / "lab"
    root.mkdir(exist_ok=True)
    calls, legacy = [], []
    snapshot = {"lab_id": "sample-conjecture", "ideas": {"primary": {"runs": []}}}

    class Network:
        def heartbeat(self, lab_id, payload):
            calls.append((lab_id, payload))
            if offline:
                raise OSError("hub unreachable")

    class Orchestrator:
        def __init__(self, **kwargs):
            self.network = Network()
            self.paths = SimpleNamespace(notebook=root / "lab_notebook.md")

        def _network_heartbeat_payload(self):
            return {"status": "running", "runs": 3, "spend_usd": 0.12,
                    "owner_evals": snapshot, "journal_uses": [{"publication_id": "journal:source:paper"}],
                    "halt_reason": "owner pause retained"}

        def run(self, **kwargs):
            if crash:
                raise RuntimeError("executor crashed")

    monkeypatch.setattr("efferents.agents.orchestrator.Orchestrator", Orchestrator)
    monkeypatch.setattr("efferents.agents.progress.write_progress", lambda *a, **k: None)
    monkeypatch.setattr("efferents.event.sync", lambda *a, **k: legacy.append(k.get("runtime_status")))
    if crash:
        with pytest.raises(RuntimeError, match="executor crashed"):
            _orchestrator_loop(lab_root=root, context_dir=sub / "context", max_iterations=3)
    else:
        _orchestrator_loop(lab_root=root, context_dir=sub / "context", max_iterations=3)
    assert len(calls) == 1
    lab_id, payload = calls[0]
    assert lab_id == "sample-conjecture"
    assert payload["status"] == ("crashed" if crash else "stopped")
    assert payload["owner_evals"] == snapshot
    assert payload["runs"] == 3 and payload["spend_usd"] == 0.12
    assert payload["journal_uses"][0]["publication_id"] == "journal:source:paper"
    assert payload["halt_reason"] == ("RuntimeError: executor crashed" if crash else "owner pause retained")
    assert legacy[-1] == payload["status"]
    if offline:
        assert "final hub heartbeat failed: OSError" in (root / "lab_notebook.md").read_text()


@pytest.mark.parametrize("crash", [False, True])
def test_fast_detached_child_keeps_terminal_registry_status(tmp_path, monkeypatch, crash):
    from efferents.cli import main
    from efferents.registry import Registry

    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "registry"))
    sub = tmp_path / "submission"
    shutil.copytree(Path(__file__).parent / "fixtures" / "sample_submission", sub)

    def bounded_loop(**kwargs):
        if crash:
            raise RuntimeError("bounded child crashed")

    def child_finishes_before_parent(lab_root, loop):
        try:
            loop()
        except RuntimeError:
            pass  # daemon child records the exception and exits independently
        return 4242

    monkeypatch.setattr("efferents.cli._orchestrator_loop", bounded_loop)
    monkeypatch.setattr("efferents.cli.daemon.daemonize_and_run", child_finishes_before_parent)
    assert main(["start", "--submission", str(sub), "--detach", "--max-iterations", "0"]) == 0
    record = Registry().get("sample-conjecture")
    assert record.pid == 4242
    assert record.status == ("crashed" if crash else "stopped")
