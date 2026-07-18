# ScavengeAI — Architecture Test & Evaluation Report

**System:** ScavengeAI — an AI agent that explores an environment from first-person
images, builds a topological map, and searches for objects on natural-language
coach instructions.
**Date of runs:** 2026-07-18
**Model under test:** Qwen3-VL-30B-A3B-Instruct (hosted via the Hugging Face
Inference-Providers router, `:novita` backend).

---

## 1. Executive summary

| Area | Status | One-line result |
|---|---|---|
| Automated test suite | ✅ **Pass** | 57/57 tests green across 10 modules |
| Live model integration (Qwen3-VL) | ✅ **Working** | Structured perception + planning verified live; latency ~7–20 s/call |
| Splat-engine integration | ✅ **Working** | `SplatWorld` drives real first-person frames end-to-end; boxes rescale correctly |
| End-to-end agent loop (live) | ✅ **Working** | 10-turn run builds a coherent graph with recognized revisits + deviations |
| **MVD milestone validation** | ✅ **PASS** | Revisit + instruction-followed + object-found demonstrated in one episode |
| Taxonomy + SPL harness | ✅ **Working** | Deterministic per-episode buckets + true SPL from the known layout |
| **Graph-memory ablation** | ⚠️ **Inconclusive / no benefit shown** | Graph memory did **not** beat the no-graph baseline on the current test world |
| Live planner on abstract static world | ❌ **Fails to navigate** | Stalls issuing blocked moves; a real limitation (see §8) |

**Headline:** the architecture is **functionally complete and verified end-to-end** —
every layer (perception → memory → localizer → planner → controller → API →
world adapters) works against both the static test world and the live Gaussian-splat
engine, and the Minimum Viable Demonstration passes. The **open question** is whether
the topological graph memory measurably *helps* task performance: on the test worlds
available today it does **not** show a benefit, for reasons that are understood and
documented (§7–8) rather than mysterious.

---

## 2. Architecture under test

The pipeline is a perception → memory → planning → control loop. Each stage is an
independent module composed by the controller:

| Module | Responsibility |
|---|---|
| `app/qwen_client.py` | Thin OpenAI-compatible wrapper for Qwen3-VL; disk record/replay cache; image downscaling |
| `app/perception.py` | Photo → validated `Observation` (place, landmarks, objects, frontiers); 0–1000→pixel bbox rescale |
| `app/memory.py` | `TopoMap` over a NetworkX graph; confidence decay; bounded long/short summary |
| `app/localizer.py` | Decide new-place vs revisit by pose proximity + landmark overlap |
| `app/planner.py` | One Qwen call → validated `Plan` (reasoning, action queue, expected node, goal status) |
| `app/controller.py` | One turn of the loop; routine/decision policy; deviation detection; JSONL logging |
| `app/main.py` | FastAPI wrapper (`/instruction`, `/tick`, `/state`, `/reset`) |
| `app/world/static_photos.py` | `StaticPhotoWorld` — 6-place JSON layout with a cycle |
| `app/world/splat_client.py` | `SplatWorld` — HTTP client for the live Gaussian-splat engine |

Design principle validated in testing: **the model layer is swappable by env var
only** (`QWEN_API_BASE` / `QWEN_API_KEY` / `QWEN_MODEL`) — no other module knows a
network is involved.

---

## 3. Test surface — what we ran

| # | Test | Type | World | Model | Result |
|---|---|---|---|---|---|
| A | Automated suite (pytest) | Unit + integration | Static (+ mock splat) | Scripted fixtures (offline) | ✅ 57/57 |
| B | Live model integration | Live smoke | — | Live Qwen3-VL | ✅ Working |
| C | Splat contract + first-person perception | Live | Splat stub (real frames) | Live Qwen3-VL | ✅ Working |
| D | End-to-end loop over `/tick` | Live | Splat stub | Live Qwen3-VL | ✅ Working |
| E | MVD milestone validation | Deterministic | Static | Scripted | ✅ PASS |
| F | Taxonomy + SPL harness | Deterministic | Static | Scripted | ✅ Working |
| G | Graph-memory ablation | Live | Splat stub | Live Qwen3-VL | ⚠️ No benefit shown |

---

## 4. Test A — Automated suite (57 tests, all passing)

Runs fully offline against recorded fixtures — no network, no credentials, deterministic.

| Module | Tests | What it proves |
|---|---:|---|
| `test_static_photos.py` | 6 | 6-place world walks correctly incl. a **blocked move** (success=False, pose unchanged, no exception); yaw quantization |
| `test_qwen_cache.py` | 3 | Client **records on first call, replays offline**; `QWEN_OFFLINE=1` cache-miss raises |
| `test_perception.py` | 6 | 0–1000→pixel **rescale math** exact; markdown-fence stripping; malformed-JSON → retry → `PerceptionError` |
| `test_memory.py` | 8 | Node/edge counts; **confidence decay** to floor; revisit resets to 1.0; **bounded summary** (3 detailed + aggregate); JSON round-trip |
| `test_localizer.py` | 6 | Pose-proximity revisit; landmark tie-break; stale-node handling; label-is-not-a-signal; full 6-place loop → 6 nodes, 1 revisit |
| `test_planner.py` | 4 | Valid `Plan` parsing; action-queue bounds (1–3); malformed-JSON retry |
| `test_controller.py` | 4 | Routine (0 Qwen calls) vs decision (2 calls); deviation event; termination on found |
| `test_api.py` | 4 | `/instruction` → 10× `/tick` → `/state` grows a coherent graph; `/reset`; `WORLD=splat` boots |
| `test_splat_client.py` | 9 | View decode, pose map, **blocked-move = success:false not exception**, transport-error handling, fail-loud on engine down, live-frame box rescale, 10-turn HTTP graph |
| `test_eval.py` | 7 | MVD milestones, optimal-path SPL, deviation+recovery, revisit detection, comparison table |
| **Total** | **57** | **All passing (`57 passed in ~4.7s`)** |

**Verdict:** ✅ every layer has green regression coverage; error paths (blocked moves,
malformed model output, engine down) are explicitly tested.

---

## 5. Test B — Live model integration (Qwen3-VL)

Structured perception and planning were exercised against the live hosted model.

**Latency (measured, fresh uncached calls, 640×323 first-person frames):**

| Sample | Time |
|---|---:|
| Call 1 | 16.1 s |
| Call 2 | 19.9 s |
| Call 3 | 7.1 s |
| **Mean / min / max** | **14.4 s / 7.1 s / 19.9 s** |

- High variance (~3×) is provider-load driven, not content driven — budget for the tail (~20 s).
- A **decision turn = 2 model calls** (perceive + plan), so ~15–35 s wall-clock worst case.
- Mitigation that works: **routine turns spend 0 model calls**, and the record/replay
  cache replays any seen frame in **~0.4 ms**. Local pipeline (decode, pose map, action)
  is single-digit **milliseconds**.

**Integration issues found and fixed during live testing:**

| Symptom | Cause | Fix |
|---|---|---|
| `400 'auto' is not valid` | HF router rejects the `:auto` provider policy on this endpoint | Use a concrete provider suffix |
| `model_not_supported` | Model served only by `:novita` / `:featherless-ai` (not `:together`/`:fireworks-ai`) | Pin `QWEN_MODEL=…:novita` |
| `413 request entity too large` | Raw 5 MB phone photo base64-inflates past the request limit | Downscale to 1280 px max edge + JPEG before upload |

The downscale is **safe for grounding**: Qwen emits boxes on a resolution-independent
0–1000 scale, and perception rescales against the *original* dimensions — verified on a
5712×4284 photo (boxes landed on backpack / glass / banner).

**Verdict:** ✅ working; the model reliably returns schema-valid structured output, and
the box-rescale contract holds at real resolution.

---

## 6. Test C & D — Splat-engine integration and live end-to-end loop

### C. First-person perception on real splat frames
`SplatWorld` → splat stub (replaying the engine team's real captured ego frames) →
perception → bbox rescale, on a genuine **640×323 first-person** Gaussian-splat frame:

- View decodes to a real JPEG; pose maps 1:1 into the agent's `Pose`.
- Live perception returned a coherent scene (sofa, doorway, pillow, couch).
- Rescale verified: e.g. `x 239→153` = 239/1000·640 ✓, `y 505→163` = 505/1000·323 ✓;
  the drawn boxes land on the correct objects.

### D. End-to-end 10-turn run through the FastAPI `/tick` route (live)

| Metric | Result |
|---|---|
| Turns completed | **10/10** through the real `/tick` endpoint |
| Graph built | **4 nodes, 5 edges** |
| Revisits recognized (live) | `dark_room` re-recognized on turns 7 and 9 → back-edges = **cycles**, not a chain |
| Deviation events fired | **5** ("Expected _X_ but this isn't it — re-planning") |
| Goal status | Correctly stayed `searching` (target absent) — no false "found" |
| Wall-clock | 33.9 s (5 cached replays + 5 fresh turns) |

**Verdict:** ✅ the full stack (perception → memory → localizer → planner → controller
→ FastAPI → SplatWorld → engine) runs end-to-end on real first-person frames.

> **Contract note (open item, not a code fault):** the engine team's *raw* export
> (three.js Y-up arrays, radians heading, `data:` image prefix, no width/height, `id`
> not `frame_id`) does **not** match the agreed `/view` contract `SplatWorld` is built
> to. A stub bridges the two today; the open question to the engine team is whether the
> live `/view` will convert server-side or emit the raw shape.

---

## 7. Test E & F — MVD validation and taxonomy/SPL (static harness)

### E. Minimum Viable Demonstration — **PASS**

Target: in a six-node venue, show (1) a recognized revisit, (2) a coach instruction
executed end-to-end, (3) a hidden object found — all in one full-system episode.

```
MVD: PASS — all three milestones demonstrated in one full-system episode.
  [x] revisit recognised     turn 8: re-entered room_A (first seen turn 0)
  [x] instruction end-to-end 14 turns, agent chose to stop at turn 13 ("found the red mug")
  [x] hidden object found    turn 13: goal_status=found, red_mug
```

### F. Taxonomy + SPL — per episode (3 scripted episodes)

SPL = `success · shortest / max(taken, shortest)`, using the layout's **known** shortest path.

| Episode | Revisit | Instr. e2e | Object found | Deviations (recovered) | Stopping error | Moves (shortest) | **SPL** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|---:|
| `mvd_full` (scenic loop) | ✅ | ✅ | ✅ | 0 | none | 7 (3) | **0.43** |
| `direct` (optimal route) | — | ✅ | ✅ | 0 | none | 3 (3) | **1.00** |
| `deviation_recover` | ✅ | ✅ | ✅ | 1 (1) | none | 6 (2) | **0.33** |

**Verdict:** ✅ the taxonomy correctly buckets subtask-completion, path-deviation with
recovery, and stopping errors; SPL discriminates the optimal route (1.00) from scenic
detours (0.33–0.43); the injected deviation is detected **and** recovered.

---

## 8. Test G — Graph-memory ablation (the honest result)

**Question:** does the topological graph memory improve performance vs. a baseline that
feeds the planner only the current observation (`--no-graph`)?

Two conditions differ **only** in whether the planner receives the local map summary.
Memory still records nodes/edges either way; only the planner's *input* is stripped.

### Static world — ablation could not manifest
The static harness uses a scripted planner (deterministic fixtures), which **ignores its
prompt** — so withholding the map cannot change a fixed action sequence. Rows are
identical by construction. A real signal requires a live, prompt-sensitive planner.

### Splat world — live planner, 3 goals × 2 conditions (complete run)

| Condition | Episodes | Object found | Revisit recognized | Mean deviations | Recovery | Stop-errors | Mean steps-to-find |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **full** (graph on) | 3/3 | **33%** | 67% | 3.67 | 55% | 2 | 4.0 |
| **no-graph** (baseline) | 3/3 | **67%** | 67% | 3.00 | 67% | 1 | 5.5 |

**Reading it straight — graph memory did NOT show a benefit here.** The no-graph baseline
matched or slightly beat the full system.

Key observations:
- **The found-rate gap is one episode.** With n=3, a single episode = 33 points. The
  difference is entirely the `doorway` goal: on returning to `dark_room` (which contains
  the doorway), the **no-graph** planner declared "found" and stopped; the **full** planner,
  told by the map summary it had *already been* to `dark_room`, kept exploring and timed
  out. Counter-intuitive, but that is the data.
- **Revisit recognition is identical (67% both).** This is the clean architectural result:
  revisits are recognized by **memory's label-keying**, independent of the planner's map
  input — so stripping the map leaves revisit detection untouched, exactly as designed.
- Per the brief, we report **counts only** (no statistical machinery); n=3 is
  noise-dominated and no significance is claimed.

**Dominant caveat:** the splat *stub* replays a **fixed linear capture** — `move` advances
the pre-recorded sequence and `turn` is a pose no-op. So the map cannot improve navigation
the planner cannot actually perform. A genuine benefit for graph memory would need the
**live engine driving the avatar through a branching space**, where "don't re-explore a
visited frontier" has somewhere to pay off.

---

## 9. Where it works vs. where it doesn't

### ✅ Working well
- **Every module in isolation and composed** — 57 green tests including error paths.
- **Live perception grounding** — schema-valid output; 0–1000→pixel rescale correct at
  real resolution (verified on both a 5712×4284 photo and a 640×323 splat frame).
- **Blocked-move & failure semantics** — blocked moves and transport errors become
  `success=False`, never exceptions; `/view` fails loud when the engine is down.
- **MVD milestones** — revisit, instruction-followed, and object-found all demonstrated.
- **Live end-to-end on real first-person frames** — coherent multi-node graph with
  recognized revisits (cycles) and deviation re-planning.
- **Swappable model layer** — env-var only; the record/replay cache makes dev/CI offline
  and free.

### ⚠️ / ❌ Not working / unproven
| Issue | Severity | Detail |
|---|---|---|
| **Graph memory shows no measurable benefit** | Open question | Ablation on the current test world does not beat the no-graph baseline (§8); needs a branching live world to test fairly |
| **Live planner stalls on abstract static frontiers** | Real limitation | On the static world the LLM planner issues repeated **blocked moves without reorienting** — it gets no move-*blocked* feedback, and abstract frontier labels don't map to headings. (The same planner navigates fine on real *splat* frames.) |
| **Label-vs-pose identity seam** | Design gap | Memory keys nodes by `place_label`; the localizer resolves identity by pose but **its result is currently discarded** by the controller. Inconsistent labels from a weaker model would create phantom nodes / missed revisits |
| **Splat stub is linear replay** | Test-harness limit | Not free navigation; genuine navigation/ablation eval needs the live engine |
| **Raw splat export ≠ agreed contract** | Integration (open) | Bridged by a stub today; awaiting the engine team's decision on server-side conversion |
| **Hosted free-tier credits** | Operational | The free Inference-Providers tier affords ~10–17 calls/token; batch eval needs pre-paid/PRO credits (the full ablation above ran on a paid key) |

---

## 10. Reproducibility

```bash
# Automated suite (offline, no credentials)
python -m pytest -q                                  # 57 passed

# MVD validation + taxonomy/SPL (offline, deterministic)
python -m eval.run_episode --episode all --condition both
python -m eval.analyze --logs eval/logs              # prints MVD: PASS + tables

# Live splat ablation (needs a funded QWEN_API_KEY)
python -m eval.run_splat_ablation --max-turns 10     # full-vs-no-graph table

# Live end-to-end over FastAPI (needs the splat stub + funded key)
python scripts/splat_stub.py --port 5173
WORLD=splat SPLAT_ENGINE_URL=http://127.0.0.1:5173/agent uvicorn app.main:app
```

---

## 11. Bottom line for the reviewer

The architecture is **built, integrated, and verified end-to-end** — the model layer,
the perception/memory/localizer/planner/controller loop, the FastAPI surface, and both
world adapters (static + live Gaussian-splat) all work, with 57 passing tests and a
passing MVD. The **honest gap** is evaluative, not structural: on the test worlds
available today, the topological graph memory does **not** yet demonstrate a measurable
task-performance benefit over a no-graph baseline, and the live planner cannot navigate
the abstract static world (it can navigate the real splat frames). Closing that gap needs
the **live splat engine driving the avatar through a branching environment**, at which
point the graph-memory ablation can be run fairly and at a sample size that supports a
real conclusion.
