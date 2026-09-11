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

import json
from typing import Callable

from efferents import daemon
from efferents import steer as steer_mod
from efferents.cli import _init_lab_root
from efferents.dashboard import reader
from efferents.dashboard.cache import TTLCache
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
    # Present when the lab was created inside a hosted cluster
    # (<submission>/owner.json); None for a plain local lab.
    owner_id: str | None = None
    owner_name: str | None = None
    track: str | None = None


OWNER_FILENAME = "owner.json"


def read_owner_meta(submission_dir: Path) -> dict:
    """Ownership/track metadata written by a cluster at lab creation, if any."""
    path = Path(submission_dir) / OWNER_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def connected_lab_from_record(record: LabRecord, cfg: LabConfig) -> ConnectedLab:
    submission = Path(record.submission_dir).expanduser().resolve()
    meta = read_owner_meta(submission)
    return ConnectedLab(
        cfg=cfg,
        submission_dir=submission,
        lab_root=Path(record.lab_root).expanduser().resolve(),
        source=str(submission / "README.md"),
        repository=cfg.code_repo,
        readme_path="README.md" if (submission / "README.md").is_file() else None,
        owner_id=meta.get("owner_id"),
        owner_name=meta.get("owner_name"),
        track=meta.get("track"),
    )


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


class LabCatalog:
    """Resolve registered labs to ``ConnectedLab`` objects, per request.

    Nothing here touches the process-global active config, so any number of
    labs can be read by one server. Entries are cached until ``lab.yaml``,
    ``hypothesis.md`` or ``owner.json`` changes on disk.
    """

    def __init__(self, default: Callable[[], ConnectedLab | None] | None = None):
        self._default = default
        self._cache: dict[str, tuple[tuple, ConnectedLab]] = {}
        self._lock = threading.Lock()

    def records(self) -> list[LabRecord]:
        return Registry().list()

    def _fingerprint(self, submission: Path) -> tuple:
        return tuple(
            _mtime(submission / name)
            for name in ("lab.yaml", "hypothesis.md", OWNER_FILENAME)
        )

    def resolve(self, lab_id: str) -> ConnectedLab:
        lab_id = lab_id.strip()
        if not lab_id:
            raise ControlError("Choose a local lab to inspect.")
        record = Registry().get(lab_id)
        if record is None:
            default = self._default() if self._default is not None else None
            if default is not None and default.cfg.lab_id == lab_id:
                return default
            raise ControlError(f"Unknown local lab: {lab_id!r}.", status=404)
        submission = Path(record.submission_dir).expanduser().resolve()
        fingerprint = (str(submission), str(record.lab_root), *self._fingerprint(submission))
        with self._lock:
            hit = self._cache.get(lab_id)
            if hit is not None and hit[0] == fingerprint:
                return hit[1]
        try:
            cfg = LabConfig.from_submission(submission, check_paths=False)
        except SubmissionError as exc:
            raise ControlError(
                f"Registered lab can no longer be loaded: {exc}", status=422
            ) from exc
        lab = connected_lab_from_record(record, cfg)
        with self._lock:
            self._cache[lab_id] = (fingerprint, lab)
        return lab

    def invalidate(self, lab_id: str | None = None) -> None:
        with self._lock:
            if lab_id is None:
                self._cache.clear()
            else:
                self._cache.pop(lab_id, None)




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


class ControlContext:
    """Thread-safe lab control shared by dashboard request handlers.

    Every per-lab operation takes an explicit ``ConnectedLab`` (resolved by
    the handler from the URL through ``self.labs``). The legacy single-lab
    methods (``info()``, ``steer()``, ``start()``, ``stop()``) operate on the
    *default* lab: the one given to ``efferents serve --lab-root`` or the one
    most recently connected/selected in the browser.
    """

    def __init__(
        self,
        connected: ConnectedLab | None = None,
        *,
        paused_demo: bool = False,
    ):
        self._connected = connected
        self.paused_demo = paused_demo
        self._lock = threading.RLock()
        self.labs = LabCatalog(default=self.snapshot)
        # A hosted cluster installs a callable that derives extra network
        # edges (reviews, citations, reproductions) from its shared files.
        self.extra_edges: Callable[[list[dict]], list[dict]] | None = None
        # A hosted cluster also lists labs that run elsewhere and report in
        # through its hub; rows in the portfolio shape, never selectable here.
        self.extra_labs: Callable[[], list[dict]] | None = None
        self._portfolio_cache = TTLCache(ttl_s=2.0)

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
            from efferents import lab as lab_mod  # noqa: PLC0415
            try:
                cfg = lab_mod.get_config()
            except RuntimeError:
                return cls(paused_demo=paused_demo)
            if not (submission / "context").exists():
                submission = lab_root
        meta = read_owner_meta(submission)
        return cls(
            ConnectedLab(
                cfg=cfg, submission_dir=submission, lab_root=lab_root,
                owner_id=meta.get("owner_id"), owner_name=meta.get("owner_name"),
                track=meta.get("track"),
            ),
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

    def _require_default(self, verb: str) -> ConnectedLab:
        connected = self.snapshot()
        if connected is None:
            raise ControlError(f"Connect a lab before {verb} it.", status=409)
        return connected

    # --- portfolio -----------------------------------------------------------

    def _portfolio_rows(self) -> list[dict]:
        selected = self.snapshot()
        records = {record.lab_id: record for record in self.labs.records()}
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
            try:
                if selected is not None and record.lab_id == selected.cfg.lab_id:
                    lab = selected
                else:
                    lab = self.labs.resolve(record.lab_id)
                summary = reader.read_summary(lab.lab_root, lab.cfg)
            except (OSError, ControlError, SubmissionError, RuntimeError):
                continue
            labs.append({
                "lab_id": lab.cfg.lab_id,
                "domain": lab.cfg.domain,
                "subdomain": lab.cfg.subdomain,
                "pi_handle": lab.cfg.pi_handle,
                "repository": lab.cfg.code_repo,
                "submission_dir": str(lab.submission_dir),
                "owner_id": lab.owner_id,
                "owner_name": lab.owner_name,
                "track": lab.track,
                "visibility": "private",
                **summary,
            })
        return labs

    def portfolio(self) -> dict:
        """Return every valid local lab without implying public registration."""
        import sqlite3  # noqa: PLC0415
        rows = self._portfolio_cache.get(
            "portfolio", self._portfolio_rows, stale_on=(sqlite3.OperationalError,)
        )
        selected = self.snapshot()
        selected_id = selected.cfg.lab_id if selected is not None else None
        labs = [
            {**row, "selected": row["lab_id"] == selected_id}
            for row in rows
        ]
        if self.extra_labs is not None:
            try:
                local_ids = {row["lab_id"] for row in labs}
                labs.extend({**row, "selected": False} for row in self.extra_labs()
                            if row.get("lab_id") not in local_ids)
            except Exception:  # a broken hub file must not hide the local labs
                pass
        if self.paused_demo:
            for row in labs:
                if row["selected"]:
                    row["status"] = "paused"

        # Registry order is the persistent rail order. Selection changes only
        # the highlighted row; it must not move that row underneath the cursor.
        edges: list[dict] = []
        for index, source in enumerate(labs):
            for target in labs[index + 1:]:
                if source["domain"] == target["domain"]:
                    edges.append({
                        "source": source["lab_id"],
                        "target": target["lab_id"],
                        "kind": "shared-domain",
                    })
                if source.get("track") and source.get("track") == target.get("track"):
                    edges.append({
                        "source": source["lab_id"],
                        "target": target["lab_id"],
                        "kind": "shared-track",
                    })
        if self.extra_edges is not None:
            try:
                edges.extend(self.extra_edges(labs))
            except Exception:  # never let a derived edge break the network view
                pass
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
        """Make a registered lab the default without executing it."""
        connected = self.labs.resolve(lab_id)
        with self._lock:
            self._connected = connected
        self._portfolio_cache.invalidate()
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

        lab_root = (submission / "lab").resolve()
        _init_lab_root(submission, lab_root, cfg=cfg)

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

        meta = read_owner_meta(submission)
        connected = ConnectedLab(
            cfg=cfg,
            submission_dir=submission,
            lab_root=lab_root,
            source=source_label,
            repository=repository,
            readme_path=str(readme.relative_to(search_root))
            if readme.is_relative_to(search_root) else str(readme),
            owner_id=meta.get("owner_id"),
            owner_name=meta.get("owner_name"),
            track=meta.get("track"),
        )
        with self._lock:
            self._connected = connected
        self.labs.invalidate(cfg.lab_id)
        self._portfolio_cache.invalidate()
        return self.info()

    # --- per-lab reads ---------------------------------------------------------

    def _status_of(self, lab: ConnectedLab) -> str:
        if self.paused_demo:
            return "paused"
        pid = daemon.read_pidfile(lab.lab_root / "daemon.pid")
        running = pid is not None and daemon.is_pid_alive(pid)
        if running and steer_mod.owner_paused(lab.lab_root) is not None:
            return "paused"
        return "running" if running else "stopped"

    def lab_info(self, lab: ConnectedLab) -> dict:
        status = self._status_of(lab)
        return {
            "connected": True,
            "lab_id": lab.cfg.lab_id,
            "domain": lab.cfg.domain,
            "submission_dir": str(lab.submission_dir),
            "lab_root": str(lab.lab_root),
            "source": lab.source,
            "repository": lab.repository or lab.cfg.code_repo,
            "readme_path": lab.readme_path,
            "status": status,
            "owner_paused": (
                steer_mod.owner_paused(lab.lab_root) is not None
                if not self.paused_demo else False
            ),
            "owner_id": lab.owner_id,
            "owner_name": lab.owner_name,
            "track": lab.track,
            "has_api_key": (
                False if self.paused_demo else _dotenv_has_key(lab.submission_dir)
            ),
            "paused_demo": self.paused_demo,
            "modes": list(STEERING_MODES),
            "steering": recent_steering(lab.lab_root),
            "contract": {
                "readme": bool(lab.readme_path or lab.source),
                "lab_yaml": True,
                "hypothesis": True,
            },
        }

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
        return self.lab_info(connected)

    # --- per-lab mutations -----------------------------------------------------

    def steer_lab(
        self,
        lab: ConnectedLab,
        message: str,
        mode: str = "auto",
        *,
        by: str = "lab owner",
    ) -> dict:
        """Record funder direction in the auditable steering ledger.

        The text goes verbatim into the charter (``context/popper.md``) and
        ``lab/steering.jsonl`` (acknowledged by the daemon on its next step).
        A requested Researcher mode additionally writes the ``force_mode``
        block the Researcher reads from ``context/research_log.md``.
        """
        self._require_mutable()
        message = message.strip()
        if not message or len(message) > 4000:
            raise ControlError("Steering instructions must be between 1 and 4,000 characters.")
        if "\x00" in message:
            raise ControlError("Steering instructions contain an invalid null byte.")
        if mode not in STEERING_MODES:
            raise ControlError(f"Unknown researcher mode: {mode!r}.")
        by = by.strip()[:120] or "lab owner"

        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            try:
                steer_mod.steer(
                    lab.submission_dir,
                    text=message,
                    by=by,
                    lab_root=lab.lab_root,
                    extra={"mode": mode},
                )
            except steer_mod.SteeringError as exc:
                raise ControlError(str(exc)) from exc
            if mode != "auto":
                context_dir = lab.submission_dir / "context"
                context_dir.mkdir(exist_ok=True)
                research_log = context_dir / "research_log.md"
                block = (
                    f"\n## {timestamp} — Human steering\n\n{message}\n"
                    f"\nforce_mode: {mode}\n"
                )
                with research_log.open("a") as handle:
                    handle.write(block)
        info = self.lab_info(lab)
        return {
            "ok": True,
            "recorded_at": timestamp,
            "mode": mode,
            "by": by,
            "status": info["status"],
            "steering": info["steering"],
        }

    def steer(self, message: str, mode: str = "auto") -> dict:
        self._require_mutable()
        return self.steer_lab(self._require_default("steering"), message, mode)

    def _pause_or_resume(
        self, lab: ConnectedLab, action: str, reason: str, *, by: str
    ) -> dict:
        self._require_mutable()
        by = by.strip()[:120] or "lab owner"
        reason = reason.strip()[:4000] or f"{action} requested by {by}"
        try:
            steer_mod.steer(
                lab.submission_dir, text=reason, by=by, action=action,
                lab_root=lab.lab_root,
            )
        except steer_mod.SteeringError as exc:
            raise ControlError(str(exc)) from exc
        return {**self.lab_info(lab), "queued": action}

    def pause_lab(self, lab: ConnectedLab, reason: str = "", *, by: str = "lab owner") -> dict:
        """Queue an owner pause; the daemon halts spending on its next step."""
        return self._pause_or_resume(lab, "pause", reason, by=by)

    def resume_lab(self, lab: ConnectedLab, reason: str = "", *, by: str = "lab owner") -> dict:
        return self._pause_or_resume(lab, "resume", reason, by=by)

    def start_lab(
        self,
        lab: ConnectedLab,
        confirmed: bool,
        *,
        env_extra: dict[str, str] | None = None,
    ) -> dict:
        self._require_mutable()
        if not confirmed:
            raise ControlError(
                "Starting requires explicit confirmation because it executes repository "
                "commands and may incur compute or LLM cost.",
                status=409,
            )
        if not _dotenv_has_key(lab.submission_dir):
            from efferents.agents.model_client import credential_help
            raise ControlError(
                f"{credential_help()} Put the selected provider's credentials in "
                "the submission .env or export them before starting.",
                status=409,
            )
        pid = daemon.read_pidfile(lab.lab_root / "daemon.pid")
        if pid is not None and daemon.is_pid_alive(pid):
            return self.lab_info(lab)

        command = [
            sys.executable,
            "-m",
            "efferents",
            "start",
            "--submission",
            str(lab.submission_dir),
            "--lab-root",
            str(lab.lab_root),
            "--detach",
        ]
        result = subprocess.run(
            command,
            cwd=lab.submission_dir,
            env={**os.environ, **(env_extra or {})},
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ControlError(f"Lab did not start: {detail}", status=409)
        self._portfolio_cache.invalidate()
        return self.lab_info(lab)

    def start(self, confirmed: bool) -> dict:
        self._require_mutable()
        return self.start_lab(self._require_default("starting"), confirmed)

    def stop_lab(
        self,
        lab: ConnectedLab,
        confirmed: bool,
        *,
        env_extra: dict[str, str] | None = None,
    ) -> dict:
        self._require_mutable()
        if not confirmed:
            raise ControlError("Stopping the lab requires explicit confirmation.", status=409)

        record = Registry().get(lab.cfg.lab_id)
        if record is None:
            return self.lab_info(lab)
        if Path(record.submission_dir).resolve() != lab.submission_dir.resolve():
            raise ControlError("Registry record does not match the connected lab.", status=409)
        result = subprocess.run(
            [sys.executable, "-m", "efferents", "stop", "--lab-id", lab.cfg.lab_id],
            env={**os.environ, **(env_extra or {})},
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()
            raise ControlError(f"Lab did not stop: {detail}", status=409)
        self._portfolio_cache.invalidate()
        return self.lab_info(lab)

    def stop(self, confirmed: bool) -> dict:
        self._require_mutable()
        return self.stop_lab(self._require_default("stopping"), confirmed)


def recent_steering(lab_root: Path, limit: int = 8) -> list[dict]:
    """Newest-first steering records for the observer panel."""
    records = steer_mod.read_steering(lab_root)
    out = []
    for rec in records[-limit:][::-1]:
        out.append({
            "timestamp": rec.get("ts"),
            "message": rec.get("text", ""),
            "mode": rec.get("mode", "auto"),
            "by": rec.get("by"),
            "action": rec.get("action"),
            "acknowledged": rec.get("ack") is not None,
        })
    return out
