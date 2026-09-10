import json
import tarfile
import sqlite3

from efferents.agents import federation
from efferents.migrations.runner import apply_campaigns_migration
from efferents.agents.state import campaign_insert


def _setup(tmp_path, metric, direction, vals):
    paper_dir = tmp_path / "paper"
    paper_dir.mkdir()
    (paper_dir / "c1.md").write_text("---\nlab_id: L\n---\n## Motivation\nx\n")
    db = tmp_path / "runs.sqlite"
    apply_campaigns_migration(db)
    campaign_insert(
        db, id="c1", lab_id="L", question="q", hypothesis_path="h.md",
        hypothesis_hash="sha256:" + "a" * 64,
        headline_metric=metric, headline_direction=direction,
    )
    conn = sqlite3.connect(db)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS runs (run_id TEXT, started_at TEXT, "
        f"campaign_id TEXT, seed INTEGER, config_yaml TEXT, {metric} REAL)"
    )
    for i, v in enumerate(vals):
        conn.execute(
            f"INSERT INTO runs (run_id, started_at, campaign_id, seed, {metric}) "
            f"VALUES (?, ?, 'c1', 0, ?)",
            (f"r{i}", f"2026-01-01T00:0{i}:00+00:00", v),
        )
    conn.commit()
    conn.close()
    return paper_dir, db


def test_max_direction_exports_highest_as_primary(tmp_path):
    # accuracy: higher is better. Best run = 0.9.
    paper_dir, db = _setup(tmp_path, "accuracy", "max", [0.6, 0.9, 0.7])
    out = tmp_path / "b.tar.gz"
    federation.export_paper_bundle(
        campaign_id="c1", db=db, paper_dir=paper_dir, out_path=out,
        lab_id="L", code_repo="https://e.com/r", code_sha="abc",
    )
    with tarfile.open(out) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert manifest["primary_metric"]["value"] == 0.9


def test_min_direction_exports_lowest_as_primary(tmp_path):
    # loss: lower is better. Best run = 0.1.
    paper_dir, db = _setup(tmp_path, "loss", "min", [0.4, 0.1, 0.3])
    out = tmp_path / "b.tar.gz"
    federation.export_paper_bundle(
        campaign_id="c1", db=db, paper_dir=paper_dir, out_path=out,
        lab_id="L", code_repo="https://e.com/r", code_sha="abc",
    )
    with tarfile.open(out) as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
    assert manifest["primary_metric"]["value"] == 0.1
