"""The Qwen stand-in used by the eval harness.

Two channels, distinguished exactly as the real client is (image vs text-only):

* Perception (``image_bytes`` present): returns a deterministic observation for
  the world's *current* place. Held constant across conditions and free — the
  ablation is about the planner, not perception, so perception is never varied.
* Planning (``image_bytes is None``): either replays a scripted plan list in
  order (``live=False`` — deterministic, offline) or calls the real Qwen planner
  (``live=True``) so the --no-graph ablation manifests a genuine difference.

Perception reads ``world._place`` directly: on the static world the current place
fully determines what should be seen, so the same physical place always yields
the same observation (which is what lets memory recognise an honest revisit).
"""

from __future__ import annotations

import json
from typing import Callable, Optional


class EvalQwen:
    def __init__(
        self,
        world,
        place_observations: dict[str, dict],
        plans: Optional[list[str]] = None,
        live: bool = False,
        live_call: Optional[Callable[..., str]] = None,
    ) -> None:
        self._world = world
        self._place_observations = place_observations
        self._plans = list(plans or [])
        self._live = live
        self._live_call = live_call
        self.perception_calls = 0
        self.plan_calls = 0
        if live and live_call is None:
            raise ValueError("live=True requires a live_call")

    def __call__(self, prompt: str, image_bytes, json_mode: bool = True) -> str:
        if image_bytes is not None:
            self.perception_calls += 1
            place = self._world._place  # current place fully determines perception
            return json.dumps(self._place_observations[place])

        self.plan_calls += 1
        if self._live:
            return self._live_call(prompt, None, json_mode)
        if not self._plans:
            # Should not happen for a well-formed scripted episode; stop safely.
            return json.dumps({
                "reasoning": "no scripted plan left",
                "action_queue": [{"type": "stop", "reason": "scripted plan exhausted"}],
                "expected_next_node": "room_END",
                "goal_status": "stuck",
            })
        return self._plans.pop(0)
