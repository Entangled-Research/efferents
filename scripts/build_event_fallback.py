"""Build a saved, honest evacuation result for a no-network event fallback.

Run from an installed Efferents environment before the event. The resulting
directory can be copied to the organizer laptop and USB drive; no model or
event credential is used or included.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "efferents" / "templates" / "starter-evacuation-lab"


def run(*args: str, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(args, cwd=cwd, env=env, check=True)


def build(destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite {destination}")
    destination.mkdir(parents=True)
    lab = destination / "evacuation-lab"
    shutil.copytree(
        TEMPLATE, lab, ignore=shutil.ignore_patterns("artifacts", "__pycache__", "*.pyc")
    )
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    # No inherited event or provider settings should influence the saved run.
    for key in list(env):
        if key.startswith("EFFERENTS_MODEL") or key.endswith("_API_KEY") or key.startswith("EVENT_"):
            env.pop(key, None)
    run(sys.executable, "-m", "efferents", "validate", "--submission", str(lab), cwd=lab, env=env)
    run(sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v", cwd=lab, env=env)
    run(
        sys.executable, "-m", "efferents", "start", "--submission", str(lab),
        "--dry-run", "--max-iterations", "1", cwd=lab, env=env,
    )
    with sqlite3.connect(lab / "lab" / "runs.sqlite") as conn:
        successes = conn.execute("SELECT COUNT(*) FROM runs WHERE status='succeeded'").fetchone()[0]
        # Freeze the saved ledger into one portable SQLite file before USB
        # packaging, rather than relying on a companion WAL file.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    if successes < 1 or not (lab / "lab" / "progress.html").is_file():
        raise RuntimeError("fallback did not produce a successful run and dashboard")
    if not list((lab / "lab" / "artifacts").glob("*.svg")):
        raise RuntimeError("fallback did not produce an SVG artifact")
    wheels = destination / "wheelhouse"
    wheels.mkdir()
    run("uv", "build", "--wheel", "--out-dir", str(wheels), cwd=ROOT, env=env)
    (destination / "README.txt").write_text(
        "Efferents event offline fallback\n\n"
        "This is one deterministic, non-canned paired evacuation run. It is a\n"
        "saved example, not a live event lab or proof of the hypothesis.\n\n"
        "Open evacuation-lab/lab/progress.html for the saved dashboard.\n"
        "Inspect evacuation-lab/lab/runs.sqlite, lab_notebook.md, and artifacts/.\n"
        "On a laptop with Efferents and its dependencies already installed,\n"
        "activate that environment, then run:\n"
        "  cd evacuation-lab\n"
        "  python3 src/run_experiment.py --config configs/default.yaml\n"
        "Change one candidate parameter in configs/default.yaml and rerun.\n"
        "The framework wheel is in wheelhouse/, but its third-party\n"
        "dependencies must be installed or cached before Wi-Fi is disabled.\n"
    )
    archive = shutil.make_archive(
        str(destination), "zip", root_dir=destination.parent, base_dir=destination.name
    )
    print(f"fallback ready: {destination}")
    print(f"USB archive: {archive}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    build(args.out.expanduser().resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
