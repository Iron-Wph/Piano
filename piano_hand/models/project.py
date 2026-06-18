"""Project configuration models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    type: Literal["auto", "midi", "musicxml"] = "auto"


class TimelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "./timeline.json"
    fingering_overrides: str = "./fingering.csv"


class PlaybackConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tempo_mode: Literal["multiplier", "bpm"] = "multiplier"
    tempo_value: float = Field(default=1.0, gt=0)
    start_measure: int = Field(default=1, ge=1)
    end_measure: int | None = Field(default=None, ge=1)
    count_in_beats: int = Field(default=4, ge=0)

    @model_validator(mode="after")
    def validate_measure_range(self) -> PlaybackConfig:
        if self.end_measure is not None and self.end_measure < self.start_measure:
            raise ValueError("end_measure must not precede start_measure")
        return self


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(default=1280, ge=320)
    height: int = Field(default=720, ge=240)
    fps: int = Field(default=30, ge=1, le=120)
    theme: Literal["dark", "light"] = "dark"
    show_finger_numbers: bool = True
    show_measure: bool = True
    show_note_names: bool = False
    random_seed: int = 0


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    soundfont_path: str | None = None
    sample_rate: int = Field(default=48_000, ge=8_000, le=192_000)


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    video_path: str = "./output.mp4"
    report_path: str = "./validation-report.json"


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1.0"
    input: InputConfig
    timeline: TimelineConfig = Field(default_factory=TimelineConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

