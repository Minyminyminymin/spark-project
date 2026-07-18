"""Decide whether an observation is a new place or a revisit of a known node.

The rules are deliberately simple and fully deterministic — no ML, no
embeddings, no visual feature extraction. Matching is driven by pose proximity,
with landmark-name overlap used only to disambiguate when several known places
sit within the pose radius.

    localize(observation, pose, topo_map) -> LocalizationResult

The model's self-reported ``place_label`` is never a matching signal on its own;
memory stores it, but identity here comes from geometry (and, on ties, from
which landmark names co-occur).
"""

from __future__ import annotations

import math
from typing import Any, Optional

from pydantic import BaseModel

# Pose radius: nodes whose centroid is within this distance are revisit
# candidates. Configurable via the ``radius`` argument to :func:`localize`.
DEFAULT_RADIUS = 1.5

# Nodes below this confidence are "stale": they still match by pose, but lose
# the landmark tie-breaker to a fresh node on an equal score.
STALE_CONFIDENCE = 0.5


class LocalizationResult(BaseModel):
    is_revisit: bool
    node_id: Optional[str] = None
    matched_by: str  # "pose" | "pose+visual" | "new"


def localize(
    observation: Any,
    pose: Any,
    topo_map: Any,
    radius: float = DEFAULT_RADIUS,
) -> LocalizationResult:
    """Localize ``observation`` taken at ``pose`` against ``topo_map``."""

    px, py = _pose_xy(pose)
    obs_names = _landmark_names(observation)

    # (1) Dominant signal: pose proximity to each node centroid.
    candidates = []  # (node_id, node, distance)
    for node_id in topo_map.g.nodes:
        node = topo_map.get_node(node_id)
        distance = math.hypot(node.x - px, node.y - py)
        if distance <= radius:
            candidates.append((node_id, node, distance))

    # (4) Nothing within R -> a genuinely new place.
    if not candidates:
        return LocalizationResult(is_revisit=False, node_id=None, matched_by="new")

    # (2) Exactly one candidate -> revisit it, on pose alone.
    if len(candidates) == 1:
        return LocalizationResult(
            is_revisit=True, node_id=candidates[0][0], matched_by="pose"
        )

    # (3) Several candidates -> break the tie with landmark-name Jaccard.
    # Order by: highest overlap, then fresh over stale, then nearest.
    def rank(item):
        node_id, node, distance = item
        cand_names = {
            lm.name.strip().lower() for lm in node.landmarks if lm.name
        }
        jaccard = _jaccard(obs_names, cand_names)
        is_fresh = node.confidence >= STALE_CONFIDENCE
        return (jaccard, 1 if is_fresh else 0, -distance)

    best_id = max(candidates, key=rank)[0]
    return LocalizationResult(is_revisit=True, node_id=best_id, matched_by="pose+visual")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _pose_xy(pose: Any) -> tuple[float, float]:
    if isinstance(pose, BaseModel):
        pose = pose.model_dump()
    if isinstance(pose, dict):
        return float(pose["x"]), float(pose["y"])
    return float(pose.x), float(pose.y)


def _landmark_names(observation: Any) -> set[str]:
    if isinstance(observation, BaseModel):
        landmarks = observation.landmarks
    elif isinstance(observation, dict):
        landmarks = observation.get("landmarks", [])
    else:
        landmarks = getattr(observation, "landmarks", [])

    names: set[str] = set()
    for lm in landmarks:
        if isinstance(lm, BaseModel):
            name = lm.name
        elif isinstance(lm, dict):
            name = lm.get("name")
        else:
            name = getattr(lm, "name", None)
        if name:
            names.add(name.strip().lower())
    return names


def _jaccard(a: set[str], b: set[str]) -> float:
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)
