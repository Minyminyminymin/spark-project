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

from app.localizer import localize
from app.memory import TopoMap
from app.perception import perceive
from app.planner import plan
from app.world.base import MoveAction, TurnAction, World


def _goal_visible(goal: str, observation: Any) -> bool:
    """Return True if any perceived object/landmark matches the goal target.

    Strips navigation verbs so 'walk to the sofa' matches 'sofa', 'couch',
    'settee', 'loveseat' etc. via substring matching on the full description.
    """
    STOP = {"walk", "go", "to", "the", "find", "get", "reach", "move", "a", "an"}
    keywords = {w.lower() for w in goal.split() if w.lower() not in STOP and len(w) > 2}
    if not keywords:
        return False
    items = list(getattr(observation, "objects", [])) + list(getattr(observation, "landmarks", []))
    for item in items:
        text = f"{getattr(item, 'name', '')} {getattr(item, 'description', '')}".lower()
        if any(kw in text for kw in keywords):
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

        # Always re-perceive every step — the FSM inside _run_decision_from_frame
        # handles scanning vs approaching without any blind ROUTINE moves.
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

        FSM logic (no planner call when goal is not visible):
          SCANNING: goal not in frame → turn 90°.
                    After 4 turns (full 360°) with no sighting → move 1m to
                    a new position and reset the scan counter.
          APPROACHING: goal visible → call planner with spatial hints so it
                    can turn toward it and move. Stop when proximity is close.
        """
        observation = perceive(image_bytes, width, height, self.qwen_call, goal=self.goal)  # Qwen #1
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

        # ── APPROACHING: goal IS visible → call planner for precise nav ───────
        self.turns_since_goal_visible = 0
        self.consecutive_scan_turns = 0
        self._scan_direction = 1

        summary = self.topo_map.summary(node_id)
        result = plan(self.goal, observation, summary, self.action_history[-6:], self.qwen_call)

        self.action_queue = list(result.action_queue)
        self.expected_next_node = result.expected_next_node
        self.plan_origin_node = node_id
        self.actions_since_expected = 0
        self.last_goal_status = result.goal_status
        self.consecutive_routine_turns = 0

        action = self.action_queue.pop(0)
        self.action_history.append(action.model_dump())
        self.actions_since_expected += 1
        if action.type == "stop" or result.goal_status == "found":
            self.done = True

        return self._record(turn, DECISION, action, node_id,
                            deviation=deviated, goal_status=result.goal_status,
                            observation=observation, plan=result)
