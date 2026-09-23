import json
import sqlite3
from dataclasses import replace

from efferents.dashboard import reader
from efferents.lab import Evidence


def test_visual_evidence_uses_observation_contract_and_opaque_artifact_tokens(
    tmp_path, smoke_lab_config
):
    samples = smoke_lab_config.source.dir / "samples"
    samples.mkdir()
    image = samples / "run-1.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\nvisual-evidence")
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\noutside")

    observation = {
        "name": "control-sample",
        "dimensions": {"variant": "control", "seed": 7},
        "metrics": {"synthetic_loss": 0.031},
        "artifacts": [
            {"kind": "sample_grid", "path": str(image)},
            {"kind": "sample_grid", "path": str(outside)},
        ],
    }
    db = tmp_path / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, "
        "synthetic_loss REAL, artifacts_json TEXT, observations_json TEXT)"
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?)",
        (
            "run-1",
            "2026-08-02T12:00:00+00:00",
            0.031,
            "[]",
            json.dumps([observation]),
        ),
    )
    conn.commit()
    conn.close()

    payload = reader.read_evidence(tmp_path, cfg=smoke_lab_config)

    assert payload["artifact_count"] == 1
    assert payload["panels"][0]["label"] == "Loss"
    record = payload["records"][0]
    assert record["dimensions"] == {"variant": "control", "seed": 7}
    assert record["metrics"] == {"synthetic_loss": 0.031}
    assert record["eligible"] is True
    artifact = record["artifacts"][0]
    assert artifact["url"].startswith("/api/artifacts/")
    assert str(image) not in artifact["url"]
    assert reader.resolve_artifact(
        tmp_path, artifact["token"], cfg=smoke_lab_config
    ) == image.resolve()


def test_visual_evidence_is_empty_without_declared_artifacts(
    tmp_path, smoke_lab_config
):
    assert reader.read_evidence(tmp_path, cfg=smoke_lab_config) == {
        "suite": {"status": "missing", "message": "Eval suite not configured. Run efferents evals generate."},
        "panels": [{
            "column": "synthetic_loss",
            "label": "Loss",
            "direction": "min",
            "target": None,
        }],
        "constraints": [],
        "comparison": {"axis": None, "labels": {}, "order": []},
        "records": [],
        "artifact_count": 0,
    }


def test_visual_evidence_exposes_lab_owned_comparison_metadata(
    tmp_path, smoke_lab_config
):
    cfg = replace(
        smoke_lab_config,
        evidence=Evidence(
            comparison_axis="variant",
            comparison_labels=(("qfm", "QFM"), ("px", "Pixel baseline")),
            comparison_order=("qfm", "px"),
        ),
    )

    payload = reader.read_evidence(tmp_path, cfg=cfg)

    assert payload["comparison"] == {
        "axis": "variant",
        "labels": {"qfm": "QFM", "px": "Pixel baseline"},
        "order": ["qfm", "px"],
    }


def test_deployment_images_join_the_same_generic_evidence_surface(
    tmp_path, smoke_lab_config
):
    deployment = tmp_path / "deployments" / "champion" / "verification"
    deployment.mkdir(parents=True)
    image = deployment / "samples.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\ndeployment")
    (deployment / "metrics.json").write_text(json.dumps({
        "metrics": {"synthetic_loss": 0.019},
    }))
    (deployment.parent / "manifest.json").write_text(json.dumps({
        "source_run_id": "champion-run",
        "promoted_at": "2026-08-02T12:00:00+00:00",
        "model": "control",
        "seed": 9,
        "metrics": {"synthetic_loss": 0.021},
    }))

    payload = reader.read_evidence(tmp_path, cfg=smoke_lab_config)

    assert payload["artifact_count"] == 1
    assert payload["records"][0]["name"] == "champion / verification / samples"
    assert payload["records"][0]["run_id"] == "champion-run"
    assert payload["records"][0]["dimensions"] == {"model": "control", "seed": 9}
    assert payload["records"][0]["metrics"]["synthetic_loss"] == 0.019
