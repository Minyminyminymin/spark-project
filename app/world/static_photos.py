"""A :class:`World` backed by a fixed set of hand-captured photos.

The world is described by a JSON layout file listing exactly six places. Each
place has fake (x, y) coordinates, one photo per quantized heading
(0/90/180/270), and an adjacency map sending each heading to a neighbor place
(or ``null`` when that direction is blocked).

Movement is purely topological: ``turn`` snaps the yaw to the nearest 90 deg,
and ``move`` advances to the neighbor in the currently faced direction if one
exists, otherwise it fails with the pose unchanged.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, Optional

from PIL import Image

from app.world.base import (
    Action,
    ActionResult,
    MoveAction,
    Pose,
    StopAction,
    TurnAction,
    View,
    World,
)

HEADINGS = (0, 90, 180, 270)
_EXPECTED_PLACE_COUNT = 6


def quantize_heading(yaw_deg: float) -> int:
    """Snap an arbitrary yaw to the nearest of {0, 90, 180, 270}."""

    return int(round(yaw_deg / 90.0)) % 4 * 90


class StaticPhotoWorld(World):
    """World that walks a static, photo-backed graph of six places."""

    def __init__(self, layout_path: str | Path):
        self._layout_path = Path(layout_path)
        with self._layout_path.open() as fh:
            layout = json.load(fh)

        self._photos_dir = self._layout_path.parent
        self._places: Dict[str, dict] = layout["places"]

        if len(self._places) != _EXPECTED_PLACE_COUNT:
            raise ValueError(
                f"layout must describe exactly {_EXPECTED_PLACE_COUNT} places, "
                f"got {len(self._places)}"
            )

        start = layout["start"]
        self._place: str = start["place"]
        self._yaw: int = quantize_heading(start["yaw_deg"])
        self._frame_id = 0

        if self._place not in self._places:
            raise ValueError(f"start place {self._place!r} not in layout")

    # -- helpers ---------------------------------------------------------

    def _current_pose(self) -> Pose:
        place = self._places[self._place]
        return Pose(x=float(place["x"]), y=float(place["y"]), z=0.0, yaw_deg=float(self._yaw))

    def _neighbor(self, heading: int) -> Optional[str]:
        adjacency = self._places[self._place]["adjacency"]
        return adjacency.get(str(heading))

    # -- World interface -------------------------------------------------

    def get_current_view(self) -> View:
        place = self._places[self._place]
        photo_name = place["photos"][str(self._yaw)]
        image_bytes = (self._photos_dir / photo_name).read_bytes()
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size

        view = View(
            image=image_bytes,
            width=width,
            height=height,
            pose=self._current_pose(),
            frame_id=self._frame_id,
        )
        self._frame_id += 1
        return view

    def execute_action(self, action: Action) -> ActionResult:
        if isinstance(action, TurnAction):
            self._yaw = quantize_heading(self._yaw + action.degrees)
            return ActionResult(
                success=True,
                pose=self._current_pose(),
                message=f"turned to {self._yaw} deg",
            )

        if isinstance(action, MoveAction):
            neighbor = self._neighbor(self._yaw)
            if neighbor is None:
                return ActionResult(
                    success=False,
                    pose=self._current_pose(),
                    message=f"blocked: no neighbor to the {self._yaw} deg",
                )
            self._place = neighbor
            return ActionResult(
                success=True,
                pose=self._current_pose(),
                message=f"moved to {self._place}",
            )

        if isinstance(action, StopAction):
            return ActionResult(
                success=True,
                pose=self._current_pose(),
                message=f"stopped: {action.reason}",
            )

        raise TypeError(f"unknown action: {action!r}")
