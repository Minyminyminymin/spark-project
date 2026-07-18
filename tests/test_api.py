"""FastAPI wrapper: instruction -> 10 ticks -> state, with recorded Qwen fixtures."""

import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)

GOAL = "find the red mug"
LABELS = [f"n{i}" for i in range(10)]  # 10 distinct places -> a growing graph


def _perception(label, with_object=False):
    objects = (
        [{"name": "red_mug", "description": "a bright red mug",
          "bbox_norm": {"x_min": 500, "y_min": 500, "x_max": 560, "y_max": 560}}]
        if with_object else []
    )
    return json.dumps({
        "place_label": label,
        "place_description": f"room {label}",
        "landmarks": [{"name": f"lm_{label}", "description": "a landmark",
                       "bbox_norm": {"x_min": 100, "y_min": 100, "x_max": 200, "y_max": 200}}],
        "objects": objects,
        # Two frontiers => never a ROUTINE turn, so every tick is a decision.
        "frontiers": [{"direction": "forward", "description": "ahead"},
                      {"direction": "left", "description": "to the left"}],
        "inferred_heading": "north",
    })


def _plan(expected, status="searching", stop=False):
    queue = ([{"type": "stop", "reason": "found the red mug"}] if stop
             else [{"type": "move", "distance": 1.0}])
    return json.dumps({"reasoning": "step", "action_queue": queue,
                       "expected_next_node": expected, "goal_status": status})


class ScriptedQwen:
    def __init__(self, perception, plan):
        self._perception = list(perception)
        self._plan = list(plan)

    def __call__(self, prompt, image_bytes, json_mode=True):
        return self._plan.pop(0) if image_bytes is None else self._perception.pop(0)


def _install_scenario():
    perception = [_perception(l, with_object=(i == 9)) for i, l in enumerate(LABELS)]
    # plan[k] expects to arrive at the next place; the last one finds the goal.
    plan = [_plan(LABELS[k + 1]) for k in range(9)] + [_plan(LABELS[9], "found", stop=True)]
    main.configure_qwen(ScriptedQwen(perception, plan))
    resp = client.post("/reset")
    assert resp.status_code == 200


def test_instruction_tick_state_flow():
    _install_scenario()

    # POST /instruction echoes the parsed goal.
    resp = client.post("/instruction", json={"text": GOAL})
    assert resp.status_code == 200
    assert resp.json() == {"goal": GOAL}

    # Graph starts empty.
    graph0 = client.get("/state").json()["graph"]
    assert graph0["nodes"] == []

    statuses = []
    node_counts = []
    for i in range(10):
        rec = client.post("/tick").json()
        assert rec["turn"] == i
        assert rec["type"] == "decision"
        statuses.append(rec["goal_status"])
        node_counts.append(len(client.get("/state").json()["graph"]["nodes"]))

    # Graph grew monotonically to 10 nodes with 9 edges.
    assert node_counts == list(range(1, 11))
    state = client.get("/state").json()
    assert len(state["graph"]["nodes"]) == 10
    assert len(state["graph"]["edges"]) == 9

    # goal_status transitioned searching -> found exactly once, at the end.
    assert statuses[:9] == ["searching"] * 9
    assert statuses[9] == "found"
    assert state["goal_status"] == "found"

    # State snapshot is coherent.
    assert state["goal"] == GOAL
    assert state["last_turn_type"] == "decision"
    assert state["pose"] is not None and {"x", "y", "yaw_deg"} <= state["pose"].keys()
    assert len(state["commentary"]) == 10               # one record per turn
    assert state["commentary"][-1]["action"] == {"type": "stop", "reason": "found the red mug"}

    # A node carries its found object; nodes expose the required fields.
    target = next(n for n in state["graph"]["nodes"] if n["id"] == "n9")
    assert "red_mug" in target["objects"]
    assert {"id", "label", "x", "y", "confidence", "visited", "objects"} <= target.keys()


def test_tick_after_done_is_graceful():
    _install_scenario()
    client.post("/instruction", json={"text": GOAL})
    for _ in range(10):
        client.post("/tick")
    # Agent has terminated; further ticks don't error or advance.
    resp = client.post("/tick")
    assert resp.status_code == 200
    assert resp.json()["done"] is True


def test_reset_clears_graph_and_goal():
    _install_scenario()
    client.post("/instruction", json={"text": GOAL})
    for _ in range(3):
        client.post("/tick")
    assert len(client.get("/state").json()["graph"]["nodes"]) == 3

    _install_scenario()  # re-installs fixtures and calls /reset
    state = client.get("/state").json()
    assert state["graph"]["nodes"] == []
    assert state["goal"] == ""
    assert state["deviation_events"] == []


def test_world_splat_boots_as_splatworld(monkeypatch):
    """WORLD=splat now boots a real SplatWorld (constructing it does no I/O)."""
    from app.world.splat_client import SplatWorld

    monkeypatch.setenv("WORLD", "splat")
    resp = client.post("/reset")
    assert resp.status_code == 200
    assert resp.json()["world"] == "splat"
    assert isinstance(main.state.agent.world, SplatWorld)

    # /state stays reachable even with no engine up: pose falls back to None.
    snapshot = client.get("/state").json()
    assert snapshot["graph"]["nodes"] == []
    assert snapshot["pose"] is None

    # Restore a working static agent for any later tests.
    monkeypatch.delenv("WORLD", raising=False)
    _install_scenario()
