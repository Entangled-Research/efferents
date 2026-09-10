import json
import tarfile
import sqlite3

from efferents.agents import federation
from efferents.migrations.runner import apply_campaigns_migration
from efferents.agents.state import campaign_insert


def _setup(tmp_path, metric):
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "c1.md").write_text("---\nlab_id: L\n---\n## Motivation\nx\n")
    db = tmp_path / "runs.sqlite"
    apply_campaigns_migration(db)
    campaign_insert(
        db, id="c1", lab_id="L", question="q", hypothesis_path="h.md",
        hypothesis_hash="sha256:" + "a" * 64,
        headline_metric=metric, headline_direction="min",
    )
    conn = sqlite3.connect(db)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS runs (run_id TEXT, started_at TEXT, "
        f"campaign_id TEXT, seed INTEGER, config_yaml TEXT, {metric} REAL)"
    )
    conn.execute(
        f"INSERT INTO runs (run_id, started_at, campaign_id, seed, {metric}) "
        f"VALUES ('r0','2026-01-01T00:00:00+00:00','c1',0,0.12)"
    )
    conn.commit()
    conn.close()
    return paper_dir, db


def test_export_provenance_uses_campaign_metric(tmp_path):
    paper_dir, db = _setup(tmp_path, "synthetic_loss")
    out = tmp_path / "bundle.tar.gz"
    federation.export_paper_bundle(
        campaign_id="c1", db=db, paper_dir=paper_dir, out_path=out,
        lab_id="L", code_repo="https://example.com/r", code_sha="abc123",
    )
    with tarfile.open(out) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        metrics = json.loads(tar.extractfile("metric_provenance.json").read())
    assert manifest["primary_metric"]["name"] == "synthetic_loss"
    assert metrics[0]["synthetic_loss"] == 0.12
