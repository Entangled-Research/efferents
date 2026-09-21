"""Run one paired seed and emit the Efferents stdout-JSON contract."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import yaml

from render import render_svg
from simulate import paired_run, validate_config


def metrics_for(baseline, candidate) -> dict[str, float | int]:
    improvement = (
        0.0 if baseline.median_steps <= 0
        else (baseline.median_steps - candidate.median_steps) / baseline.median_steps * 100.0
    )
    return {
        "evacuation_improvement_pct": round(improvement, 6),
        "baseline_median_steps": baseline.median_steps,
        "candidate_median_steps": candidate.median_steps,
        "baseline_p95_steps": baseline.p95_steps,
        "candidate_p95_steps": candidate.p95_steps,
        "baseline_completion_rate": round(baseline.completion_rate, 6),
        "candidate_completion_rate": round(candidate.completion_rate, 6),
        "baseline_congestion_waits": baseline.congestion_waits,
        "candidate_congestion_waits": candidate.congestion_waits,
        "candidate_reroute_count": candidate.reroute_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    config_path = Path(args.config).resolve()
    config = yaml.safe_load(config_path.read_text()) or {}
    validate_config(config)
    if args.smoke:
        config = dict(config)
        config["layout"] = {**config["layout"], "agents": min(12, config["layout"]["agents"])}
        config["simulation"] = {**config["simulation"], "max_steps": min(60, config["simulation"]["max_steps"])}
    layout, baseline, candidate = paired_run(config)
    metrics = metrics_for(baseline, candidate)
    artifact_root = config_path.parent.parent / "artifacts"
    stem = f"seed-{config['seed']}-{layout.layout_hash[:12]}"
    svg_path = artifact_root / f"{stem}.svg"
    summary_path = artifact_root / f"{stem}.json"
    render_svg(layout, baseline, candidate, svg_path)
    summary = {
        "protocol": "efferents-evacuation/v1",
        "seed": config["seed"],
        "layout_hash": layout.layout_hash,
        "candidate": config["candidate"],
        "metrics": metrics,
    }
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    observations = []
    for result in (baseline, candidate):
        policy = "congestion" if result.policy == "congestion" else result.policy
        observations.append({
            "name": f"{policy}-seed-{config['seed']}",
            "dimensions": {
                "policy": policy,
                "seed": config["seed"],
                "layout_hash": layout.layout_hash,
            },
            "metrics": {
                "median_steps": result.median_steps,
                "p95_steps": result.p95_steps,
                "completion_rate": round(result.completion_rate, 6),
                "congestion_waits": result.congestion_waits,
            },
            "artifacts": [{"kind": "evacuation_replay", "path": str(svg_path)}],
        })
    result = {
        "run_id": stem,
        "metrics": metrics,
        "observations": observations,
        "artifacts": [
            {"kind": "evacuation_replay", "path": str(svg_path)},
            {"kind": "evacuation_summary", "path": str(summary_path)},
        ],
        "elapsed_s": round(time.monotonic() - started, 6),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
