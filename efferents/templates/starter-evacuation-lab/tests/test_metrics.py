from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from run_experiment import metrics_for  # noqa: E402
from simulate import paired_run  # noqa: E402


class MetricTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text())

    def test_improvement_formula_uses_paired_medians(self):
        _layout, baseline, candidate = paired_run(self.config)
        metrics = metrics_for(baseline, candidate)
        expected = (baseline.median_steps - candidate.median_steps) / baseline.median_steps * 100
        self.assertAlmostEqual(metrics["evacuation_improvement_pct"], expected, places=5)

    def test_broken_candidate_fails_completion_constraint(self):
        broken = copy.deepcopy(self.config)
        broken["candidate"]["policy"] = "blocked"
        _layout, _baseline, candidate = paired_run(broken)
        self.assertLess(candidate.completion_rate, 0.95)


if __name__ == "__main__":
    unittest.main()
