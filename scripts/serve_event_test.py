"""Prepare a persistent, private event rehearsal on this computer.

No model credentials or cloud account are required. Credentials and generated
lab evidence live only in the ignored event-output directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# These are deliberately ordinary words: the final password also contains a
# 72-bit URL-safe suffix, so the phrase is easy to read aloud without making
# the credential guessable.  The lists are not credentials or an allow-list.
_PASSWORD_WORDS = (
    "apricot", "badger", "bamboo", "beacon", "bluebird", "cactus", "candle", "canyon",
    "comet", "copper", "cricket", "dahlia", "dolphin", "ember", "falcon", "feather",
    "fig", "firefly", "garden", "glacier", "harbor", "hazel", "honey", "island",
    "jasmine", "kettle", "lantern", "lemon", "marble", "meadow", "meteor", "mint",
    "nebula", "otter", "pebble", "pepper", "pine", "planet", "puddle", "quartz",
    "radish", "raven", "river", "rocket", "saffron", "sailboat", "shadow", "sparrow",
    "spruce", "sunbeam", "thistle", "tiger", "topaz", "velvet", "violet", "walnut",
    "whistle", "willow", "windmill", "wombat", "yonder", "zephyr", "zinnia", "zucchini",
)
_USERNAME_WORDS = (
    "brisk", "cosmic", "curious", "daring", "gentle", "jolly", "lucky", "merry",
    "nimble", "playful", "quiet", "radiant", "sleepy", "sunny", "tiny", "witty",
)
_USERNAME_ANIMALS = (
    "badger", "beaver", "fox", "gecko", "hedgehog", "otter", "panda", "penguin",
    "quokka", "raccoon", "redpanda", "sparrow", "turtle", "walrus", "wombat", "yak",
)


def generate_access_credentials() -> dict[str, str]:
    """Return a memorable handoff credential with high random entropy."""
    username = f"{secrets.choice(_USERNAME_WORDS)}-{secrets.choice(_USERNAME_ANIMALS)}-{secrets.randbelow(10000):04d}"
    phrase = "-".join(secrets.choice(_PASSWORD_WORDS) for _ in range(4))
    # hex only: the suffix must never contain the "-" that separates words
    password = f"{phrase}-{secrets.token_hex(6)}"
    return {"username": username, "password": password}


def prepare(directory: Path, port: int) -> dict:
    from efferents.event import _atomic_private_json
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    path = directory / "access.json"
    if path.exists():
        access = json.loads(path.read_text())
        if access["port"] != port:
            raise ValueError(f"This workspace uses port {access['port']}; choose another --directory for a new port")
    else:
        access = {**generate_access_credentials(), "port": port}
        _atomic_private_json(path, access)
    path.chmod(0o600)
    guide = directory / "TEST_ACCESS.md"
    guide.write_text(
        "# Private event test\n\n"
        f"Link: http://localhost:{port}/#network\n\n"
        f"Username: {access['username']}\n\nPassword: {access['password']}\n\n"
        "This link works on this computer. It does not expose the workspace publicly.\n\n"
        "1. Inspect the three seeded labs and the findings beneath the graph. These are real CPU runs.\n"
        "2. Switch Group by between Shared goal and Research domain.\n"
        "3. Choose Add an idea or lab. Try shared goal Reduce congestion with idea Frequent rerouting.\n"
        "4. Choose Infer defaults and run. The graph refreshes every four seconds.\n"
        "5. Add Numerical integration as an independent lab.\n"
        "6. Choose Observe peer findings. Inspect which labs received which measurements.\n"
        "7. Open a local lab to inspect its verdict, metrics and SVG evidence. Three evacuation seeds "
        "are preliminary; its claim needs twelve.\n\n"
        "No model spending is enabled by this test. To test autonomous model research, join a configured "
        "event proxy or supply your own provider credentials locally, then use Start lab.\n\n"
        "If the link is unavailable after a reboot, restart from the repository:\n\n"
        f"    cd {ROOT}\n"
        f"    uv run python scripts/serve_event_test.py --background --port {port} --directory {directory}\n\n"
        f"Evidence and logs: {directory}\n"
    )
    guide.chmod(0o600)
    return access


def seed(directory: Path) -> None:
    from efferents.onboarding import create_lab
    from efferents.dashboard.control import ControlContext
    cases = [("responsive-routing", "Frequent rerouting", "Reduce congestion"),
             ("stable-routing", "Stable routes", "Reduce congestion"),
             ("numerical-integration", "Numerical integration", "")]
    for name, idea, goal in cases:
        path = directory / "labs" / name
        if path.exists():
            continue
        create_lab(path, idea=idea, goal=goal, exchange=True)
        result = subprocess.run([sys.executable, "-m", "efferents", "trial", "--submission", str(path),
                                 "--runs", "3"], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(f"Seed failed for {name}: {result.stderr[-2000:]}")
    ControlContext().observe_peers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "event-output" / "tomorrow-test")
    parser.add_argument("--port", type=int, default=8840)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--empty", action="store_true", help="Skip creating the three real example labs")
    parser.add_argument("--enable-models", action="store_true", help="Allow explicitly configured model credentials in this workspace")
    args = parser.parse_args()
    directory = args.directory.expanduser().resolve()
    access = prepare(directory, args.port)
    if not args.enable_models:
        from efferents.agents.model_client import PROVIDER_KEY_ENV
        for key in list(os.environ):
            if key in PROVIDER_KEY_ENV.values() or key.startswith("EFFERENTS_MODEL") or key in {
                "EFFERENTS_API_BASE", "EFFERENTS_EVENT_PROXY_ACTIVE", "EFFERENTS_EVENT_MODEL_PRICING",
            }:
                os.environ.pop(key, None)
    os.environ["EFFERENTS_HOME"] = str(directory / "workspace")
    os.environ["EFFERENTS_DASHBOARD_USER"] = access["username"]
    os.environ["EFFERENTS_DASHBOARD_PASSWORD_HASH"] = hashlib.sha256(access["password"].encode()).hexdigest()
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    if args.background:
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", args.port)) == 0:
                raise SystemExit(f"Port {args.port} is already in use. Existing access notes: {directory / 'TEST_ACCESS.md'}")
        command = [sys.executable, str(Path(__file__).resolve()), "--directory", str(directory), "--port", str(args.port)]
        if args.empty:
            command.append("--empty")
        if args.enable_models:
            command.append("--enable-models")
        with (directory / "server.log").open("a") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
        (directory / "server.pid").write_text(str(process.pid))
        for _ in range(60):
            if process.poll() is not None:
                raise SystemExit(f"Test server exited; inspect {directory / 'server.log'}")
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{args.port}/", timeout=1)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit(f"Still preparing; inspect {directory / 'server.log'}")
        print(f"Private test ready: http://localhost:{args.port}/#network")
        print(f"Login and test steps: {directory / 'TEST_ACCESS.md'}")
        return
    if not args.empty:
        seed(directory)
    from efferents.dashboard.server import serve
    serve(None, port=args.port, open_browser=False)


if __name__ == "__main__":
    main()
