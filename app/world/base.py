"""Abstract world interface and the value types the agent exchanges with it.

Heading / yaw convention used throughout ScavengeAI:

    yaw   0 deg -> +y (north)
    yaw  90 deg -> +x (east)
    yaw 180 deg -> -y (south)
    yaw 270 deg -> -x (west)

Yaw is always quantized to one of {0, 90, 180, 270} by worlds that implement
this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Pose(BaseModel):
    """Where the agent is and which way it faces."""

    x: float
    y: float
    z: float = 0.0
    yaw_deg: float = 0.0


class View(BaseModel):
    """A single first-person frame returned by the world."""

    image: bytes
    width: int
    height: int
    pose: Pose
    frame_id: int


class MoveAction(BaseModel):
    """Advance forward by ``distance`` in the currently faced direction."""

    type: Literal["move"] = "move"
    distance: float


class TurnAction(BaseModel):
    """Rotate in place by ``degrees`` (positive = clockwise)."""

    type: Literal["turn"] = "turn"
    degrees: float


class StopAction(BaseModel):
    """Halt exploration, recording why."""

    type: Literal["stop"] = "stop"
    reason: str


# Discriminated union: the "type" field selects which model to validate.
Action = Annotated[
    Union[MoveAction, TurnAction, StopAction],
    Field(discriminator="type"),
]


class ActionResult(BaseModel):
    """Outcome of :meth:`World.execute_action`.

    A blocked or otherwise unsuccessful action reports ``success=False`` with
    the pose left unchanged; it never raises.
    """

    success: bool
    pose: Pose
    message: str = ""


class World(ABC):
    """An environment the agent can look at and act within."""

    @abstractmethod
    def get_current_view(self) -> View:
        """Return the current first-person frame."""

    @abstractmethod
    def execute_action(self, action: Action) -> ActionResult:
        """Apply ``action`` and return the outcome.

        Implementations must never raise on a merely blocked/invalid move;
        they return ``ActionResult(success=False, ...)`` with the pose
        unchanged instead.
        """
