"""Motion planning data models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

Point = tuple[float, float]


class HandPose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wrist: Point
    fingers: dict[int, list[Point]]


class MotionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_sec: float = Field(ge=0)
    left: HandPose | None = None
    right: HandPose | None = None
    pressed_keys: list[int] = Field(default_factory=list)
    active_fingers: dict[str, list[int]] = Field(default_factory=dict)
    measure: int | None = Field(default=None, ge=1)
