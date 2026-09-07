"""LabConfig construction and defaults."""
from __future__ import annotations
import dataclasses
import shutil
from pathlib import Path

import pytest
import yaml

from efferents.lab import (
    Budget, Executor, Headline, LabConfig, Metrics, Panel, Source, SubmissionError,
)
from efferents import lab as lab_mod


def test_from_submission_happy_path(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    cfg = LabConfig.from_submission(sub)
    assert cfg.lab_id == "sample-conjecture"
    assert cfg.domain == "synthetic"
    assert cfg.source.dir.is_absolute()
    assert cfg.source.dir.name == "src"
    assert cfg.executor.run_command == "python -m sample.run --config {config_path}"
    assert cfg.metrics.headline.column == "synthetic_loss"
    assert cfg.metrics.headline.direction == "min"
    assert cfg.budget.daily_cap_usd == 10.0


def test_env_passthrough_rejects_daemon_credentials(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw.setdefault("executor", {})["env_passthrough"] = ["ANTHROPIC_API_KEY"]
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(SubmissionError, match="daemon credentials"):
        LabConfig.from_submission(sub)


def test_from_submission_missing_hypothesis(tmp_path):
    (tmp_path / "lab.yaml").write_text("lab_id: x\ndomain: y\n")
    with pytest.raises(SubmissionError, match="hypothesis.md"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_falsifiability_failed(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: failed\nstatus: unfalsifiable\n---\n\nbody"
    )
    (tmp_path / "lab.yaml").write_text("lab_id: x\ndomain: y\n")
    with pytest.raises(SubmissionError, match="falsifiability_gate"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_missing_lab_yaml(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    with pytest.raises(SubmissionError, match="lab.yaml"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_source_dir_missing(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./nonexistent/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
    )
    with pytest.raises(SubmissionError, match="source.dir"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_check_paths_false_skips_existence_checks(tmp_path):
    # Neither source.dir nor config_template exist on disk. With check_paths
    # disabled (read-only serve use), from_submission must still succeed —
    # the copied lab.yaml in an initialized lab/ dir has paths rooted at the
    # parent submission, not lab/.
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./nonexistent/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: missing.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
    )
    # Default (check_paths=True) still rejects the missing source.dir.
    with pytest.raises(SubmissionError, match="source.dir"):
        LabConfig.from_submission(tmp_path)
    # check_paths=False loads successfully.
    cfg = LabConfig.from_submission(tmp_path, check_paths=False)
    assert cfg.lab_id == "x"
    assert cfg.metrics.headline.column == "m"


def test_from_submission_run_command_missing_placeholder(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo no-placeholder'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
    )
    with pytest.raises(SubmissionError, match=r"\{config_path\}"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_bad_direction(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: maximum\n"
    )
    with pytest.raises(SubmissionError, match="direction"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_lab_id_defaults_to_hypothesis_slug(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: defaulted-id\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        # no lab_id
        "domain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
    )
    cfg = LabConfig.from_submission(tmp_path)
    assert cfg.lab_id == "defaulted-id"


def test_labconfig_construction_with_defaults():
    cfg = LabConfig(
        lab_id="test-lab",
        domain="test-domain",
        pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(
            run_command="python -m test --config {config_path}",
            smoke_command=None,
            config_template=Path("configs/default.yaml"),
        ),
        metrics=Metrics(
            headline=Headline(column="loss", direction="min"),
            panels=(Panel(column="loss", label="Loss"),),
        ),
        budget=Budget(),
    )
    assert cfg.lab_id == "test-lab"
    assert cfg.budget.daily_cap_usd == 10.0
    assert cfg.budget.sonnet_default is True
    assert cfg.metrics.flat_digest_epsilon == 0.005
    assert cfg.executor.run_timeout_s == 7200
    assert cfg.executor.smoke_timeout_s == 300
    assert cfg.executor.env_passthrough == ()
    assert cfg.source.allowed_patterns == ("**/*.py",)
    assert cfg.peer_review_enabled is False
    assert len(cfg.students) == 1
    assert cfg.students[0]["id"] == "primary"


def test_labconfig_frozen():
    cfg = LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(run_command="x {config_path}", smoke_command=None, config_template=Path("c.yaml")),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.lab_id = "different"  # type: ignore[misc]


def test_submission_error_is_value_error():
    assert issubclass(SubmissionError, ValueError)
    with pytest.raises(ValueError, match="bad submission"):
        raise SubmissionError("bad submission")


def test_headline_direction_max():
    h = Headline(column="accuracy", direction="max")
    assert h.direction == "max"
    cfg = LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(run_command="x {config_path}", smoke_command=None, config_template=Path("c.yaml")),
        metrics=Metrics(headline=h, panels=()),
        budget=Budget(),
    )
    assert cfg.metrics.headline.direction == "max"


def test_from_submission_config_template_missing(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    # NOTE: config_template path is declared but the file is NOT created.
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: configs/missing.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
    )
    with pytest.raises(SubmissionError, match="config_template"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_panel_missing_column(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: m\n    direction: min\n"
        "  panels:\n    - { label: 'Loss' }\n"  # no column key
    )
    with pytest.raises(SubmissionError, match=r"panels\[0\]"):
        LabConfig.from_submission(tmp_path)


def test_get_config_raises_before_set():
    from efferents import lab as lab_mod
    lab_mod._active = None  # ensure clean state
    with pytest.raises(RuntimeError, match="LabConfig not loaded"):
        lab_mod.get_config()


def test_set_get_round_trip(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    cfg = LabConfig.from_submission(sub)
    from efferents import lab as lab_mod
    lab_mod.set_config(cfg)
    assert lab_mod.get_config() is cfg
    lab_mod._active = None


def test_shim_exposes_lab_id_when_loaded(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    cfg = LabConfig.from_submission(sub)
    from efferents import lab as lab_mod
    lab_mod.set_config(cfg)
    assert lab_mod._labconfig_attr_via_shim("LAB_ID") == "sample-conjecture"
    assert lab_mod._labconfig_attr_via_shim("DOMAIN") == "synthetic"
    lab_mod._active = None


def test_shim_unknown_name_raises_attribute_error(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    cfg = LabConfig.from_submission(sub)
    from efferents import lab as lab_mod
    lab_mod.set_config(cfg)
    with pytest.raises(AttributeError):
        lab_mod._labconfig_attr_via_shim("BOGUS_NAME")
    lab_mod._active = None


def test_from_submission_bad_headline_column_name(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: 'bad name; drop table runs;--'\n    direction: min\n"
    )
    with pytest.raises(SubmissionError, match="column"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_bad_panel_column_name(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: loss\n    direction: min\n"
        "  panels:\n    - { column: '1bad', label: 'Bad' }\n"
    )
    with pytest.raises(SubmissionError, match="column"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_dot_in_column_name_rejected(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: 'foo.bar'\n    direction: min\n"
    )
    with pytest.raises(SubmissionError, match="column"):
        LabConfig.from_submission(tmp_path)


def test_from_submission_accepts_underscore_and_digits_after_first(tmp_path):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "c.yaml").touch()
    (tmp_path / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: synthetic_loss_2\n    direction: min\n"
        "  panels:\n    - { column: _internal, label: 'I' }\n"
    )
    cfg = LabConfig.from_submission(tmp_path)
    assert cfg.metrics.headline.column == "synthetic_loss_2"
    assert cfg.metrics.panels[0].column == "_internal"


def test_prompts_dir_set_when_directory_exists(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    (sub / "prompts").mkdir()
    cfg = LabConfig.from_submission(sub)
    assert cfg.prompts_dir == sub / "prompts"


def test_prompts_dir_none_when_absent(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    cfg = LabConfig.from_submission(sub)
    assert cfg.prompts_dir is None


def test_prompts_dir_defaults_none_on_direct_construction():
    cfg = LabConfig(
        lab_id="t", domain="d", pi_handle=None,
        source=Source(dir=Path("/tmp")),
        executor=Executor(run_command="x {config_path}", smoke_command=None, config_template=Path("c.yaml")),
        metrics=Metrics(headline=Headline(column="m", direction="min"), panels=()),
        budget=Budget(),
    )
    assert cfg.prompts_dir is None


def test_from_submission_loads_autonomy_students_and_review_thresholds(tmp_path):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    with (sub / "lab.yaml").open("a") as f:
        f.write(
            "\nautonomy:\n"
            "  coder_enabled: true\n"
            "peer_review:\n"
            "  enabled: true\n"
            "  gain_threshold: 0.12\n"
            "  accept_mean_threshold: 7.0\n"
            "  accept_min_threshold: 5\n"
            "students:\n"
            "  - id: explorer\n"
            "    focus: robustness\n"
            "default_student_id: explorer\n"
            "max_open_campaigns_per_student: 3\n"
        )

    cfg = LabConfig.from_submission(sub)
    lab_mod.set_config(cfg)

    assert cfg.autonomy.coder_enabled is True
    assert cfg.peer_review_gain_threshold == 0.12
    assert cfg.students[0]["id"] == "explorer"
    assert lab_mod.LAB_ID == "sample-conjecture"
    assert lab_mod.PEER_REVIEW_ENABLED is True
    assert lab_mod.PEER_REVIEW_GAIN_THRESHOLD == 0.12
    assert lab_mod.DEFAULT_STUDENT_ID == "explorer"


def test_from_submission_rejects_paths_outside_submission(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c.yaml").write_text("x: 1\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (sub / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ../outside\n"
        "executor:\n  run_command: 'echo {config_path}'\n  config_template: c.yaml\n"
        "metrics:\n  headline:\n    column: loss\n    direction: min\n"
    )

    with pytest.raises(SubmissionError, match="inside the submission"):
        LabConfig.from_submission(sub)


def test_from_submission_rejects_traversing_config_template(tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("x: 1\n")
    sub = tmp_path / "sub"
    (sub / "src").mkdir(parents=True)
    (sub / "hypothesis.md").write_text(
        "---\nslug: x\nfalsifiability_gate: passed\nstatus: active\n---\n\nbody"
    )
    (sub / "lab.yaml").write_text(
        "lab_id: x\ndomain: y\n"
        "source:\n  dir: ./src\n"
        "executor:\n  run_command: 'echo {config_path}'\n"
        "  config_template: ../../outside.yaml\n"
        "metrics:\n  headline:\n    column: loss\n    direction: min\n"
    )

    with pytest.raises(SubmissionError, match="inside the submission"):
        LabConfig.from_submission(sub)


# ---------------------------------------------------------------------------
# falsifiers: declarative rules over bucket-level aggregates
# ---------------------------------------------------------------------------

def _submission_with_falsifiers(tmp_path, falsifiers, bucket_axes=("raw_q",)):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw["metrics"]["bucket_axes"] = list(bucket_axes)
    raw["falsifiers"] = falsifiers
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    return sub


def test_falsifiers_parse_aggregate_and_paired_forms(tmp_path):
    sub = _submission_with_falsifiers(tmp_path, [
        {"id": "F1", "description": "no gain at raw_q=16",
         "when": {"column": "delta_loss", "agg": "median", "bucket": 16,
                  "op": ">=", "value": 0, "min_n": 4}},
        {"id": "F2", "description": "mostly worse",
         "when": {"column": "delta_loss", "agg": "frac_ge", "threshold": 0,
                  "op": ">", "value": 0.5}},
        {"id": "F3", "description": "paired CI includes zero",
         "when": {"column": "delta_loss", "ci95_excludes_zero": False, "bucket": "all"}},
        {"id": "F4", "description": "arm-derived paired CI includes zero",
         "when": {"metric": "synthetic_loss", "ci95_excludes_zero": False}},
    ])
    cfg = LabConfig.from_submission(sub)
    f1, f2, f3, f4 = cfg.falsifiers
    assert (f1.kind, f1.agg, f1.op, f1.value, f1.bucket, f1.min_n) == (
        "aggregate", "median", ">=", 0.0, 16, 4)
    assert (f2.threshold, f2.bucket, f2.min_n) == (0.0, "all", 3)
    assert (f3.kind, f3.ci95_excludes_zero, f3.agg, f3.metric) == ("paired", False, None, None)
    assert (f4.kind, f4.column, f4.metric) == ("paired", None, "synthetic_loss")


def test_falsifiers_default_empty(tmp_path):
    sub = _submission_with_falsifiers(tmp_path, None)
    assert LabConfig.from_submission(sub).falsifiers == ()


@pytest.mark.parametrize("falsifiers, match", [
    ({"id": "F1"}, "must be a list"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0}, "extra": 1}], "unknown keys"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0, "bogus": 1}}], "unknown keys"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "p95",
       "op": "<", "value": 0}}], "agg must be one of"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "~", "value": 0}}], "op must be one of"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<"}}], "value must be numeric"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "frac_ge",
       "op": "<", "value": 0}}], "threshold must be numeric"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0, "threshold": 1}}], "threshold only applies"),
    ([{"id": "F1", "description": "d", "when": {"column": "bad-col", "agg": "median",
       "op": "<", "value": 0}}], "column must match"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0, "min_n": 0}}], "min_n must be a positive integer"),
    ([{"id": "F1", "description": "d", "when": {"column": "x",
       "ci95_excludes_zero": "yes"}}], "ci95_excludes_zero must be a boolean"),
    ([{"id": "F1", "description": "d", "when": {"column": "x",
       "ci95_excludes_zero": True, "agg": "median"}}], "unknown keys"),
    ([{"id": "F1", "description": "d", "when": {"ci95_excludes_zero": True}}],
     "exactly one of column"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "metric": "y",
       "ci95_excludes_zero": True}}], "exactly one of column"),
    ([{"id": "F1", "description": "d", "when": {"metric": "y", "agg": "median",
       "op": "<", "value": 0}}], "unknown keys"),
    ([{"id": "F1", "description": "d", "when": {"metric": "bad-name",
       "ci95_excludes_zero": True}}], "metric must match"),
    ([{"id": "F1", "description": "", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0}}], "description must be a non-empty string"),
    ([{"id": "F1", "description": "d", "when": "median < 0"}], "when must be a mapping"),
    ([{"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0}},
      {"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
       "op": "<", "value": 0}}], "duplicate falsifier id"),
])
def test_falsifiers_reject_bad_rules(tmp_path, falsifiers, match):
    sub = _submission_with_falsifiers(tmp_path, falsifiers)
    with pytest.raises(SubmissionError, match=match):
        LabConfig.from_submission(sub)


def test_falsifier_named_bucket_requires_bucket_axes(tmp_path):
    sub = _submission_with_falsifiers(tmp_path, [
        {"id": "F1", "description": "d", "when": {"column": "x", "agg": "median",
         "bucket": 16, "op": "<", "value": 0}},
    ], bucket_axes=())
    with pytest.raises(SubmissionError, match="requires metrics.bucket_axes"):
        LabConfig.from_submission(sub)


# --- supersession frontmatter -------------------------------------------------

def _minimal_sub(tmp_path, frontmatter_extra=""):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    (sub / "hypothesis.md").write_text(
        "---\nslug: sample-conjecture\nfalsifiability_gate: passed\nstatus: active\n"
        f"{frontmatter_extra}---\n\nbody\n"
    )
    return sub


def test_hypothesis_slug_and_supersedes_exposed(tmp_path):
    sub = _minimal_sub(tmp_path, "supersedes: earlier-claim\n")
    cfg = LabConfig.from_submission(sub)
    assert cfg.hypothesis_slug == "sample-conjecture"
    assert cfg.hypothesis_supersedes == "earlier-claim"
    assert LabConfig.from_submission(_minimal_sub(tmp_path / "b")).hypothesis_supersedes is None


def test_superseded_hypothesis_is_rejected_with_pointer_to_successor(tmp_path):
    sub = _minimal_sub(tmp_path, "superseded_by: newer-claim\n")
    with pytest.raises(SubmissionError, match="retired.*superseded_by='newer-claim'"):
        LabConfig.from_submission(sub)


@pytest.mark.parametrize("extra", ["supersedes: ''\n", "superseded_by: [a, b]\n"])
def test_supersession_keys_must_be_slug_strings(tmp_path, extra):
    with pytest.raises(SubmissionError, match="must be a non-empty slug string"):
        LabConfig.from_submission(_minimal_sub(tmp_path, extra))


# --- budget.total_cap_usd ---------------------------------------------------------

def _with_budget(tmp_path, budget: dict):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw["budget"] = budget
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    return sub


def test_total_cap_usd_parsed_and_defaults_to_none(tmp_path):
    cfg = LabConfig.from_submission(_with_budget(tmp_path, {"daily_cap_usd": 5, "total_cap_usd": 250}))
    assert cfg.budget.total_cap_usd == 250.0
    assert cfg.budget.daily_cap_usd == 5.0
    assert Budget().total_cap_usd is None
    cfg2 = LabConfig.from_submission(_with_budget(tmp_path / "b", {"daily_cap_usd": 5}))
    assert cfg2.budget.total_cap_usd is None


def test_total_cap_usd_must_cover_daily_cap(tmp_path):
    with pytest.raises(SubmissionError, match="total_cap_usd must be at least"):
        LabConfig.from_submission(_with_budget(tmp_path, {"daily_cap_usd": 10, "total_cap_usd": 9.5}))
    # Equal to the daily cap is allowed (a one-day lab).
    cfg = LabConfig.from_submission(_with_budget(tmp_path / "b", {"daily_cap_usd": 10, "total_cap_usd": 10}))
    assert cfg.budget.total_cap_usd == 10.0


def test_total_cap_usd_must_be_numeric(tmp_path):
    with pytest.raises(SubmissionError, match="total_cap_usd must be numeric"):
        LabConfig.from_submission(_with_budget(tmp_path, {"total_cap_usd": "lots"}))


def _with_autonomy(tmp_path, autonomy: dict):
    src = Path(__file__).parent / "fixtures" / "sample_submission"
    sub = tmp_path / "sub"
    shutil.copytree(src, sub)
    raw = yaml.safe_load((sub / "lab.yaml").read_text())
    raw["autonomy"] = autonomy
    (sub / "lab.yaml").write_text(yaml.safe_dump(raw))
    return sub


def test_coder_mode_parsed_and_defaults_to_auto(tmp_path):
    cfg = LabConfig.from_submission(
        _with_autonomy(tmp_path, {"coder_enabled": True, "coder_mode": "review"})
    )
    assert cfg.autonomy.coder_mode == "review"
    assert cfg.autonomy.coder_enabled is True
    cfg2 = LabConfig.from_submission(_with_autonomy(tmp_path / "b", {"coder_enabled": True}))
    assert cfg2.autonomy.coder_mode == "auto"
    assert lab_mod.Autonomy().coder_mode == "auto"


@pytest.mark.parametrize("mode", ["manual", "", 1, None])
def test_coder_mode_rejects_unknown_values(tmp_path, mode):
    with pytest.raises(SubmissionError, match="autonomy.coder_mode must be one of auto | review"):
        LabConfig.from_submission(_with_autonomy(tmp_path, {"coder_mode": mode}))
