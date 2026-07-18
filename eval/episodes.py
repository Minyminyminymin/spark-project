"""Scripted eval episodes over the static 6-place layout.

Each episode picks a place-path through the known layout, an object to hide at the
final place, and (optionally) an injected wrong expectation to force a deviation.
``build_episode`` simulates the real :class:`StaticPhotoWorld` along that path to
derive, for every turn: the place the agent will perceive, and a one-action Plan
to install. Perception is generated per-place (deterministic, object only at the
target), and every place is given two frontiers so every turn is a DECISION turn
— which makes plan consumption exactly one-per-turn and fully predictable.

These are deterministic, offline, credit-free scenarios: the "Qwen" that returns
them is a scripted stand-in (see qwen_eval.py), exactly as the existing
tests/fixtures/agent_scenario.json demo already does.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.world.base import MoveAction, StopAction, TurnAction
from app.world.static_photos import StaticPhotoWorld
from eval.nav import load_layout, path_to_actions, shortest_moves

ROOT = Path(__file__).resolve().parent.parent
LAYOUT_PATH = ROOT / "photos" / "layout.json"

# --------------------------------------------------------------------------- #
# Episode specs. place_path[0] is the start place; each later place is adjacent.
# --------------------------------------------------------------------------- #

EPISODE_SPECS = [
    {
        "id": "mvd_full",
        "goal": "find the red mug",
        "object": "red_mug",
        "place_path": ["A", "D", "C", "B", "A", "D", "E", "F"],  # revisits A and D
        "description": "Scenic loop that revisits A, then reaches the mug at F. "
                       "Exercises all three MVD milestones.",
    },
    {
        "id": "direct",
        "goal": "find the red mug",
        "object": "red_mug",
        "place_path": ["A", "B", "C", "F"],  # shortest route, no revisit
        "description": "Efficient straight route to the mug at F (SPL == 1, no revisit).",
    },
    {
        "id": "deviation_recover",
        "goal": "find the brass key",
        "object": "brass_key",
        "place_path": ["A", "B", "C", "D", "A", "D", "E"],  # revisits A, key at E
        "deviate_before_place": "D",  # first arrival at D: mis-expect, then recover
        "description": "Loops back through A with an injected wrong expectation "
                       "(deviation + recovery), key at E.",
    },
]


def _label(place: str) -> str:
    return f"room_{place}"


def _observation(place: str, object_name: str | None) -> dict:
    """A deterministic per-place observation (object present only if given)."""
    objects = []
    if object_name is not None:
        objects.append({
            "name": object_name,
            "description": f"the {object_name.replace('_', ' ')}, in plain view here",
            "bbox_norm": {"x_min": 480, "y_min": 480, "x_max": 560, "y_max": 560},
        })
    return {
        "place_label": _label(place),
        "place_description": f"room {place} of the venue",
        "landmarks": [{
            "name": f"marker_{place}",
            "description": f"a fixed wall marker labelled {place}",
            "bbox_norm": {"x_min": 100, "y_min": 100, "x_max": 300, "y_max": 400},
        }],
        "objects": objects,
        "frontiers": [
            {"direction": "forward", "description": "an opening straight ahead"},
            {"direction": "left", "description": "a side passage"},
        ],
        "inferred_heading": "unknown",
    }


def _action_model(a: dict):
    if a["type"] == "move":
        return MoveAction(**a)
    if a["type"] == "turn":
        return TurnAction(**a)
    return StopAction(**a)


def _plan_json(action: dict, expected_place: str, goal_status: str, reason: str) -> str:
    return json.dumps({
        "reasoning": reason,
        "action_queue": [action],
        "expected_next_node": _label(expected_place),
        "goal_status": goal_status,
    })


def build_episode(spec: dict, layout_path: Path = LAYOUT_PATH) -> dict:
    """Expand a spec into concrete per-place perception + a scripted plan list."""
    layout = load_layout(layout_path)
    start = layout["start"]["place"]
    start_yaw = int(layout["start"]["yaw_deg"])
    path = spec["place_path"]
    assert path[0] == start, f"episode {spec['id']} must start at layout start {start!r}"

    object_place = path[-1]
    actions = path_to_actions(layout, path, start_yaw)

    # Simulate the real world to learn which place is perceived at each turn.
    world = StaticPhotoWorld(layout_path)
    perceived: list[str] = []
    for a in actions:
        perceived.append(world._place)
        world.execute_action(_action_model(a))
    perceived.append(world._place)  # the final (stop) turn perceives the object place
    assert perceived[-1] == object_place

    # Per-place perception (object only at the target place).
    place_observations = {
        p: _observation(p, spec["object"] if p == object_place else None)
        for p in layout["places"]
    }

    # One scripted plan per turn: actions[i] on turn i, then a terminal stop.
    deviate_place = spec.get("deviate_before_place")
    deviated_once = False
    plans: list[str] = []
    for i, a in enumerate(actions):
        expected = perceived[i + 1]
        # Inject a single wrong expectation on the MOVE that departs
        # `deviate_place`, so the next turn's real node (the neighbour) contradicts
        # it and the controller logs a deviation + re-plan. (Injecting on a turn
        # action would be wasted: the agent stays put, so nothing contradicts.)
        if (deviate_place and not deviated_once
                and perceived[i] == deviate_place and a["type"] == "move"):
            expected = "NOWHERE"  # _plan_json applies the room_ prefix
            deviated_once = True
        plans.append(_plan_json(a, expected, "searching",
                                f"heading toward {_label(perceived[i + 1])}"))
    plans.append(_plan_json(
        {"type": "stop", "reason": f"found the {spec['object'].replace('_', ' ')}"},
        object_place, "found", f"the {spec['object']} is visible here — goal complete"))

    return {
        "id": spec["id"],
        "goal": spec["goal"],
        "object": spec["object"],
        "object_place": object_place,
        "start_place": start,
        "shortest_moves": shortest_moves(layout, start, object_place),
        "place_observations": place_observations,
        "plans": plans,
        "description": spec["description"],
    }


def all_episodes(layout_path: Path = LAYOUT_PATH) -> dict[str, dict]:
    return {s["id"]: build_episode(s, layout_path) for s in EPISODE_SPECS}
