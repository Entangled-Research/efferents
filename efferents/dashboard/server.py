"""Local HTTP workspace for connecting, steering, and observing a lab.

Stdlib http.server only — no web framework dependency. Repository connection
validates and initializes local state without executing repository code.
Mutating routes require a per-process CSRF token and execution is separately
confirmed by the user.
"""

from __future__ import annotations

import json
import logging
import secrets
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from efferents.dashboard.control import ControlContext, ControlError
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


class DashboardHandler(BaseHTTPRequestHandler):
    lab_root: Path | None
    control: ControlContext
    csrf_token: str

    def __init__(
        self,
        *args,
        lab_root: Path | None,
        control: ControlContext,
        csrf_token: str,
        **kwargs,
    ):
        self.lab_root = Path(lab_root) if lab_root is not None else None
        self.control = control
        self.csrf_token = csrf_token
        super().__init__(*args, **kwargs)

    def do_GET(self):  # noqa: N802 (stdlib naming)
        try:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send_file(STATIC_DIR / "dashboard.html")
            if path == "/api/control":
                payload = self.control.info()
                payload["csrf_token"] = self.csrf_token
                return self._send_json(payload)
            if path == "/api/labs":
                return self._send_json(self.control.portfolio())

            connected = self.control.snapshot()
            if path == "/api/state":
                if connected is None:
                    return self._send_json(_empty_state())
                payload = reader.read_state(connected.lab_root, cfg=connected.cfg)
                if self.control.paused_demo:
                    payload["status"] = "paused"
                return self._send_json(payload)
            if path == "/api/runs":
                if connected is None:
                    return self._send_json(_empty_runs())
                return self._send_json(
                    reader.read_runs(connected.lab_root, cfg=connected.cfg)
                )
            if path == "/api/papers":
                return self._send_json(
                    reader.read_papers(connected.lab_root) if connected else []
                )
            if path == "/api/activity":
                return self._send_json(
                    reader.read_activity(connected.lab_root) if connected else []
                )
            if path == "/api/evidence":
                if connected is None:
                    return self._send_json(_empty_evidence())
                return self._send_json(
                    reader.read_evidence(connected.lab_root, cfg=connected.cfg)
                )
            if path.startswith("/api/artifacts/"):
                if connected is None:
                    return self.send_error(404)
                token = path.removeprefix("/api/artifacts/")
                if len(token) != 24 or not token.isalnum():
                    return self.send_error(404)
                artifact = reader.resolve_artifact(
                    connected.lab_root, token, cfg=connected.cfg
                )
                if artifact is None:
                    return self.send_error(404)
                return self._send_file(artifact)
            if path.startswith("/static/"):
                target = (STATIC_DIR / path[len("/static/"):]).resolve()
                if STATIC_DIR in target.parents and target.is_file():
                    return self._send_file(target)
            self.send_error(404)
        except Exception:  # read-only server: log server-side, return generic 500
            _log.exception("dashboard request failed: %s", self.path)
            self.send_error(500)

    def do_POST(self):  # noqa: N802 (stdlib naming)
        path = self.path.split("?", 1)[0]
        try:
            self._require_csrf()
            payload = self._read_json()
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
            self.send_error(404)
        except ControlError as exc:
            self._send_json({"error": str(exc)}, status=exc.status)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            self._send_json({"error": "Request body must be valid JSON."}, status=400)
        except Exception:
            _log.exception("dashboard mutation failed: %s", path)
            self._send_json({"error": "Local control request failed."}, status=500)

    def _require_csrf(self) -> None:
        supplied = self.headers.get("X-Efferents-CSRF", "")
        if not secrets.compare_digest(supplied, self.csrf_token):
            raise ControlError("Missing or invalid local control token.", status=403)

    def _read_json(self) -> dict:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            raise ControlError("Content-Type must be application/json.", status=415)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ControlError("Content-Length is invalid.") from exc
        if length <= 0 or length > _MAX_BODY_BYTES:
            raise ControlError("Request body is empty or too large.", status=413)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ControlError("Request body must be a JSON object.")
        return payload

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

    def _send_json(self, obj, *, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
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
        self.end_headers()
        self.wfile.write(data)

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


def make_server(
    lab_root: Path | None,
    port: int = 8800,
    *,
    control: ControlContext | None = None,
    paused_demo: bool = False,
) -> ThreadingHTTPServer:
    control = control or ControlContext.from_initial_root(
        lab_root,
        paused_demo=paused_demo,
    )
    csrf_token = secrets.token_urlsafe(32)
    handler = partial(
        DashboardHandler,
        lab_root=Path(lab_root) if lab_root is not None else None,
        control=control,
        csrf_token=csrf_token,
    )
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(
    lab_root: Path | None,
    port: int = 8800,
    open_browser: bool = True,
    *,
    paused_demo: bool = False,
) -> None:
    httpd = make_server(lab_root, port, paused_demo=paused_demo)
    base_url = f"http://localhost:{httpd.server_address[1]}"
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
