from __future__ import annotations

import http.client
import json
import shutil
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from efferents.dashboard import server
from efferents.dashboard.control import (
    ControlContext,
    ControlError,
    parse_github_readme_url,
)

SAMPLE = Path(__file__).parent / "fixtures" / "sample_submission"


def _submission(tmp_path: Path) -> Path:
    sub = tmp_path / "submission"
    shutil.copytree(SAMPLE, sub)
    (sub / "README.md").write_text("# Connected lab\n")
    return sub


def test_parse_github_repository_url_defaults_to_root_readme():
    source = parse_github_readme_url("https://github.com/acme/particle-lab")
    assert source.owner == "acme"
    assert source.repo == "particle-lab"
    assert source.ref is None
    assert source.readme_path == Path("README.md")


def test_parse_github_blob_readme_preserves_submission_path():
    source = parse_github_readme_url(
        "https://github.com/acme/particle-lab/blob/main/submission/README.md"
    )
    assert source.ref == "main"
    assert source.readme_path == Path("submission/README.md")


def test_parse_raw_github_readme():
    source = parse_github_readme_url(
        "https://raw.githubusercontent.com/acme/particle-lab/main/README.md"
    )
    assert source.owner == "acme"
    assert source.repo == "particle-lab"
    assert source.ref == "main"


@pytest.mark.parametrize(
    "value",
    [
        "https://gitlab.com/acme/lab/README.md",
        "https://github.com/acme/lab/blob/main/src/train.py",
        "https://github.com/acme/lab/blob/main/../README.md",
    ],
)
def test_parse_github_readme_rejects_unsupported_sources(value):
    with pytest.raises(ControlError):
        parse_github_readme_url(value)


def test_connect_local_submission_validates_and_initializes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = _submission(tmp_path)
    control = ControlContext()

    result = control.connect(str(sub / "README.md"))

    assert result["connected"] is True
    assert result["lab_id"] == "sample-conjecture"
    assert result["contract"] == {
        "readme": True,
        "lab_yaml": True,
        "hypothesis": True,
    }
    assert (sub / "lab" / "runs.sqlite").is_file()
    assert (sub / "context" / "research_log.md").is_file()


def test_steering_is_appended_with_optional_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = _submission(tmp_path)
    control = ControlContext()
    control.connect(str(sub / "README.md"))

    result = control.steer(
        "Prioritize the held-out seed before broadening the sweep.",
        "devils_advocate",
    )

    log = (sub / "context" / "research_log.md").read_text()
    assert "Prioritize the held-out seed" in log
    assert "force_mode: devils_advocate" in log
    assert result["steering"][0]["mode"] == "devils_advocate"


def test_start_requires_explicit_confirmation_and_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sub = _submission(tmp_path)
    control = ControlContext()
    control.connect(str(sub / "README.md"))

    with pytest.raises(ControlError, match="explicit confirmation"):
        control.start(False)
    with pytest.raises(ControlError, match="ANTHROPIC_API_KEY"):
        control.start(True)


def test_paused_demo_rejects_mutations(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    sub = _submission(tmp_path)
    mutable = ControlContext()
    mutable.connect(str(sub / "README.md"))
    control = ControlContext(mutable.snapshot(), paused_demo=True)

    assert control.info()["status"] == "paused"
    assert control.info()["has_api_key"] is False
    for action in (
        lambda: control.connect(str(sub / "README.md")),
        lambda: control.steer("Change course."),
        lambda: control.start(True),
        lambda: control.stop(True),
    ):
        with pytest.raises(ControlError, match="read-only"):
            action()


@pytest.fixture
def entry_server(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    control = ControlContext()
    httpd = server.make_server(None, port=0, control=control)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port, control
    httpd.shutdown()
    httpd.server_close()


def _json_request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    csrf: str | None = None,
):
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if csrf is not None:
        headers["X-Efferents-CSRF"] = csrf
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read())


def test_entry_server_connects_then_serves_lab_state(
    entry_server, tmp_path
):
    port, _control = entry_server
    sub = _submission(tmp_path)
    _, initial = _json_request(port, "/api/control")
    assert initial["connected"] is False

    status, connected = _json_request(
        port,
        "/api/connect",
        method="POST",
        payload={"source": str(sub / "README.md")},
        csrf=initial["csrf_token"],
    )

    assert status == 200
    assert connected["lab_id"] == "sample-conjecture"
    _, state = _json_request(port, "/api/state")
    assert state["lab_id"] == "sample-conjecture"


def test_entry_server_rejects_post_without_csrf(entry_server, tmp_path):
    port, _control = entry_server
    sub = _submission(tmp_path)
    connection = http.client.HTTPConnection("127.0.0.1", port)
    payload = json.dumps({"source": str(sub / "README.md")})
    connection.request(
        "POST",
        "/api/connect",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    response = connection.getresponse()
    body = json.loads(response.read())
    connection.close()

    assert response.status == 403
    assert "control token" in body["error"]


def test_entry_server_sends_browser_security_headers(entry_server):
    port, _control = entry_server
    request = urllib.request.Request(f"http://127.0.0.1:{port}/")
    with urllib.request.urlopen(request) as response:
        response.read()
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
