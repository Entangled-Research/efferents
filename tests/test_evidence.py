"""Deterministic evidence aggregation: buckets, seed-paired deltas, falsifiers."""
from __future__ import annotations

import json
import math

from efferents import evidence as ev
from efferents.lab import (
    Budget, Evidence, Executor, Falsifier, Headline, LabConfig, Metrics, Panel, Source,
)


def make_cfg(tmp_path, *, falsifiers=(), bucket_axes=("raw_q",), axis="role"):
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    (src / "c.yaml").touch()
    return LabConfig(
        lab_id="x", domain="y", pi_handle=None,
        source=Source(dir=src),
        executor=Executor(run_command="echo {config_path}", smoke_command=None,
                          config_template=src / "c.yaml"),
        metrics=Metrics(headline=Headline(column="e_w1", direction="min"),
                        panels=(Panel(column="delta_e_w1", label="Delta"),),
                        bucket_axes=bucket_axes),
        budget=Budget(),
        evidence=Evidence(comparison_axis=axis, comparison_order=("quantum", "classical")),
        falsifiers=tuple(falsifiers),
    )


def make_rows(*, deltas_by_q: dict[int, list[float]], base: float = 1.0) -> list[dict]:
    """One run per (raw_q, seed); the quantum arm scores base + delta, classical base.
    The flat ``delta_e_w1`` column mirrors the per-run paired difference."""
    rows = []
    for q, deltas in deltas_by_q.items():
        for seed, d in enumerate(deltas):
            obs = [
                {"name": "quantum", "dimensions": {"role": "quantum", "seed": seed},
                 "metrics": {"e_w1": base + d}},
                {"name": "classical", "dimensions": {"role": "classical", "seed": seed},
                 "metrics": {"e_w1": base}},
            ]
            rows.append({
                "run_id": f"r-{q}-{seed}", "started_at": "2026-09-01", "status": "succeeded",
                "raw_q": q, "seed": seed, "e_w1": base + d, "delta_e_w1": d,
                "observations_json": json.dumps(obs),
            })
    return rows


def agg(id_, **when):
    when.setdefault("min_n", 3)
    return Falsifier(id=id_, description=id_, kind="aggregate", **when)


def paired(id_, **when):
    when.setdefault("min_n", 3)
    return Falsifier(id=id_, description=id_, kind="paired", **when)


# --- bucket_summary ---------------------------------------------------------

def test_bucket_summary_groups_by_axis_with_arms_and_paired_deltas(tmp_path):
    cfg = make_cfg(tmp_path)
    rows = make_rows(deltas_by_q={8: [0.1, 0.3, 0.2], 16: [-0.2, -0.1, -0.3, -0.4]})
    summary = ev.bucket_summary(rows, cfg)
    assert list(summary) == ["raw_q=8", "raw_q=16"]
    b16 = summary["raw_q=16"]
    assert b16["n"] == 4
    assert b16["columns"]["delta_e_w1"]["median"] == -0.25
    assert b16["columns"]["e_w1"]["n"] == 4
    assert "raw_q" not in b16["columns"] and "seed" not in b16["columns"]
    assert list(b16["arms"]) == ["quantum", "classical"]
    assert b16["arms"]["classical"]["e_w1"]["median"] == 1.0
    p = b16["paired"]["e_w1"]
    assert p["arms"] == ("quantum", "classical")
    assert p["n"] == 4 and math.isclose(p["median"], -0.25)
    assert p["ci95"] is not None and p["ci95"][1] < 0


def test_bucket_summary_tolerates_nan_none_and_bad_json(tmp_path):
    cfg = make_cfg(tmp_path)
    rows = make_rows(deltas_by_q={8: [0.1, 0.2]})
    rows[0]["e_w1"] = float("nan")
    rows[1]["delta_e_w1"] = None
    rows[0]["observations_json"] = "{not json"
    rows.append({"run_id": "r-x", "raw_q": 8, "seed": 9, "e_w1": "oops",
                 "observations_json": [{"dimensions": "bad", "metrics": None}, 42]})
    summary = ev.bucket_summary(rows, cfg)
    b = summary["raw_q=8"]
    assert b["n"] == 3
    assert b["columns"]["e_w1"]["n"] == 1
    assert b["columns"]["delta_e_w1"]["n"] == 1
    assert b["paired"]["e_w1"]["n"] == 1  # only the run with parseable observations


def test_bucket_summary_single_bucket_without_axes(tmp_path):
    cfg = make_cfg(tmp_path, bucket_axes=(), axis=None)
    rows = make_rows(deltas_by_q={8: [0.1], 16: [0.2]})
    summary = ev.bucket_summary(rows, cfg)
    assert list(summary) == ["all"]
    assert summary["all"]["n"] == 2 and summary["all"]["arms"] == {}


def test_paired_deltas_fall_back_to_run_seed_column(tmp_path):
    cfg = make_cfg(tmp_path)
    rows = make_rows(deltas_by_q={8: [0.5, 0.5, 0.5]})
    for r in rows:  # drop the seed dimension; pairing must use the row's seed
        obs = json.loads(r["observations_json"])
        for o in obs:
            del o["dimensions"]["seed"]
        r["observations_json"] = json.dumps(obs)
    assert ev.bucket_summary(rows, cfg)["raw_q=8"]["paired"]["e_w1"]["n"] == 3


# --- bootstrap ---------------------------------------------------------------

def test_bootstrap_ci_sign_and_determinism():
    neg = [-0.3, -0.25, -0.2, -0.35, -0.3, -0.28]
    lo, hi = ev.bootstrap_median_ci(neg)
    assert lo <= hi < 0
    assert ev.bootstrap_median_ci(neg) == (lo, hi)
    pos_lo, pos_hi = ev.bootstrap_median_ci([-v for v in neg])
    assert 0 < pos_lo <= pos_hi
    mixed_lo, mixed_hi = ev.bootstrap_median_ci([-0.3, 0.2, -0.1, 0.3, -0.2, 0.1])
    assert mixed_lo <= 0 <= mixed_hi
    assert ev.bootstrap_median_ci([1.0]) is None


# --- falsifiers --------------------------------------------------------------

def test_aggregate_rule_fires_survives_and_insufficient(tmp_path):
    cfg = make_cfg(tmp_path, falsifiers=[
        agg("fires", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=8),
        agg("survives", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=16),
        agg("thin", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=16, min_n=10),
        agg("absent", column="delta_e_w1", agg="median", op=">=", value=0.0, bucket=32),
        agg("missing_col", column="nope", agg="mean", op="<", value=1.0, bucket="any"),
    ])
    rows = make_rows(deltas_by_q={8: [0.1, 0.3, 0.2], 16: [-0.2, -0.1, -0.3, -0.4]})
    results = {r["id"]: r for r in ev.evaluate_falsifiers(rows, cfg)}
    assert results["fires"]["status"] == "fired"
    assert "raw_q=8: median(delta_e_w1)=0.2 >= 0? n=3" == results["fires"]["detail"]
    assert results["survives"]["status"] == "survived"
    assert results["thin"]["status"] == "insufficient_data"
    assert "n=4 < min_n=10" in results["thin"]["detail"]
    assert results["absent"]["status"] == "insufficient_data"
    assert results["missing_col"]["status"] == "insufficient_data"
    assert ev.verdict(list(results.values())) == "falsified"


def test_any_and_all_bucket_semantics(tmp_path):
    rows = make_rows(deltas_by_q={8: [0.1, 0.3, 0.2], 16: [-0.2, -0.1, -0.3, -0.4]})
    cfg = make_cfg(tmp_path, falsifiers=[
        agg("any", column="delta_e_w1", agg="min", op=">", value=0.0, bucket="any"),
        agg("all", column="delta_e_w1", agg="min", op=">", value=0.0, bucket="all"),
        agg("all_fire", column="delta_e_w1", agg="count", op=">=", value=3, bucket="all"),
        agg("frac", column="delta_e_w1", agg="frac_le", threshold=0.0, op="==", value=1.0,
            bucket=16),
    ])
    results = {r["id"]: r["status"] for r in ev.evaluate_falsifiers(rows, cfg)}
    assert results == {"any": "fired", "all": "survived", "all_fire": "fired", "frac": "fired"}

    # `all` with one bucket undecidable and the other firing stays undecided.
    thin = make_rows(deltas_by_q={8: [0.1, 0.3, 0.2], 16: [-0.2]})
    cfg2 = make_cfg(tmp_path, falsifiers=[
        agg("all", column="delta_e_w1", agg="count", op=">=", value=3, bucket="all"),
    ])
    assert ev.evaluate_falsifiers(thin, cfg2)[0]["status"] == "insufficient_data"


def test_paired_rule_uses_flat_column_or_derived_arm_deltas(tmp_path):
    rows = make_rows(deltas_by_q={16: [-0.2, -0.1, -0.3, -0.4, -0.25]})
    cfg = make_cfg(tmp_path, falsifiers=[
        paired("null_effect", column="delta_e_w1", ci95_excludes_zero=False, bucket=16),
        paired("derived", metric="e_w1", ci95_excludes_zero=True, bucket=16),
    ])
    results = {r["id"]: r for r in ev.evaluate_falsifiers(rows, cfg)}
    # A clear negative effect: CI excludes zero, so "effect is null" survives...
    assert results["null_effect"]["status"] == "survived"
    assert "excludes zero" in results["null_effect"]["detail"]
    # ...and the arm-derived e_w1 paired CI (quantum − classical per seed) excludes zero.
    assert results["derived"]["status"] == "fired"
    assert "median(\u0394e_w1)=-0.25" in results["derived"]["detail"]
    assert ev.verdict(list(results.values())) == "falsified"

    noisy = make_rows(deltas_by_q={16: [-0.3, 0.2, -0.1, 0.3, -0.2, 0.1]})
    r = ev.evaluate_falsifiers(noisy, cfg)
    assert [x["status"] for x in r] == ["fired", "survived"]


def test_paired_rule_insufficient_without_arms_or_column(tmp_path):
    cfg = make_cfg(tmp_path, axis=None, falsifiers=[
        paired("p", metric="e_w1", ci95_excludes_zero=True, bucket="all"),
    ])
    rows = [{"run_id": "r", "raw_q": 8, "e_w1": 1.0}]
    assert ev.evaluate_falsifiers(rows, cfg)[0]["status"] == "insufficient_data"


def test_verdict_rules(tmp_path):
    assert ev.verdict([]) == "undecided"
    assert ev.verdict([{"status": "survived"}, {"status": "survived"}]) == "survives"
    assert ev.verdict([{"status": "survived"}, {"status": "insufficient_data"}]) == "undecided"
    assert ev.verdict([{"status": "survived"}, {"status": "fired"}]) == "falsified"
