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
- Set goal_status to "found" and queue a single "stop" action when the goal
  object is visible in the current observation.
- Set goal_status to "stuck" and stop if no progress is possible.
- Otherwise keep "searching" and move/turn toward an unexplored frontier."""

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


def _observation_brief(observation: Any) -> dict:
    """A compact, text-friendly view of the observation (drops bbox noise)."""
    obs = observation.model_dump() if isinstance(observation, BaseModel) else dict(observation)
    return {
        "place_label": obs.get("place_label"),
        "place_description": obs.get("place_description"),
        "landmarks": [
            {"name": l.get("name"), "description": l.get("description")}
            for l in obs.get("landmarks", [])
        ],
        "objects": [
            {"name": o.get("name"), "description": o.get("description")}
            for o in obs.get("objects", [])
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
