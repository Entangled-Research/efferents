# efferents intake compatibility entry

The canonical agent onboarding contract now lives at the repository root:

```text
https://raw.githubusercontent.com/Entangled-Research/efferents/main/intake.md
```

Fetch that file and follow it from the beginning. It replaces the older
submission-only flow that used to live here and adds:

- existing-repo, fresh-lab, and framework-contributor entry paths;
- inferred experiment contracts, with optional deeper Popper review;
- a bounded first run and dashboard review; and
- shared-goal and independent-lab participation, and the separate
  fail-closed `efferents public-check` release preflight.

Do not combine the old flow with the canonical one.

Do not ask the owner to choose a private or public lab. The old visibility
branch is retired; continue the research intake using the canonical contract.

## Evaluation validity before conclusions

Before starting, map every part of the claim to an implemented check. A numeric
proxy is not a formal proof, and a smoke test is not the full benchmark. Add
`metrics.constraints` for required coverage, sample counts, successful model
calls, source retrieval and proof compilation, using metrics actually emitted
by this executor. Constraints gate both ranking and scientific falsification.
`min_n` counts run rows (or paired observations), not benchmark cases inside a
run; enforce within-run coverage with a metric constraint.

Emit `evaluation_valid: 0` for incomplete or technically invalid evaluations and
`evaluation_valid: 1` only when the declared protocol completed. Preserve missing
measurements as null, not zero. Missing safety reporting is not evidence of harm.
Provider failures and failed self-tests must not become negative scientific
results. Verify one real bounded end-to-end execution and its saved measurements
before unattended operation. If it fails, repair in the same folder and retain
the failed attempt. Do not weaken the hypothesis to make the evaluator pass.
