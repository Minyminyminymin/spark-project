"""Controller policy: routine vs decision, Qwen budget, deviation, termination."""

import json
from pathlib import Path

from app.controller import Agent
from app.memory import TopoMap
from app.world.static_photos import StaticPhotoWorld

LAYOUT = Path(__file__).resolve().parent.parent / "photos" / "layout.json"
SCENARIO = json.loads((Path(__file__).resolve().parent / "fixtures" / "agent_scenario.json").read_text())


class ScriptedQwen:
    """Routes calls: image bytes -> perception response; text-only -> plan response."""

    def __init__(self, perception, plan):
        self._perception = list(perception)
        self._plan = list(plan)
        self.count = 0

    def __call__(self, prompt, image_bytes, json_mode=True):
        self.count += 1
        if image_bytes is None:
            return self._plan.pop(0)
        return self._perception.pop(0)


def _build_agent(tmp_path):
    world = StaticPhotoWorld(LAYOUT)
    topo = TopoMap()
    qwen = ScriptedQwen(SCENARIO["perception"], SCENARIO["plan"])
    log = tmp_path / "agent_log.jsonl"
    agent = Agent(world, topo, SCENARIO["goal"], qwen, log)
    return agent, qwen, log


def test_full_run_log_meets_done_criteria(tmp_path):
    agent, qwen, log = _build_agent(tmp_path)
    records = agent.run(max_turns=20)

    # Terminated on a found+stop.
    assert agent.done is True
    last = records[-1]
    assert last["goal_status"] == "found"
    assert last["action"] == {"type": "stop", "reason": "found the red mug"}

    # At least one ROUTINE turn happened.
    routine = [r for r in records if r["type"] == "routine"]
    assert len(routine) >= 1

    # A deviation event is visible in the log.
    deviations = [r for r in records if r["deviation"]]
    assert len(deviations) >= 1
    assert "isn't it" in deviations[0]["event"]

    # The JSONL file on disk matches the in-memory records, one line per turn.
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert lines == records
    assert all({"turn", "type", "action", "node", "deviation", "goal_status"} <= r.keys() for r in lines)


def test_routine_turn_spends_zero_qwen_calls_decisions_spend_two(tmp_path):
    agent, qwen, _ = _build_agent(tmp_path)

    per_turn_calls = []
    while not agent.done and agent.turn < 20:
        before = qwen.count
        rec = agent.step()
        per_turn_calls.append((rec["type"], qwen.count - before))

    # Every routine turn used 0 Qwen calls; every decision used exactly 2.
    assert ("routine", 0) in per_turn_calls
    for turn_type, n in per_turn_calls:
        assert n == (0 if turn_type == "routine" else 2)


def test_deviation_clears_queue_and_forces_replan(tmp_path):
    agent, _, _ = _build_agent(tmp_path)
    records = agent.run(max_turns=20)

    dev_turn = next(r["turn"] for r in records if r["deviation"])
    # The deviating turn is a decision (re-plan), not a routine continuation.
    assert records[dev_turn]["type"] == "decision"
    # It localized to the unexpected node.
    assert records[dev_turn]["node"] == "wrong_room"


def test_expected_node_reached_does_not_deviate(tmp_path):
    """A decision that lands on the expected node must not log a deviation."""
    agent, _, _ = _build_agent(tmp_path)
    records = agent.run(max_turns=20)

    # The final decision arrives at 'target_area', which was the expected node.
    final = records[-1]
    assert final["node"] == "target_area"
    assert final["deviation"] is False
