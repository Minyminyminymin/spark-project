"""The eval harness: scripted episodes, the taxonomy/SPL analyzer, and MVD PASS."""

from pathlib import Path

from eval.analyze import analyze_episode, comparison_table, detect_revisit
from eval.episodes import all_episodes, build_episode, EPISODE_SPECS
from eval.run_episode import run_episode


def _load(log_path):
    import json
    return [json.loads(l) for l in Path(log_path).read_text().splitlines() if l.strip()]


def test_mvd_full_episode_hits_all_three_milestones(tmp_path):
    ep = all_episodes()["mvd_full"]
    meta = run_episode(ep, "full", live=False, out_dir=tmp_path)
    result = analyze_episode(_load(meta["log"]), meta)

    sc = result["subtask_completion"]
    assert sc["revisit"] and sc["instruction_followed"] and sc["object_found"], sc
    assert result["stopping_error"] == "none"
    # scenic loop: strictly worse than the shortest path
    assert 0.0 < result["spl"] < 1.0


def test_direct_episode_is_optimal_and_has_no_revisit(tmp_path):
    ep = all_episodes()["direct"]
    meta = run_episode(ep, "full", live=False, out_dir=tmp_path)
    result = analyze_episode(_load(meta["log"]), meta)

    assert result["subtask_completion"]["object_found"] is True
    assert result["subtask_completion"]["revisit"] is False
    assert result["spl"] == 1.0  # took the shortest path


def test_deviation_episode_records_and_recovers(tmp_path):
    ep = all_episodes()["deviation_recover"]
    meta = run_episode(ep, "full", live=False, out_dir=tmp_path)
    result = analyze_episode(_load(meta["log"]), meta)

    pd = result["path_deviation"]
    assert pd["count"] >= 1
    assert pd["recovered_all"] is True
    assert result["subtask_completion"]["object_found"] is True


def test_no_graph_condition_still_runs_and_records_nodes(tmp_path):
    ep = all_episodes()["mvd_full"]
    meta = run_episode(ep, "no-graph", live=False, out_dir=tmp_path)
    records = _load(meta["log"])
    assert meta["condition"] == "no-graph"
    assert any(r["node"] for r in records)  # memory still records nodes


def test_revisit_detection_ignores_consecutive_same_node():
    # same node twice in a row is NOT a revisit (never left)
    recs = [{"turn": 0, "node": "room_A"}, {"turn": 1, "node": "room_A"},
            {"turn": 2, "node": "room_B"}]
    assert detect_revisit(recs)[0] is False
    # leaving and returning IS a revisit
    recs2 = recs + [{"turn": 3, "node": "room_A"}]
    assert detect_revisit(recs2)[0] is True


def test_comparison_table_is_markdown_with_both_conditions(tmp_path):
    results = []
    for ep in all_episodes().values():
        for cond in ("full", "no-graph"):
            meta = run_episode(ep, cond, live=False, out_dir=tmp_path)
            results.append(analyze_episode(_load(meta["log"]), meta))
    table = comparison_table(results)
    assert "| Condition |" in table
    assert "| full |" in table and "| no-graph |" in table


def test_all_specs_build():
    eps = all_episodes()
    assert len(eps) == len(EPISODE_SPECS) >= 3
    for ep in eps.values():
        assert ep["plans"] and ep["place_observations"]
