"""A :class:`World` backed by the Gaussian-splat engine's HTTP contract.

The engine (spark.js dev server) serves two endpoints under ``SPLAT_ENGINE_URL``:

    GET  {base}/view   -> {"image_base64", "width", "height",
                          "pose": {"x","y","z","yaw_deg"}, "frame_id"}
    POST {base}/action <- one of the agent's Action models
                       -> {"success": bool, "pose": {...}, "message": str}

Contract notes baked in here (agreed with the engine team):
- The engine already reports pose in OUR ground-plane frame: x/y are the floor,
  z is height (ignored by the agent). So we map the pose fields straight through.
- Images are base64 JPEG (no ``data:`` prefix) at a true 1280x720; we decode to
  bytes and trust the width/height in the payload (perception rescales its
  0-1000 boxes against these).
- A *blocked* move is a normal 200 with ``success: false`` and the pose where the
  collider actually stopped — not an error. Only genuine failures are non-2xx.
- ``get_current_view`` has no success channel, so it fails loud (raises) if the
  engine is unreachable (e.g. HTTP 503 when the app tab is closed). Actions, by
  contract, translate HTTP/network errors into ``ActionResult(success=False)``.
"""

from __future__ import annotations

import base64
import os
from typing import Optional

import requests

from app.world.base import Action, ActionResult, Pose, View, World

DEFAULT_URL = "http://localhost:5173/agent"
VIEW_TIMEOUT_SECONDS = 10.0
ACTION_TIMEOUT_SECONDS = 15.0


class SplatEngineError(RuntimeError):
    """Raised when GET /view cannot be satisfied (no fallback frame exists)."""


class SplatWorld(World):
    def __init__(self, base_url: Optional[str] = None, session: Optional[requests.Session] = None):
        self.base_url = (base_url or os.environ.get("SPLAT_ENGINE_URL", DEFAULT_URL)).rstrip("/")
        self._session = session or requests.Session()
        self._frame_counter = 0
        self._last_pose = Pose(x=0.0, y=0.0, z=0.0, yaw_deg=0.0)

    # -- World interface -------------------------------------------------

    def get_current_view(self) -> View:
        """Fetch and decode the current first-person frame. Fails loud on error."""
        try:
            resp = self._session.get(f"{self.base_url}/view", timeout=VIEW_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            image = base64.b64decode(data["image_base64"])
            pose = self._parse_pose(data["pose"])
            width = int(data["width"])
            height = int(data["height"])
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            raise SplatEngineError(
                f"GET {self.base_url}/view failed (is the engine dev server up with "
                f"the app tab open?): {exc}"
            ) from exc

        frame_id = data.get("frame_id")
        frame_id = self._frame_counter if frame_id is None else int(frame_id)
        self._frame_counter += 1
        self._last_pose = pose
        return View(image=image, width=width, height=height, pose=pose, frame_id=frame_id)

    def execute_action(self, action: Action) -> ActionResult:
        """Post an action. Network/HTTP failures become success=False, never raise."""
        try:
            resp = self._session.post(
                f"{self.base_url}/action",
                json=action.model_dump(),
                timeout=ACTION_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            # Genuine transport/server error: report failure with the last pose.
            return ActionResult(
                success=False,
                pose=self._last_pose,
                message=f"splat engine error: {exc}",
            )

        pose = self._parse_pose(data["pose"]) if isinstance(data.get("pose"), dict) else self._last_pose
        self._last_pose = pose
        return ActionResult(
            success=bool(data.get("success", False)),
            pose=pose,
            message=str(data.get("message", "")),
        )

    # -- optional convenience (matches the engine's nice-to-have /reset) --

    def reset(self) -> ActionResult:
        """Return the avatar to its home/spawn pose, if the engine supports it."""
        try:
            resp = self._session.post(f"{self.base_url}/reset", timeout=ACTION_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            return ActionResult(success=False, pose=self._last_pose, message=f"splat reset error: {exc}")

        pose = self._parse_pose(data["pose"]) if isinstance(data.get("pose"), dict) else self._last_pose
        self._last_pose = pose
        return ActionResult(success=bool(data.get("success", True)), pose=pose, message=str(data.get("message", "reset")))

    # -- helpers ---------------------------------------------------------

    @staticmethod
    def _parse_pose(d: dict) -> Pose:
        return Pose(
            x=float(d["x"]),
            y=float(d["y"]),
            z=float(d.get("z", 0.0)),
            yaw_deg=float(d.get("yaw_deg", 0.0)),
        )
