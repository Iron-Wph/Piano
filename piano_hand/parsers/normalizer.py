"""Shared normalization utilities for score parsers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from piano_hand.models import (
    FingerSource,
    Hand,
    NoteEvent,
    PedalEvent,
    ScoreSource,
    ScoreTimeline,
    TempoChange,
    TimeSignature,
)

DEFAULT_BPM = 120.0
_VOICE_ID_PATTERN = re.compile(r"[^A-Za-z0-9_-]+")
_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


@dataclass(slots=True)
class RawNote:
    """Parser-neutral note data before seconds and stable IDs are assigned."""

    pitch: int
    onset_beat: float
    duration_beat: float
    measure: int
    voice: str | None = None
    staff: int | None = None
    track: int | None = None
    velocity: int = 64
    hand: Hand = Hand.UNKNOWN
    hand_confidence: float = 0.0
    finger: int | None = None
    finger_source: FingerSource = FingerSource.UNKNOWN
    finger_confidence: float = 0.0
    pedal_down: bool = False
    explanation: list[str] = field(default_factory=list)


class TempoMap:
    """Deterministic piecewise-constant beat/second converter."""

    def __init__(self, changes: Iterable[TempoChange | tuple[float, float]]) -> None:
        by_beat: dict[float, float] = {}
        for change in changes:
            if isinstance(change, TempoChange):
                beat, bpm = change.beat, change.bpm
            else:
                beat, bpm = change
            if beat < 0 or bpm <= 0:
                continue
            by_beat[float(beat)] = float(bpm)
        if 0.0 not in by_beat:
            by_beat[0.0] = DEFAULT_BPM
        self.changes = [
            TempoChange(beat=beat, bpm=bpm) for beat, bpm in sorted(by_beat.items())
        ]

        self._seconds_at_change: list[float] = [0.0]
        for previous, current in zip(self.changes, self.changes[1:], strict=False):
            elapsed_beats = current.beat - previous.beat
            self._seconds_at_change.append(
                self._seconds_at_change[-1] + elapsed_beats * 60.0 / previous.bpm
            )

    def seconds_at(self, beat: float) -> float:
        if beat < 0:
            raise ValueError("beat must be non-negative")
        index = 0
        for candidate in range(1, len(self.changes)):
            if self.changes[candidate].beat > beat:
                break
            index = candidate
        change = self.changes[index]
        return self._seconds_at_change[index] + (beat - change.beat) * 60.0 / change.bpm

    def duration_seconds(self, onset_beat: float, duration_beat: float) -> float:
        return self.seconds_at(onset_beat + duration_beat) - self.seconds_at(onset_beat)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pitch_name(pitch: int) -> str:
    if not 0 <= pitch <= 127:
        raise ValueError(f"MIDI pitch is outside 0..127: {pitch}")
    octave = pitch // 12 - 1
    return f"{_PITCH_NAMES[pitch % 12]}{octave}"


def normalize_time_signatures(
    signatures: Iterable[TimeSignature | tuple[int, int, int]],
) -> list[TimeSignature]:
    by_measure: dict[int, TimeSignature] = {}
    for signature in signatures:
        if isinstance(signature, TimeSignature):
            item = signature
        else:
            measure, numerator, denominator = signature
            item = TimeSignature(
                measure=max(1, int(measure)),
                numerator=int(numerator),
                denominator=int(denominator),
            )
        by_measure[item.measure] = item
    if 1 not in by_measure:
        by_measure[1] = TimeSignature(measure=1, numerator=4, denominator=4)
    return [by_measure[measure] for measure in sorted(by_measure)]


def build_timeline(
    *,
    path: str | Path,
    source_type: str,
    raw_notes: Iterable[RawNote],
    tempo_changes: Iterable[TempoChange | tuple[float, float]],
    time_signatures: Iterable[TimeSignature | tuple[int, int, int]],
    pedal_changes: Iterable[tuple[float, bool]] = (),
) -> ScoreTimeline:
    """Convert parser-neutral events into the shared ScoreTimeline model."""

    source_path = Path(path)
    tempo_map = TempoMap(tempo_changes)
    ordered_raw = sorted(
        raw_notes,
        key=lambda note: (
            note.onset_beat,
            note.pitch,
            -1 if note.track is None else note.track,
            -1 if note.staff is None else note.staff,
            "" if note.voice is None else note.voice,
            note.duration_beat,
        ),
    )

    counters: dict[tuple[int, int, str], int] = {}
    notes: list[NoteEvent] = []
    for raw in ordered_raw:
        track_id = 0 if raw.track is None else raw.track
        voice_id = raw.voice or "0"
        counter_key = (raw.measure, track_id, voice_id)
        counters[counter_key] = counters.get(counter_key, 0) + 1
        safe_voice = _VOICE_ID_PATTERN.sub("_", voice_id).strip("_") or "0"
        note_id = (
            f"m{raw.measure:04d}-t{track_id:02d}-v{safe_voice}-"
            f"n{counters[counter_key]:04d}"
        )
        onset_sec = tempo_map.seconds_at(raw.onset_beat)
        duration_sec = tempo_map.duration_seconds(raw.onset_beat, raw.duration_beat)
        notes.append(
            NoteEvent(
                id=note_id,
                pitch=raw.pitch,
                pitch_name=pitch_name(raw.pitch),
                onset_beat=raw.onset_beat,
                duration_beat=raw.duration_beat,
                onset_sec=onset_sec,
                duration_sec=duration_sec,
                measure=raw.measure,
                voice=raw.voice,
                staff=raw.staff,
                track=raw.track,
                velocity=raw.velocity,
                hand=raw.hand,
                hand_confidence=raw.hand_confidence,
                finger=raw.finger,
                finger_source=raw.finger_source,
                finger_confidence=raw.finger_confidence,
                pedal_down=raw.pedal_down,
                explanation=list(raw.explanation),
            )
        )

    pedal_events = [
        PedalEvent(time_sec=tempo_map.seconds_at(beat), down=down)
        for beat, down in sorted(pedal_changes, key=lambda event: event[0])
    ]
    duration_sec = max(
        [note.offset_sec for note in notes]
        + [event.time_sec for event in pedal_events]
        + [0.0]
    )
    return ScoreTimeline(
        source=ScoreSource(
            path=str(source_path),
            type=source_type,
            sha256=sha256_file(source_path),
        ),
        tempo_map=tempo_map.changes,
        time_signatures=normalize_time_signatures(time_signatures),
        notes=notes,
        pedal_events=pedal_events,
        duration_sec=duration_sec,
    )


__all__ = [
    "RawNote",
    "TempoMap",
    "build_timeline",
    "normalize_time_signatures",
    "pitch_name",
    "sha256_file",
]
