"""ScavengeAI coach frontend.

A thin Streamlit client over the FastAPI backend. The human is a *coach*: they
type natural-language goals (chat only — no direct movement controls, by
design), press Step/Auto to advance the agent, and watch the topological map
grow and fade with confidence.

It talks to the backend over HTTP only:
    POST /instruction {"text": str}   -> {"goal": str}
    POST /tick                         -> one turn's log record
    GET  /state                        -> snapshot (see STATE_SHAPE below)
    POST /reset                        -> fresh agent + empty graph

STATE_SHAPE (GET /state):
    {
      "goal": str,
      "pose": {"x","y","z","yaw_deg"} | null,
      "last_turn_type": "decision"|"routine"|null,
      "deviation_events": [{"turn": int, "event": str}],
      "commentary": [ {turn,type,action,node,deviation,goal_status,event}, ... ],
      "graph": {"nodes":[{id,label,x,y,confidence,visited,objects}],
                "edges":[{from,to}]},
      "goal_status": str
    }

Run it with the backend up:
    WORLD=static uvicorn app.main:app --port 8000
    streamlit run ui/streamlit_app.py
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import requests

DEFAULT_API = os.environ.get("SCAVENGE_API", "http://localhost:8000")
AUTO_INTERVAL_SECONDS = 2.0
STALE_THRESHOLD = 0.5
TERMINAL_STATUSES = {"found", "stuck"}


# --------------------------------------------------------------------------- #
# Backend client (pure HTTP; no Streamlit here)
# --------------------------------------------------------------------------- #


def get_state(base_url: str) -> dict:
    resp = requests.get(f"{base_url}/state", timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_instruction(base_url: str, text: str) -> dict:
    resp = requests.post(f"{base_url}/instruction", json={"text": text}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def post_tick(base_url: str) -> dict:
    resp = requests.post(f"{base_url}/tick", timeout=30)
    resp.raise_for_status()
    return resp.json()


def post_reset(base_url: str) -> dict:
    resp = requests.post(f"{base_url}/reset", timeout=10)
    resp.raise_for_status()
    return resp.json()


# --------------------------------------------------------------------------- #
# Pure presentation helpers (testable without Streamlit)
# --------------------------------------------------------------------------- #


def node_rgba(confidence: float) -> tuple[float, float, float, float]:
    """Teal shaded by confidence; stale (<0.5) nodes fade to a faint gray."""
    conf = max(0.0, min(1.0, confidence))
    if conf < STALE_THRESHOLD:
        return (0.60, 0.60, 0.62, 0.30)  # visibly faded
    return (0.12, 0.53, 0.53, 0.40 + 0.55 * conf)


def action_summary(action: Optional[dict]) -> str:
    if not action:
        return "—"
    kind = action.get("type")
    if kind == "move":
        return f"move {action.get('distance')}"
    if kind == "turn":
        return f"turn {action.get('degrees')}°"
    if kind == "stop":
        return f"stop ({action.get('reason')})"
    return str(action)


def current_node_id(state: dict) -> Optional[str]:
    """The node from the most recent turn record, if any."""
    commentary = state.get("commentary") or []
    for record in reversed(commentary):
        if record.get("node"):
            return record["node"]
    return None


def build_map_figure(graph: dict, current_node: Optional[str] = None):
    """Render the topological graph at stored (x, y), shaded by confidence."""
    fig, ax = plt.subplots(figsize=(6.5, 6.5))
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if not nodes:
        ax.text(0.5, 0.5, "no places mapped yet", ha="center", va="center",
                fontsize=12, color="#888888")
        ax.axis("off")
        return fig

    g = nx.DiGraph()
    pos: dict[str, tuple[float, float]] = {}
    for n in nodes:
        g.add_node(n["id"])
        pos[n["id"]] = (float(n["x"]), float(n["y"]))
    for e in edges:
        if e["from"] in pos and e["to"] in pos:
            g.add_edge(e["from"], e["to"])

    fills, borders, widths = [], [], []
    for n in nodes:
        fills.append(node_rgba(n.get("confidence", 1.0)))
        if n["id"] == current_node:
            borders.append("#d62728")
            widths.append(3.5)  # highlight the current node
        else:
            borders.append("#33333366")
            widths.append(1.0)

    if g.number_of_edges():
        nx.draw_networkx_edges(
            g, pos, ax=ax, edge_color="#9aa0a6", width=1.4,
            arrows=True, arrowsize=12, node_size=1200,
        )
    nx.draw_networkx_nodes(
        g, pos, nodelist=[n["id"] for n in nodes], node_color=fills,
        edgecolors=borders, linewidths=widths, node_size=1200, ax=ax,
    )
    nx.draw_networkx_labels(
        g, pos, {n["id"]: n.get("label", n["id"]) for n in nodes},
        font_size=8, ax=ax,
    )

    # Badge nodes that hold found objects.
    for n in nodes:
        objs = n.get("objects") or []
        if objs:
            x, y = pos[n["id"]]
            ax.annotate(
                "◆ " + ", ".join(objs), (x, y),
                textcoords="offset points", xytext=(0, 20), ha="center",
                fontsize=8, color="#8a6d00",
                bbox=dict(boxstyle="round,pad=0.25", fc="#ffe680", ec="#d4af37", lw=0.8),
            )

    ax.set_title("Topological map  ·  faded = stale (conf < 0.5)", fontsize=10)
    ax.margins(0.18)
    ax.set_aspect("equal", adjustable="datalim")
    ax.axis("off")
    return fig


# --------------------------------------------------------------------------- #
# Streamlit UI
# --------------------------------------------------------------------------- #


def render_app() -> None:
    import streamlit as st

    st.set_page_config(page_title="ScavengeAI Coach", layout="wide")
    st.session_state.setdefault("auto", False)
    st.session_state.setdefault("chat", [])          # coach's typed instructions
    st.session_state.setdefault("api", DEFAULT_API)

    with st.sidebar:
        st.header("Connection")
        st.session_state.api = st.text_input("Backend URL", st.session_state.api)
    base = st.session_state.api

    # Poll fresh state every rerun.
    try:
        state = get_state(base)
    except Exception as exc:  # backend down / unreachable
        st.error(f"Cannot reach backend at {base}: {exc}")
        st.info("Start it with:  `WORLD=static uvicorn app.main:app --port 8000`")
        st.stop()

    goal = state.get("goal") or ""
    status = state.get("goal_status") or "—"
    terminal = status in TERMINAL_STATUSES

    st.title("🔦 ScavengeAI — Coach")
    top = st.columns([3, 1, 1, 1])
    top[0].markdown(f"**Goal:** {goal or '_none yet_'}  \n**Status:** `{status}`  ·  "
                    f"last turn: `{state.get('last_turn_type')}`")
    if top[1].button("Step", use_container_width=True, disabled=terminal):
        _safe_tick(st, base)
        st.rerun()
    st.session_state.auto = top[2].toggle("Auto", value=st.session_state.auto, disabled=terminal)
    if top[3].button("Reset", use_container_width=True):
        try:
            post_reset(base)
        except Exception as exc:
            st.warning(f"Reset failed: {exc}")
        st.session_state.chat = []
        st.session_state.auto = False
        st.rerun()

    left, right = st.columns([1, 1], gap="large")

    # ---- Left: coach chat + agent feed ---------------------------------- #
    with left:
        st.subheader("Coach chat")

        # Prominent deviation banner.
        for dev in state.get("deviation_events") or []:
            st.warning(f"⚠️ Turn {dev['turn']}: {dev['event']}")

        feed = st.container(height=460)
        with feed:
            for msg in st.session_state.chat:
                with st.chat_message("user"):
                    st.write(msg)
            for rec in state.get("commentary") or []:
                with st.chat_message("assistant"):
                    st.markdown(
                        f"**Turn {rec['turn']}** · `{rec['type']}` · node "
                        f"`{rec.get('node')}` · {action_summary(rec.get('action'))} · "
                        f"_{rec.get('goal_status')}_"
                    )
                    if rec.get("deviation") and rec.get("event"):
                        st.warning(f"⚠️ {rec['event']}")

        prompt = st.chat_input("Coach the agent (e.g. 'explore and find the hidden object')")
        if prompt:
            try:
                post_instruction(base, prompt)
                st.session_state.chat.append(prompt)
            except Exception as exc:
                st.warning(f"Instruction failed: {exc}")
            st.rerun()

    # ---- Right: the map ------------------------------------------------- #
    with right:
        st.subheader("Map")
        if terminal:
            st.success(f"Goal status: {status}")
        fig = build_map_figure(state.get("graph") or {}, current_node=current_node_id(state))
        st.pyplot(fig, use_container_width=True)
        plt.close(fig)

    # ---- Auto loop: tick every ~2s, then rerun -------------------------- #
    if st.session_state.auto and not terminal:
        time.sleep(AUTO_INTERVAL_SECONDS)
        _safe_tick(st, base)
        st.rerun()


def _safe_tick(st, base: str) -> None:
    try:
        post_tick(base)
    except Exception as exc:
        st.warning(f"Tick failed: {exc}")


if __name__ == "__main__":
    render_app()
