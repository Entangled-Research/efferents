import sqlite3

from efferents.migrations.runner import apply_campaigns_migration
from efferents.agents.state import campaign_insert


def _row(db, cid):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return dict(conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone())
    finally:
        conn.close()


def test_insert_with_metric(tmp_path):
    db = tmp_path / "runs.sqlite"
    apply_campaigns_migration(db)
    campaign_insert(
        db, id="c1", lab_id="L", question="q", hypothesis_path="h.md",
        hypothesis_hash="sha256:" + "a" * 64,
        headline_metric="synthetic_loss", headline_direction="min",
    )
    row = _row(db, "c1")
    assert row["headline_metric"] == "synthetic_loss"
    assert row["headline_direction"] == "min"


def test_insert_without_metric_leaves_nulls(tmp_path):
    db = tmp_path / "runs.sqlite"
    apply_campaigns_migration(db)
    campaign_insert(
        db, id="c2", lab_id="L", question="q", hypothesis_path="h.md",
        hypothesis_hash="sha256:" + "b" * 64,
    )
    row = _row(db, "c2")
    assert row["headline_metric"] is None
    assert row["headline_direction"] is None
    assert row["finding_kind"] is None


def test_explicit_finding_kind_is_persisted_without_reclassifying_old_campaigns(tmp_path):
    db = tmp_path / "runs.sqlite"
    apply_campaigns_migration(db)
    campaign_insert(
        db, id="c3", lab_id="L", question="Does it replicate?",
        hypothesis_path="h.md", hypothesis_hash="sha256:" + "c" * 64,
        finding_kind="verification",
    )
    assert _row(db, "c3")["finding_kind"] == "verification"
