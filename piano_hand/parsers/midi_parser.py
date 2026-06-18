"""MIDI parser producing normalized score timelines."""

from __future__ import annotations

import importlib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import ScoreTimeline
from piano_hand.parsers.normalizer import RawNote, build_timeline

DEFAULT_TEMPO_US_PER_BEAT = 500_000


@dataclass(slots=True)
class _ActiveNote:
    pitch: int
    start_tick: int
    velocity: int
    channel: int
    track_index: int
    pedal_at_onset: bool
    physical_off_tick: int | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _MeterSegment:
    start_beat: float
    start_measure: int
    numerator: int
    denominator: int

    @property
    def measure_length(self) -> float:
        return self.numerator * 4.0 / self.denominator


def _require_mido() -> ModuleType:
    try:
        return importlib.import_module("mido")
    except (ImportError, ModuleNotFoundError) as exc:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            "The mido package is required to parse MIDI files.",
            "Install project dependencies with `pip install -e .`.",
        ) from exc


def _message_channel(message: Any) -> int:
    return int(getattr(message, "channel", 0))


def _tempo_to_bpm(tempo: int) -> float:
    if tempo <= 0:
        raise ValueError("MIDI tempo must be positive")
    return 60_000_000.0 / tempo


def _collect_absolute_events(midi_file: Any) -> tuple[list[tuple[int, int, int, Any]], list[int]]:
    events: list[tuple[int, int, int, Any]] = []
    track_end_ticks: list[int] = []
    for track_index, track in enumerate(midi_file.tracks):
        absolute_tick = 0
        for order, message in enumerate(track):
            absolute_tick += int(getattr(message, "time", 0))
            events.append((absolute_tick, track_index, order, message))
        track_end_ticks.append(absolute_tick)
    return events, track_end_ticks


def _build_meter_segments(
    events: list[tuple[int, int, int, Any]],
    ticks_per_beat: int,
) -> tuple[list[_MeterSegment], list[tuple[int, int, int]]]:
    changes: dict[float, tuple[int, int]] = {}
    for tick, _, _, message in sorted(events):
        if getattr(message, "type", "") != "time_signature":
            continue
        numerator = int(message.numerator)
        denominator = int(message.denominator)
        if numerator <= 0 or denominator <= 0:
            continue
        changes[tick / ticks_per_beat] = (numerator, denominator)

    if 0.0 not in changes:
        changes[0.0] = (4, 4)

    segments: list[_MeterSegment] = []
    for beat, (numerator, denominator) in sorted(changes.items()):
        if not segments:
            segments.append(_MeterSegment(beat, 1, numerator, denominator))
            continue
        previous = segments[-1]
        elapsed = max(0.0, beat - previous.start_beat)
        completed = int(round(elapsed / previous.measure_length))
        if beat > previous.start_beat:
            completed = max(1, completed)
        segments.append(
            _MeterSegment(
                start_beat=beat,
                start_measure=previous.start_measure + completed,
                numerator=numerator,
                denominator=denominator,
            )
        )

    signatures = [
        (segment.start_measure, segment.numerator, segment.denominator) for segment in segments
    ]
    return segments, signatures


def _measure_at(beat: float, segments: list[_MeterSegment]) -> int:
    segment = segments[0]
    for candidate in segments[1:]:
        if candidate.start_beat > beat:
            break
        segment = candidate
    elapsed = max(0.0, beat - segment.start_beat)
    return segment.start_measure + int(elapsed // segment.measure_length)


def _parse_midi_notes(
    *,
    events: list[tuple[int, int, int, Any]],
    track_end_ticks: list[int],
    ticks_per_beat: int,
    meter_segments: list[_MeterSegment],
) -> tuple[list[RawNote], list[tuple[float, bool]]]:
    pressed: dict[tuple[int, int, int], deque[_ActiveNote]] = defaultdict(deque)
    sustained: dict[tuple[int, int, int], list[_ActiveNote]] = defaultdict(list)
    pedal_by_channel: dict[int, bool] = defaultdict(bool)
    raw_notes: list[RawNote] = []
    pedal_changes: list[tuple[float, bool]] = []

    def finish(active: _ActiveNote, sounding_end_tick: int) -> None:
        end_tick = max(sounding_end_tick, active.start_tick + 1)
        physical_end = active.physical_off_tick
        if physical_end is None:
            physical_end = end_tick
        physical_end = max(physical_end, active.start_tick + 1)
        onset_beat = active.start_tick / ticks_per_beat
        duration_beat = (end_tick - active.start_tick) / ticks_per_beat
        physical_duration = (physical_end - active.start_tick) / ticks_per_beat
        explanations = [
            f"physical_duration_beat={physical_duration:.9g}",
            f"physical_offset_beat={physical_end / ticks_per_beat:.9g}",
            *active.warnings,
        ]
        raw_notes.append(
            RawNote(
                pitch=active.pitch,
                onset_beat=onset_beat,
                duration_beat=duration_beat,
                measure=_measure_at(onset_beat, meter_segments),
                voice=str(active.channel + 1),
                track=active.track_index,
                velocity=active.velocity,
                pedal_down=(
                    active.pedal_at_onset
                    or active.physical_off_tick is not None
                    and end_tick > active.physical_off_tick
                ),
                explanation=explanations,
            )
        )

    for absolute_tick, track_index, _, message in sorted(events):
        message_type = getattr(message, "type", "")
        channel = _message_channel(message)

        if message_type == "control_change" and int(getattr(message, "control", -1)) == 64:
            down = int(getattr(message, "value", 0)) >= 64
            if down == pedal_by_channel[channel]:
                continue
            pedal_by_channel[channel] = down
            pedal_changes.append((absolute_tick / ticks_per_beat, down))
            if not down:
                for key in [key for key in sustained if key[1] == channel]:
                    for active in sustained.pop(key):
                        finish(active, absolute_tick)
            continue

        if message_type not in {"note_on", "note_off"}:
            continue
        pitch = int(getattr(message, "note", -1))
        velocity = int(getattr(message, "velocity", 0))
        is_note_on = message_type == "note_on" and velocity > 0
        key = (track_index, channel, pitch)

        if is_note_on:
            if key in sustained:
                for active in sustained.pop(key):
                    finish(active, absolute_tick)
            pressed[key].append(
                _ActiveNote(
                    pitch=pitch,
                    start_tick=absolute_tick,
                    velocity=velocity,
                    channel=channel,
                    track_index=track_index,
                    pedal_at_onset=pedal_by_channel[channel],
                )
            )
            continue

        if not pressed[key]:
            continue
        active = pressed[key].popleft()
        active.physical_off_tick = absolute_tick
        if pedal_by_channel[channel]:
            sustained[key].append(active)
        else:
            finish(active, absolute_tick)

    for queue in pressed.values():
        while queue:
            active = queue.popleft()
            active.warnings.append("warning: unclosed MIDI note auto-closed at end of track")
            finish(
                active,
                max(track_end_ticks[active.track_index], active.start_tick + 1),
            )
    file_end_tick = max(track_end_ticks, default=0)
    for notes in sustained.values():
        for active in notes:
            active.warnings.append("warning: sustain pedal auto-released at end of MIDI file")
            finish(active, max(file_end_tick, active.start_tick + 1))

    return raw_notes, pedal_changes


def parse_midi(path: str | Path) -> ScoreTimeline:
    """Parse a Standard MIDI File using a lazily imported mido dependency."""

    source_path = Path(path)
    if not source_path.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"MIDI input does not exist or is not a file: {source_path}",
            "Provide an existing .mid or .midi file.",
        )

    mido = _require_mido()
    try:
        midi_file = mido.MidiFile(str(source_path))
        ticks_per_beat = int(midi_file.ticks_per_beat)
        if ticks_per_beat <= 0:
            raise ValueError("ticks_per_beat must be positive")
        events, track_end_ticks = _collect_absolute_events(midi_file)

        tempo_changes: list[tuple[float, float]] = [(0.0, _tempo_to_bpm(DEFAULT_TEMPO_US_PER_BEAT))]
        for tick, _, _, message in sorted(events):
            if getattr(message, "type", "") == "set_tempo":
                tempo_changes.append(
                    (tick / ticks_per_beat, _tempo_to_bpm(int(message.tempo)))
                )

        meter_segments, time_signatures = _build_meter_segments(events, ticks_per_beat)
        raw_notes, pedal_changes = _parse_midi_notes(
            events=events,
            track_end_ticks=track_end_ticks,
            ticks_per_beat=ticks_per_beat,
            meter_segments=meter_segments,
        )

        return build_timeline(
            path=source_path,
            source_type="midi",
            raw_notes=raw_notes,
            tempo_changes=tempo_changes,
            time_signatures=time_signatures,
            pedal_changes=pedal_changes,
        )
    except PianoHandError:
        raise
    except Exception as exc:
        raise PianoHandError(
            ErrorCode.PARSE_ERROR,
            f"Failed to parse MIDI file {source_path}: {exc}",
            "Verify that the file is a valid Standard MIDI File.",
        ) from exc


__all__ = ["parse_midi"]
