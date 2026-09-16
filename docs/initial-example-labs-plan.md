# Initial example labs

**Status:** selected directions; implementation pending
**Date:** 2026-09-16

## Goal

Build four small, independent example labs that demonstrate Efferents across
mathematics, machine learning, physics, and empirically grounded simulation.
Each should produce a real comparison and inspectable evidence on an ordinary
laptop. The examples belong to their own lab repositories or example
directories; domain algorithms, datasets, metrics, and prompts do not become
framework defaults.

These are proposed examples, not claims that the labs or their research results
already exist. The existing evacuation starter is separate from this set.

## Shared example contract

- One YAML config describes one run. The executor emits a machine-readable
  result, metric provenance, and a visual or tabular artifact.
- Give participants a working baseline and one bounded change to investigate.
  Keep a held-out evaluation set or exact reference that the change cannot
  silently redefine.
- Record seeds, input or dataset version, code revision, parameters, runtime,
  and excluded cases. Preserve negative and inconclusive results.
- Keep core experiments CPU-only and deterministic for fixed inputs. Prepare
  any external data before an event; running an experiment must not depend on a
  live download, GPU, model call, or credential.
- Put each hypothesis through the external Popper Probe and commit its validated
  corpus copy with the lab. Do not vendor Popper Probe.
- Bound search, training, and simulation time. Aim for a first local result in
  under 30 seconds on a typical laptop; measure this in a clean rehearsal.
- Use the research-console dashboard and standard evidence/provenance views.
  Do not add a separate visual language for examples.

## 1. Mathematics: graph coloring at the hard edge

**Question:** Under a fixed search budget, when does a bounded coloring
heuristic improve on greedy coloring, and how close is it to the minimum number
of colors?

- **Input:** versioned, seeded graph families with several sizes and edge
  densities. Freeze train/tuning and held-out graph seeds before comparing
  approaches.
- **Baseline:** deterministic greedy coloring with a documented vertex order.
- **First approaches:** DSATUR ordering and bounded local recoloring. Count
  explored states or candidate moves as well as wall time.
- **Ground truth:** use an exact backtracking solver on small graphs to establish
  the chromatic number. For larger graphs, report valid color count and bounds;
  do not label a heuristic result "optimal" without a certificate.
- **Metrics:** colors used, validity, gap to exact optimum where available,
  search operations, and runtime. Invalid colorings are failed runs.
- **Artifact:** a colored graph beside a plot of quality versus search budget.
- **Research extension:** characterize failure regions across graph families
  instead of reporting only an average win.

The first implementation should keep exact instances small enough for a
reliable laptop run and cap every heuristic explicitly.

## 2. Machine learning: learn with fewer labels

**Question:** Does uncertainty sampling reach useful classification accuracy
with fewer labels than random sampling?

- **Data:** the [UCI Banknote Authentication dataset][banknote] (1,372 rows,
  four numeric features; CC BY 4.0). Pin the downloaded bytes, attribution,
  feature schema, and split seeds in the example.
- **Baseline:** a fixed classifier trained with randomly selected labels.
- **Candidate:** the same classifier and preprocessing, with labels selected by
  uncertainty. Only selection strategy may differ in the first comparison.
- **Protocol:** reserve one test set before any tuning. Start each paired run
  from the same labeled set; hide pool labels until selected. Evaluate at fixed
  label budgets across multiple seeds.
- **Metrics:** held-out accuracy versus labels used, area under the learning
  curve, runtime, and variation across seeds. A failed or negligible gain is a
  valid finding.
- **Artifact:** paired learning curves with individual seed traces or uncertainty
  bands.
- **Research extension:** test whether a strategy tuned on one split survives
  a deliberately different feature distribution, with that shift defined in
  advance.

This is an active-learning experiment, not a model leaderboard. Data leakage
checks are part of the starter.

## 3. Physics: keep a planet in orbit

**Question:** Which numerical integrator preserves an ideal two-body orbit
longest for the same number of force evaluations?

- **Model:** a normalized Newtonian two-body system with fixed initial
  conditions and time span.
- **Baseline:** explicit Euler.
- **First approaches:** velocity Verlet and fourth-order Runge-Kutta. Compare
  equal force-evaluation budgets rather than equal step counts alone.
- **Ground truth:** analytic position for a circular orbit, plus conserved
  energy and angular momentum. Use a high-precision numerical solution only as
  a labeled reference for cases without a simple analytic trajectory.
- **Metrics:** position error, relative energy and angular-momentum drift,
  completed orbits, force evaluations, and runtime.
- **Artifact:** overlaid trajectories and conservation-error plots.
- **Research extension:** test eccentric initial conditions and long horizons;
  identify where a locally accurate method accumulates unacceptable drift.

The model is intentionally idealized. Do not describe its trajectories as
measured planetary data.

## 4. Grounded simulation: autonomous vehicle following

**Question:** Can a simple adaptive-cruise-control simulator predict a
following vehicle's speed and spacing during unseen lead-vehicle braking?

- **Observed data:** select a small, clean set of real car-following recordings
  from the European Commission Joint Research Centre's [Open ACC Database][openacc].
  Its low-speed campaign documents time, speed, and inter-vehicle spacing at
  10 Hz, along with missing values and acquisition caveats [in its notes][acc-notes].
- **Simulator input:** replay the observed lead vehicle's trajectory. Simulate
  the follower from its observed initial state, using a bounded acceleration
  policy and explicit integration step.
- **Baseline:** a simple constant-gap controller. **First candidate:** a
  time-headway controller with a small, documented parameter search.
- **Validation:** fit or tune on designated trips, then freeze parameters and
  predict different trips. Split by trip or experiment, not by adjacent time
  rows from the same trajectory. Record every omitted interval and why.
- **Metrics:** held-out follower speed and gap error, minimum simulated gap,
  acceleration and jerk, plus the number of valid episodes. A collision or
  physically implausible output fails validity even if average error is low.
- **Artifact:** observed-versus-simulated speed and gap timelines, with braking
  episodes highlighted.
- **Research extension:** test transfer across vehicles or distance settings,
  and show where a controller calibrated on one condition breaks down.

The recordings are ground truth for *observed behavior*. They cannot validate
what would have happened under a different controller. Any intervention result
must be labeled as a simulation. Before packaging data, check the source's
reuse notice, preserve attribution, and either bundle a permitted frozen subset
or provide a pinned preparation script. Do not require attendees to fetch the
full database during the event.

## Implementation sequence

1. **Data feasibility first:** inspect Open ACC files, choose valid episodes,
   verify reuse conditions, and produce a small reproducible fixture. This is
   the largest external dependency.
2. **Build the math and physics labs:** both have self-contained generators or
   analytic checks and establish the reusable example packaging pattern.
3. **Build the ML lab:** pin data and splits, then add leakage checks and paired
   learning curves.
4. **Finish the vehicle simulator:** add closed-loop replay, held-out validation,
   data-quality accounting, and observed-versus-simulated artifacts.
5. **Rehearse all four:** from a clean `uv` environment, validate each lab,
   run its bounded baseline and candidate, inspect the dashboard and evidence,
   and record time to first result. Put only rehearsed examples in the event
   participant menu.

## Acceptance checks for each lab

- The baseline and candidate receive the same eligible inputs or paired seeds.
- Identical config, seed, data, and revision produce equivalent metrics and
  artifacts; validity failures and exclusions remain visible.
- The result answers the lab's stated question using a held-out observation,
  analytic truth, or exact certificate as applicable.
- One bounded run completes within the measured laptop target; a failed
  hypothesis still yields a useful evidence memo.
- No example implies that a simulated safety improvement is proven in real
  traffic, or that a numerical reference is a physical measurement.

[banknote]: https://archive.ics.uci.edu/dataset/267/banknote%2Bauthentication
[openacc]: https://data.jrc.ec.europa.eu/dataset/9702c950-c80f-4d2f-982f-44d06ea0009f
[acc-notes]: https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/TransportExpData/JRCDBT0001/LATEST/JRC%20low%20speed/JRC_tests_notes.txt
