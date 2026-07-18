"""Planner: valid Plan parsing, action-queue bounds, and malformed-JSON retry."""

import pytest

from app.planner import Plan, PlannerError, plan

GOAL = "find the red mug"
SUMMARY = {"current": "hall", "detailed": [], "aggregate": {"count": 0, "names": [], "unexplored_frontiers": []}}
OBS = {
    "place_label": "hall", "place_description": "a hall",
    "landmarks": [], "objects": [], "frontiers": [{"direction": "forward", "description": "ahead"}],
    "inferred_heading": "north",
}

GOOD = (
    '{"reasoning":"go forward","action_queue":[{"type":"move","distance":1.0}],'
    '"expected_next_node":"corridor","goal_status":"searching"}'
)


def _stub(*responses):
    calls = []

    def qwen_call(prompt, image_bytes, json_mode=True):
        calls.append({"prompt": prompt, "image_bytes": image_bytes, "json_mode": json_mode})
        return responses[len(calls) - 1]

    qwen_call.calls = calls
    return qwen_call


def test_returns_validated_plan_text_only():
    qwen = _stub(GOOD)
    result = plan(GOAL, OBS, SUMMARY, [], qwen)
    assert isinstance(result, Plan)
    assert result.action_queue[0].type == "move"
    assert result.goal_status == "searching"
    # Planner is text-only: no image bytes are sent.
    assert qwen.calls[0]["image_bytes"] is None
    assert qwen.calls[0]["json_mode"] is True


def test_action_queue_must_be_1_to_3():
    too_many = (
        '{"reasoning":"x","action_queue":['
        '{"type":"move","distance":1.0},{"type":"move","distance":1.0},'
        '{"type":"move","distance":1.0},{"type":"move","distance":1.0}],'
        '"expected_next_node":"n","goal_status":"searching"}'
    )
    # First response over-long -> retry -> good.
    qwen = _stub(too_many, GOOD)
    result = plan(GOAL, OBS, SUMMARY, [], qwen)
    assert len(result.action_queue) == 1
    assert len(qwen.calls) == 2


def test_malformed_json_then_retry():
    qwen = _stub("not json {", GOOD)
    result = plan(GOAL, OBS, SUMMARY, [], qwen)
    assert isinstance(result, Plan)
    assert len(qwen.calls) == 2
    assert "valid JSON" in qwen.calls[1]["prompt"]


def test_two_failures_raise():
    qwen = _stub("garbage", "still garbage")
    with pytest.raises(PlannerError):
        plan(GOAL, OBS, SUMMARY, [], qwen)
