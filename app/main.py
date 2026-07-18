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

import os
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.controller import Agent
from app.memory import TopoMap
from app.qwen_client import call_qwen
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
