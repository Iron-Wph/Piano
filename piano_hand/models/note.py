"""Normalized note event model."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Hand(StrEnum):
    LEFT = "left"
    RIGHT = "right"
    UNKNOWN = "unknown"


class FingerSource(StrEnum):
    SCORE = "score"
    GENERATED = "generated"
    MANUAL = "manual"
    UNKNOWN = "unknown"


class NoteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    pitch: int = Field(ge=0, le=127)
    pitch_name: str = Field(min_length=1)
    onset_beat: float = Field(ge=0)
    duration_beat: float = Field(gt=0)
    onset_sec: float = Field(ge=0)
    duration_sec: float = Field(gt=0)
    measure: int = Field(ge=1)
    voice: str | None = None
    staff: int | None = Field(default=None, ge=1)
    track: int | None = Field(default=None, ge=0)
    velocity: int = Field(default=64, ge=0, le=127)
    hand: Hand = Hand.UNKNOWN
    hand_confidence: float = Field(default=0, ge=0, le=1)
    finger: int | None = Field(default=None, ge=1, le=5)
    finger_source: FingerSource = FingerSource.UNKNOWN
    finger_confidence: float = Field(default=0, ge=0, le=1)
    pedal_down: bool = False
    explanation: list[str] = Field(default_factory=list)

    @property
    def offset_beat(self) -> float:
        return self.onset_beat + self.duration_beat

    @property
    def offset_sec(self) -> float:
        return self.onset_sec + self.duration_sec

    @model_validator(mode="after")
    def validate_finger_source(self) -> NoteEvent:
        if self.finger is None and self.finger_source != FingerSource.UNKNOWN:
            raise ValueError("finger_source must be unknown when finger is unset")
        return self

