"""The planner: one Qwen call that turns the current situation into a Plan.

``plan()`` sends a text-only prompt (goal + current observation + local map
summary + recent action history) and expects strict JSON describing a short
queue of concrete actions plus where the planner expects to end up and whether
the goal is met. Actions reuse the world's own :data:`Action` models, so the
controller can hand them straight to ``world.execute_action``.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from app.world.base import Action


class Plan(BaseModel):
    reasoning: str
    action_queue: list[Action] = Field(min_length=1, max_length=3)
    expected_next_node: str
    goal_status: Literal["searching", "found", "stuck"]


class PlannerError(RuntimeError):
    """Raised when Qwen fails to return valid Plan JSON after a retry."""


_PROMPT = """\
You are the mission planner for an agent exploring an environment from
first-person photos. You do not see the image directly; you reason over the
structured observation, the map summary, and the recent action history below.

GOAL (from the coach): {goal}

CURRENT OBSERVATION (structured):
{observation}

LOCAL MAP SUMMARY:
{map_summary}

RECENT ACTION HISTORY (oldest first):
{action_history}

Decide the next 1 to 3 actions. Return a SINGLE JSON object (no prose, no
markdown) with EXACTLY this shape:
{{
  "reasoning": str,
  "action_queue": [ 1 to 3 actions ],
  "expected_next_node": str,   // the place you expect to reach next
  "goal_status": "searching" | "found" | "stuck"
}}

Each action is one of:
  {{"type": "move", "distance": 1.0}}
  {{"type": "turn", "degrees": 90}}    // use +90 or -90
  {{"type": "stop", "reason": str}}    // only when goal_status is "found" or "stuck"

Rules:
- The queue must hold between 1 and 3 actions.
- Each object and landmark now includes "screen_position" (left/center/right)
  and "proximity" (very_close/close/medium/far) derived from its bounding box.
- Use these to navigate precisely:
    * If goal is on the LEFT → turn -90 first, then move
    * If goal is on the RIGHT → turn +90 first, then move
    * If goal is in CENTER → move forward (1-3 steps depending on proximity)
    * If proximity is "very_close" or "close" → stop (goal_status "found")
    * If goal is NOT visible → turn to scan a new direction, then move
- Set goal_status "found" and queue {{"type":"stop","reason":"reached goal"}}
  when proximity is very_close or close.
- Set goal_status "stuck" only after exhausting all scan directions."""

_CORRECTION = (
    "\n\nYour previous response was not valid JSON for this schema. Return ONLY a "
    "single valid JSON object matching the schema exactly — no markdown, no code "
    "fences, no commentary."
)


def plan(
    goal: str,
    observation: Any,
    map_summary: dict,
    action_history: list,
    qwen_call: Callable[..., str],
) -> Plan:
    """Produce a validated :class:`Plan`. Retries once on malformed JSON."""

    base_prompt = _build_prompt(goal, observation, map_summary, action_history)

    last_error: Exception | None = None
    for attempt in range(2):
        prompt = base_prompt if attempt == 0 else base_prompt + _CORRECTION
        raw = qwen_call(prompt, None, json_mode=True)  # text-only: no image
        try:
            return Plan.model_validate(json.loads(_strip_fences(raw)))
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    raise PlannerError(
        f"planner did not return valid Plan JSON after a retry: {last_error}"
    ) from last_error


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _build_prompt(goal: str, observation: Any, map_summary: dict, action_history: list) -> str:
    return _PROMPT.format(
        goal=goal,
        observation=json.dumps(_observation_brief(observation), indent=2),
        map_summary=json.dumps(map_summary, indent=2),
        action_history=json.dumps(list(action_history), indent=2),
    )


def _object_spatial(obj: dict, img_w: int, img_h: int) -> dict:
    """Add screen-position and proximity hints from the pixel bounding box."""
    result = {"name": obj.get("name"), "description": obj.get("description")}
    bbox = obj.get("bbox_px") or obj.get("bbox_norm")
    if bbox and img_w and img_h:
        if obj.get("bbox_px"):
            x_min, x_max = bbox.get("x_min", 0), bbox.get("x_max", img_w)
            y_min, y_max = bbox.get("y_min", 0), bbox.get("y_max", img_h)
        else:
            # bbox_norm is 0-1000 scale
            x_min = bbox.get("x_min", 0) * img_w / 1000
            x_max = bbox.get("x_max", img_w) * img_w / 1000
            y_min = bbox.get("y_min", 0) * img_h / 1000
            y_max = bbox.get("y_max", img_h) * img_h / 1000

        cx = (x_min + x_max) / 2
        area_frac = (x_max - x_min) * (y_max - y_min) / (img_w * img_h)

        # Horizontal position in frame
        if cx < img_w * 0.33:
            result["screen_position"] = "left"
        elif cx > img_w * 0.67:
            result["screen_position"] = "right"
        else:
            result["screen_position"] = "center"

        # Proximity from bbox area
        if area_frac > 0.20:
            result["proximity"] = "very_close"
        elif area_frac > 0.08:
            result["proximity"] = "close"
        elif area_frac > 0.02:
            result["proximity"] = "medium"
        else:
            result["proximity"] = "far"
    return result


def _observation_brief(observation: Any) -> dict:
    """Compact observation for the planner including spatial hints from bboxes."""
    obs = observation.model_dump() if isinstance(observation, BaseModel) else dict(observation)
    img_w = obs.get("image_width", 640)
    img_h = obs.get("image_height", 360)
    return {
        "place_label": obs.get("place_label"),
        "place_description": obs.get("place_description"),
        "landmarks": [
            _object_spatial(l, img_w, img_h) for l in obs.get("landmarks", [])
        ],
        "objects": [
            _object_spatial(o, img_w, img_h) for o in obs.get("objects", [])
        ],
        "frontiers": obs.get("frontiers", []),
        "inferred_heading": obs.get("inferred_heading"),
    }


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[A-Za-z0-9_-]*\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()
