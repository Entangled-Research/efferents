from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from render import render_svg  # noqa: E402
from simulate import generate_layout, paired_run, validate_config  # noqa: E402


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.config = yaml.safe_load((ROOT / "configs" / "default.yaml").read_text())

    def test_same_seed_is_byte_stable(self):
        first = paired_run(copy.deepcopy(self.config))
        second = paired_run(copy.deepcopy(self.config))
        self.assertEqual(first, second)
        left = ROOT / "artifacts" / "test-left.svg"
        right = ROOT / "artifacts" / "test-right.svg"
        render_svg(*first, left)
        render_svg(*second, right)
        self.assertEqual(left.read_bytes(), right.read_bytes())
        left.unlink()
        right.unlink()

    def test_paired_policies_share_layout_and_starts(self):
        layout, baseline, candidate = paired_run(self.config)
        self.assertEqual(len(layout.starts), self.config["layout"]["agents"])
        self.assertEqual(len(baseline.trajectories), len(candidate.trajectories))
        self.assertEqual(
            tuple(path[0] for path in baseline.trajectories),
            tuple(path[0] for path in candidate.trajectories),
        )
        self.assertEqual(
            layout.layout_hash,
            generate_layout(copy.deepcopy(self.config)).layout_hash,
        )

    def test_invalid_config_is_rejected(self):
        bad = copy.deepcopy(self.config)
        bad["candidate"]["reroute_interval"] = 0
        with self.assertRaisesRegex(ValueError, "reroute_interval"):
            validate_config(bad)


if __name__ == "__main__":
    unittest.main()
