"""~/.efferents/registry.json read/write with file lock."""
from __future__ import annotations
import fcntl
import json
import os
import threading
import time

from efferents import registry as registry_mod
from efferents.registry import LabRecord, Registry


def test_registry_empty_on_first_read(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg = Registry()
    assert reg.list() == []


def test_register_and_list(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg = Registry()
    rec = LabRecord(
        lab_id="my-lab",
        submission_dir=str(tmp_path / "sub"),
        lab_root=str(tmp_path / "sub/lab"),
        pid=12345,
        started_at="2026-05-26T14:02:00Z",
        status="running",
    )
    reg.register(rec)
    listed = reg.list()
    assert len(listed) == 1
    assert listed[0].lab_id == "my-lab"
    assert listed[0].pid == 12345


def test_register_idempotent_on_lab_id(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg = Registry()
    rec1 = LabRecord(lab_id="x", submission_dir="/a", lab_root="/a/lab",
                     pid=1, started_at="t1", status="running")
    rec2 = LabRecord(lab_id="x", submission_dir="/a", lab_root="/a/lab",
                     pid=2, started_at="t2", status="running")
    reg.register(rec1)
    reg.register(rec2)
    listed = reg.list()
    assert len(listed) == 1
    assert listed[0].pid == 2  # latest wins


def test_get_by_lab_id(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg = Registry()
    reg.register(LabRecord(lab_id="y", submission_dir="/b", lab_root="/b/lab",
                           pid=99, started_at="t", status="running"))
    rec = reg.get("y")
    assert rec is not None
    assert rec.pid == 99
    assert reg.get("nonexistent") is None


def test_update_status(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg = Registry()
    reg.register(LabRecord(lab_id="z", submission_dir="/c", lab_root="/c/lab",
                           pid=5, started_at="t", status="running"))
    reg.update_status("z", "stopped")
    assert reg.get("z").status == "stopped"


def test_corrupted_json_recovered(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path))
    reg_path = tmp_path / "registry.json"
    reg_path.write_text("{not valid json")
    reg = Registry()
    assert reg.list() == []


# --- concurrency + lifecycle robustness ---------------------------------------


def _rec(lab_id: str = "x", status: str = "running") -> LabRecord:
    return LabRecord(
        lab_id=lab_id, submission_dir="/s", lab_root="/s/lab",
        pid=os.getpid(), started_at="t", status=status,
    )


def test_concurrent_readers_do_not_lose_records(tmp_path, monkeypatch):
    """Regression: `get()`/`list()` from several threads wiped the file to [].

    Each read used to truncate + rewrite the file and release the flock
    before the buffered write was flushed, so the next locker read an empty
    file and "reset" the registry.
    """
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    Registry().register(_rec())
    failures: list[str] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                if Registry().get("x") is None:
                    failures.append("record vanished")
                    return
            except Exception as exc:  # noqa: BLE001 - surface any crash
                failures.append(repr(exc))
                return

    def writer():
        while not stop.is_set():
            Registry().update_status("x", "running")

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    time.sleep(1.0)
    stop.set()
    for t in threads:
        t.join()

    assert not failures, failures
    assert Registry().get("x") is not None
    assert not (tmp_path / "home" / "registry.json.corrupt").exists()


def test_write_is_visible_on_disk_before_unlock(tmp_path, monkeypatch):
    """The on-disk file must be complete JSON at the moment the lock drops."""
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    path = tmp_path / "home" / "registry.json"
    seen: list[str] = []
    real_flock = fcntl.flock

    def spy(fd, op):
        if op == fcntl.LOCK_UN:
            seen.append(path.read_text())
        return real_flock(fd, op)

    monkeypatch.setattr(registry_mod.fcntl, "flock", spy)
    Registry().register(_rec("alpha"))
    Registry().update_status("alpha", "stopped")

    assert len(seen) == 2
    for raw in seen:
        data = json.loads(raw)  # was "" (truncated, unflushed) before the fix
        assert [r["lab_id"] for r in data] == ["alpha"]


def test_reads_do_not_rewrite_file(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    Registry().register(_rec())
    path = tmp_path / "home" / "registry.json"
    before = path.stat().st_mtime_ns
    time.sleep(0.01)
    Registry().list()
    Registry().get("x")
    assert path.stat().st_mtime_ns == before


def test_update_status_missing_record_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    reg = Registry()
    assert reg.update_status("ghost", "stopped") is False
    assert reg.list() == []
    reg.register(_rec("real"))
    assert reg.update_status("real", "stopped") is True
    assert reg.get("real").status == "stopped"


def test_corrupt_registry_is_backed_up_not_silently_dropped(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "registry.json").write_text('[]{"lab_id": "tail"}')
    assert Registry().list() == []
    backup = tmp_path / "home" / "registry.json.corrupt"
    assert backup.read_text() == '[]{"lab_id": "tail"}'
    assert "corrupted JSON" in capsys.readouterr().err
