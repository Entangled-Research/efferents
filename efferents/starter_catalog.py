"""The four example directions selected in docs/initial-example-labs-plan.md."""
DOCUMENTED = {
    "coloring": {"domain": "mathematics", "approach": "dsatur-vs-greedy",
        "title": "Graph coloring", "claim": "DSATUR uses no more colors than fixed-order greedy on the frozen seeded small-graph evaluation.",
        "measurement": "Compare valid color counts to the exact chromatic number; retain search operations and every failure.",
        "stop": "Stop after the bounded seed batch; at most 11 vertices and 200000 exact states. A negative median improvement or invalid coloring fails this claim."},
    "active-learning": {"domain": "machine-learning", "approach": "uncertainty-vs-random",
        "title": "Learn with fewer labels", "claim": "Uncertainty selection matches or improves label-normalized held-out learning-curve area over random selection with the same fixed classifier.",
        "measurement": "UCI Banknote; fixed test split 20260916; paired initial labels and budgets 12,24,48,96. No test labels enter fitting or selection.",
        "stop": "Stop at 96 labels and the bounded seed batch. Negative median AUC difference fails the claim; no test-set tuning."},
    "orbit": {"domain": "physics", "approach": "verlet-vs-euler",
        "title": "Keep a planet in orbit", "claim": "Velocity Verlet has no greater final circular-orbit position error than explicit Euler at the same force-evaluation budget.",
        "measurement": "Analytic circular position, relative energy and angular momentum drift; 400 force evaluations per arm.",
        "stop": "Stop at the configured horizon (one to three ideal orbits). Negative median error reduction fails the claim. Ideal simulation only."},
    "vehicle": {"domain": "vehicle-simulation", "approach": "time-headway-vs-constant-gap",
        "title": "Autonomous vehicle following", "claim": "A time-headway controller calibrated on JRC part1 predicts held-out spacing at least as accurately as a constant-gap controller on the frozen parts 4 and 6.",
        "measurement": "Held-out gap and follower-speed RMSE, minimum gap, acceleration and jerk. Two unique episodes; repeated seeds are not independent observations.",
        "stop": "Stop after the two held-out episodes; negative median gap-error reduction or nonpositive simulated gap fails the claim. No counterfactual safety conclusion."},
}
