"""Drive a scripted fake trajectory through TopoMap and check the invariants."""

import pytest

from app.memory import DECAY_RATE, MIN_CONFIDENCE, TopoMap


def _obs(label, landmarks=None, objects=None, frontiers=None):
    """Build a minimal observation dict (memory ignores bbox/image fields)."""
    return {
        "place_label": label,
        "place_description": f"description of {label}",
        "landmarks": [{"name": n, "description": ""} for n in (landmarks or [])],
        "objects": [{"name": n, "description": ""} for n in (objects or [])],
        "frontiers": [{"direction": d, "description": f"{d} opening"} for d in (frontiers or [])],
        "inferred_heading": "north",
    }


def _build_scripted_map():
    """Visit A -> B -> C, then revisit A. Edges A->B and B->C via 'forward'."""
    m = TopoMap()
    m.add_or_update_node(_obs("A", landmarks=["la"], objects=["mug"],
                              frontiers=["forward", "left"]), {"x": 0, "y": 0}, turn=0)
    m.add_or_update_node(_obs("B", landmarks=["lb"], frontiers=["forward", "right"]),
                         {"x": 1, "y": 0}, turn=1)
    m.add_edge("A", "B", heading="forward")   # explores A.forward
    m.add_or_update_node(_obs("C", landmarks=["lc"], frontiers=["forward"]),
                         {"x": 2, "y": 0}, turn=2)
    m.add_edge("B", "C", heading="forward")   # explores B.forward
    m.add_or_update_node(_obs("A", landmarks=["la"], objects=["mug"],
                              frontiers=["forward", "left"]), {"x": 0, "y": 0}, turn=3)
    return m


def test_node_and_edge_counts():
    m = _build_scripted_map()
    assert m.node_count == 3          # A, B, C (A revisited, not duplicated)
    assert m.edge_count == 2          # A->B, B->C


def test_revisit_resets_confidence_to_one():
    m = _build_scripted_map()
    a = m.get_node("A")
    assert a.confidence == 1.0
    assert a.last_confirmed_turn == 3
    # A's landmark was re-seen on the revisit, so it re-confirmed too.
    assert a.landmarks[0].name == "la"
    assert a.landmarks[0].confidence == 1.0
    assert a.landmarks[0].last_confirmed_turn == 3


def test_confidence_decays_on_unconfirmed_ticks():
    m = _build_scripted_map()
    for turn in range(4, 24):          # 20 ticks, none confirming any node
        m.tick(turn)

    expected = DECAY_RATE ** 20        # ~0.6676, above the 0.2 floor
    for nid in ("A", "B", "C"):
        node = m.get_node(nid)
        assert node.confidence == pytest.approx(expected)
        assert node.landmarks[0].confidence == pytest.approx(expected)


def test_pose_centroid_is_running_mean():
    m = TopoMap()
    m.add_or_update_node(_obs("A"), {"x": 0, "y": 0}, turn=0)
    m.add_or_update_node(_obs("A"), {"x": 4, "y": 2}, turn=1)
    a = m.get_node("A")
    assert (a.x, a.y) == (2.0, 1.0)


def test_confidence_floor_is_enforced():
    m = TopoMap()
    m.add_or_update_node(_obs("A"), {"x": 0, "y": 0}, turn=0)
    for turn in range(1, 500):          # decay far past the floor
        m.tick(turn)
    assert m.get_node("A").confidence == MIN_CONFIDENCE


def test_summary_three_details_and_unexplored_frontiers():
    m = _build_scripted_map()
    summ = m.summary("A")

    assert summ["current"] == "A"
    # Exactly 3 detailed nodes; with only 3 places the aggregate is empty.
    assert len(summ["detailed"]) == 3
    assert summ["aggregate"]["count"] == 0
    assert summ["aggregate"]["names"] == []

    # A.forward and B.forward were explored by the two edges; the rest remain.
    unexplored = {
        (f["node_id"], f["direction"]) for f in summ["aggregate"]["unexplored_frontiers"]
    }
    assert unexplored == {("A", "left"), ("B", "right"), ("C", "forward")}


def test_summary_bounds_detail_and_aggregates_older_nodes():
    """With more than 3 places, only the newest 3 stay detailed."""
    m = TopoMap()
    for i, label in enumerate(["A", "B", "C", "D", "E"]):
        m.add_or_update_node(_obs(label, frontiers=["forward"]), {"x": i, "y": 0}, turn=i)

    summ = m.summary("E")
    detailed_names = [d["place_label"] for d in summ["detailed"]]
    assert detailed_names == ["E", "D", "C"]        # newest first
    assert summ["aggregate"]["count"] == 2
    assert set(summ["aggregate"]["names"]) == {"A", "B"}
    # All five forward frontiers are still unexplored (no edges added).
    assert len(summ["aggregate"]["unexplored_frontiers"]) == 5


def test_to_json_round_trip_preserves_state():
    m = _build_scripted_map()
    restored = TopoMap.from_json(m.to_json())

    assert restored.node_count == m.node_count
    assert restored.edge_count == m.edge_count
    assert restored.summary("A") == m.summary("A")
    # Edge heading survives the round trip.
    assert restored.g["A"]["B"]["heading"] == "forward"
