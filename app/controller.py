"""The controller: one turn of the perception -> memory -> planning -> control loop.

``Agent.step()`` implements a fixed, mostly rule-based policy that spends Qwen
calls only when it must:

  1. Deviation check (rules) — bail out of a stale plan and re-plan.
  2. Turn classification (rules) — ROUTINE (cheap) vs DECISION (two Qwen calls).
  3. ROUTINE  -> pop+execute one queued action, tick memory. Zero Qwen calls.
  4. DECISION -> view -> perceive -> localize -> update map -> summarize -> plan
                 -> execute the queue's first action. Two Qwen calls.
  5. Append one JSONL log line per turn.

It composes the existing perception, memory, localizer, planner, and world
modules — it does not reimplement them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import math

from app.localizer import localize
from app.memory import TopoMap
from app.perception import perceive
from app.planner import plan
from app.world.base import MoveAction, StopAction, TurnAction, WalkToAction, World


def _goal_visible(goal: str, observation: Any) -> bool:
    """Return True if a perceived object matches ALL meaningful keywords in the goal.

    Navigation verbs are stripped. The remaining words are split into:
      - noun keywords  (sofa, chair, table …)
      - modifier keywords (dark, brown, red, big, small …)

    An object matches only if its name+description contains the noun AND
    every modifier. This prevents "dark brown sofa" from matching a beige sofa
    just because the word "sofa" appears.
    """
    STOP = {"walk", "go", "to", "the", "find", "get", "reach", "move", "a", "an",
            "towards", "toward", "near", "next", "that", "which", "with"}
    MODIFIERS = {"dark", "light", "big", "small", "large", "red", "blue", "green",
                 "brown", "black", "white", "grey", "gray", "yellow", "orange",
                 "pink", "purple", "wooden", "metal", "glass", "tall", "short",
                 "round", "square", "old", "new", "bright", "dim"}

    words = [w.lower().strip(".,!?") for w in goal.split()
             if w.lower() not in STOP and len(w) > 1]
    if not words:
        return False

    noun_kws = [w for w in words if w not in MODIFIERS]
    mod_kws  = [w for w in words if w in MODIFIERS]

    items = list(getattr(observation, "objects", [])) + list(getattr(observation, "landmarks", []))
    for item in items:
        text = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}".lower()
        # Noun must match (any noun keyword suffices — handles sofa/couch synonyms)
        if not any(kw in text for kw in noun_kws):
            continue
        # All modifiers must match (color, size, material)
        if all(kw in text for kw in mod_kws):
            return True
    return False


# Policy constants.
ROUTINE_FRONTIER_COUNT = 1     # a corridor-like view has exactly one frontier
MAX_ROUTINE_STREAK = 3         # force a DECISION after this many routine turns
DEVIATION_ACTION_LIMIT = 4     # actions allowed toward expected before giving up
GOAL_SEEN_GRACE = 4            # turns of trust after last seeing the goal object
SCAN_ANGLE = 90                # degrees per scan turn (~91° horizontal FOV at 16:9)
MAX_SCAN_STREAK = 4            # 4 × 90° = full 360° sweep before forcing a move
FREE_MOVE_TURNS = 2            # planner-free moves after a full sweep

ROUTINE = "routine"
DECISION = "decision"


class Agent:
    def __init__(
        self,
        world: World,
        topo_map: TopoMap,
        goal: str,
        qwen_call: Callable[..., str],
        log_path: str | Path,
    ) -> None:
        self.world = world
        self.topo_map = topo_map
        self.goal = goal
        self.qwen_call = qwen_call
        self.log_path = Path(log_path)
        self.log_path.write_text("")  # truncate/create the log

        # Turn/loop state.
        self.turn = 0
        self.done = False
        self.log_records: list[dict] = []

        # Plan state.
        self.action_queue: list = []
        self.expected_next_node: str | None = None
        self.plan_origin_node: str | None = None
        self.actions_since_expected = 0
        self.last_goal_status = "searching"

        # Perception/localization memory across turns.
        self.prev_observation: Any = None
        self.new_landmark_last_turn = False
        self.last_localized_node: str | None = None
        self.prev_committed_node: str | None = None
        self.consecutive_routine_turns = 0

        # Scan state: track consecutive scan turns so we alternate direction
        # and eventually move forward rather than spinning in one direction.
        self.consecutive_scan_turns = 0
        self._scan_direction = 1  # +1 = right (clockwise), -1 = left
        # How many turns ago we last saw the goal object in the frame.
        # While this is < GOAL_SEEN_GRACE, skip scan enforcement (trust the
        # planner — it knows we were heading the right way).
        self.turns_since_goal_visible = 999
        # How many planner-free (scan-enforced) moves remain after a full sweep.
        self._free_move_turns = 0

        # Rolling history handed to the planner.
        self.action_history: list[dict] = []

    # ------------------------------------------------------------------ #
    # Public loop
    # ------------------------------------------------------------------ #

    def run(self, max_turns: int = 50) -> list[dict]:
        while not self.done and self.turn < max_turns:
            self.step()
        return self.log_records

    def step(self) -> dict | None:
        if self.done:
            return None

        turn = self.turn
        self._pending_event: str | None = None

        # (1) Deviation check — uses the most recent localization we have.
        deviated = self._detect_deviation(self.last_localized_node)

        # (2) Turn classification.
        routine = (
            len(self.action_queue) > 0
            and self.prev_observation is not None
            and len(self.prev_observation.frontiers) == ROUTINE_FRONTIER_COUNT
            and not self.new_landmark_last_turn
            and not deviated
            and self.consecutive_routine_turns < MAX_ROUTINE_STREAK
        )

        record = self._run_routine(turn) if routine else self._run_decision(turn, deviated)

        self._write_log(record)
        self.turn += 1
        return record

    # ------------------------------------------------------------------ #
    # (3) ROUTINE — no Qwen
    # ------------------------------------------------------------------ #

    def _run_routine(self, turn: int) -> dict:
        action = self.action_queue.pop(0)
        self._execute(action)
        self.topo_map.tick(turn)

        self.consecutive_routine_turns += 1
        self.new_landmark_last_turn = False  # no perception happened this turn
        if action.type == "stop":
            self.done = True

        return self._record(turn, ROUTINE, action, self.last_localized_node,
                             deviation=False, goal_status=self.last_goal_status)

    # ------------------------------------------------------------------ #
    # (4) DECISION — two Qwen calls
    # ------------------------------------------------------------------ #

    def _run_decision(self, turn: int, deviated: bool) -> dict:
        view = self.world.get_current_view()
        observation = perceive(view.image, view.width, view.height, self.qwen_call)  # Qwen #1
        pose = view.pose

        localize(observation, pose, self.topo_map)  # geometry only, no Qwen
        node_id, new_landmark = self._commit(observation, pose, turn)

        self.last_localized_node = node_id
        self.new_landmark_last_turn = new_landmark
        self.prev_observation = observation

        # Fresh-arrival deviation: did we land somewhere other than expected?
        if not deviated:
            deviated = self._detect_deviation(node_id)

        summary = self.topo_map.summary(node_id)
        result = plan(self.goal, observation, summary, self.action_history[-6:], self.qwen_call)  # Qwen #2

        # Install the new plan.
        self.action_queue = list(result.action_queue)
        self.expected_next_node = result.expected_next_node
        self.plan_origin_node = node_id
        self.actions_since_expected = 0
        self.last_goal_status = result.goal_status
        self.consecutive_routine_turns = 0

        action = self.action_queue.pop(0)
        self._execute(action)
        if action.type == "stop" or result.goal_status == "found":
            self.done = True

        return self._record(turn, DECISION, action, node_id,
                            deviation=deviated, goal_status=result.goal_status)

    # ------------------------------------------------------------------ #
    # Rules & helpers
    # ------------------------------------------------------------------ #

    def _detect_deviation(self, node: str | None) -> bool:
        """Return True (and clear the queue + log an event) on a deviation.

        Fires when either too many actions have been spent chasing the expected
        node without reaching it, or we've localized to some *other* known node
        (not the origin we planned from, not the node we expected).
        """
        if self.expected_next_node is None or node is None:
            return False

        overdue = (
            self.actions_since_expected >= DEVIATION_ACTION_LIMIT
            and node != self.expected_next_node
        )
        contradicts = node != self.expected_next_node and node != self.plan_origin_node

        if overdue or contradicts:
            self.action_queue = []
            self._pending_event = (
                f"Expected {self.expected_next_node} but this isn't it — re-planning"
            )
            return True
        return False

    def _commit(self, observation: Any, pose: Any, turn: int) -> tuple[str, bool]:
        """Write the observation into the map; report if a new landmark appeared."""
        label = observation.place_label
        before = set()
        if label in self.topo_map.g:
            before = {lm.name for lm in self.topo_map.get_node(label).landmarks}

        node_id = self.topo_map.add_or_update_node(observation.model_dump(), pose.model_dump(), turn)

        after = {lm.name for lm in self.topo_map.get_node(node_id).landmarks}
        new_landmark = bool(after - before)

        # Record traversal as an edge (heading = the yaw we arrived with).
        if self.prev_committed_node is not None and self.prev_committed_node != node_id:
            try:
                self.topo_map.add_edge(
                    self.prev_committed_node, node_id, heading=str(int(round(pose.yaw_deg)))
                )
            except ValueError:
                pass
        self.prev_committed_node = node_id
        return node_id, new_landmark

    def _execute(self, action: Any) -> None:
        self.world.execute_action(action)
        self.action_history.append(action.model_dump())
        self.actions_since_expected += 1

    def _record(self, turn, turn_type, action, node, deviation, goal_status,
                observation=None, plan=None) -> dict:
        record: dict = {
            "turn": turn,
            "type": turn_type,
            "action": action.model_dump() if action is not None else None,
            "node": node,
            "deviation": deviation,
            "goal_status": goal_status,
            "event": self._pending_event,
        }
        if observation is not None:
            obs = observation.model_dump() if hasattr(observation, "model_dump") else {}
            record["observation"] = {
                "place_label": obs.get("place_label"),
                "objects": [{"name": o["name"], "screen_position": o.get("screen_position"),
                             "proximity": o.get("proximity")}
                            for o in obs.get("objects", [])],
                "frontiers": [f["direction"] for f in obs.get("frontiers", [])],
            }
        if plan is not None:
            record["plan_reasoning"] = getattr(plan, "reasoning", None)
            record["plan_queue"] = [a.model_dump() for a in getattr(plan, "action_queue", [])]
        return record

    def _write_log(self, record: dict) -> None:
        with self.log_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        self.log_records.append(record)

    def _find_goal_object(self, observation: Any) -> Any | None:
        """Find the observation object that matches the goal."""
        STOP = {"walk", "go", "to", "the", "find", "get", "reach", "move", "a", "an",
                "towards", "toward", "near", "next", "that", "which", "with"}
        words = [w.lower().strip(".,!?") for w in self.goal.split()
                 if w.lower() not in STOP and len(w) > 1]
        if not words:
            return None

        items = list(getattr(observation, "objects", [])) + list(getattr(observation, "landmarks", []))
        for item in items:
            text = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}".lower()
            if any(kw in text for kw in words):
                return item
        return None

    def _compute_approach_actions(self, goal_obj: Any, observation: Any, pose: Any) -> list:
        """Compute a walk_to action using world coordinates.

        From the object's bbox center-x we get the bearing angle offset from
        the agent's current yaw. From the bbox area we estimate distance.
        Combine with the agent's world position to get an absolute (x, z) target.

        Coordinate convention (from base.py):
          yaw   0° → +y (north)
          yaw  90° → +x (east)
          yaw 180° → -y (south)
          yaw 270° → -x (west)

        agent.js maps: backend.x = three.x, backend.y = -three.z
        So walk_to.x → three.x, walk_to.z → -backend.y (→ three.z)
        """
        if goal_obj is None:
            return [MoveAction(distance=1.0)]

        bbox = getattr(goal_obj, "bbox_norm", None)
        if bbox is None:
            return [MoveAction(distance=1.0)]

        # bbox_norm is on 0-1000 scale
        x_min = bbox.x_min
        x_max = bbox.x_max
        y_min = bbox.y_min
        y_max = bbox.y_max

        cx = (x_min + x_max) / 2 / 1000  # 0-1 normalized
        box_w = (x_max - x_min) / 1000
        box_h = (y_max - y_min) / 1000
        area_frac = box_w * box_h

        # "Very close" if EITHER the bbox area is large OR the object fills
        # most of the frame height (handles tall/thin objects like lamps)
        if area_frac > 0.30 or box_h > 0.75 or box_w > 0.60:
            return [StopAction(reason="reached goal — object is very close")]

        # Use the larger dimension for distance estimation (more robust for
        # non-square objects like lamps, paintings, etc.)
        size = max(box_w, box_h)

        # Estimate distance from apparent size:
        #   size 0.75 → ~0.5m
        #   size 0.50 → ~1.5m
        #   size 0.25 → ~3m
        #   size 0.10 → ~6m
        # Rough inverse: dist ≈ k / size
        est_distance = min(1.0 / max(size, 0.05), 8.0)

        # Walk to 90% of estimated distance — get close, re-evaluate on arrival
        walk_distance = est_distance * 0.9

        # Bearing angle from screen position:
        # Assuming ~90° horizontal FOV, center of image = current yaw
        # offset_angle = (cx - 0.5) * FOV
        H_FOV_DEG = 90.0
        angle_offset_deg = (cx - 0.5) * H_FOV_DEG

        # World bearing to target
        bearing_deg = pose.yaw_deg + angle_offset_deg
        bearing_rad = math.radians(bearing_deg)

        # Compute world target (yaw 0=+y, 90=+x convention):
        #   dx = sin(bearing) * distance
        #   dy = cos(bearing) * distance
        target_x = pose.x + math.sin(bearing_rad) * walk_distance
        target_y = pose.y + math.cos(bearing_rad) * walk_distance

        # walk_to uses backend coords: x and z where z = -backend.y for Three.js
        return [WalkToAction(x=round(target_x, 3), z=round(target_y, 3))]

    # ------------------------------------------------------------------ #
    # Browser-driven path (no World I/O)
    # ------------------------------------------------------------------ #

    def step_from_frame(
        self,
        image_bytes: bytes,
        width: int,
        height: int,
        pose: Any,
    ) -> dict | None:
        """One agent turn using a caller-supplied frame; skips world I/O entirely.

        Used by POST /agent/step: the browser supplies the rendered frame and
        true pose. This method returns an Action dict without ever calling
        world.get_current_view() or world.execute_action().
        """
        if self.done:
            return None

        turn = self.turn
        self._pending_event: str | None = None

        deviated = self._detect_deviation(self.last_localized_node)

        # If there's a queued move action from the previous plan, execute it
        # without re-perceiving — this ensures turn+move pairs complete.
        if self.action_queue and not deviated:
            record = self._run_routine_no_world(turn)
        else:
            record = self._run_decision_from_frame(turn, deviated, image_bytes, width, height, pose)

        self._write_log(record)
        self.turn += 1
        return record

    def _run_routine_no_world(self, turn: int) -> dict:
        """ROUTINE turn: pop the queued action but skip world.execute_action."""
        action = self.action_queue.pop(0)
        # Inline _execute() minus the world call.
        self.action_history.append(action.model_dump())
        self.actions_since_expected += 1
        self.topo_map.tick(turn)
        self.consecutive_routine_turns += 1
        self.new_landmark_last_turn = False
        if action.type == "stop":
            self.done = True
        return self._record(turn, ROUTINE, action, self.last_localized_node,
                            deviation=False, goal_status=self.last_goal_status)

    def _run_decision_from_frame(
        self,
        turn: int,
        deviated: bool,
        image_bytes: bytes,
        width: int,
        height: int,
        pose: Any,
    ) -> dict:
        """DECISION turn: perception → FSM → action.

        FSM logic:
          SCANNING: goal not in frame → turn 90°.
                    After 4 turns (full 360°) with no sighting → move 1m to
                    a new position and reset the scan counter.
          APPROACHING: goal visible → compute world-space target from bbox +
                    pose, emit walk_to(x, z) to go directly there. No planner.
        """
        try:
            observation = perceive(image_bytes, width, height, self.qwen_call, goal=self.goal)
        except Exception:
            # Qwen returned invalid JSON — treat this frame as a scan turn
            action = TurnAction(degrees=SCAN_ANGLE)
            self.action_history.append(action.model_dump())
            self.last_goal_status = "searching"
            return self._record(turn, DECISION, action, self.last_localized_node,
                                deviation=deviated, goal_status="searching")

        localize(observation, pose, self.topo_map)
        node_id, new_landmark = self._commit(observation, pose, turn)

        self.last_localized_node = node_id
        self.new_landmark_last_turn = new_landmark
        self.prev_observation = observation

        goal_visible = _goal_visible(self.goal, observation)

        # ── SCANNING: goal not visible → turn, don't call planner ────────────
        if not goal_visible:
            self.turns_since_goal_visible += 1
            self.consecutive_routine_turns = 0

            if self.consecutive_scan_turns < MAX_SCAN_STREAK:
                # Always turn clockwise (+90°) for a systematic sweep.
                # 4 × 90° = full 360° before giving up and moving forward.
                action = TurnAction(degrees=SCAN_ANGLE)
                self.consecutive_scan_turns += 1
                goal_status = "searching"
            else:
                # Full sweep done, nothing found → move forward to new area
                self.consecutive_scan_turns = 0
                action = MoveAction(distance=1.0)
                goal_status = "searching"

            self.action_history.append(action.model_dump())
            self.last_goal_status = goal_status
            return self._record(turn, DECISION, action, node_id,
                                deviation=deviated, goal_status=goal_status,
                                observation=observation)

        # ── APPROACHING: goal IS visible → compute WASD path from bbox ────────
        # No planner call. Use the object's screen position and proximity to
        # derive forward + strafe commands, like a player using WASD keys.
        self.turns_since_goal_visible = 0
        self.consecutive_scan_turns = 0
        self._scan_direction = 1

        goal_obj = self._find_goal_object(observation)
        actions = self._compute_approach_actions(goal_obj, observation, pose)

        self.action_queue = actions[1:] if len(actions) > 1 else []
        self.expected_next_node = node_id
        self.plan_origin_node = node_id
        self.actions_since_expected = 0
        self.consecutive_routine_turns = 0

        action = actions[0]
        goal_status = "found" if action.type == "stop" else "searching"
        self.last_goal_status = goal_status

        self.action_history.append(action.model_dump())
        self.actions_since_expected += 1
        if action.type == "stop":
            self.done = True

        return self._record(turn, DECISION, action, node_id,
                            deviation=deviated, goal_status=goal_status,
                            observation=observation)
