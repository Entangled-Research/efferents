# Congestion-aware evacuation starter lab

This CPU-only lab compares the same seeded building and evacuee positions under
two routing policies: static shortest path and a policy that periodically
replans with occupied and reserved cells as added path cost. It uses synthetic
scenarios and makes no claim about real-world evacuation safety.

The starter question is whether the candidate improves median evacuation time
by at least 10% across 12 paired seeds while preserving at least 95% completion.
Choose a distinct `approach` in `lab.yaml`, then change configuration values or
review a Coder-proposed patch. Do not treat this simulator as safety guidance.

## Local check

```bash
efferents validate --submission .
python3 -m unittest discover -s tests -v
python3 src/run_experiment.py --config configs/default.yaml
efferents start --submission . --dry-run --max-iterations 1
efferents serve --lab-root lab
```

The last JSON object printed by `run_experiment.py` is the framework result.
The SVG replay and JSON summary are written under `artifacts/`; an initialized
lab copies them into its immutable per-run evidence directory.

## Research directions

- Tune how local occupancy changes route cost.
- Decide when an evacuee abandons its current route.
- Balance exits without prescribing one assignment rule.
- Test staggered departures.
- Add controlled route diversity.
- Model corridor capacity in the search cost.
- Test blocked exits or noisy occupancy observations.

The default Coder mode is `review`: proposed source edits become patches for the
owner to inspect. Configuration-only experiments are the beginner path.
