"""Lab-agnostic view over a run set.

Single source of truth for "what a run's columns mean": which is the headline
metric, which are configured panels, which are other (auto-discovered) columns,
and direction-aware best/improvement. Everything derives from the active
LabConfig.metrics plus the runs schema, so no consumer hardcodes domain column
names.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path

from efferents import lab as _lab

META_COLUMNS = (
    "run_id", "started_at", "ended_at", "config_path",
    "campaign_id", "researcher_mode", "student_id",
    "git_commit", "duration_seconds",
)


def finite(x) -> float | None:
    """Return x as a float iff it is a finite real number, else None.
    bool is excluded; NaN/inf and non-numeric values return None."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if math.isfinite(x) else None


def discover_columns(db_path, *, meta: tuple[str, ...] = META_COLUMNS) -> list[str]:
    """Non-meta columns present in the runs table (a lab's params + metrics).
    Missing db or missing table -> []."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    conn = sqlite3.connect(db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(runs)")]
    except sqlite3.OperationalError:
        return []  # DB-level error (corruption/lock); safety net, not the missing-table case
    finally:
        conn.close()
    if not cols:  # no `runs` table -> PRAGMA yields no rows
        return []
    return [c for c in cols if c not in meta]


def headline():
    """The active lab's headline metric (column + direction)."""
    return _lab.get_config().metrics.headline


def panels():
    """The active lab's configured metric panels."""
    return _lab.get_config().metrics.panels


def headline_value(row: dict) -> float | None:
    """The finite headline-metric value of a run row, or None."""
    return finite(row.get(headline().column))


def constraint_failures(row: dict, *, cfg=None) -> list[str]:
    """Return human-readable failures for configured ranking constraints."""
    cfg = cfg or _lab.get_config()
    failures: list[str] = []
    if row.get("status") not in (None, "succeeded"):
        failures.append("Execution did not succeed")
    if "exit_code" in row and finite(row["exit_code"]) != 0:
        failures.append("Executor exit code is not zero")
    if "evaluation_valid" in row and finite(row["evaluation_valid"]) != 1:
        failures.append("Evaluation incomplete or invalid")
    operators = {
        "<": lambda actual, wanted: actual < wanted,
        "<=": lambda actual, wanted: actual <= wanted,
        ">": lambda actual, wanted: actual > wanted,
        ">=": lambda actual, wanted: actual >= wanted,
        "==": lambda actual, wanted: actual == wanted,
    }
    for constraint in cfg.metrics.constraints:
        actual = finite(row.get(constraint.column))
        if actual is None or not operators[constraint.op](actual, constraint.value):
            label = constraint.label or constraint.column
            rendered = "missing" if actual is None else f"{actual:g}"
            failures.append(
                f"{label}: {rendered} (requires {constraint.op} {constraint.value:g})"
            )
    return failures


def eligible(row: dict, *, cfg=None) -> bool:
    """Whether a run may participate in best-run selection."""
    return not constraint_failures(row, cfg=cfg)


def best_run(rows: list[dict], *, cfg=None) -> dict | None:
    """Best row by the headline column + direction, skipping rows whose headline
    value isn't finite. None if no scored rows."""
    cfg = cfg or _lab.get_config()
    h = cfg.metrics.headline
    scored = [
        r for r in rows
        if finite(r.get(h.column)) is not None and eligible(r, cfg=cfg)
    ]
    if not scored:
        return None
    chooser = min if h.direction == "min" else max
    return chooser(scored, key=lambda r: finite(r.get(h.column)))


def best_run_from_db(db_path: Path, *, cfg=None) -> dict | None:
    """Return the best eligible persisted run without truncating history."""
    db_path = Path(db_path)
    if not db_path.exists():
        return None
    cfg = cfg or _lab.get_config()
    headline_col = cfg.metrics.headline.column
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
        required = {headline_col, *(c.column for c in cfg.metrics.constraints)}
        if not required.issubset(columns):
            return None
        clauses = [f"typeof({headline_col}) IN ('integer', 'real')"]
        params: list[float] = []
        if "status" in columns:
            clauses.append("status = 'succeeded'")
        for constraint in cfg.metrics.constraints:
            clauses.append(
                f"typeof({constraint.column}) IN ('integer', 'real') "
                f"AND {constraint.column} {constraint.op} ?"
            )
            params.append(constraint.value)
        order = "ASC" if cfg.metrics.headline.direction == "min" else "DESC"
        row = conn.execute(
            f"SELECT * FROM runs WHERE {' AND '.join(clauses)} "
            f"ORDER BY {headline_col} {order} LIMIT 1",
            params,
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    return dict(row) if row is not None else None


def improved(prev: float | None, current: float | None, *,
             direction: str, epsilon: float) -> bool:
    """True iff `current` improves on `prev` by more than epsilon in `direction`
    ('min' -> decrease, 'max' -> increase). prev None -> True when current is not
    None (first measurement counts as improvement)."""
    if current is None:
        return False
    if prev is None:
        return True
    return (prev - current) > epsilon if direction == "min" else (current - prev) > epsilon
