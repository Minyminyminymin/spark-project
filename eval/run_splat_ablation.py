"""Live full-vs-no-graph ablation on the splat world.

The static world's abstract frontiers stall the LLM planner, so the ablation
can't manifest there. On the splat world the planner provably navigates real
first-person frames, so we run the ablation here instead.

Perception AND planning are the real Qwen client. Perception (the expensive VL
call) is almost always a cache hit from earlier splat runs, so the live cost is
essentially the text-only plan calls. The only thing the two conditions vary is
whether the planner receives the map summary:

    full      TopoMap.summary(...)   -> real local map memory
    no-graph  {}                     -> only the current observation (ablation)

Splat has no known layout, so there is no true shortest path: we report a
steps-to-find efficiency proxy instead of SPL, plus the same taxonomy buckets.

    python -m eval.run_splat_ablation            # 3 goals x 2 conditions, max 10 turns
"""

from __future__ import annotations

import argparse
import threading
from pathlib import Path

from app.controller import Agent
from app.memory import TopoMap
from app.world.splat_client import SplatWorld
from eval.analyze import (detect_instruction_followed, detect_object_found,
                          detect_revisit, path_deviation, stopping_error)
from eval.run_episode import _NoGraphMap

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "eval" / "logs_splat"

GOALS = [
    ("sofa", "find the sofa or couch"),
    ("doorway", "find the doorway or opening to another room"),
    ("passage", "find a tunnel or dark passage leading deeper into the space"),
]


def _steps_to_found(records: list[dict]) -> int | None:
    """Number of move actions up to and including the turn the object was found."""
    moves = 0
    for r in records:
        act = r.get("action") or {}
        if act.get("type") == "move":
            moves += 1
        if r.get("goal_status") == "found":
            return moves
    return None


def run_one(name: str, goal: str, condition: str, base_url: str, max_turns: int) -> dict:
    world = SplatWorld(base_url=base_url)
    world.reset()  # home pose (stub supports /reset)
    topo_map = _NoGraphMap() if condition == "no-graph" else TopoMap()

    from app.qwen_client import call_qwen  # live for BOTH perception and planning
    OUT.mkdir(parents=True, exist_ok=True)
    log_path = OUT / f"{name}__{condition}.jsonl"
    agent = Agent(world, topo_map, goal, call_qwen, log_path)
    completed = True
    error = None
    try:
        agent.run(max_turns=max_turns)
    except Exception as exc:  # e.g. HTTP 402 credit depletion mid-run
        completed = False
        error = f"{type(exc).__name__}: {str(exc)[:120]}"
    records = agent.log_records  # whatever completed before any error

    found, _ = detect_object_found(records, {"object": goal})
    revisit, _ = detect_revisit(records)
    instr, _ = detect_instruction_followed(records, {"goal": goal})
    pd = path_deviation(records)
    moves = sum(1 for r in records if (r.get("action") or {}).get("type") == "move")
    nodes = {r["node"] for r in records if r.get("node")}
    return {
        "condition": condition, "goal": goal,
        "completed": completed, "error": error,
        "found": found, "revisit": revisit, "instruction_followed": instr,
        "turns": len(records), "moves": moves, "nodes": len(nodes),
        "deviations": pd["count"], "recovered": pd["recovered"],
        "stopping_error": stopping_error(records, found),
        "steps_to_found": _steps_to_found(records),
    }


def _rate(vals):
    return sum(1 for v in vals if v) / len(vals) if vals else 0.0


def table(results: list[dict]) -> str:
    by = {}
    for r in results:
        by.setdefault(r["condition"], []).append(r)
    header = ("| Condition | Episodes (done/total) | Found | Revisit | Mean nodes | "
              "Mean turns | Mean moves | Mean devs | Recovery | Stop-errors | "
              "Mean steps-to-find |")
    rows = [header, "|" + "|".join(["---"] * 11) + "|"]
    for cond in ("full", "no-graph"):
        allrs = by.get(cond)
        if not allrs:
            continue
        rs = [r for r in allrs if r.get("completed")]  # aggregate over completed only
        total = len(allrs)
        n = len(rs)
        if n == 0:
            rows.append(f"| {cond} | 0/{total} | — | — | — | — | — | — | — | — | — |")
            continue
        stf = [r["steps_to_found"] for r in rs if r["steps_to_found"] is not None]
        tot_dev = sum(r["deviations"] for r in rs)
        tot_rec = sum(r["recovered"] for r in rs)
        rows.append(
            f"| {cond} | {n}/{total} | {_rate([r['found'] for r in rs]):.0%} | "
            f"{_rate([r['revisit'] for r in rs]):.0%} | "
            f"{sum(r['nodes'] for r in rs)/n:.1f} | {sum(r['turns'] for r in rs)/n:.1f} | "
            f"{sum(r['moves'] for r in rs)/n:.1f} | {tot_dev/n:.2f} | "
            f"{(tot_rec/tot_dev if tot_dev else 1.0):.0%} | "
            f"{sum(1 for r in rs if r['stopping_error'] != 'none')} | "
            f"{(sum(stf)/len(stf) if stf else float('nan')):.1f} |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=5210)
    ap.add_argument("--max-turns", type=int, default=10)
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    import scripts.splat_stub as stub
    srv = stub.build_server(args.port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{args.port}/agent"
    print(f"splat stub up: {len(srv.frames)} ego frames on :{args.port}\n")

    results = []
    for name, goal in GOALS:
        for cond in ("full", "no-graph"):
            r = run_one(name, goal, cond, base_url, args.max_turns)
            results.append(r)
            flag = "ok" if r["completed"] else f"INCOMPLETE ({r['error']})"
            print(f"{goal[:34]:<34} {cond:<9} | found={str(r['found']):<5} "
                  f"turns={r['turns']:>2} moves={r['moves']:>2} nodes={r['nodes']} "
                  f"dev={r['deviations']} stop={r['stopping_error']} | {flag}")

    srv.shutdown()
    print("\n" + "=" * 78)
    print("SPLAT-WORLD ABLATION: full graph memory vs --no-graph (live planner)")
    print("=" * 78 + "\n")
    print(table(results))
    print("\n(splat has no known layout -> no true SPL; 'steps-to-find' is the "
          "moves-until-found efficiency proxy. NaN = not found in any episode.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
