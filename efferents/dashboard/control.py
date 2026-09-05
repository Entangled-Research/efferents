"""Local onboarding and steering controls for the dashboard.

The browser never runs repository code merely because a URL was pasted.
Connection means: clone (for GitHub sources), locate the submission contract,
validate ``lab.yaml`` + ``hypothesis.md``, and initialize file-backed lab state.
Starting the daemon remains a separate, explicit action.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

from efferents import daemon
from efferents import lab as lab_mod
from efferents.cli import _init_lab_root
from efferents.dashboard import reader
from efferents.lab import LabConfig, SubmissionError
from efferents.registry import LabRecord, Registry

_GITHUB_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_README_RE = re.compile(r"^readme(?:\.[A-Za-z0-9_-]+)?$", re.IGNORECASE)
STEERING_MODES = ("auto", "refine", "moonshot", "devils_advocate", "escape_to_code")


class ControlError(ValueError):
    """A user-facing control-plane error with an HTTP-compatible status."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GitHubReadme:
    owner: str
    repo: str
    ref: str | None
    readme_path: Path

    @property
    def clone_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}.git"

    @property
    def display_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"


@dataclass(frozen=True)
class ConnectedLab:
    cfg: LabConfig
    submission_dir: Path
    lab_root: Path
    source: str | None = None
    repository: str | None = None
    readme_path: str | None = None


def _safe_url_parts(path: str) -> list[str]:
    parts = [unquote(part) for part in path.split("/") if part]
    if any(part in (".", "..") or "/" in part or "\\" in part for part in parts):
        raise ControlError("GitHub URL contains an unsafe path component.")
    return parts


def parse_github_readme_url(value: str) -> GitHubReadme:
    """Parse a GitHub repository or README URL into a safe clone target."""
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    parts = _safe_url_parts(parsed.path)

    if host in ("github.com", "www.github.com"):
        if len(parts) < 2:
            raise ControlError("Use a GitHub repository or README URL.")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref: str | None = None
        readme_parts = ["README.md"]
        if len(parts) > 2:
            if parts[2] not in ("blob", "tree") or len(parts) < 5:
                raise ControlError(
                    "GitHub file URLs must look like "
                    "github.com/owner/repo/blob/main/path/README.md."
                )
            ref = parts[3]
            readme_parts = parts[4:]
    elif host == "raw.githubusercontent.com":
        if len(parts) < 4:
            raise ControlError("Use a complete raw GitHub README URL.")
        owner, repo, ref = parts[0], parts[1].removesuffix(".git"), parts[2]
        readme_parts = parts[3:]
    else:
        raise ControlError("Only github.com README or repository URLs are accepted.")

    if not _GITHUB_PART_RE.fullmatch(owner) or not _GITHUB_PART_RE.fullmatch(repo):
        raise ControlError("GitHub owner or repository name is not valid.")
    if ref is not None and not _GITHUB_PART_RE.fullmatch(ref):
        raise ControlError(
            "Branch names containing slashes are not supported in README URLs; "
            "use the repository URL to connect its default branch."
        )
    if not readme_parts or not _README_RE.fullmatch(readme_parts[-1]):
        raise ControlError("Paste the repository README file, not an arbitrary GitHub file.")

    return GitHubReadme(
        owner=owner,
        repo=repo,
        ref=ref,
        readme_path=Path(*readme_parts),
    )


def _efferents_home() -> Path:
    return Path(os.environ.get("EFFERENTS_HOME", str(Path.home() / ".efferents")))


def _run_checked(command: list[str], *, timeout: int = 120) -> None:
    try:
        result = subprocess.run(
            command,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ControlError(f"Could not run git: {exc}", status=502) from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"git exited with {result.returncode}"
        raise ControlError(f"GitHub checkout failed: {message}", status=502)


def _checkout_github(source: GitHubReadme) -> tuple[Path, Path]:
    checkouts = _efferents_home() / "checkouts"
    checkout = checkouts / source.owner / source.repo
    if not (checkout / ".git").is_dir():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        if checkout.exists():
            shutil.rmtree(checkout)
        command = ["git", "clone", "--depth", "1", "--filter=blob:none"]
        if source.ref:
            command.extend(["--branch", source.ref, "--single-branch"])
        command.extend([source.clone_url, str(checkout)])
        try:
            _run_checked(command)
        except ControlError:
            if checkout.exists():
                shutil.rmtree(checkout)
            raise

    readme = (checkout / source.readme_path).resolve()
    try:
        readme.relative_to(checkout.resolve())
    except ValueError as exc:
        raise ControlError("README path escaped the GitHub checkout.") from exc
    if not readme.is_file():
        parent = readme.parent
        matches = [
            path for path in parent.iterdir()
            if path.is_file() and _README_RE.fullmatch(path.name)
        ] if parent.is_dir() else []
        if len(matches) == 1:
            readme = matches[0]
        else:
            raise ControlError(
                f"README was not found at {source.readme_path.as_posix()} in the checkout.",
                status=422,
            )
    return checkout, readme


def _submission_candidates(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for lab_yaml in repo_root.rglob("lab.yaml"):
        if ".git" in lab_yaml.parts:
            continue
        candidate = lab_yaml.parent
        if (candidate / "hypothesis.md").is_file():
            candidates.append(candidate.resolve())
    return sorted(set(candidates), key=str)


def _locate_submission(readme: Path, search_root: Path) -> Path:
    direct = readme.parent.resolve()
    if (direct / "lab.yaml").is_file() and (direct / "hypothesis.md").is_file():
        return direct

    candidates = _submission_candidates(search_root.resolve())
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ControlError(
            "No valid submission contract was found. The repository must contain "
            "lab.yaml and a Popper-passed hypothesis.md in the same directory.",
            status=422,
        )
    relative = ", ".join(str(path.relative_to(search_root)) for path in candidates[:6])
    raise ControlError(
        f"Multiple lab submissions were found ({relative}). Paste the README inside "
        "the intended submission directory.",
        status=422,
    )


def _local_source(value: str) -> tuple[Path, Path] | None:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return None
    candidate = candidate.resolve()
    if candidate.is_file():
        if not _README_RE.fullmatch(candidate.name):
            raise ControlError("Local file paths must point to a README file.")
        return candidate.parent, candidate
    if candidate.is_dir():
        readmes = [
            path for path in candidate.iterdir()
            if path.is_file() and _README_RE.fullmatch(path.name)
        ]
        readme = readmes[0] if len(readmes) == 1 else candidate / "README.md"
        return candidate, readme
    raise ControlError("The local README or submission path does not exist.")


def _dotenv_has_key(submission_dir: Path) -> bool:
    from efferents.agents.model_client import credentials_available
    if credentials_available():
        return True
    env_file = submission_dir / ".env"
    if not env_file.is_file():
        return False
    try:
        values: dict[str, str] = {}
        for line in env_file.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() and value.strip():
                values[key.strip()] = value.strip().strip("'\"")
        model = values.get("EFFERENTS_MODEL") or os.environ.get("EFFERENTS_MODEL")
        from efferents.agents.model_client import required_key_env
        key_name = required_key_env(model)
        return key_name is None or bool(values.get(key_name) or os.environ.get(key_name, "").strip())
    except OSError:
        return False


def _recent_steering(path: Path, limit: int = 8) -> list[dict]:
    if not path.is_file():
        return []
    records: list[dict] = []
    for block in path.read_text().split("\n## "):
        block = block.strip()
        if not block:
            continue
        heading, _, body = block.partition("\n")
        timestamp, separator, title = heading.partition(" — ")
        if not separator or title.strip() != "Human steering":
            continue
        mode = "auto"
        message_lines: list[str] = []
        for line in body.strip().splitlines():
            if line.startswith("force_mode:"):
                mode = line.partition(":")[2].strip()
            else:
                message_lines.append(line)
        records.append({
            "timestamp": timestamp.strip(),
            "message": "\n".join(message_lines).strip(),
            "mode": mode,
        })
    return records[-limit:][::-1]


class ControlContext:
    """Thread-safe active-lab state shared by dashboard request handlers."""

    def __init__(
        self,
        connected: ConnectedLab | None = None,
        *,
        paused_demo: bool = False,
    ):
        self._connected = connected
        self.paused_demo = paused_demo
        self._lock = threading.RLock()

    @classmethod
    def from_initial_root(
        cls,
        lab_root: Path | None,
        *,
        paused_demo: bool = False,
    ) -> "ControlContext":
        if lab_root is None:
            return cls(paused_demo=paused_demo)
        lab_root = Path(lab_root).resolve()
        submission = lab_root.parent
        cfg: LabConfig | None = None
        if (submission / "lab.yaml").is_file() and (submission / "hypothesis.md").is_file():
            try:
                cfg = LabConfig.from_submission(submission)
            except SubmissionError:
                cfg = None
        if cfg is None:
            try:
                cfg = lab_mod.get_config()
            except RuntimeError:
                return cls(paused_demo=paused_demo)
            if not (submission / "context").exists():
                submission = lab_root
        return cls(
            ConnectedLab(cfg=cfg, submission_dir=submission, lab_root=lab_root),
            paused_demo=paused_demo,
        )

    def _require_mutable(self) -> None:
        if self.paused_demo:
            raise ControlError(
                "Paused demo mode is read-only; execution and state changes are disabled.",
                status=409,
            )

    def snapshot(self) -> ConnectedLab | None:
        with self._lock:
            return self._connected

    def portfolio(self) -> dict:
        """Return every valid local lab without implying public registration."""
        selected = self.snapshot()
        records = {record.lab_id: record for record in Registry().list()}
        if selected is not None and selected.cfg.lab_id not in records:
            records[selected.cfg.lab_id] = LabRecord(
                lab_id=selected.cfg.lab_id,
                submission_dir=str(selected.submission_dir),
                lab_root=str(selected.lab_root),
                pid=0,
                started_at="",
                status="stopped",
            )

        labs: list[dict] = []
        for record in records.values():
            submission = Path(record.submission_dir).expanduser().resolve()
            lab_root = Path(record.lab_root).expanduser().resolve()
            try:
                if selected is not None and record.lab_id == selected.cfg.lab_id:
                    cfg = selected.cfg
                else:
                    cfg = LabConfig.from_submission(submission, check_paths=False)
                summary = reader.read_summary(lab_root, cfg)
            except (OSError, SubmissionError, RuntimeError):
                continue
            labs.append({
                "lab_id": cfg.lab_id,
                "domain": cfg.domain,
                "subdomain": cfg.subdomain,
                "pi_handle": cfg.pi_handle,
                "repository": cfg.code_repo,
                "submission_dir": str(submission),
                "selected": selected is not None and cfg.lab_id == selected.cfg.lab_id,
                "visibility": "private",
                **summary,
            })
            if self.paused_demo and selected is not None and cfg.lab_id == selected.cfg.lab_id:
                labs[-1]["status"] = "paused"

        # Registry order is the persistent rail order. Selection changes only
        # the highlighted row; it must not move that row underneath the cursor.
        edges = []
        for index, source in enumerate(labs):
            for target in labs[index + 1:]:
                if source["domain"] == target["domain"]:
                    edges.append({
                        "source": source["lab_id"],
                        "target": target["lab_id"],
                        "kind": "shared-domain",
                    })
        return {
            "labs": labs,
            "edges": edges,
            "public_network": {
                "connected": False,
                "labs": 0,
                "message": (
                    "No public registry is connected. Local labs remain private until "
                    "a human explicitly authorizes publication."
                ),
            },
        }

    def select_lab(self, lab_id: str) -> dict:
        """Switch the dashboard to a registered lab without executing it."""
        lab_id = lab_id.strip()
        if not lab_id:
            raise ControlError("Choose a local lab to inspect.")
        record = Registry().get(lab_id)
        if record is None:
            raise ControlError(f"Unknown local lab: {lab_id!r}.", status=404)
        submission = Path(record.submission_dir).expanduser().resolve()
        lab_root = Path(record.lab_root).expanduser().resolve()
        try:
            cfg = LabConfig.from_submission(submission, check_paths=False)
        except SubmissionError as exc:
            raise ControlError(
                f"Registered lab can no longer be loaded: {exc}", status=422
            ) from exc
        connected = ConnectedLab(
            cfg=cfg,
            submission_dir=submission,
            lab_root=lab_root,
            source=str(submission / "README.md"),
            repository=cfg.code_repo,
            readme_path="README.md" if (submission / "README.md").is_file() else None,
        )
        lab_mod.set_config(cfg)
        with self._lock:
            self._connected = connected
        return self.info()

    def connect(self, value: str) -> dict:
        self._require_mutable()
        value = value.strip()
        if not value or len(value) > 2048:
            raise ControlError("Paste a GitHub README URL or local submission path.")

        local = _local_source(value)
        repository: str | None = None
        if local is not None:
            search_root, readme = local
            source_label = str(readme)
        else:
            github = parse_github_readme_url(value)
            search_root, readme = _checkout_github(github)
            repository = github.display_url
            source_label = value

        submission = _locate_submission(readme, search_root)
        try:
            cfg = LabConfig.from_submission(submission)
        except SubmissionError as exc:
            raise ControlError(f"Lab validation failed: {exc}", status=422) from exc

        lab_mod.set_config(cfg)
        lab_root = (submission / "lab").resolve()
        _init_lab_root(submission, lab_root)

        existing = Registry().get(cfg.lab_id)
        if existing is None or not daemon.is_pid_alive(existing.pid):
            Registry().register(LabRecord(
                lab_id=cfg.lab_id,
                submission_dir=str(submission),
                lab_root=str(lab_root),
                pid=0,
                started_at=datetime.now(timezone.utc).isoformat(),
                status="stopped",
            ))

        connected = ConnectedLab(
            cfg=cfg,
            submission_dir=submission,
            lab_root=lab_root,
            source=source_label,
            repository=repository,
            readme_path=str(readme.relative_to(search_root))
            if readme.is_relative_to(search_root) else str(readme),
        )
        with self._lock:
            self._connected = connected
        return self.info()

    def info(self) -> dict:
        connected = self.snapshot()
        if connected is None:
            return {
                "connected": False,
                "paused_demo": self.paused_demo,
                "modes": list(STEERING_MODES),
                "contract": {
                    "readme": False,
                    "lab_yaml": False,
                    "hypothesis": False,
                },
            }
        pid = daemon.read_pidfile(connected.lab_root / "daemon.pid")
        running = pid is not None and daemon.is_pid_alive(pid)
        research_log = connected.submission_dir / "context" / "research_log.md"
        status = "paused" if self.paused_demo else ("running" if running else "stopped")
        return {
            "connected": True,
            "lab_id": connected.cfg.lab_id,
            "domain": connected.cfg.domain,
            "submission_dir": str(connected.submission_dir),
            "lab_root": str(connected.lab_root),
            "source": connected.source,
            "repository": connected.repository or connected.cfg.code_repo,
            "readme_path": connected.readme_path,
            "status": status,
            "has_api_key": (
                False if self.paused_demo else _dotenv_has_key(connected.submission_dir)
            ),
            "paused_demo": self.paused_demo,
            "modes": list(STEERING_MODES),
            "steering": _recent_steering(research_log),
            "contract": {
                "readme": bool(connected.readme_path or connected.source),
                "lab_yaml": True,
                "hypothesis": True,
            },
        }

    def steer(self, message: str, mode: str = "auto") -> dict:
        self._require_mutable()
        connected = self.snapshot()
        if connected is None:
            raise ControlError("Connect a lab before steering it.", status=409)
        message = message.strip()
        if not message or len(message) > 4000:
            raise ControlError("Steering instructions must be between 1 and 4,000 characters.")
        if "\x00" in message:
            raise ControlError("Steering instructions contain an invalid null byte.")
        if mode not in STEERING_MODES:
            raise ControlError(f"Unknown researcher mode: {mode!r}.")

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        context_dir = connected.submission_dir / "context"
        context_dir.mkdir(exist_ok=True)
        research_log = context_dir / "research_log.md"
        block = f"\n## {timestamp} — Human steering\n\n{message}\n"
        if mode != "auto":
            block += f"\nforce_mode: {mode}\n"
        with self._lock:
            with research_log.open("a") as handle:
                handle.write(block)
        return {
            "ok": True,
            "recorded_at": timestamp,
            "mode": mode,
            "status": self.info()["status"],
            "steering": self.info()["steering"],
        }

    def start(self, confirmed: bool) -> dict:
        self._require_mutable()
        connected = self.snapshot()
        if connected is None:
            raise ControlError("Connect a lab before starting it.", status=409)
        if not confirmed:
            raise ControlError(
                "Starting requires explicit confirmation because it executes repository "
                "commands and may incur compute or LLM cost.",
                status=409,
            )
        if not _dotenv_has_key(connected.submission_dir):
            from efferents.agents.model_client import credential_help
            raise ControlError(
                f"{credential_help()} Put the selected provider's credentials in "
                "the submission .env or export them before starting.",
                status=409,
            )
        pid = daemon.read_pidfile(connected.lab_root / "daemon.pid")
        if pid is not None and daemon.is_pid_alive(pid):
            return self.info()

        command = [
            sys.executable,
            "-m",
            "efferents",
            "start",
            "--submission",
            str(connected.submission_dir),
            "--lab-root",
            str(connected.lab_root),
            "--detach",
        ]
        result = subprocess.run(
            command,
            cwd=connected.submission_dir,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ControlError(f"Lab did not start: {detail}", status=409)
        return self.info()

    def stop(self, confirmed: bool) -> dict:
        self._require_mutable()
        connected = self.snapshot()
        if connected is None:
            raise ControlError("Connect a lab before stopping it.", status=409)
        if not confirmed:
            raise ControlError("Stopping the lab requires explicit confirmation.", status=409)

        record = Registry().get(connected.cfg.lab_id)
        if record is None:
            return self.info()
        if Path(record.submission_dir).resolve() != connected.submission_dir.resolve():
            raise ControlError("Registry record does not match the connected lab.", status=409)
        result = subprocess.run(
            [sys.executable, "-m", "efferents", "stop", "--lab-id", connected.cfg.lab_id],
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ControlError(f"Lab did not stop: {detail}", status=409)
        return self.info()
