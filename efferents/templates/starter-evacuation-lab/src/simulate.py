"""Deterministic paired synthetic evacuation simulation."""
from __future__ import annotations

import hashlib
import json
import random
import statistics
from dataclasses import dataclass
from math import ceil

from policies import congestion_paths, static_paths

Cell = tuple[int, int]


@dataclass(frozen=True)
class Layout:
    width: int
    height: int
    walls: frozenset[Cell]
    exits: tuple[Cell, ...]
    starts: tuple[Cell, ...]
    layout_hash: str


@dataclass(frozen=True)
class Result:
    policy: str
    completion_rate: float
    median_steps: float
    p95_steps: float
    congestion_waits: int
    reroute_count: int
    exit_steps: tuple[int, ...]
    trajectories: tuple[tuple[Cell, ...], ...]


def validate_config(config: dict) -> None:
    layout = config.get("layout") or {}
    simulation = config.get("simulation") or {}
    candidate = config.get("candidate") or {}
    for key, minimum in (("width", 15), ("height", 11), ("agents", 2)):
        value = layout.get(key)
        if type(value) is not int or value < minimum:
            raise ValueError(f"layout.{key} must be an integer >= {minimum}")
    if layout["width"] > 61 or layout["height"] > 41 or layout["agents"] > 300:
        raise ValueError("layout exceeds the starter lab's bounded size")
    if type(config.get("seed")) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an integer in [0, 2**32)")
    if type(simulation.get("max_steps")) is not int or not 1 <= simulation["max_steps"] <= 1000:
        raise ValueError("simulation.max_steps must be an integer in [1, 1000]")
    if candidate.get("policy") not in {"congestion", "blocked"}:
        raise ValueError("candidate.policy must be congestion or blocked")
    weight = candidate.get("congestion_weight")
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not 0 <= weight <= 50:
        raise ValueError("candidate.congestion_weight must be in [0, 50]")
    for key in ("reroute_interval", "reservation_depth"):
        value = candidate.get(key)
        if type(value) is not int or not 1 <= value <= 100:
            raise ValueError(f"candidate.{key} must be an integer in [1, 100]")


def generate_layout(config: dict) -> Layout:
    validate_config(config)
    raw = config["layout"]
    width, height = raw["width"], raw["height"]
    rng = random.Random(config["seed"])
    left_exit = (0, height // 3)
    right_exit = (width - 1, (height * 2) // 3)
    exits = (left_exit, right_exit)
    walls = {
        (x, y)
        for x in range(width)
        for y in range(height)
        if x in {0, width - 1} or y in {0, height - 1}
    }
    walls.difference_update(exits)
    # Paired offset dividers create narrow, seeded corridor choices while
    # retaining at least two routes between the occupied room and both exits.
    obstacle_pairs = max(0, min(int(raw.get("obstacle_pairs", 3)), 6))
    for index in range(obstacle_pairs):
        x = 4 + index * max(2, (width - 8) // max(obstacle_pairs, 1))
        if x >= width - 3:
            break
        gaps = {2 + rng.randrange(max(1, height // 3)), height - 3 - rng.randrange(max(1, height // 3))}
        for y in range(2, height - 2):
            if y not in gaps:
                walls.add((x, y))
    candidates = [
        (x, y)
        for x in range(2, max(3, width // 2 + 2))
        for y in range(2, height - 2)
        if (x, y) not in walls
    ]
    rng.shuffle(candidates)
    if len(candidates) < raw["agents"]:
        raise ValueError("layout has too few free start cells for layout.agents")
    starts = tuple(sorted(candidates[:raw["agents"]]))
    serial = json.dumps(
        {"width": width, "height": height, "walls": sorted(walls), "exits": exits, "starts": starts},
        separators=(",", ":"), sort_keys=True,
    )
    return Layout(
        width=width, height=height, walls=frozenset(walls), exits=exits,
        starts=starts, layout_hash=hashlib.sha256(serial.encode()).hexdigest(),
    )


def percentile95(values: list[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, ceil(0.95 * len(ordered)) - 1)])


def run_policy(layout: Layout, config: dict, policy: str) -> Result:
    max_steps = config["simulation"]["max_steps"]
    candidate = config["candidate"]
    positions = {index: cell for index, cell in enumerate(layout.starts)}
    histories = {index: [cell] for index, cell in positions.items()}
    exit_steps: dict[int, int] = {}
    waits = 0
    reroutes = 0
    paths = static_paths(layout, positions)

    for step in range(1, max_steps + 1):
        if not positions:
            break
        if policy == "blocked":
            waits += len(positions)
            for agent_id, cell in positions.items():
                histories[agent_id].append(cell)
            continue
        if policy == "congestion" and (step == 1 or (step - 1) % candidate["reroute_interval"] == 0):
            paths = congestion_paths(
                layout, positions,
                congestion_weight=float(candidate["congestion_weight"]),
                reservation_depth=int(candidate["reservation_depth"]),
            )
            reroutes += len(positions)
        occupied = set(positions.values())
        proposals: dict[Cell, list[int]] = {}
        for agent_id, cell in sorted(positions.items()):
            path = paths.get(agent_id, [cell])
            try:
                index = path.index(cell)
            except ValueError:
                index = 0
            nxt = path[index + 1] if index + 1 < len(path) else cell
            proposals.setdefault(nxt, []).append(agent_id)
        winners: set[int] = set()
        for destination, agents in sorted(proposals.items()):
            # Exits drain one agent per step. Other cells accept one mover only
            # when unoccupied at the boundary, making queues explicit evidence.
            if destination in layout.exits or destination not in occupied:
                winners.add(min(agents))
        for agent_id in sorted(list(positions)):
            cell = positions[agent_id]
            path = paths.get(agent_id, [cell])
            try:
                index = path.index(cell)
            except ValueError:
                index = 0
            nxt = path[index + 1] if index + 1 < len(path) else cell
            if agent_id in winners and nxt != cell:
                positions[agent_id] = nxt
            else:
                waits += 1
            histories[agent_id].append(positions[agent_id])
            if positions[agent_id] in layout.exits:
                exit_steps[agent_id] = step
                del positions[agent_id]
                paths.pop(agent_id, None)
    completed = list(exit_steps.values())
    scored = completed + [max_steps] * (len(layout.starts) - len(completed))
    return Result(
        policy=policy,
        completion_rate=len(completed) / len(layout.starts),
        median_steps=float(statistics.median(scored)),
        p95_steps=percentile95(scored),
        congestion_waits=waits,
        reroute_count=reroutes,
        exit_steps=tuple(exit_steps.get(i, max_steps) for i in range(len(layout.starts))),
        trajectories=tuple(tuple(histories[i]) for i in range(len(layout.starts))),
    )


def paired_run(config: dict) -> tuple[Layout, Result, Result]:
    layout = generate_layout(config)
    baseline = run_policy(layout, config, "static")
    candidate = run_policy(layout, config, config["candidate"]["policy"])
    return layout, baseline, candidate
