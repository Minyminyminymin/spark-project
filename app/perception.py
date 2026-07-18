"""Turn a first-person photo into a validated :class:`Observation`.

The single public entry point, :func:`perceive`, sends the image to Qwe3-VL
through an injected ``qwen_call`` callable, parses the strict-JSON reply into the
schema from section 2.3 of the architecture spec, and fills in pixel-space
bounding boxes.

Qwen3-VL emits bounding boxes on a fixed 0-1000 normalized scale regardless of
the actual image resolution (a verified property of the model). Immediately
after parsing we rescale every box to pixels:

    px = round(norm / 1000 * image_dimension)

using ``image_width`` for x coordinates and ``image_height`` for y. Downstream
components read only ``bbox_px``; ``bbox_norm`` is retained solely for debugging.
"""

from __future__ import annotations

import json
import re
from typing import Callable, Literal, Optional

from pydantic import BaseModel, ValidationError


# --------------------------------------------------------------------------- #
# Schema (section 2.3, verbatim field-for-field)
# --------------------------------------------------------------------------- #


class BBoxNorm(BaseModel):
    """Box exactly as Qwen emits it, on the 0-1000 scale."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int


class BBoxPx(BaseModel):
    """Box in actual pixels, filled only by the rescaling post-processor."""

    x_min: int
    y_min: int
    x_max: int
    y_max: int


class Landmark(BaseModel):
    name: str
    description: str
    bbox_norm: BBoxNorm
    bbox_px: Optional[BBoxPx] = None
    confidence: float = 1.0


class VisibleObject(BaseModel):
    name: str
    description: str
    bbox_norm: BBoxNorm
    bbox_px: Optional[BBoxPx] = None


class Frontier(BaseModel):
    direction: Literal["left", "forward", "right", "back"]
    description: str


class Observation(BaseModel):
    place_label: str
    place_description: str
    landmarks: list[Landmark]
    objects: list[VisibleObject]
    frontiers: list[Frontier]
    inferred_heading: str
    image_width: int
    image_height: int


class PerceptionError(RuntimeError):
    """Raised when Qwen fails to return valid Observation JSON after a retry."""


# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

_PROMPT = """\
You are the visual perception module of an agent exploring an environment from \
first-person photos. Analyze the attached image and return a SINGLE JSON object \
(no prose, no markdown) describing what you see.

Report:
- place_label: a short, stable name for where you think you are (e.g. "stone_courtyard").
- place_description: one or two sentences describing the place.
- landmarks: LARGE, FIXED features useful for navigation and relocalization
  (walls, doorways, statues, staircases, big furniture). For each: name (short
  stable identifier like "red_fountain"), description, bbox_norm, confidence (0-1).
- objects: SMALL, findable objects that could be search targets (a mug, a book, a
  key). Keep these SEPARATE from landmarks. For each: name, description, bbox_norm.
- frontiers: navigable openings you could move toward, each with a direction that
  MUST be one of "left", "forward", "right", "back", plus a description
  (e.g. "dark corridor with arched ceiling").
- inferred_heading: your best guess of which way you are currently facing, in
  words (e.g. "north", "toward the tall window").

Bounding boxes (bbox_norm) MUST be integers on a 0-1000 normalized scale for both
axes, regardless of the image's pixel size, as {"x_min":..,"y_min":..,"x_max":..,"y_max":..}.

Return ONLY the JSON object matching this exact shape:
{
  "place_label": str,
  "place_description": str,
  "landmarks": [{"name": str, "description": str,
                 "bbox_norm": {"x_min": int, "y_min": int, "x_max": int, "y_max": int},
                 "confidence": float}],
  "objects": [{"name": str, "description": str,
               "bbox_norm": {"x_min": int, "y_min": int, "x_max": int, "y_max": int}}],
  "frontiers": [{"direction": "left|forward|right|back", "description": str}],
  "inferred_heading": str
}"""

_CORRECTION = (
    "\n\nYour previous response was not valid JSON for this schema. "
    "Return ONLY a single valid JSON object matching the schema exactly — "
    "no markdown, no code fences, no commentary."
)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def perceive(
    image_bytes: bytes,
    image_width: int,
    image_height: int,
    qwen_call: Callable[..., str],
) -> Observation:
    """Perceive one photo and return a validated, pixel-annotated Observation.

    ``qwen_call`` is invoked as ``qwen_call(prompt, image_bytes, json_mode=True)``.
    On a JSON-parse or schema-validation failure the call is retried once with an
    appended correction; a second failure raises :class:`PerceptionError`.
    """

    last_error: Exception | None = None
    for attempt in range(2):
        prompt = _PROMPT if attempt == 0 else _PROMPT + _CORRECTION
        raw = qwen_call(prompt, image_bytes, json_mode=True)
        try:
            return _parse_and_rescale(raw, image_width, image_height)
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    raise PerceptionError(
        f"Qwen did not return valid Observation JSON after a retry: {last_error}"
    ) from last_error


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _strip_fences(text: str) -> str:
    """Remove a leading/trailing markdown code fence if present."""

    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[A-Za-z0-9_-]*\s*\n?", "", stripped)
        stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


def _rescale_box(box: BBoxNorm, width: int, height: int) -> BBoxPx:
    """px = round(norm / 1000 * dimension); x uses width, y uses height."""

    return BBoxPx(
        x_min=round(box.x_min / 1000 * width),
        y_min=round(box.y_min / 1000 * height),
        x_max=round(box.x_max / 1000 * width),
        y_max=round(box.y_max / 1000 * height),
    )


def _parse_and_rescale(raw: str, width: int, height: int) -> Observation:
    data = json.loads(_strip_fences(raw))

    # We know the true dimensions; inject them so validation never fails on a
    # model that omitted them, and so rescaling always uses ground truth.
    if isinstance(data, dict):
        data["image_width"] = width
        data["image_height"] = height

    observation = Observation.model_validate(data)

    for landmark in observation.landmarks:
        landmark.bbox_px = _rescale_box(landmark.bbox_norm, width, height)
    for obj in observation.objects:
        obj.bbox_px = _rescale_box(obj.bbox_norm, width, height)

    return observation
