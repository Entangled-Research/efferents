"""Per-run notebook entries and the digest's recent-runs table stay compact:
headline + panel columns + constraint verdict, with the rest counted."""
from __future__ import annotations

from efferents import lab as lab_mod
from efferents.agents import executor, notebook
from efferents.agents.analyst import _format_recent_runs
from efferents.exec import RunResult
from efferents.lab import (
    Budget, Constraint, Executor, Headline, LabConfig, Metrics, Panel, Source,
)


def _sixty_metrics(headline: float = 0.42) -> dict:
    metrics = {"synthetic_loss": headline}
    metrics.update({f"metric_{i:02d}": float(i) for i in range(59)})
    return metrics


def _table_columns(text: str) -> list[str]:
    header = next(line for line in text.splitlines() if line.startswith("| "))
    return [c.strip() for c in header.strip().strip("|").split("|")]


def _cfg_with_constraint(tmp_path):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "c.yaml").touch()
    return LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(run_command="echo {config_path}", smoke_command=None,
                          config_template=src / "c.yaml"),
        metrics=Metrics(
            headline=Headline(column="synthetic_loss", direction="min"),
            panels=(Panel(column="metric_01", label="Aux"), Panel(column="metric_02", label="Aux2")),
            constraints=(Constraint(column="metric_03", op=">=", value=10.0, label="fidelity"),),
        ),
        budget=Budget(),
    )


def test_run_entry_with_sixty_columns_is_compact():
    result = RunResult(ok=True, metrics=_sixty_metrics())
    entry = notebook.format_run_entry(
        name="trial-1", hypothesis="h", expected="e", overrides={"a": 1},
        result=result, duration=1.5, started="2026-09-07T00:00:00",
    )
    assert entry.startswith("## 2026-09-07T00:00:00 — trial-1")
    for line in ("**Hypothesis**: h", "**Expected**: e", "**Overrides**: `{'a': 1}`", "**Duration**: 1.5s"):
        assert line in entry
    cols = _table_columns(entry)
    assert len(cols) <= 12
    assert cols == ["synthetic_loss"]
    assert "| 0.42 |" in entry
    assert "+59 more metrics in ledger" in entry
    assert "metric_42" not in entry
    assert "**Constraints**: n/a (none configured)" in entry


def test_run_entry_reports_constraint_failures(tmp_path):
    lab_mod.set_config(_cfg_with_constraint(tmp_path))
    metrics = _sixty_metrics()
    good = notebook.render_metrics_summary(metrics)
    assert _table_columns(good) == ["synthetic_loss", "metric_01", "metric_02"]
    assert "**Constraints**: FAIL — fidelity: 3 (requires >= 10)" in good
    assert "+57 more metrics in ledger" in good
    metrics["metric_03"] = 11.0
    assert "**Constraints**: pass" in notebook.render_metrics_summary(metrics)


def test_failed_run_entry_shows_error_tail():
    result = RunResult(ok=False, metrics=None, error="boom", stderr="x" * 2000)
    entry = notebook.format_run_entry(
        name="f", hypothesis="h", expected="e", overrides={}, result=result,
        duration=0.1, started="t",
    )
    assert "**Error**: boom" in entry and "```" in entry
    assert "x" * 1024 in entry and "x" * 1025 not in entry


def test_orchestrator_installs_compact_renderer_on_executor():
    import efferents.agents.orchestrator  # noqa: F401 - import performs the swap
    assert executor._format_outcome is notebook.format_run_entry


def test_recent_runs_table_uses_same_compact_set(tmp_path):
    lab_mod.set_config(_cfg_with_constraint(tmp_path))
    row = {"run_id": "run-1", "started_at": "2026-09-07", "campaign_id": "c1",
           "researcher_mode": "refine", "duration_seconds": 3.0, "status": "succeeded",
           "config_yaml": "a: 1", **_sixty_metrics()}
    table = _format_recent_runs([row], tmp_path / "missing.sqlite")
    cols = _table_columns(table)
    assert len(cols) <= 12
    assert cols == ["run_id", "started_at", "campaign_id", "researcher_mode",
                    "duration_seconds", "synthetic_loss", "metric_01", "metric_02", "constraints"]
    assert "| FAIL — fidelity: 3 (requires >= 10) |" in table
    assert "+57 more metric columns in the ledger" in table
    assert "metric_42" not in table
    assert _format_recent_runs([], tmp_path / "missing.sqlite") == "(no runs yet)"
