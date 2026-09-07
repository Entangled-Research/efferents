"""Daemon fork + pidfile lifecycle. No-fork foreground path tested directly."""
from __future__ import annotations
import os

import pytest

from efferents.daemon import (
    is_pid_alive, read_pidfile, write_pidfile, clear_pidfile, run_foreground,
)


def test_write_and_read_pidfile(tmp_path):
    p = tmp_path / "daemon.pid"
    write_pidfile(p, 12345)
    assert read_pidfile(p) == 12345


def test_read_pidfile_missing(tmp_path):
    p = tmp_path / "missing.pid"
    assert read_pidfile(p) is None


def test_clear_pidfile_idempotent(tmp_path):
    p = tmp_path / "daemon.pid"
    clear_pidfile(p)  # absent
    write_pidfile(p, 99)
    clear_pidfile(p)
    assert not p.exists()


def test_is_pid_alive_current_process():
    assert is_pid_alive(os.getpid()) is True


def test_is_pid_alive_dead_pid():
    assert is_pid_alive(999999) is False


def test_run_foreground_invokes_callback(tmp_path):
    called = []
    def fake_loop():
        called.append(1)
    run_foreground(tmp_path, fake_loop)
    assert called == [1]


def test_is_pid_alive_zero_and_negative_are_dead():
    # 0 / negatives address process groups; a placeholder pid must never
    # make `stop` signal its own group.
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False


def test_run_foreground_writes_and_clears_pidfile(tmp_path):
    seen = []
    def fake_loop():
        seen.append(read_pidfile(tmp_path / "daemon.pid"))
    run_foreground(tmp_path, fake_loop)
    assert seen == [os.getpid()]
    assert not (tmp_path / "daemon.pid").exists()


def test_run_foreground_clears_pidfile_on_error(tmp_path):
    def boom():
        raise RuntimeError("x")
    with pytest.raises(RuntimeError):
        run_foreground(tmp_path, boom)
    assert not (tmp_path / "daemon.pid").exists()
