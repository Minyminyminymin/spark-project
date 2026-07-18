"""Layout navigation helpers for the eval harness.

The static layout (``photos/layout.json``) is a known 6-place graph with a cycle,
so we can compute the *shortest* place-path between any two places (for SPL) and
translate a chosen place-path into the concrete turn/move actions that walk it.
Nothing here talks to Qwen or the agent — it is pure graph/geometry.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Optional

HEADINGS = (0, 90, 180, 270)


def load_layout(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def neighbors(layout: dict, place: str) -> dict[int, str]:
    """heading -> neighbor place (only headings that lead somewhere)."""
    adj = layout["places"][place]["adjacency"]
    return {int(h): n for h, n in adj.items() if n is not None}


def heading_to(layout: dict, place: str, dest: str) -> Optional[int]:
    """The heading you must face at ``place`` to move to adjacent ``dest``."""
    for h, n in neighbors(layout, place).items():
        if n == dest:
            return h
    return None


def shortest_moves(layout: dict, start: str, goal: str) -> int:
    """Fewest move-hops from ``start`` to ``goal`` on the place graph (BFS)."""
    if start == goal:
        return 0
    seen = {start}
    q: deque[tuple[str, int]] = deque([(start, 0)])
    while q:
        place, dist = q.popleft()
        for nxt in neighbors(layout, place).values():
            if nxt == goal:
                return dist + 1
            if nxt not in seen:
                seen.add(nxt)
                q.append((nxt, dist + 1))
    raise ValueError(f"no path from {start!r} to {goal!r}")


def _turns_between(cur_yaw: int, target_yaw: int) -> list[float]:
    """Minimal sequence of +/-90 turns to rotate cur_yaw onto target_yaw."""
    diff = (target_yaw - cur_yaw) % 360
    if diff == 0:
        return []
    if diff == 90:
        return [90.0]
    if diff == 270:
        return [-90.0]
    return [90.0, 90.0]  # 180 degrees


def path_to_actions(layout: dict, place_path: list[str], start_yaw: int) -> list[dict]:
    """Turn/move actions that walk ``place_path`` starting from ``start_yaw``.

    ``place_path[0]`` is where the agent already stands; each subsequent place
    must be adjacent to its predecessor. Returns a flat list of action dicts
    ({"type":"turn","degrees":±90} / {"type":"move","distance":1.0}).
    """
    actions: list[dict] = []
    yaw = start_yaw % 360
    for a, b in zip(place_path, place_path[1:]):
        h = heading_to(layout, a, b)
        if h is None:
            raise ValueError(f"{b!r} is not adjacent to {a!r} in the layout")
        for deg in _turns_between(yaw, h):
            actions.append({"type": "turn", "degrees": deg})
            yaw = int((yaw + deg) % 360)
        actions.append({"type": "move", "distance": 1.0})
    return actions
