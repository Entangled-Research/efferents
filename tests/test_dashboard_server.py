import http.client
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from efferents.dashboard import server


def _make_runs_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, "
        "ended_at TEXT, synthetic_loss REAL)"
    )
    conn.execute(
        "INSERT INTO runs (run_id, started_at, synthetic_loss) VALUES (?, ?, ?)",
        ("r1", "2026-06-01T10:00:00", 0.05),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def running_server(tmp_path, smoke_lab_config):
    _make_runs_db(tmp_path / "runs.sqlite")
    httpd = server.make_server(tmp_path, port=0)  # port 0 -> OS picks a free port
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield port
    httpd.shutdown()
    httpd.server_close()


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as resp:
        return resp.status, json.loads(resp.read())


def test_api_state(running_server):
    status, body = _get(running_server, "/api/state")
    assert status == 200
    assert body["lab_id"] == "smoke-fixture"
    assert body["status"] == "stopped"


def test_paused_demo_reports_read_only_paused_state(tmp_path, smoke_lab_config):
    _make_runs_db(tmp_path / "runs.sqlite")
    httpd = server.make_server(tmp_path, port=0, paused_demo=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        _, control = _get(port, "/api/control")
        _, state = _get(port, "/api/state")
        assert control["paused_demo"] is True
        assert control["status"] == "paused"
        assert control["has_api_key"] is False
        assert state["status"] == "paused"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_api_runs(running_server):
    status, body = _get(running_server, "/api/runs")
    assert status == 200
    assert body["headline"]["column"] == "synthetic_loss"
    assert body["runs"][0]["run_id"] == "r1"


def test_api_serves_only_catalogued_visual_evidence(
    running_server, tmp_path, smoke_lab_config
):
    image = smoke_lab_config.source.dir / "sample.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nserved")
    observation = json.dumps([{
        "name": "sample",
        "dimensions": {"seed": 4},
        "metrics": {"synthetic_loss": 0.05},
        "artifacts": [{"kind": "sample_grid", "path": str(image)}],
    }])
    conn = sqlite3.connect(tmp_path / "runs.sqlite")
    conn.execute("ALTER TABLE runs ADD COLUMN observations_json TEXT")
    conn.execute("ALTER TABLE runs ADD COLUMN artifacts_json TEXT")
    conn.execute(
        "UPDATE runs SET observations_json = ?, artifacts_json = '[]' WHERE run_id = 'r1'",
        (observation,),
    )
    conn.commit()
    conn.close()

    status, body = _get(running_server, "/api/evidence")

    assert status == 200
    artifact_url = body["records"][0]["artifacts"][0]["url"]
    assert str(image) not in artifact_url
    with urllib.request.urlopen(
        f"http://127.0.0.1:{running_server}{artifact_url}"
    ) as response:
        assert response.headers["Content-Type"] == "image/png"
        assert response.read() == image.read_bytes()


def test_api_papers_empty(running_server):
    status, body = _get(running_server, "/api/papers")
    assert status == 200
    assert body == []


def test_api_activity_empty(running_server):
    status, body = _get(running_server, "/api/activity")
    assert status == 200
    assert body == []


def test_unknown_path_404(running_server):
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{running_server}/nope")
    assert exc.value.code == 404


def test_static_traversal_blocked(running_server):
    conn = http.client.HTTPConnection("127.0.0.1", running_server)
    conn.request("GET", "/static/../../../etc/passwd")
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 404


def test_root_serves_dashboard_html(running_server):
    with urllib.request.urlopen(
        f"http://127.0.0.1:{running_server}/"
    ) as resp:
        assert resp.status == 200
        assert resp.headers["Content-Type"].startswith("text/html")
        html = resp.read().decode()
    assert "dashboard.js" in html
