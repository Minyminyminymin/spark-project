"""Run the ScavengeAI agent in StaticPhotoWorld and print its JSONL log.

By default this runs OFFLINE against the recorded Qwen fixtures in
``tests/fixtures/agent_scenario.json`` (no credentials needed) — it demonstrates
the full policy: a routine turn with zero Qwen calls, a forced re-plan after an
injected wrong-node situation, and termination with goal_status="found".

    python scripts/run_agent.py                 # offline demo (recorded fixtures)
    python scripts/run_agent.py --live "<goal>" # real Qwen (needs .env creds)
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.controller import Agent  # noqa: E402
from app.memory import TopoMap  # noqa: E402
from app.world.static_photos import StaticPhotoWorld  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LAYOUT = ROOT / "photos" / "layout.json"
SCENARIO = ROOT / "tests" / "fixtures" / "agent_scenario.json"


class _ScriptedQwen:
    """Recorded-fixture Qwen: image bytes -> perception, text-only -> plan."""

    def __init__(self, perception, plan):
        self._perception = list(perception)
        self._plan = list(plan)

    def __call__(self, prompt, image_bytes, json_mode=True):
        return self._plan.pop(0) if image_bytes is None else self._perception.pop(0)


def main() -> int:
    live = len(sys.argv) > 1 and sys.argv[1] == "--live"
    log_path = ROOT / "agent_log.jsonl"

    if live:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        from app.qwen_client import call_qwen

        goal = sys.argv[2] if len(sys.argv) > 2 else "find the red mug"
        qwen = call_qwen
    else:
        scenario = json.loads(SCENARIO.read_text())
        goal = scenario["goal"]
        qwen = _ScriptedQwen(scenario["perception"], scenario["plan"])

    agent = Agent(StaticPhotoWorld(LAYOUT), TopoMap(), goal, qwen, log_path)
    records = agent.run(max_turns=50)

    print(f"goal: {goal!r}   (mode: {'live' if live else 'offline fixtures'})")
    print(f"log:  {log_path}\n")
    for r in records:
        tag = "DEVIATION " if r["deviation"] else ""
        print(
            f"turn {r['turn']:>2} | {r['type']:<8} | {tag}{r['goal_status']:<9} "
            f"| node={r['node']} | action={r['action']}"
        )
        if r["event"]:
            print(f"          ↳ {r['event']}")
    print(f"\nfinished: done={agent.done}, final goal_status={records[-1]['goal_status']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
