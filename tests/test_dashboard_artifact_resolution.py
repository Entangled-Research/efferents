"""Dashboard artifact resolution prefers the per-run copy kept by exec.py."""
import json
import sqlite3

from efferents.dashboard import reader


def _runs_db_with_artifacts(db, artifacts: list[dict]) -> None:
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, "
        "synthetic_loss REAL, artifacts_json TEXT, observations_json TEXT)"
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
        ("run-1", "2026-08-02T12:00:00+00:00", 0.031, json.dumps(artifacts), "[]"),
    )
    conn.commit()
    conn.close()


def test_evidence_prefers_per_run_artifact_copy_over_executor_path(
    tmp_path, smoke_lab_config
):
    original = smoke_lab_config.source.dir / "grid.png"
    original.write_bytes(b"\x89PNG\r\n\x1a\noverwritten-by-rerun")
    copy = tmp_path / "artifacts" / "run-1" / "grid" / "grid.png"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"\x89PNG\r\n\x1a\nrun-1")
    _runs_db_with_artifacts(tmp_path / "runs.sqlite", [
        {"kind": "grid", "path": str(copy), "source_path": str(original)},
    ])

    payload = reader.read_evidence(tmp_path, cfg=smoke_lab_config)
    token = payload["records"][0]["artifacts"][0]["token"]

    assert reader.resolve_artifact(tmp_path, token, cfg=smoke_lab_config) == copy.resolve()


def test_evidence_falls_back_to_source_path_when_copy_is_absent(
    tmp_path, smoke_lab_config
):
    original = smoke_lab_config.source.dir / "grid.png"
    original.write_bytes(b"\x89PNG\r\n\x1a\nstill-here")
    _runs_db_with_artifacts(tmp_path / "runs.sqlite", [
        {"kind": "grid", "path": str(tmp_path / "artifacts" / "gone.png"),
         "source_path": str(original)},
        # Older ledgers carry no source_path and still resolve by path alone.
        {"kind": "grid", "path": "grid.png"},
    ])

    payload = reader.read_evidence(tmp_path, cfg=smoke_lab_config)
    record = payload["records"][0]

    assert len(record["artifacts"]) == 1  # both records resolve to one file
    token = record["artifacts"][0]["token"]
    assert reader.resolve_artifact(tmp_path, token, cfg=smoke_lab_config) == original.resolve()


def test_submission_artifacts_are_preserved_without_trusting_sibling_paths(tmp_path, monkeypatch):
    from efferents.lab import LabConfig
    from efferents import lab
    from efferents.exec import _preserve_artifacts
    from pathlib import Path
    import shutil
    sub = tmp_path / 'submission'
    shutil.copytree(Path(__file__).parent / 'fixtures/sample_submission', sub)
    cfg = LabConfig.from_submission(sub)
    monkeypatch.setattr(lab, 'get_config', lambda: cfg)
    root = sub / 'lab'
    root.mkdir()
    output = sub / 'artifacts'
    output.mkdir()
    image = output / 'samples.png'
    image.write_bytes(b'\x89PNG\r\n\x1a\noriginal')
    private = sub / 'private.png'
    private.write_bytes(b'private')
    outside = tmp_path / 'outside.png'
    outside.write_bytes(b'outside')
    (output / 'escape.png').symlink_to(outside)
    assert reader._artifact_path(str(image), root, cfg) == image
    assert reader._artifact_path(str(private), root, cfg) is None
    assert reader._artifact_path(str(output / 'escape.png'), root, cfg) is None
    records = _preserve_artifacts([{'kind': 'samples', 'path': str(image)}], 'real-run', root)
    saved = Path(records[0]['path'])
    image.write_bytes(b'overwritten')
    assert saved.read_bytes().endswith(b'original')
    assert root in saved.parents
