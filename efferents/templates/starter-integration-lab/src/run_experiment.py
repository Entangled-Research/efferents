"""Compare quadrature rules against analytic ground truth, without model calls."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import yaml


def integrate(values: list[float], method: str) -> float:
    n = len(values) - 1
    if n < 2 or n % 2:
        raise ValueError("an even number of at least two intervals is required")
    if method == "trapezoid":
        return (sum(values) - (values[0] + values[-1]) / 2) / n
    if method != "simpson":
        raise ValueError("candidate must be simpson or trapezoid")
    return (values[0] + values[-1] + 4 * sum(values[1:-1:2])
            + 2 * sum(values[2:-1:2])) / (3 * n)


def run(config: dict, artifact_root: Path) -> dict:
    n = config.get("intervals", 20)
    seed = config.get("seed", 0)
    if type(n) is not int or n < 2 or n > 10000 or n % 2:
        raise ValueError("intervals must be an even integer between 2 and 10000")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    cases = [("x³", lambda x: x**3, 0.25),
             ("sin(x)", math.sin, 1 - math.cos(1)),
             ("exp(x)", math.exp, math.e - 1)]
    name, function, exact = cases[seed % len(cases)]
    values = [function(i / n) for i in range(n + 1)]
    baseline = integrate(values, "trapezoid")
    candidate = integrate(values, config.get("candidate", "simpson"))
    baseline_error, candidate_error = abs(baseline - exact), abs(candidate - exact)
    artifact_root.mkdir(parents=True, exist_ok=True)
    path = artifact_root / f"quadrature-{seed}.svg"
    scale = max(values) or 1
    points = " ".join(f"{40 + 520 * i / n:.3f},{200 - 160 * y / scale:.3f}"
                      for i, y in enumerate(values))
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 270">'
        '<rect width="600" height="270" fill="white"/>'
        '<path d="M40 30V200H560" fill="none" stroke="#172334"/>'
        f'<polyline points="{points}" fill="none" stroke="#172334" stroke-width="2"/>'
        f'<text x="40" y="235" font-family="monospace" fill="#172334">{name} · {n + 1} evaluations</text>'
        f'<text x="40" y="255" font-family="monospace" fill="#172334">absolute error: {candidate_error:.8g}</text></svg>'
    )
    return {"metrics": {"candidate_error": candidate_error, "baseline_error": baseline_error,
                        "improvement_pct": 100 * (1 - candidate_error / baseline_error) if baseline_error else 0,
                        "evaluations": n + 1},
            "artifacts": [{"kind": "quadrature", "path": str(path.resolve())}],
            "observations": [{"name": name, "dimensions": {"function": name, "seed": seed},
                              "metrics": {"exact": exact, "candidate": candidate, "baseline": baseline}}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config_path = args.config.resolve()
    print(json.dumps(run(yaml.safe_load(config_path.read_text()), config_path.parent.parent / "artifacts")))
