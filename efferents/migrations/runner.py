"""Idempotent migration applier for Phase A campaign schema.

SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so we
PRAGMA table_info first and only ALTER when missing.

The migration DDL is inlined (rather than a separate .sql file) so the
runner survives flit packaging, which by default ships only .py files.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS campaigns (
    id              TEXT PRIMARY KEY,
    lab_id          TEXT NOT NULL,
    question        TEXT NOT NULL,
    hypothesis_path TEXT NOT NULL,
    hypothesis_hash TEXT NOT NULL,
    opened_at       TEXT NOT NULL,
    closed_at       TEXT,
    close_reason    TEXT
);

CREATE INDEX IF NOT EXISTS idx_campaigns_lab_open
    ON campaigns(lab_id) WHERE closed_at IS NULL;
"""

_NEW_RUN_COLUMNS = (
    ("campaign_id", "TEXT"),
    ("researcher_mode", "TEXT"),
    # Phase B: each run is attributed to a student. Default backfills to
    # 'primary' so the existing 600+ rows show up under the original
    # student id without a separate data migration.
    ("student_id", "TEXT DEFAULT 'primary'"),
    ("status", "TEXT DEFAULT 'succeeded'"),
    ("exit_code", "INTEGER"),
    ("error", "TEXT"),
    ("stdout_path", "TEXT"),
    ("stderr_path", "TEXT"),
    ("config_yaml", "TEXT"),
    ("config_hash", "TEXT"),
    ("artifacts_json", "TEXT"),
    ("raw_metrics_json", "TEXT"),
    ("observations_json", "TEXT"),
    ("seed", "INTEGER"),
)

# Idempotent ALTERs for the campaigns table. SQLite can't conditionally add
# a column in DDL, so we PRAGMA first and ALTER only when missing.
_NEW_CAMPAIGN_COLUMNS = (
    ("student_id", "TEXT DEFAULT 'primary'"),
    # v0.1.3: the agent-proposed headline metric for this campaign. Null →
    # consumers fall back to LabConfig.metrics.headline.
    ("headline_metric", "TEXT"),
    ("headline_direction", "TEXT"),
    # Explicit finding route. Existing campaigns remain ordinary improvements.
    ("finding_kind", "TEXT"),
)


def apply_campaigns_migration(db_path: str | Path) -> None:
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    try:
        # executescript() implicitly COMMITs the script's own transaction.
        # The ALTER TABLE loop runs in a fresh transaction finalized below.
        conn.executescript(_MIGRATION_DDL)

        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "runs" in tables:
            existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
            for name, sqltype in _NEW_RUN_COLUMNS:
                if name not in existing:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {sqltype}")
        # campaigns table is always created by the DDL above; just add any
        # missing columns.
        existing = {row[1] for row in conn.execute("PRAGMA table_info(campaigns)")}
        for name, sqltype in _NEW_CAMPAIGN_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE campaigns ADD COLUMN {name} {sqltype}")
        conn.commit()
    finally:
        conn.close()


_RUNS_BASE_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    config_path TEXT,
    campaign_id TEXT,
    researcher_mode TEXT,
    student_id TEXT DEFAULT 'primary',
    status TEXT DEFAULT 'succeeded',
    exit_code INTEGER,
    error TEXT,
    stdout_path TEXT,
    stderr_path TEXT,
    config_yaml TEXT,
    config_hash TEXT,
    artifacts_json TEXT,
    raw_metrics_json TEXT,
    observations_json TEXT,
    seed INTEGER,
    git_commit TEXT,
    duration_seconds REAL
);
"""


def ensure_runs_table(db_path, cfg) -> None:
    """Create the runs table if absent; add REAL columns for any LabConfig
    metric not already present. Idempotent.

    db_path: str | Path. cfg: LabConfig.
    """
    db_path = Path(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_RUNS_BASE_DDL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        metric_cols = {cfg.metrics.headline.column,
                       *(p.column for p in cfg.metrics.panels)}
        if cfg.metrics.headline.comparator_column:
            metric_cols.add(cfg.metrics.headline.comparator_column)
        for col in sorted(metric_cols - existing):
            conn.execute(f"ALTER TABLE runs ADD COLUMN {col} REAL")
        conn.commit()
    finally:
        conn.close()
