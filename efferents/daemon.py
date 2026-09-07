"""Daemon lifecycle helpers: fork (double-fork + setsid), pidfile mgmt,
SIGTERM handler. The "loop body" passed in is `orchestrator.start()` or
equivalent; the daemon module doesn't import it directly so it stays testable
without a full orchestrator setup.
"""
from __future__ import annotations

import os
import signal
import sys
import time
from pathlib import Path
from typing import Callable


def write_pidfile(path: Path, pid: int) -> None:
    path.write_text(str(pid))


def read_pidfile(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except (ValueError, OSError):
        return None


def clear_pidfile(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def is_pid_alive(pid: int) -> bool:
    """Send signal 0 (no-op) to test process existence."""
    if pid <= 0:
        # 0 / negatives address process groups, never a single daemon; a
        # placeholder pid must read as dead, or `stop` would signal ourselves.
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive but not ours
    return True


def run_foreground(lab_root: Path, loop: Callable[[], None]) -> None:
    """Run the loop in the foreground (no fork).

    Writes the same ``daemon.pid`` a detached run does so the lab root alone
    (without the registry) is enough to detect a live lab.
    """
    _install_signal_handlers(lab_root)
    write_pidfile(lab_root / "daemon.pid", os.getpid())
    try:
        loop()
    finally:
        clear_pidfile(lab_root / "daemon.pid")


def daemonize_and_run(lab_root: Path, loop: Callable[[], None]) -> int:
    """Double-fork + setsid + write pidfile + run loop.

    Returns the child PID (in the original parent). In the child, this never
    returns — it calls `loop()` until SIGTERM or the loop exits naturally.
    """
    pid = os.fork()
    if pid > 0:
        # Original parent: wait for first child to exit, then read grandchild PID
        os.waitpid(pid, 0)
        pidfile = lab_root / "daemon.pid"
        for _ in range(50):
            if pidfile.exists():
                child_pid = read_pidfile(pidfile)
                if child_pid:
                    return child_pid
            time.sleep(0.01)
        raise RuntimeError("daemon did not write pidfile within 500ms")

    # First child
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # Grandchild
    write_pidfile(lab_root / "daemon.pid", os.getpid())

    logfile = lab_root / "daemon.log"
    sys.stdout.flush()
    sys.stderr.flush()
    with open(logfile, "a", buffering=1) as f:
        os.dup2(f.fileno(), sys.stdout.fileno())
        os.dup2(f.fileno(), sys.stderr.fileno())

    _install_signal_handlers(lab_root)

    try:
        loop()
    except SystemExit:
        raise
    except BaseException as e:
        (lab_root / "halt_reason.txt").write_text(f"unhandled exception: {e!r}")
        # os._exit skips the finally below; clear the pidfile here or a crash
        # leaves a stale pid that can block restarts once the pid is recycled.
        clear_pidfile(lab_root / "daemon.pid")
        os._exit(1)
    finally:
        clear_pidfile(lab_root / "daemon.pid")
    os._exit(0)


def _install_signal_handlers(lab_root: Path) -> None:
    def _term(signum, frame):
        (lab_root / "halt_reason.txt").write_text(f"received signal {signum}")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
