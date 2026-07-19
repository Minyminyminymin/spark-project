"""FastAPI wrapper around the in-process ScavengeAI agent.

A single agent lives in this process (no DB, no auth, no websockets, no
background threads). The frontend drives it by polling ``GET /state`` and
calling ``POST /tick``. The world is selected by the ``WORLD`` env var
(``static`` uses the bundled photo layout; ``splat`` talks to the Gaussian-splat
engine at ``SPLAT_ENGINE_URL`` — its server must be up when you ``/tick``).

This module only wires the existing controller/memory/world together — it does
not change any agent, memory, or perception logic.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

# Load .env from the project root so QWEN_* vars are available without
# having to export them manually before starting uvicorn.
_env_file = Path(__file__).parent.parent / ".env"  # project root
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.controller import Agent
from app.memory import TopoMap
from app.qwen_client import call_qwen
from app.world.base import Pose, World
from app.world.static_photos import StaticPhotoWorld

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = ROOT / "photos" / "layout.json"
LOG_PATH = Path(os.environ.get("AGENT_LOG", ROOT / "agent_log.jsonl"))
DEFAULT_GOAL = ""


# --------------------------------------------------------------------------- #
# Dependency wiring (the Qwen callable is injectable so tests can supply
# recorded fixtures instead of hitting the network).
# --------------------------------------------------------------------------- #

_qwen_call: Callable[..., str] = call_qwen


def configure_qwen(fn: Callable[..., str]) -> None:
    """Override the Qwen callable used when (re)building the agent."""
    global _qwen_call
    _qwen_call = fn


def _make_world():
    world = os.environ.get("WORLD", "static").lower()
    if world == "static":
        return StaticPhotoWorld(LAYOUT)
    if world == "splat":
        from app.world.splat_client import SplatWorld

        return SplatWorld()
    raise ValueError(f"unknown WORLD={world!r} (expected 'static' or 'splat')")


class _State:
    """Holds the single live agent plus derived, poll-friendly bookkeeping."""

    def __init__(self) -> None:
        self.agent: Optional[Agent] = None
        self.deviation_events: list[dict] = []

    def reset(self) -> None:
        self.agent = Agent(_make_world(), TopoMap(), DEFAULT_GOAL, _qwen_call, LOG_PATH)
        self.deviation_events = []

    def require_agent(self) -> Agent:
        if self.agent is None:
            raise HTTPException(status_code=503, detail="agent is not initialized")
        return self.agent


state = _State()

# Build the singleton at import. Construction does no network I/O (SplatWorld only
# calls the engine on /view and /action), so this succeeds even with the splat
# engine down or misconfigured; any such error surfaces later on /reset or /tick.
try:
    state.reset()
except Exception:
    state.agent = None


app = FastAPI(title="ScavengeAI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:5174"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


class Instruction(BaseModel):
    text: str


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@app.post("/instruction")
def post_instruction(body: Instruction) -> dict:
    """Set/replace the agent's current goal; echo the parsed goal."""
    agent = state.require_agent()
    agent.goal = body.text
    return {"goal": agent.goal}


@app.post("/tick")
def post_tick() -> dict:
    """Advance exactly one agent turn and return that turn's log record."""
    agent = state.require_agent()
    if agent.done:
        last = agent.log_records[-1] if agent.log_records else None
        return {"done": True, "record": last}

    record = agent.step()
    if record and record.get("deviation") and record.get("event"):
        state.deviation_events.append({"turn": record["turn"], "event": record["event"]})
    return record


@app.get("/state")
def get_state() -> dict:
    """Snapshot for the polling frontend."""
    agent = state.require_agent()

    try:
        pose: Any = agent.world.get_current_view().pose.model_dump()
    except Exception:
        pose = None

    nodes = []
    for node_id in agent.topo_map.g.nodes:
        node = agent.topo_map.get_node(node_id)
        nodes.append(
            {
                "id": node_id,
                "label": node.place_label,
                "x": node.x,
                "y": node.y,
                "confidence": node.confidence,
                "visited": node.visited,
                "objects": [o.name for o in node.objects],
            }
        )
    edges = [{"from": u, "to": v} for u, v in agent.topo_map.g.edges()]

    last_turn_type = agent.log_records[-1]["type"] if agent.log_records else None

    return {
        "goal": agent.goal,
        "pose": pose,
        "last_turn_type": last_turn_type,
        "deviation_events": state.deviation_events,
        "commentary": agent.log_records[-50:],
        "graph": {"nodes": nodes, "edges": edges},
        "goal_status": agent.last_goal_status,
    }


@app.post("/reset")
def post_reset() -> dict:
    """Fresh agent + empty graph against the configured world."""
    try:
        state.reset()
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"ok": True, "world": os.environ.get("WORLD", "static")}


# --------------------------------------------------------------------------- #
# Browser-driven agent path — POST /agent/step
#
# The renderer POSTs its own rendered frame + true pose here instead of the
# old World.get_current_view() pull model. The World abstraction is not used
# on this path; a stub is supplied only to satisfy the Agent constructor.
# --------------------------------------------------------------------------- #


class _StubWorld(World):
    """Placeholder world for the browser-driven path; never called."""

    def get_current_view(self):  # pragma: no cover
        raise NotImplementedError("live agent: world not used on this path")

    def execute_action(self, action):  # pragma: no cover
        raise NotImplementedError("live agent: world not used on this path")


LIVE_LOG_PATH = ROOT / "agent_log_live.jsonl"


class _LiveState:
    def __init__(self) -> None:
        self.agent: Optional[Agent] = None

    def reset(self) -> None:
        self.agent = Agent(_StubWorld(), TopoMap(), DEFAULT_GOAL, _qwen_call, LIVE_LOG_PATH)

    def require_agent(self) -> Agent:
        if self.agent is None:
            self.reset()
        return self.agent


live_state = _LiveState()
live_state.reset()


class _PosePayload(BaseModel):
    x: float
    y: float
    z: float = 0.0
    yaw_deg: float = 0.0


class AgentStepRequest(BaseModel):
    image_base64: str
    image_width: int
    image_height: int
    pose: _PosePayload
    goal: Optional[str] = None


class AgentStepResponse(BaseModel):
    action: dict
    turn_type: str
    deviation: bool
    goal_status: str


@app.post("/agent/step", response_model=AgentStepResponse)
def post_agent_step(body: AgentStepRequest) -> AgentStepResponse:
    """Single browser-driven agent turn.

    The browser sends the rendered frame (base64 JPEG/PNG, no data: prefix)
    plus the true pose read from the Three.js scene graph. Returns the next
    action for the renderer to apply directly to player.rig.
    """
    agent = live_state.require_agent()

    # Optional inline goal override for this request.
    if body.goal is not None:
        # New goal after a completed run → reset so the agent starts fresh.
        if agent.done:
            live_state.reset()
            agent = live_state.require_agent()
        agent.goal = body.goal

    # No active goal → idle, do not burn a Qwen call.
    if not agent.goal:
        return AgentStepResponse(
            action={"type": "stop", "reason": "no goal set"},
            turn_type="idle",
            deviation=False,
            goal_status="idle",
        )

    try:
        image_bytes = base64.b64decode(body.image_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid base64: {exc}") from exc

    pose = Pose(x=body.pose.x, y=body.pose.y, z=body.pose.z, yaw_deg=body.pose.yaw_deg)
    record = agent.step_from_frame(image_bytes, body.image_width, body.image_height, pose)

    if record is None:
        # Agent was already done before this tick (done flag set by prior turn).
        # This path is now only hit if the agent finished mid-session without a
        # new goal being supplied. Return stop so the browser loop halts cleanly.
        return AgentStepResponse(
            action={"type": "stop", "reason": agent.last_goal_status or "done"},
            turn_type="idle",
            deviation=False,
            goal_status=agent.last_goal_status or "done",
        )

    return AgentStepResponse(
        action=record["action"],
        turn_type=record["type"],
        deviation=bool(record.get("deviation", False)),
        goal_status=record.get("goal_status", "searching"),
    )


@app.post("/agent/reset")
def post_agent_reset() -> dict:
    """Fresh live agent (clears topo-map and turn counter)."""
    live_state.reset()
    return {"ok": True}
