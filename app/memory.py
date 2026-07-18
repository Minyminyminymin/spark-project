"""Topological map memory for ScavengeAI.

``TopoMap`` wraps a NetworkX directed graph whose nodes are places and whose
edges are traversable connections (annotated with the heading used). It offers a
long-short memory summary: the few most-recently-visited places in full detail
plus a single aggregate block for everything older.

Every mutation is deterministic and rule-based — this module never calls a model
— and every write is validated against the Pydantic models below before it
enters the graph. Localization is out of scope: the caller establishes a place's
identity through the observation's ``place_label``, which is used as the node id.
"""

from __future__ import annotations

import json
from typing import Any

import networkx as nx
from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

DECAY_RATE = 0.98        # confidence multiplier applied per un-confirmed tick
MIN_CONFIDENCE = 0.2     # confidence floor
DETAIL_COUNT = 3         # nodes kept in full detail by summary()


# --------------------------------------------------------------------------- #
# Input models (lenient): accept raw perception output or a hand-built subset.
# extra="ignore" lets a full Observation dict (bbox_px, image_width, ...) pass.
# --------------------------------------------------------------------------- #


class _ObsLandmarkIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""
    confidence: float = 1.0


class _ObsObjectIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    description: str = ""


class _ObsFrontierIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    direction: str
    description: str = ""


class _ObservationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    place_label: str
    place_description: str = ""
    landmarks: list[_ObsLandmarkIn] = Field(default_factory=list)
    objects: list[_ObsObjectIn] = Field(default_factory=list)
    frontiers: list[_ObsFrontierIn] = Field(default_factory=list)
    inferred_heading: str = ""


class _PoseIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    x: float
    y: float
    z: float = 0.0
    yaw_deg: float = 0.0


# --------------------------------------------------------------------------- #
# Stored (graph) models: the validated shape held in each node.
# --------------------------------------------------------------------------- #


class LandmarkMem(BaseModel):
    name: str
    description: str = ""
    confidence: float = 1.0
    last_confirmed_turn: int = 0


class ObjectMem(BaseModel):
    name: str
    description: str = ""


class FrontierMem(BaseModel):
    direction: str
    description: str = ""
    explored: bool = False


class NodeMem(BaseModel):
    place_label: str
    description: str = ""
    landmarks: list[LandmarkMem] = Field(default_factory=list)
    objects: list[ObjectMem] = Field(default_factory=list)
    x: float = 0.0
    y: float = 0.0
    visited: bool = True
    confidence: float = 1.0
    last_confirmed_turn: int = 0
    frontiers: list[FrontierMem] = Field(default_factory=list)
    # bookkeeping
    last_visit_seq: int = 0     # monotonic visit order, for recency ranking
    pose_sample_count: int = 0  # number of poses averaged into the centroid


# --------------------------------------------------------------------------- #
# TopoMap
# --------------------------------------------------------------------------- #


class TopoMap:
    """A confidence-decaying topological map of visited places."""

    def __init__(self) -> None:
        self.g = nx.DiGraph()
        self._visit_counter = 0

    # -- introspection ---------------------------------------------------

    @property
    def node_count(self) -> int:
        return self.g.number_of_nodes()

    @property
    def edge_count(self) -> int:
        return self.g.number_of_edges()

    def get_node(self, node_id: str) -> NodeMem:
        """Return a validated copy of a node's stored state."""
        return NodeMem.model_validate(self.g.nodes[node_id]["data"])

    # -- mutations -------------------------------------------------------

    def add_or_update_node(self, observation_dict: dict, pose: Any, turn: int) -> str:
        """Insert or refresh the place named by ``observation_dict['place_label']``.

        Touching a node counts as a confirmation: its confidence resets to 1.0
        and ``last_confirmed_turn`` is set to ``turn``. Returns the node id.
        """
        obs = _ObservationIn.model_validate(observation_dict)
        if isinstance(pose, BaseModel):
            pose = pose.model_dump()
        pose_in = _PoseIn.model_validate(pose)

        node_id = obs.place_label
        self._visit_counter += 1

        if node_id in self.g:
            data = self.get_node(node_id)
            self._merge_into(data, obs, pose_in, turn)
        else:
            data = self._new_node(obs, pose_in, turn)

        # Confirmation semantics (applied on every touch).
        data.visited = True
        data.confidence = 1.0
        data.last_confirmed_turn = turn
        data.last_visit_seq = self._visit_counter

        self.g.add_node(node_id, data=data.model_dump())
        return node_id

    def add_edge(self, from_id: str, to_id: str, heading: Any) -> None:
        """Record a traversable connection ``from_id -> to_id`` via ``heading``.

        If ``from_id`` has a frontier whose direction equals ``str(heading)``,
        that frontier is marked explored (traversing it is what explores it).
        """
        if from_id not in self.g or to_id not in self.g:
            raise ValueError(
                f"both nodes must exist before adding an edge: {from_id!r} -> {to_id!r}"
            )
        self.g.add_edge(from_id, to_id, heading=heading)

        data = self.get_node(from_id)
        changed = False
        for frontier in data.frontiers:
            if frontier.direction == str(heading) and not frontier.explored:
                frontier.explored = True
                changed = True
        if changed:
            self.g.nodes[from_id]["data"] = data.model_dump()

    def tick(self, turn: int) -> None:
        """Decay confidence for every node/landmark not confirmed on ``turn``."""
        for node_id in self.g.nodes:
            data = self.get_node(node_id)

            if data.last_confirmed_turn != turn:
                data.confidence = max(MIN_CONFIDENCE, data.confidence * DECAY_RATE)
            for landmark in data.landmarks:
                if landmark.last_confirmed_turn != turn:
                    landmark.confidence = max(
                        MIN_CONFIDENCE, landmark.confidence * DECAY_RATE
                    )

            self.g.nodes[node_id]["data"] = data.model_dump()

    # -- summary ---------------------------------------------------------

    def summary(self, current_node_id: str) -> dict:
        """Long-short memory: newest ``DETAIL_COUNT`` nodes in full detail plus a
        single aggregate block for all older nodes. Never returns the whole graph.
        """
        ordered = sorted(
            (self.get_node(nid) for nid in self.g.nodes),
            key=lambda d: d.last_visit_seq,
            reverse=True,
        )
        detailed = ordered[:DETAIL_COUNT]
        older = ordered[DETAIL_COUNT:]

        unexplored_frontiers = [
            {"node_id": d.place_label, "direction": f.direction, "description": f.description}
            for d in ordered
            for f in d.frontiers
            if not f.explored
        ]

        return {
            "current": current_node_id,
            "detailed": [d.model_dump() for d in detailed],
            "aggregate": {
                "count": len(older),
                "names": [d.place_label for d in older],
                "unexplored_frontiers": unexplored_frontiers,
            },
        }

    # -- persistence -----------------------------------------------------

    def to_json(self) -> str:
        payload = {
            "visit_counter": self._visit_counter,
            "nodes": [
                {"id": nid, "data": self.g.nodes[nid]["data"]} for nid in self.g.nodes
            ],
            "edges": [
                {"from": u, "to": v, "heading": d.get("heading")}
                for u, v, d in self.g.edges(data=True)
            ],
        }
        return json.dumps(payload)

    @classmethod
    def from_json(cls, text: str) -> "TopoMap":
        payload = json.loads(text)
        topo = cls()
        topo._visit_counter = int(payload.get("visit_counter", 0))
        for node in payload["nodes"]:
            # Re-validate on the way back in.
            data = NodeMem.model_validate(node["data"]).model_dump()
            topo.g.add_node(node["id"], data=data)
        for edge in payload["edges"]:
            topo.g.add_edge(edge["from"], edge["to"], heading=edge.get("heading"))
        return topo

    # -- internals -------------------------------------------------------

    def _new_node(self, obs: _ObservationIn, pose: _PoseIn, turn: int) -> NodeMem:
        return NodeMem(
            place_label=obs.place_label,
            description=obs.place_description,
            landmarks=[
                LandmarkMem(name=l.name, description=l.description, last_confirmed_turn=turn)
                for l in obs.landmarks
            ],
            objects=[ObjectMem(name=o.name, description=o.description) for o in obs.objects],
            x=pose.x,
            y=pose.y,
            frontiers=[
                FrontierMem(direction=f.direction, description=f.description)
                for f in obs.frontiers
            ],
            pose_sample_count=1,
        )

    def _merge_into(
        self, data: NodeMem, obs: _ObservationIn, pose: _PoseIn, turn: int
    ) -> None:
        data.description = obs.place_description or data.description

        # Pose centroid: running mean over all observations of this place.
        n = data.pose_sample_count
        data.x = (data.x * n + pose.x) / (n + 1)
        data.y = (data.y * n + pose.y) / (n + 1)
        data.pose_sample_count = n + 1

        # Landmarks: re-seen ones are re-confirmed; new ones appended.
        by_name = {lm.name: lm for lm in data.landmarks}
        for obs_lm in obs.landmarks:
            existing = by_name.get(obs_lm.name)
            if existing is not None:
                existing.description = obs_lm.description or existing.description
                existing.confidence = 1.0
                existing.last_confirmed_turn = turn
            else:
                new = LandmarkMem(
                    name=obs_lm.name, description=obs_lm.description, last_confirmed_turn=turn
                )
                data.landmarks.append(new)
                by_name[new.name] = new

        # Objects: union by name.
        have = {o.name for o in data.objects}
        for obs_obj in obs.objects:
            if obs_obj.name not in have:
                data.objects.append(ObjectMem(name=obs_obj.name, description=obs_obj.description))
                have.add(obs_obj.name)

        # Frontiers: union by direction, preserving the explored flag.
        by_dir = {f.direction: f for f in data.frontiers}
        for obs_fr in obs.frontiers:
            existing_fr = by_dir.get(obs_fr.direction)
            if existing_fr is not None:
                existing_fr.description = obs_fr.description or existing_fr.description
            else:
                fr = FrontierMem(direction=obs_fr.direction, description=obs_fr.description)
                data.frontiers.append(fr)
                by_dir[fr.direction] = fr
