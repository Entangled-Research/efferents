import sqlite3
from pathlib import Path

from efferents.agents.writer import (
    GateInputs,
    _best_metric,
    _resolve_campaign_metric,
    should_publish,
)


def test_best_metric_min():
    rows = [{"loss": 0.5}, {"loss": 0.2}, {"loss": None}, {}]
    assert _best_metric(rows, "loss", "min") == 0.2


def test_best_metric_max():
    rows = [{"acc": 0.5}, {"acc": 0.9}, {"acc": None}]
    assert _best_metric(rows, "acc", "max") == 0.9


def test_best_metric_absent_column_returns_none():
    rows = [{"other": 1.0}, {}]
    assert _best_metric(rows, "loss", "min") is None


def test_resolve_campaign_metric_prefers_campaign():
    campaign = {"headline_metric": "synthetic_loss", "headline_direction": "min"}
    assert _resolve_campaign_metric(campaign, default=("e_w1", "min")) == ("synthetic_loss", "min")


def test_resolve_campaign_metric_falls_back_when_null():
    campaign = {"headline_metric": None, "headline_direction": None}
    assert _resolve_campaign_metric(campaign, default=("e_w1", "min")) == ("e_w1", "min")


def _seed_runs(db: Path, campaign_id: str, metric: str, vals: list[float]):
    conn = sqlite3.connect(db)
    conn.execute(
        f"CREATE TABLE runs (run_id TEXT, started_at TEXT, campaign_id TEXT, "
        f"seed INTEGER, {metric} REAL)"
    )
    for i, v in enumerate(vals):
        conn.execute(
            "INSERT INTO runs (run_id, started_at, campaign_id, seed, " + metric + ") "
            "VALUES (?, ?, ?, ?, ?)",
            (f"r{i}", "2026-01-01T00:00:00+00:00", campaign_id, 0, v),
        )
    conn.commit()
    conn.close()


def test_best_metric_reads_campaign_runs(tmp_path):
    db = tmp_path / "runs.sqlite"
    _seed_runs(db, "c1", "synthetic_loss", [0.4, 0.1, 0.3])
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM runs WHERE campaign_id='c1'")]
    conn.close()
    assert _best_metric(rows, "synthetic_loss", "min") == 0.1


def test_should_publish_max_direction_accepts_improvement():
    inp = GateInputs(primary_metric_name="acc", baseline_value=0.70,
                     candidate_value=0.90, novelty_claim="higher acc",
                     direction="max")
    ok, reason = should_publish(inp, gain_threshold=0.05)
    assert ok, reason


def test_should_publish_min_direction_unchanged():
    inp = GateInputs(primary_metric_name="loss", baseline_value=0.50,
                     candidate_value=0.40, novelty_claim="lower loss",
                     direction="min")
    ok, _ = should_publish(inp, gain_threshold=0.05)
    assert ok


def test_resolve_campaign_metric_invalid_direction_falls_back():
    from efferents.agents.writer import _resolve_campaign_metric
    campaign = {"headline_metric": "acc", "headline_direction": "ascending"}
    assert _resolve_campaign_metric(campaign, default=("e_w1", "min")) == ("acc", "min")


def test_write_phase_a_paper_requires_measured_baseline(tmp_path):
    from efferents import lab as lab_mod
    from efferents.agents.writer import write_phase_a_paper, writer_paths
    from efferents.lab import Budget, Executor, Headline, LabConfig, Metrics, Source

    source = tmp_path / "src"
    source.mkdir()
    config = source / "config.yaml"
    config.write_text("x: 1\n")
    lab_mod.set_config(
        LabConfig(
            lab_id="baseline-test",
            domain="test",
            pi_handle=None,
            source=Source(dir=source),
            executor=Executor(
                run_command="echo {config_path}",
                smoke_command=None,
                config_template=config,
            ),
            metrics=Metrics(
                headline=Headline(column="loss", direction="min"),
                panels=(),
            ),
            budget=Budget(),
        )
    )
    lab_dir = tmp_path / "lab"
    lab_dir.mkdir()
    db = lab_dir / "runs.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT, started_at TEXT, campaign_id TEXT, "
        "status TEXT, seed INTEGER, loss REAL)"
    )
    conn.execute(
        "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
        ("r1", "2026-01-01T00:00:00+00:00", "c1", "succeeded", 0, 0.1),
    )
    conn.commit()
    conn.close()
    paths = writer_paths(
        lab=lab_dir,
        paper=lab_dir / "paper",
        reports=lab_dir / "reports",
        context=tmp_path / "context",
    )

    class NoCalls:
        @property
        def messages(self):
            raise AssertionError("writer must not call the model without a baseline")

    result = write_phase_a_paper(
        paths,
        {
            "id": "c1",
            "question": "candidate improves loss",
            "hypothesis_path": "popper-corpus/c1/hypothesis.md",
            "hypothesis_hash": "sha256:" + "0" * 64,
            "headline_metric": "loss",
            "headline_direction": "min",
        },
        NoCalls(),
    )

    assert result is None
    assert "No successful baseline run" in paths.notebook.read_text()
