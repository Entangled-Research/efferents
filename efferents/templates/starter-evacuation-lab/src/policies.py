"""Path policies for the deterministic grid simulator."""
from __future__ import annotations

import heapq
from collections import Counter, deque
from typing import Iterable

Cell = tuple[int, int]


def neighbors(cell: Cell, *, width: int, height: int, walls: frozenset[Cell]) -> list[Cell]:
    x, y = cell
    result = []
    for nxt in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nxt[0] < width and 0 <= nxt[1] < height and nxt not in walls:
            result.append(nxt)
    return sorted(result)


def shortest_path(
    start: Cell, exits: Iterable[Cell], *, width: int, height: int,
    walls: frozenset[Cell], costs: Counter[Cell] | None = None,
    congestion_weight: float = 0.0,
) -> list[Cell]:
    goals = frozenset(exits)
    occupancy = costs or Counter()
    queue: list[tuple[float, int, Cell]] = [(0.0, 0, start)]
    distance = {start: 0.0}
    previous: dict[Cell, Cell] = {}
    order = 0
    end = None
    while queue:
        score, _, cell = heapq.heappop(queue)
        if score != distance[cell]:
            continue
        if cell in goals:
            end = cell
            break
        for nxt in neighbors(cell, width=width, height=height, walls=walls):
            candidate = score + 1.0 + congestion_weight * occupancy[nxt]
            if candidate < distance.get(nxt, float("inf")):
                distance[nxt] = candidate
                previous[nxt] = cell
                order += 1
                heapq.heappush(queue, (candidate, order, nxt))
    if end is None:
        return [start]
    path = deque([end])
    while path[0] != start:
        path.appendleft(previous[path[0]])
    return list(path)


def static_paths(layout, positions: dict[int, Cell]) -> dict[int, list[Cell]]:
    return {
        agent_id: shortest_path(
            position, layout.exits, width=layout.width, height=layout.height,
            walls=layout.walls,
        )
        for agent_id, position in sorted(positions.items())
    }


def congestion_paths(
    layout,
    positions: dict[int, Cell],
    *,
    congestion_weight: float,
    reservation_depth: int,
) -> dict[int, list[Cell]]:
    # Agents plan in stable id order. Earlier paths become bounded local
    # reservations, so later agents can choose another corridor or exit.
    costs: Counter[Cell] = Counter(positions.values())
    paths: dict[int, list[Cell]] = {}
    for agent_id, position in sorted(positions.items()):
        path = shortest_path(
            position, layout.exits, width=layout.width, height=layout.height,
            walls=layout.walls, costs=costs, congestion_weight=congestion_weight,
        )
        paths[agent_id] = path
        for cell in path[1:1 + reservation_depth]:
            costs[cell] += 1
    return paths
