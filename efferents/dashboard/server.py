"""Local HTTP workspace for connecting, steering, and observing labs.

Stdlib http.server only — no web framework dependency. Repository connection
validates and initializes local state without executing repository code.
Mutating routes require a per-process CSRF token and execution is separately
confirmed by the user.

Every per-lab route exists in a lab-scoped form, ``/api/labs/<lab_id>/...``,
so one server can show many labs to many browsers without shared selection
state. The unscoped routes (``/api/state`` …) remain as aliases for the
default lab, which is what a single-lab ``efferents serve`` uses.

Subclasses (a hosted cluster) override the ``_session`` / ``_require_viewer``
/ ``_require_owner`` / ``_actor`` / ``_extra_get`` / ``_extra_post`` hooks to
add identity; here they are no-ops.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from efferents.dashboard.cache import TTLCache
from efferents.dashboard.control import ConnectedLab, ControlContext, ControlError
from efferents.dashboard import reader

STATIC_DIR = Path(__file__).parent / "static"
_MAX_BODY_BYTES = 32_768

_log = logging.getLogger(__name__)

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    **reader.ARTIFACT_CONTENT_TYPES,
}

LAB_ID_PATTERN = r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
_LAB_ROUTE = re.compile(
    rf"^/api/labs/(?P<lab_id>{LAB_ID_PATTERN})/(?P<rest>[A-Za-z0-9_./-]+)$"
)
_LAB_READS = ("state", "runs", "papers", "activity", "evidence", "verdict")
_LAB_WRITES = ("steer", "pause", "resume", "start", "stop")
_CACHED_READS = frozenset(_LAB_READS)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True
    # Default backlog (5) drops connections under a burst of pollers.
    request_queue_size = 128
    allow_reuse_address = True


class DashboardHandler(BaseHTTPRequestHandler):
    lab_root: Path | None
    control: ControlContext
    csrf_token: str
    read_cache: TTLCache | None

    def __init__(
        self,
        *args,
        lab_root: Path | None,
        control: ControlContext,
        csrf_token: str,
        read_cache: TTLCache | None = None,
        **kwargs,
    ):
        self.lab_root = Path(lab_root) if lab_root is not None else None
        self.control = control
        self.csrf_token = csrf_token
        self.read_cache = read_cache
        super().__init__(*args, **kwargs)

    # --- identity hooks (no-ops for a local single-user workspace) ------------

    def _session(self):
        return None

    def _require_viewer(self) -> None:
        """Raise ControlError(401) when reads need a joined session."""

    def _require_owner(self, lab: ConnectedLab) -> None:
        """Raise ControlError(403) when the caller may not mutate ``lab``."""

    def _actor(self) -> str:
        return "lab owner"

    def _daemon_env(self) -> dict[str, str] | None:
        return None

    def _control_payload(self) -> dict:
        payload = self.control.info()
        payload["csrf_token"] = self.csrf_token
        payload["mode"] = "local"
        return payload

    def _extra_get(self, path: str) -> bool:
        """Handle a subclass-specific GET; return True when handled."""
        return False

    def _extra_post(self, path: str, payload: dict) -> bool:
        """Handle a subclass-specific POST; return True when handled."""
        return False

    # --- request handling ------------------------------------------------------

    def do_GET(self):  # noqa: N802 (stdlib naming)
        try:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send_file(STATIC_DIR / "dashboard.html")
            if path == "/api/control":
                return self._send_json(self._control_payload())
            if self._extra_get(path):
                return None
            if path == "/api/labs":
                self._require_viewer()
                return self._send_json(self.control.portfolio())

            match = _LAB_ROUTE.match(path)
            if match:
                self._require_viewer()
                lab = self.control.labs.resolve(match.group("lab_id"))
                rest = match.group("rest")
                if rest == "control":
                    return self._send_json(self.control.lab_info(lab))
                if rest.startswith("artifacts/"):
                    return self._send_artifact(lab, rest.removeprefix("artifacts/"))
                if rest in _LAB_READS:
                    return self._send_json(self._lab_payload(lab, rest))
                return self.send_error(404)

            if path.startswith("/api/"):
                return self._legacy_get(path)
            if path.startswith("/static/"):
                target = (STATIC_DIR / path[len("/static/"):]).resolve()
                if STATIC_DIR in target.parents and target.is_file():
                    return self._send_file(target)
            self.send_error(404)
        except ControlError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
        except Exception:  # read-only server: log server-side, return generic 500
            _log.exception("dashboard request failed: %s", self.path)
            self.send_error(500)

    def _legacy_get(self, path: str) -> None:
        """Unscoped reads resolve to the default lab."""
        connected = self.control.snapshot()
        kind = path.removeprefix("/api/")
        if kind.startswith("artifacts/"):
            if connected is None:
                return self.send_error(404)
            return self._send_artifact(connected, kind.removeprefix("artifacts/"))
        if kind not in _LAB_READS:
            return self.send_error(404)
        if connected is None:
            return self._send_json(_EMPTY_PAYLOADS[kind]())
        return self._send_json(self._lab_payload(connected, kind))

    def _lab_payload(self, lab: ConnectedLab, kind: str) -> dict | list:
        def produce():
            if kind == "state":
                payload = reader.read_state(lab.lab_root, cfg=lab.cfg)
                if self.control.paused_demo:
                    payload["status"] = "paused"
                return payload
            if kind == "runs":
                return reader.read_runs(lab.lab_root, cfg=lab.cfg)
            if kind == "papers":
                return reader.read_papers(lab.lab_root)
            if kind == "activity":
                return reader.read_activity(lab.lab_root)
            if kind == "evidence":
                return reader.read_evidence(lab.lab_root, cfg=lab.cfg)
            if kind == "verdict":
                return reader.read_verdict(lab.lab_root, cfg=lab.cfg)
            raise ControlError("Unknown lab view.", status=404)

        if self.read_cache is None or kind not in _CACHED_READS:
            return produce()
        return self.read_cache.get(
            (str(lab.lab_root), kind), produce, stale_on=(sqlite3.OperationalError,)
        )

    def _send_artifact(self, lab: ConnectedLab, token: str) -> None:
        if len(token) != 24 or not token.isalnum():
            return self.send_error(404)
        artifact = reader.resolve_artifact(lab.lab_root, token, cfg=lab.cfg)
        if artifact is None:
            return self.send_error(404)
        return self._send_file(artifact)

    def do_POST(self):  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        try:
            if self._extra_post_precsrf(path):
                return None
            self._require_csrf()
            payload = self._read_json()
            if self._extra_post(path, payload):
                return None
            if path == "/api/connect":
                return self._send_json(
                    self.control.connect(str(payload.get("source") or "")),
                    status=200,
                )
            if path == "/api/labs/select":
                return self._send_json(
                    self.control.select_lab(str(payload.get("lab_id") or ""))
                )
            if path == "/api/steer":
                return self._send_json(self.control.steer(
                    str(payload.get("message") or ""),
                    str(payload.get("mode") or "auto"),
                ))
            if path == "/api/lab/start":
                return self._send_json(
                    self.control.start(payload.get("confirmed") is True)
                )
            if path == "/api/lab/stop":
                return self._send_json(
                    self.control.stop(payload.get("confirmed") is True)
                )

            match = _LAB_ROUTE.match(path)
            if match and match.group("rest") in _LAB_WRITES:
                self._require_viewer()
                lab = self.control.labs.resolve(match.group("lab_id"))
                self._require_owner(lab)
                return self._send_json(self._lab_mutation(lab, match.group("rest"), payload))
            self.send_error(404)
        except ControlError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._send_json({"error": "Request body must be valid JSON."}, status=400)
        except Exception:
            _log.exception("dashboard mutation failed: %s", path)
            self._send_json({"error": "Local control request failed."}, status=500)

    def _extra_post_precsrf(self, path: str) -> bool:
        """Hook for a subclass POST that legitimately has no CSRF token yet
        (for example the first join). Default: nothing."""
        return False

    def _lab_mutation(self, lab: ConnectedLab, verb: str, payload: dict) -> dict:
        actor = self._actor()
        if verb == "steer":
            return self.control.steer_lab(
                lab,
                str(payload.get("message") or ""),
                str(payload.get("mode") or "auto"),
                by=actor,
            )
        if verb == "pause":
            return self.control.pause_lab(lab, str(payload.get("reason") or ""), by=actor)
        if verb == "resume":
            return self.control.resume_lab(lab, str(payload.get("reason") or ""), by=actor)
        if verb == "start":
            return self.control.start_lab(
                lab, payload.get("confirmed") is True, env_extra=self._daemon_env()
            )
        if verb == "stop":
            return self.control.stop_lab(
                lab, payload.get("confirmed") is True, env_extra=self._daemon_env()
            )
        raise ControlError("Unknown lab action.", status=404)

    # --- plumbing ------------------------------------------------------------------

    def _require_csrf(self) -> None:
        supplied = self.headers.get("X-Efferents-CSRF", "")
        if not secrets.compare_digest(supplied, self.csrf_token):
            raise ControlError("Missing or invalid local control token.", status=403)

    def _read_body(self, limit: int = _MAX_BODY_BYTES) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ControlError("Content-Length is invalid.") from exc
        if length <= 0 or length > limit:
            raise ControlError("Request body is empty or too large.", status=413)
        return self.rfile.read(length)

    def _read_json(self, limit: int = _MAX_BODY_BYTES) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ControlError("Content-Type must be application/json.", status=415)
        payload = json.loads(self._read_body(limit).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ControlError("Request body must be a JSON object.")
        return payload

    def _send_bytes(self, data: bytes, content_type: str, *, status: int = 200,
                    extra_headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self._security_headers()
        self._extra_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
            "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )

    def _extra_headers(self) -> None:
        """Hook: a subclass may add headers (for example Set-Cookie)."""

    def _send_json(self, obj, *, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self._extra_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            return self.send_error(404)
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         _CONTENT_TYPES.get(path.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self._extra_headers()
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self._security_headers()
        self._extra_headers()
        self.end_headers()

    def log_message(self, *args) -> None:  # silence per-request stderr logging
        pass


def _empty_state() -> dict:
    return {
        "lab_id": "no-lab-connected",
        "domain": "",
        "status": "disconnected",
        "budget": {"spent": 0.0, "cap": 0.0},
        "hypothesis": {"question": "", "claim": "", "falsifier": "", "student": ""},
    }


def _empty_runs() -> dict:
    return {
        "headline": {"column": "metric", "direction": "min"},
        "runs": [],
        "series": [],
        "history": {"total": 0, "best": None, "best_run_id": None},
    }


def _empty_evidence() -> dict:
    return {
        "panels": [],
        "constraints": [],
        "comparison": {"axis": None, "labels": {}, "order": []},
        "records": [],
        "artifact_count": 0,
    }


def _empty_verdict() -> dict:
    return {
        "verdict": "undecided",
        "line": "verdict: undecided · no falsifiers",
        "n_runs": 0,
        "axes": [],
        "comparison": {"axis": None, "labels": {}},
        "columns": [],
        "buckets": [],
        "paired": [],
        "falsifiers": [],
    }


_EMPTY_PAYLOADS = {
    "state": _empty_state,
    "runs": _empty_runs,
    "papers": list,
    "activity": list,
    "evidence": _empty_evidence,
    "verdict": _empty_verdict,
}


def make_server(
    lab_root: Path | None,
    port: int = 8800,
    *,
    host: str = "127.0.0.1",
    control: ControlContext | None = None,
    paused_demo: bool = False,
    handler_cls: type[DashboardHandler] = DashboardHandler,
    read_ttl_s: float = 2.0,
    handler_kwargs: dict | None = None,
) -> ThreadingHTTPServer:
    control = control or ControlContext.from_initial_root(
        lab_root,
        paused_demo=paused_demo,
    )
    csrf_token = secrets.token_urlsafe(32)
    handler = partial(
        handler_cls,
        lab_root=Path(lab_root) if lab_root is not None else None,
        control=control,
        csrf_token=csrf_token,
        read_cache=TTLCache(ttl_s=read_ttl_s) if read_ttl_s > 0 else None,
        **(handler_kwargs or {}),
    )
    return DashboardServer((host, port), handler)


def serve(
    lab_root: Path | None,
    port: int = 8800,
    open_browser: bool = True,
    *,
    paused_demo: bool = False,
    host: str = "127.0.0.1",
) -> None:
    httpd = make_server(lab_root, port, host=host, paused_demo=paused_demo)
    shown_host = "localhost" if host in ("127.0.0.1", "0.0.0.0", "::") else host
    base_url = f"http://{shown_host}:{httpd.server_address[1]}"
    url = f"{base_url}/#observe" if paused_demo else base_url
    mode = "paused read-only demo" if paused_demo else "local workspace"
    print(f"efferents dashboard: {url}  ({mode}; Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        httpd.server_close()
