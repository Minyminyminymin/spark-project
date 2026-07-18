"""Ingest episode logs, bucket outcomes into the milestone taxonomy, compute an
SPL-style score, assert the MVD, and print the full-vs-ablation comparison table.

    python -m eval.analyze --logs eval/logs

Taxonomy (per episode):
  subtask-completion : which of {revisit, instruction-followed, object-found} hit
  path-deviation     : deviation event count, and whether each was recovered
  stopping-error     : stopped without the object, or never stopped at all
  SPL                : success * shortest / max(taken, shortest)   (moves = path)

MVD (six-node venue) passes when ONE full-system episode log shows all three of:
a recognised revisit, a coach instruction carried out end-to-end, and a hidden
object found. The analyzer asserts this and prints the evidence lines.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# --------------------------------------------------------------------------- #
# Milestone detection (pure functions over the JSONL records + meta)
# --------------------------------------------------------------------------- #


def _nodes(records: list[dict]) -> list:
    return [r.get("node") for r in records]


def detect_revisit(records: list[dict]) -> tuple[bool, str]:
    """A revisit = re-entering a node after having been at a *different* one."""
    seen: set = set()
    first_turn: dict = {}
    prev = object()
    for r in records:
        n = r.get("node")
        if n is None:
            continue
        if n in seen and n != prev:
            return True, (f"turn {r['turn']}: re-entered node {n!r} "
                          f"(first seen at turn {first_turn[n]}) — revisit recognised")
        if n not in seen:
            seen.add(n)
            first_turn[n] = r["turn"]
        prev = n
    return False, "no revisit: no node was re-entered after leaving it"


def _terminal_stop(records: list[dict]) -> dict | None:
    if records and (records[-1].get("action") or {}).get("type") == "stop":
        return records[-1]
    return None


def detect_instruction_followed(records: list[dict], meta: dict) -> tuple[bool, str]:
    """The coach instruction drove a full loop to a self-chosen termination."""
    goal = meta.get("goal") or ""
    stop = _terminal_stop(records)
    if goal and stop is not None and len(records) >= 1:
        reason = (stop.get("action") or {}).get("reason", "")
        return True, (f"instruction {goal!r} executed end-to-end: {len(records)} turns, "
                      f"agent chose to stop at turn {stop['turn']} ({reason!r})")
    if not goal:
        return False, "no coach instruction was set"
    return False, "instruction not completed: episode never reached a self-chosen stop"


def detect_object_found(records: list[dict], meta: dict) -> tuple[bool, str]:
    """goal_status reached 'found' and the run stopped on it."""
    obj = meta.get("object", "")
    stop = _terminal_stop(records)
    if stop is not None and stop.get("goal_status") == "found":
        reason = (stop.get("action") or {}).get("reason", "")
        return True, (f"turn {stop['turn']}: goal_status=found, stopped ({reason!r}) "
                      f"— object {obj!r} found")
    if any(r.get("goal_status") == "found" for r in records):
        return False, f"object {obj!r} seen as found but the run did not stop on it"
    return False, f"object {obj!r} never found (goal_status never reached 'found')"


# --------------------------------------------------------------------------- #
# Taxonomy + SPL
# --------------------------------------------------------------------------- #


def path_deviation(records: list[dict]) -> dict:
    dev_turns = [r["turn"] for r in records if r.get("deviation")]
    # A deviation is "recovered" if the agent kept planning past it (a later
    # non-deviation turn exists) rather than terminating on the deviation.
    recovered = 0
    for t in dev_turns:
        if any(r["turn"] > t and not r.get("deviation") for r in records):
            recovered += 1
    return {
        "count": len(dev_turns),
        "turns": dev_turns,
        "recovered": recovered,
        "recovered_all": len(dev_turns) == 0 or recovered == len(dev_turns),
    }


def stopping_error(records: list[dict], object_found: bool) -> str:
    stop = _terminal_stop(records)
    if stop is None:
        return "no_stop"                       # ran to max_turns without stopping
    if not object_found:
        return "stopped_without_object"        # stopped, but goal not achieved
    return "none"


def spl(records: list[dict], meta: dict, object_found: bool) -> float:
    taken = sum(1 for r in records if (r.get("action") or {}).get("type") == "move")
    shortest = int(meta.get("shortest_moves", 0))
    success = 1.0 if object_found else 0.0
    denom = max(taken, shortest)
    if denom == 0:
        return success
    return success * shortest / denom


def analyze_episode(records: list[dict], meta: dict) -> dict:
    revisit, ev_revisit = detect_revisit(records)
    instr, ev_instr = detect_instruction_followed(records, meta)
    found, ev_found = detect_object_found(records, meta)
    taken = sum(1 for r in records if (r.get("action") or {}).get("type") == "move")
    return {
        "episode": meta["episode"],
        "condition": meta["condition"],
        "subtask_completion": {"revisit": revisit, "instruction_followed": instr,
                               "object_found": found},
        "evidence": {"revisit": ev_revisit, "instruction_followed": ev_instr,
                     "object_found": ev_found},
        "path_deviation": path_deviation(records),
        "stopping_error": stopping_error(records, found),
        "spl": spl(records, meta, found),
        "taken_moves": taken,
        "shortest_moves": int(meta.get("shortest_moves", 0)),
        "turns": len(records),
    }


# --------------------------------------------------------------------------- #
# Loading + reporting
# --------------------------------------------------------------------------- #


def load_runs(logs_dir: Path) -> list[dict]:
    runs = []
    for meta_path in sorted(logs_dir.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text())
        records = [json.loads(line) for line in
                   Path(meta["log"]).read_text().splitlines() if line.strip()]
        runs.append({"meta": meta, "records": records,
                     "result": analyze_episode(records, meta)})
    return runs


def _rate(values: list[bool]) -> float:
    return sum(1 for v in values if v) / len(values) if values else 0.0


def comparison_table(results: list[dict]) -> str:
    by_cond: dict[str, list[dict]] = {}
    for r in results:
        by_cond.setdefault(r["condition"], []).append(r)

    header = ("| Condition | Episodes | Revisit | Instr. followed | Object found | "
              "Mean SPL | Mean deviations | Recovery | Stopping errors |")
    sep = "|" + "|".join(["---"] * 9) + "|"
    rows = [header, sep]
    for cond in ("full", "no-graph"):
        rs = by_cond.get(cond)
        if not rs:
            continue
        n = len(rs)
        revisit = _rate([r["subtask_completion"]["revisit"] for r in rs])
        instr = _rate([r["subtask_completion"]["instruction_followed"] for r in rs])
        found = _rate([r["subtask_completion"]["object_found"] for r in rs])
        mean_spl = sum(r["spl"] for r in rs) / n
        mean_dev = sum(r["path_deviation"]["count"] for r in rs) / n
        tot_dev = sum(r["path_deviation"]["count"] for r in rs)
        tot_rec = sum(r["path_deviation"]["recovered"] for r in rs)
        recov = (tot_rec / tot_dev) if tot_dev else 1.0
        stop_err = sum(1 for r in rs if r["stopping_error"] != "none")
        rows.append(f"| {cond} | {n} | {revisit:.0%} | {instr:.0%} | {found:.0%} | "
                    f"{mean_spl:.2f} | {mean_dev:.2f} | {recov:.0%} | {stop_err} |")
    return "\n".join(rows)


def print_report(runs: list[dict], mvd_episode: str = "mvd_full",
                 mvd_condition: str = "full") -> bool:
    results = [r["result"] for r in runs]

    print("=" * 78)
    print("PER-EPISODE TAXONOMY")
    print("=" * 78)
    for r in results:
        sc = r["subtask_completion"]
        pd = r["path_deviation"]
        print(f"\n[{r['episode']} / {r['condition']}]  turns={r['turns']} "
              f"moves={r['taken_moves']} (shortest={r['shortest_moves']})")
        print(f"  subtask-completion : revisit={sc['revisit']} "
              f"instruction_followed={sc['instruction_followed']} "
              f"object_found={sc['object_found']}")
        print(f"  path-deviation     : count={pd['count']} recovered={pd['recovered']} "
              f"recovered_all={pd['recovered_all']}")
        print(f"  stopping-error     : {r['stopping_error']}")
        print(f"  SPL                : {r['spl']:.3f}")

    print("\n" + "=" * 78)
    print("MVD VALIDATION  (six-node venue)")
    print("=" * 78)
    target = next((r for r in runs
                   if r["meta"]["episode"] == mvd_episode
                   and r["meta"]["condition"] == mvd_condition), None)
    mvd_pass = False
    if target is None:
        print(f"MVD: N/A — no '{mvd_episode}/{mvd_condition}' run found in logs")
    else:
        res = target["result"]
        sc = res["subtask_completion"]
        ev = res["evidence"]
        mvd_pass = all(sc.values())
        print(f"episode: {mvd_episode}  condition: {mvd_condition}\n")
        print(f"  [{'x' if sc['revisit'] else ' '}] revisit recognised")
        print(f"        {ev['revisit']}")
        print(f"  [{'x' if sc['instruction_followed'] else ' '}] coach instruction end-to-end")
        print(f"        {ev['instruction_followed']}")
        print(f"  [{'x' if sc['object_found'] else ' '}] hidden object found")
        print(f"        {ev['object_found']}")
        print()
        if mvd_pass:
            print("MVD: PASS  — all three milestones demonstrated in one full-system episode.")
        else:
            print("MVD: FAIL  — not all three milestones present in the target episode.")

    print("\n" + "=" * 78)
    print("ABLATION: full graph memory  vs  --no-graph (planner sees only current obs)")
    print("=" * 78 + "\n")
    print(comparison_table(results))
    print()
    return mvd_pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs", type=Path, default=Path(__file__).resolve().parent / "logs")
    ap.add_argument("--mvd-episode", default="mvd_full")
    ap.add_argument("--mvd-condition", default="full")
    args = ap.parse_args()

    runs = load_runs(args.logs)
    if not runs:
        print(f"no logs found in {args.logs}/ — run:  python -m eval.run_episode --episode all")
        return 2
    mvd_pass = print_report(runs, args.mvd_episode, args.mvd_condition)
    return 0 if mvd_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
