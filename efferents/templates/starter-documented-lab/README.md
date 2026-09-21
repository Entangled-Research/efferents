# Documented starter experiments

These CPU-only examples implement the bounded first comparisons from
`docs/initial-example-labs-plan.md`: graph coloring, fewer-label learning,
ideal orbital dynamics and observed vehicle following. The framework copies
this lab-owned executor into an independent submission, with its experiment
and claim selected at creation. No runtime downloads or API keys are needed.

Run `efferents trial --submission . --runs 3`. Inspect the JSON artifacts for
complete metrics, settings, split indices, query histories and trajectories.
The SVG gives a compact comparison, not a substitute for the numeric ledger.
Changing a reference or data split changes the research contract.

- Coloring: greedy vs DSATUR on 9 vertices; exact bounded backtracking; no
  optimality claim for larger instances. Independent held-out seeds.
- ML: fixed ridge classifier; random vs uncertainty selection; 12/24/48/96
  label budgets; fixed 274-row test split, preprocessing from pool only.
- Physics: Euler vs velocity Verlet, 400 force evaluations each; analytic
  circular orbit and conservation checks. This is not observed planetary data.
- Vehicles: constant-gap vs time-headway control. Five headways tuned on
  JRC part1 only; evaluated on part4/part6. Only two unique held-out episodes:
  repeating seeds must not be counted as new empirical evidence. These are
  separate experiment parts of the same campaign, not independent campaigns.
  GNSS noise and unavailable driver status limit interpretation. Predicted
  trajectories are simulations, not counterfactual safety evidence.

## Data provenance and attribution

Frozen bytes and subset hashes are in `data/manifest.json`. Both sources are
CC BY 4.0, https://creativecommons.org/licenses/by/4.0/ .

Lohweg, V. (2012). Banknote Authentication. UCI Machine Learning Repository.
https://doi.org/10.24432/C55P57 . Original unmodified CSV; four numeric features
plus binary class. Source: https://archive.ics.uci.edu/dataset/267/banknote+authentication .

European Commission, Joint Research Centre (2026). Open ACC Database.
https://doi.org/10.2905/JRC.KMH3D00 . Copyright European Union, 1995–2026.
Subsets select the first continuous valid 20-second interval from three
experiment parts, retaining time, lead speed, first follower speed and spacing.
Omissions and original hashes are recorded. Source notes describe 10Hz
interpolation, unfiltered GNSS measurements and acquisition gaps:
https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/TransportExpData/JRCDBT0001/LATEST/JRC%20low%20speed/JRC_tests_notes.txt .

Reprepare only as a maintainer with `scripts/prepare_documented_starter_data.py`.
Do not silently refresh data for an event.
