"""Shared domain models."""

from piano_hand.models.motion import HandPose, MotionFrame
from piano_hand.models.note import FingerSource, Hand, NoteEvent
from piano_hand.models.project import (
    AudioConfig,
    InputConfig,
    OutputConfig,
    PlaybackConfig,
    ProjectConfig,
    RenderConfig,
    TimelineConfig,
)
from piano_hand.models.report import Issue, IssueSeverity, ValidationReport
from piano_hand.models.score import (
    PedalEvent,
    ScoreSource,
    ScoreTimeline,
    TempoChange,
    TimeSignature,
)

__all__ = [
    "AudioConfig",
    "FingerSource",
    "Hand",
    "HandPose",
    "InputConfig",
    "Issue",
    "IssueSeverity",
    "MotionFrame",
    "NoteEvent",
    "OutputConfig",
    "PedalEvent",
    "PlaybackConfig",
    "ProjectConfig",
    "RenderConfig",
    "ScoreSource",
    "ScoreTimeline",
    "TempoChange",
    "TimelineConfig",
    "TimeSignature",
    "ValidationReport",
]
