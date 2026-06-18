"""Normalized score timeline models."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from piano_hand.models.note import NoteEvent


class ScoreSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    type: str
    sha256: str = Field(min_length=64, max_length=64)


class TempoChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat: float = Field(ge=0)
    bpm: float = Field(gt=0)


class TimeSignature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure: int = Field(ge=1)
    numerator: int = Field(gt=0)
    denominator: int = Field(gt=0)


class PedalEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time_sec: float = Field(ge=0)
    down: bool


class ScoreTimeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    source: ScoreSource
    tempo_map: list[TempoChange] = Field(default_factory=list)
    time_signatures: list[TimeSignature] = Field(default_factory=list)
    notes: list[NoteEvent] = Field(default_factory=list)
    pedal_events: list[PedalEvent] = Field(default_factory=list)
    duration_sec: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_duration(self) -> ScoreTimeline:
        if self.notes:
            max_offset = max(note.offset_sec for note in self.notes)
            if self.duration_sec + 1e-6 < max_offset:
                raise ValueError("duration_sec is shorter than the last note")
        return self

    def sorted_notes(self) -> list[NoteEvent]:
        return sorted(self.notes, key=lambda note: (note.onset_beat, note.pitch, note.id))

    @classmethod
    def source_type_for_path(cls, path: str | Path) -> str:
        suffix = Path(path).suffix.lower()
        if suffix in {".mid", ".midi"}:
            return "midi"
        if suffix in {".musicxml", ".xml", ".mxl"}:
            return "musicxml"
        return "unknown"

