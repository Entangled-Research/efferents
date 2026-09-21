"""Bounded example algorithms from docs/initial-example-labs-plan.md.

No networking, model calls or credentials. All source data are frozen locally.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import math
import random
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parents[1] / "data"


def coloring(seed, config):
    rng = random.Random(seed)
    n = int(config.get("vertices", 9))
    if not 4 <= n <= 11:
        raise ValueError("Exact instances require 4–11 vertices")
    density = [.25, .5, .75][seed % 3]
    edges = [(i, j) for i in range(n) for j in range(i) if rng.random() < density]
    adj = [set() for _ in range(n)]
    for i, j in edges:
        adj[i].add(j)
        adj[j].add(i)

    def greedy(adaptive):
        result = {}
        while len(result) < n:
            remaining = [v for v in range(n) if v not in result]
            v = max(remaining, key=lambda v: (len({result[w] for w in adj[v] if w in result}), len(adj[v]), -v)) if adaptive else remaining[0]
            used = {result[w] for w in adj[v] if w in result}
            result[v] = next(c for c in range(n) if c not in used)
        return result

    baseline, candidate = greedy(False), greedy(True)
    operations = 0
    def feasible(k, assigned):
        nonlocal operations
        operations += 1
        if operations > 200000:
            raise ValueError("Exact certificate budget exhausted; no optimality claim")
        if len(assigned) == n:
            return True
        v = max((v for v in range(n) if v not in assigned), key=lambda v: (len(adj[v]), -v))
        for c in range(min(k, 1 + max(assigned.values(), default=-1) + 1)):
            if all(assigned.get(w) != c for w in adj[v]):
                assigned[v] = c
                if feasible(k, assigned):
                    return True
                del assigned[v]
        return False
    optimum = next(k for k in range(1, n + 1) if feasible(k, {}))
    assert all(candidate[i] != candidate[j] for i, j in edges)
    b, c = max(baseline.values())+1, max(candidate.values())+1
    metrics = {"improvement": b-c, "baseline": b, "candidate": c, "reference": optimum,
               "gap_to_optimum": c-optimum, "operations": operations, "valid": 1}
    return metrics, {"seed": seed, "edges": edges, "coloring": candidate, "density": density,
                     "reference": "exact backtracking certificate", "split": "held-out seeds >= 0; tuning uses negative seed namespace"}, [[b, c, optimum]]


def orbit(seed, config):
    budget = int(config.get("force_budget", 400))
    if not 40 <= budget <= 10000:
        raise ValueError("force_budget must be 40–10000")
    horizon = 2 * math.pi * (1 + seed % 3)
    def acceleration(x, y):
        r = math.hypot(x, y)
        return -x/r**3, -y/r**3
    def solve(verlet):
        steps = budget-1 if verlet else budget
        dt = horizon/steps
        x, y, vx, vy = 1., 0., 0., 1.
        ax, ay = acceleration(x, y)
        trace, max_energy, max_momentum = [], 0., 0.
        for i in range(steps):
            if verlet:
                nx, ny = x+vx*dt+.5*ax*dt*dt, y+vy*dt+.5*ay*dt*dt
                nax, nay = acceleration(nx, ny)
                vx, vy = vx+.5*(ax+nax)*dt, vy+.5*(ay+nay)*dt
                x, y, ax, ay = nx, ny, nax, nay
            else:
                ax, ay = acceleration(x, y)
                x, y, vx, vy = x+vx*dt, y+vy*dt, vx+ax*dt, vy+ay*dt
            energy = .5*(vx*vx+vy*vy)-1/math.hypot(x,y)
            max_energy = max(max_energy, abs((energy+.5)/.5))
            max_momentum = max(max_momentum, abs(x*vy-y*vx-1))
            trace.append([x,y])
        error = math.hypot(x-math.cos(horizon), y-math.sin(horizon))
        return error, max_energy, max_momentum, trace
    b, be, bm, bt = solve(False)
    c, ce, cm, ct = solve(True)
    return {"improvement": b-c, "baseline": b, "candidate": c, "reference": 0.,
            "energy_drift": ce, "momentum_drift": cm, "force_evaluations": budget, "valid": 1}, {
                "model": "ideal normalized circular two-body orbit, not planetary observations",
                "horizon": horizon, "baseline_energy_drift": be, "baseline_momentum_drift": bm,
                "equal_force_budget": budget, "baseline_trajectory": bt, "candidate_trajectory": ct}, [[p[1] for p in bt[::max(1,len(bt)//80)]], [p[1] for p in ct[::max(1,len(ct)//80)]]]


def verify_data():
    manifest = json.loads((DATA / "manifest.json").read_text())
    for item in manifest["sources"]:
        if hashlib.sha256((DATA / item["file"]).read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("Pinned dataset hash mismatch: " + item["file"])
    return manifest


def active_learning(seed, config):
    import numpy as np
    verify_data()
    data = np.loadtxt(DATA / "banknote.csv", delimiter=",")
    # A fixed test split is frozen before selection; pool preprocessing never sees test data.
    indices = np.random.default_rng(20260916).permutation(len(data))
    test, pool = indices[:274], indices[274:]
    mean, scale = data[pool,:4].mean(axis=0), data[pool,:4].std(axis=0)
    x = np.column_stack(((data[:,:4]-mean)/scale, np.ones(len(data))))
    y = data[:,4]
    rng = np.random.default_rng(seed)
    initial = rng.permutation(pool)[:12].tolist()
    budgets = [12, 24, 48, 96]
    def model(labeled):
        # Fixed ridge least-squares classifier; only label selection changes.
        xx = x[labeled]
        return np.linalg.solve(xx.T @ xx + np.eye(5)*.1, xx.T @ (y[labeled]*2-1))
    def arm(uncertain):
        labeled = list(initial)
        curve, queried = [], []
        arm_rng = np.random.default_rng(seed)
        for budget in budgets:
            while len(labeled) < budget:
                candidates = np.array(sorted(set(pool)-set(labeled)))
                weights = model(labeled)
                selected = candidates[np.argsort(np.abs(x[candidates] @ weights))[:min(12,budget-len(labeled))]] if uncertain else arm_rng.choice(candidates, min(12,budget-len(labeled)), replace=False)
                # Labels are only accessed by model() after selection.
                labeled.extend(selected.tolist())
            curve.append(float(np.mean((x[test] @ model(labeled) >= 0) == y[test])))
            queried.append(list(labeled))
        return curve, queried
    b, bq = arm(False)
    c, cq = arm(True)
    assert not set(test).intersection(v for batch in bq+cq for v in batch)
    def auc(values):
        area = sum((values[i]+values[i-1])*.5*(budgets[i]-budgets[i-1]) for i in range(1,len(budgets)))
        return area/(budgets[-1]-budgets[0])
    return {"improvement": auc(c)-auc(b), "baseline": auc(b), "candidate": auc(c),
            "reference": 1., "labels_used": 96, "valid": 1}, {
                "dataset": "UCI Banknote Authentication", "split_seed": 20260916, "test_indices": test.tolist(),
                "label_budgets": budgets, "baseline_accuracy": b, "candidate_accuracy": c,
                "baseline_queries": bq, "candidate_queries": cq,
                "classifier": "ridge least-squares, lambda=.1; threshold zero"}, [b,c]


def vehicle(seed, config):
    verify_data()
    def read(part):
        with (DATA / f"acc-part{part}.csv").open() as handle:
            return [{k:float(v) for k,v in row.items()} for row in csv.DictReader(handle)]
    train = read(1)
    test_part = (4,6)[seed % 2]
    held_out = read(test_part)
    def simulate(rows, headway):
        speed, gap = rows[0]["follower_speed"], rows[0]["gap"]
        speeds, gaps, accelerations = [speed], [gap], []
        for previous, row in zip(rows, rows[1:]):
            dt = row["time"]-previous["time"]
            desired = 8 + headway*speed
            accel = max(-3., min(2., .2*(gap-desired)+.6*(previous["lead_speed"]-speed)))
            next_speed = max(0., speed+accel*dt)
            gap += .5*(previous["lead_speed"]+row["lead_speed"]-speed-next_speed)*dt
            speed = next_speed
            speeds.append(speed)
            gaps.append(gap)
            accelerations.append(accel)
        gap_rmse = math.sqrt(sum((p-r["gap"])**2 for p,r in zip(gaps,rows))/len(rows))
        speed_rmse = math.sqrt(sum((p-r["follower_speed"])**2 for p,r in zip(speeds,rows))/len(rows))
        return gap_rmse, speed_rmse, gaps, speeds, accelerations
    # Choose from five fixed values on a separate experiment part, never test rows.
    headway = min((.6, .9, 1.2, 1.5, 1.8), key=lambda h: simulate(train,h)[0])
    b = simulate(held_out,0)
    c = simulate(held_out,headway)
    valid = min(c[2]) > 0
    metrics = {"improvement": b[0]-c[0], "baseline": b[0], "candidate": c[0], "reference": 0.,
               "speed_rmse": c[1], "minimum_gap": min(c[2]), "valid": int(valid),
               "peak_acceleration": max(abs(a) for a in c[4]),
               "peak_jerk": max(abs(a-b)/.1 for a,b in zip(c[4],c[4][1:]))}
    return metrics, {"train_part":1, "held_out_part":test_part, "headway":headway,
                     "unique_held_out_episodes":2, "episode_repeated":seed>=2,
                     "interpretation":"Observed-behavior fit only. No counterfactual safety claim; missing ACC status and GNSS noise remain caveats.",
                     "observed_gap":[r["gap"] for r in held_out], "predicted_gap":c[2],
                     "observed_speed":[r["follower_speed"] for r in held_out], "predicted_speed":c[3]}, [[r["gap"] for r in held_out], b[2], c[2]]


def run(config, output):
    seed = config.get("seed", 0)
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    kind = config["experiment"]
    metrics, provenance, curves = {"coloring":coloring, "orbit":orbit,
                                  "active-learning":active_learning, "vehicle":vehicle}[kind](seed,config)
    output.mkdir(parents=True,exist_ok=True)
    stem = f"{kind}-{seed}"
    record = output / (stem+".json")
    record.write_text(json.dumps({"seed":seed,"config":config,"metrics":metrics,"provenance":provenance},indent=2)+"\n")
    lo, hi = min(min(c) for c in curves), max(max(c) for c in curves)
    colors = ["#8d8474", "#2d5379", "#211d15"]
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 290"><rect width="600" height="290" fill="white"/>']
    for i,curve in enumerate(curves):
        points = " ".join(f"{40+520*j/max(1,len(curve)-1):.2f},{200-150*(v-lo)/max(1e-12,hi-lo):.2f}" for j,v in enumerate(curve))
        svg.append(f'<polyline points="{points}" fill="none" stroke="{colors[i%3]}" stroke-width="2"/>')
    svg.append(f'<text x="40" y="235" font-family="monospace">{html.escape(kind)} · seed {seed}</text>')
    svg.append(f'<text x="40" y="258" font-family="monospace">baseline {metrics["baseline"]:.5g} · candidate {metrics["candidate"]:.5g}</text></svg>')
    picture = output/(stem+".svg")
    picture.write_text("".join(svg))
    return {"metrics":metrics,"artifacts":[{"kind":kind,"path":str(picture.resolve())},{"kind":"provenance","path":str(record.resolve())}],
            "observations":[{"name":kind,"dimensions":{"seed":seed,"experiment":kind},"metrics":metrics}]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    path = args.config.resolve()
    print(json.dumps(run(yaml.safe_load(path.read_text()),path.parent.parent/"artifacts")))
