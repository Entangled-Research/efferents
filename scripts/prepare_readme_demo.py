"""Prepare an isolated example network for README recordings (no model calls).

Run with uv run python scripts/prepare_readme_demo.py /tmp/efferents-readme-demo.
Then serve with EFFERENTS_HOME=<destination>/home uv run efferents serve.
Measurements come from real bundled CPU starters. Review/publication records are
explicitly illustrative fixtures, never participant submissions or claimed live
agent reviews. Capture captions must retain that distinction.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def prepare(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    os.environ['EFFERENTS_HOME'] = str(destination / 'home')
    from efferents.onboarding import create_lab
    from efferents.lab import LabConfig
    from efferents.agents.conference import attend

    cases = [('numerical-methods', 'integration'), ('graph-algorithms', 'coloring'),
             ('orbital-dynamics', 'orbit')]
    for name, starter in cases:
        submission = destination / name
        create_lab(submission, starter=starter, name=name, exchange=True)
        subprocess.run([sys.executable, '-m', 'efferents', 'trial', '--submission',
                        str(submission), '--runs', '3'], check=True, capture_output=True)

    incoming = destination / 'error-bounds'
    create_lab(incoming, starter='integration', name='error-bounds',
               idea='Numerical integration error bounds', exchange=True)
    subprocess.run([sys.executable, '-m', 'efferents', 'route', str(incoming),
                    '--offline', '--apply', '--student-id', 'error-bounds'],
                   check=True, capture_output=True)

    source = destination / 'numerical-methods'
    paper = source / 'paper'
    paper.mkdir(exist_ok=True)
    campaign = 'bounded-integration'
    # Hand-authored demonstration records: explicitly labeled, no fabricated
    # model calls, billing, new measurements or reproduction claims.
    (paper / f'{campaign}.md').write_text('''# A bounded integration comparison

**Illustrative publication record · bundled starter measurements.**
This demo shows how research moves between labs; its review scores are fixtures.

## Claim and evidence
Composite Simpson integration is compared with trapezoid integration on three
analytic functions. The local run ledger preserves each measured error and SVG.
This is a bounded numerical example, not a general accuracy claim.

## Methods for another lab to test
Use a known analytic baseline, match evaluation budgets, and record numerical
error for every case. An orbital-dynamics lab can read this methodology through
its occasional Mathematics & Computation journal subscription.

## Provenance and replication
A subscription receipt means the paper was received. It is not agreement,
experimental use or independent reproduction. Each receiving lab must perform
its own experiment before treating a result as a foundation.
''')
    (paper / 'journal.md').write_text(
        f'## 2026-09-25 10:00 UTC — {campaign}\n**Lab**: numerical-methods\n'
        '**Headline**: A bounded integration comparison\n'
        '**Scores**: critical=6, neutral=7, optimistic=7 (mean=6.67)\n'
        '**Demo**: Illustrative publication and review record, not a live review.\n')
    (paper / f'{campaign}.reviews.md').write_text(
        '**Verdict**: **ACCEPT**\n'
        '**Scores**: critical=6, neutral=7, optimistic=7 (mean=6.67)\n'
        'Illustrative review-board record for the README example network.\n')
    # Exercise actual subscription cadence and receipt persistence, including
    # an interdisciplinary read on visit five. No direct lab-to-lab messaging.
    for name, _ in cases:
        submission = destination / name
        cfg = LabConfig.from_submission(submission)
        for visit in range(5):
            attend(cfg=cfg, lab_root=submission / 'lab', now=time.time() + (visit + 1) * 601)
    (destination / 'DEMO.json').write_text(json.dumps({
        'purpose': 'README recording', 'measurements': 'real bundled starter trials',
        'reviews': 'illustrative fixtures', 'participant_data': False,
        'model_calls': 0, 'labs': [name for name, _ in cases],
    }, indent=2) + '\n')
    print(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    prepare(parser.parse_args().destination.resolve())
