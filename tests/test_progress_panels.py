"""progress._panel_metrics() reads panels from LabConfig.metrics.panels."""
from __future__ import annotations

from efferents.agents import progress
from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Panel, Source,
)
from efferents import lab as lab_mod


def test_panel_metrics_from_config(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "c.yaml").touch()
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(
            run_command="echo {config_path}", smoke_command=None,
            config_template=src / "c.yaml",
        ),
        metrics=Metrics(
            headline=Headline(column="accuracy", direction="max"),
            panels=(
                Panel(column="accuracy", label="Acc", target=0.95),
                Panel(column="loss", label="Loss", target=None),
            ),
        ),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    panels = progress._panel_metrics()
    assert panels == [
        ("accuracy", "Acc", 0.95),
        ("loss", "Loss", None),
    ]


def test_panel_metrics_empty_when_no_panels(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "c.yaml").touch()
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(
            run_command="echo {config_path}", smoke_command=None,
            config_template=src / "c.yaml",
        ),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    assert progress._panel_metrics() == []


def test_headline_metric_returns_column_and_direction(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "c.yaml").touch()
    cfg = LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(
            run_command="echo {config_path}", smoke_command=None,
            config_template=src / "c.yaml",
        ),
        metrics=Metrics(
            headline=Headline(column="accuracy", direction="max"),
            panels=(),
        ),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    col, direction = progress._headline_metric()
    assert col == "accuracy"
    assert direction == "max"


def test_smoke_lab_config_panels_present(smoke_lab_config):
    """Under the smoke_lab_config the panels list has the synthetic_loss panel."""
    panels = progress._panel_metrics()
    cols = [p[0] for p in panels]
    assert "synthetic_loss" in cols


def test_smoke_lab_config_headline_is_synthetic_loss(smoke_lab_config):
    """Under the smoke_lab_config the headline metric is synthetic_loss/min."""
    col, direction = progress._headline_metric()
    assert col == "synthetic_loss"
    assert direction == "min"
