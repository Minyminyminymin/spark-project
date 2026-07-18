"""Run one scripted eval episode to termination and save its JSONL log + meta.

    python -m eval.run_episode --episode mvd_full --condition full
    python -m eval.run_episode --episode all --condition both --out eval/logs
    python -m eval.run_episode --episode all --condition both --live   # real planner

Conditions:
  full      the planner receives the local map summary (graph memory on).
  no-graph  the planner receives an EMPTY map summary — "only the current
            observation", the ablation. Memory still records nodes/edges; only
            the planner's *input* is stripped, exactly as specified.

--live swaps the scripted planner for the real Qwen planner (perception stays
scripted). That is the only mode in which the ablation can actually diverge,
since a scripted planner ignores the prompt it is given.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.controller import Agent
from app.memory import TopoMap
from app.world.static_photos import StaticPhotoWorld
from eval.episodes import LAYOUT_PATH, all_episodes
from eval.qwen_eval import EvalQwen

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "eval" / "logs"
DEFAULT_MAX_TURNS = 40


class _NoGraphMap(TopoMap):
    """A TopoMap that reports an empty summary — the --no-graph ablation.

    Nodes/edges are still recorded (memory is unchanged); the planner simply
    receives no map context, i.e. only the current observation.
    """

    def summary(self, current_node_id: str) -> dict:
        return {}


def run_episode(episode: dict, condition: str, live: bool, live_call=None,
                out_dir: Path = DEFAULT_OUT, max_turns: int = DEFAULT_MAX_TURNS) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    world = StaticPhotoWorld(LAYOUT_PATH)
    topo_map = _NoGraphMap() if condition == "no-graph" else TopoMap()
    qwen = EvalQwen(
        world,
        episode["place_observations"],
        plans=None if live else episode["plans"],
        live=live,
        live_call=live_call,
    )

    log_path = out_dir / f"{episode['id']}__{condition}.jsonl"
    agent = Agent(world, topo_map, episode["goal"], qwen, log_path)
    records = agent.run(max_turns=max_turns)

    meta = {
        "episode": episode["id"],
        "condition": condition,
        "qwen": "live" if live else "scripted",
        "goal": episode["goal"],
        "object": episode["object"],
        "object_place": episode["object_place"],
        "start_place": episode["start_place"],
        "shortest_moves": episode["shortest_moves"],
        "turns": len(records),
        "perception_calls": qwen.perception_calls,
        "plan_calls": qwen.plan_calls,
        "done": agent.done,
        "log": str(log_path),
    }
    (out_dir / f"{episode['id']}__{condition}.meta.json").write_text(json.dumps(meta, indent=2))
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode", default="all", help="episode id or 'all'")
    ap.add_argument("--condition", default="both", choices=["full", "no-graph", "both"])
    ap.add_argument("--live", action="store_true", help="use the real Qwen planner")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    args = ap.parse_args()

    episodes = all_episodes()
    if args.episode != "all":
        if args.episode not in episodes:
            print(f"unknown episode {args.episode!r}; have: {list(episodes)}")
            return 2
        episodes = {args.episode: episodes[args.episode]}

    conditions = ["full", "no-graph"] if args.condition == "both" else [args.condition]

    live_call = None
    if args.live:
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        from app.qwen_client import call_qwen
        live_call = call_qwen

    for ep in episodes.values():
        for cond in conditions:
            meta = run_episode(ep, cond, args.live, live_call, args.out, args.max_turns)
            print(f"ran {meta['episode']:<18} {cond:<9} | turns={meta['turns']:>2} "
                  f"plan_calls={meta['plan_calls']:>2} done={meta['done']} -> {meta['log']}")
    print(f"\nlogs in {args.out}/ — analyze with:  python -m eval.analyze --logs {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
