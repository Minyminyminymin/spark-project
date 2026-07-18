"""Localizer decisions: pose proximity, landmark tie-break, and a full loop."""

import json
from pathlib import Path

from app.localizer import DEFAULT_RADIUS, localize
from app.memory import TopoMap

LAYOUT = Path(__file__).resolve().parent.parent / "photos" / "layout.json"

# The layout's fake coordinates are on a unit grid, which is denser than the
# default 1.5 pose radius. Scale it to a metric-ish spacing so distinct places
# sit well outside R while a return lands well inside it.
SCALE = 3.0


def _obs(label, landmarks=None, frontiers=None):
    return {
        "place_label": label,
        "place_description": f"description of {label}",
        "landmarks": [{"name": n, "description": ""} for n in (landmarks or [])],
        "objects": [],
        "frontiers": [{"direction": d, "description": ""} for d in (frontiers or [])],
        "inferred_heading": "north",
    }


def _seed(m, label, x, y, landmarks=None, turn=0):
    m.add_or_update_node(_obs(label, landmarks=landmarks), {"x": x, "y": y}, turn)


def test_near_identical_pose_is_revisit_by_pose():
    m = TopoMap()
    _seed(m, "A", 0.0, 0.0, landmarks=["fountain"])
    res = localize(_obs("A", landmarks=["fountain"]), {"x": 0.05, "y": -0.03}, m)
    assert res.is_revisit is True
    assert res.node_id == "A"
    assert res.matched_by == "pose"


def test_far_pose_is_new_place():
    m = TopoMap()
    _seed(m, "A", 0.0, 0.0)
    res = localize(_obs("Z"), {"x": 5.0, "y": 5.0}, m)
    assert res.is_revisit is False
    assert res.node_id is None
    assert res.matched_by == "new"


def test_close_centroids_disambiguated_by_landmark_overlap():
    m = TopoMap()
    _seed(m, "P1", 0.0, 0.0, landmarks=["fountain", "statue"])
    _seed(m, "P2", 0.5, 0.0, landmarks=["door", "window"])

    # Pose sits within R of both; landmarks match P2 exactly.
    res = localize(_obs("?", landmarks=["door", "window"]), {"x": 0.2, "y": 0.0}, m)
    assert res.is_revisit is True
    assert res.node_id == "P2"
    assert res.matched_by == "pose+visual"


def test_stale_node_loses_tiebreak_to_fresh_node():
    m = TopoMap()
    _seed(m, "STALE", 0.0, 0.0, landmarks=["key"], turn=0)
    for turn in range(1, 60):          # decay STALE below 0.5
        m.tick(turn)
    _seed(m, "FRESH", 0.4, 0.0, landmarks=["key"], turn=60)  # same landmark

    assert m.get_node("STALE").confidence < 0.5
    assert m.get_node("FRESH").confidence >= 0.5

    # Equal Jaccard (both == {"key"}) -> freshness decides.
    res = localize(_obs("?", landmarks=["key"]), {"x": 0.2, "y": 0.0}, m)
    assert res.node_id == "FRESH"
    assert res.matched_by == "pose+visual"


def test_place_label_is_not_a_matching_signal():
    """Returning to A's pose under a different label still resolves to A."""
    m = TopoMap()
    _seed(m, "A", 0.0, 0.0, landmarks=["fountain"])
    res = localize(_obs("totally_different_name", landmarks=["fountain"]),
                   {"x": 0.05, "y": 0.05}, m)
    assert res.is_revisit is True
    assert res.node_id == "A"


def test_full_six_place_loop_yields_six_nodes_one_revisit():
    places = json.loads(LAYOUT.read_text())["places"]
    # Walk every place once, then return to the start: 7 observations.
    order = ["A", "B", "C", "D", "E", "F", "A"]

    m = TopoMap()
    revisit_events = 0
    for turn, label in enumerate(order):
        p = places[label]
        pose = {"x": p["x"] * SCALE, "y": p["y"] * SCALE}
        obs = _obs(label, landmarks=[f"lm_{label}"], frontiers=["forward"])

        result = localize(obs, pose, m, radius=DEFAULT_RADIUS)
        if result.is_revisit:
            revisit_events += 1
        # Commit the observation regardless of the verdict.
        m.add_or_update_node(obs, pose, turn)

    assert m.node_count == 6           # not 7
    assert revisit_events == 1         # only the return to A
