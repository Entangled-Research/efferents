---
slug: congestion-aware-evacuation
created: 2026-09-15
status: active
falsifiability_gate: passed
literature_pass: none
---

# Congestion-aware routing for synthetic evacuation

## Original framing

> Locally avoiding congestion should make a synthetic evacuation faster than
> assigning every agent a static shortest path.

## Background

Static shortest paths can send many simulated agents through the same narrow
cells. This experiment uses only procedurally generated grid buildings; it is a
research exercise, not a model of or recommendation for real evacuation.

## Claim

A local congestion-aware routing policy reduces median simulated evacuation
steps by at least 10% relative to static shortest-path routing across at least
12 paired scenario seeds, while both policies evacuate at least 95% of agents
before the configured time limit.

## Operational restatement

For each seed, generate one layout and one set of starting positions, run both
policies on those identical inputs, and record their completion rates and
median exit steps. Across 12 eligible paired runs, the median of
`evacuation_improvement_pct` must be at least 10 and every candidate completion
rate must be at least 0.95.

## Falsifier(s)

- After at least 12 eligible paired seeds, the median
  `evacuation_improvement_pct` is less than 10.
- After at least 12 paired seeds, any candidate completion rate is below 0.95.

## Test design

The simulator is deterministic for a configuration and seed. Primary and
supporting metrics, validity gates, minimum sample size, policy parameters, and
the shared synthetic scope are declared in `lab.yaml` and
`configs/default.yaml`. Results do not generalize to physical crowds.

Each eligible run generates one synthetic layout and one set of starting
positions, then runs static and congestion-aware policies against those same
inputs. The aggregate decision is made only after 12 eligible seeds.

## Auxiliary assumptions

- The grid generator and paired starts expose both policies to equivalent
  route geometry and demand.
- Simultaneous movement and tie-breaking are deterministic and do not favor
  the candidate policy.
- Evacuation steps are an adequate performance measure for this synthetic
  comparison; no safety claim about real crowds is implied.

## Distinctiveness

The claim predicts an improvement from local congestion penalties, not merely
from a different random layout or easier starting positions. It forbids both a
sub-10% median improvement after 12 eligible pairs and any candidate completion
rate below 95%.

## References

No literature sources are claimed for this synthetic starter; the literature
pass is explicitly `none`.

## Intake log

2026-09-15 — Narrowed the starter from a broad evacuation claim to a paired,
deterministic simulation claim. Added a numerical improvement threshold,
minimum sample size, completion constraint, and explicit non-generalization to
physical crowds. The first result is not pre-approved: the falsifiers remain
binding even when the candidate performs worse.
