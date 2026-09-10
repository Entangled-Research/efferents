"""Analyst reads flat_digest_epsilon from LabConfig."""
from __future__ import annotations

from efferents.agents import analyst
from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Source,
)
from efferents import lab as lab_mod


def test_analyst_epsilon_reads_from_config(tmp_path):
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
            headline=Headline(column="m", direction="min"),
            panels=(),
            flat_digest_epsilon=0.02,  # custom
        ),
        budget=Budget(),
    )
    lab_mod.set_config(cfg)
    assert analyst._flat_digest_epsilon() == 0.02


def test_analyst_epsilon_defaults_to_005(tmp_path):
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
    assert analyst._flat_digest_epsilon() == 0.005
