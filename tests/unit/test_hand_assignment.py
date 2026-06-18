from __future__ import annotations

from piano_hand.models import Hand, NoteEvent, ScoreSource, ScoreTimeline
from piano_hand.planning.hand_assignment import assign_hands


def make_note(note_id: str, pitch: int, onset: float, **overrides: object) -> NoteEvent:
    values: dict[str, object] = {
        "id": note_id,
        "pitch": pitch,
        "pitch_name": f"midi-{pitch}",
        "onset_beat": onset,
        "duration_beat": 1.0,
        "onset_sec": onset * 0.5,
        "duration_sec": 0.5,
        "measure": int(onset // 4) + 1,
    }
    values.update(overrides)
    return NoteEvent.model_validate(values)


def make_timeline(source_type: str, notes: list[NoteEvent]) -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path=f"song.{source_type}", type=source_type, sha256="0" * 64),
        notes=notes,
        duration_sec=max((note.offset_sec for note in notes), default=0),
    )


def test_musicxml_prefers_existing_hand_then_staff() -> None:
    timeline = make_timeline(
        "musicxml",
        [
            make_note("explicit", 48, 0, staff=1, hand=Hand.LEFT, hand_confidence=1),
            make_note("upper", 72, 1, staff=1),
            make_note("lower", 48, 1, staff=2),
        ],
    )

    result = assign_hands(timeline)
    by_id = {note.id: note for note in result.notes}

    assert by_id["explicit"].hand == Hand.LEFT
    assert by_id["upper"].hand == Hand.RIGHT
    assert by_id["lower"].hand == Hand.LEFT
    assert by_id["upper"].hand_confidence == 0.98
    assert any("staff 1" in reason for reason in by_id["upper"].explanation)


def test_midi_uses_clear_track_layout_with_confidence_and_explanation() -> None:
    timeline = make_timeline(
        "midi",
        [
            make_note("bass-1", 40, 0, track=0),
            make_note("treble-1", 72, 0, track=1),
            make_note("bass-2", 43, 1, track=0),
            make_note("treble-2", 74, 1, track=1),
        ],
    )

    result = assign_hands(timeline)
    by_id = {note.id: note for note in result.notes}

    assert by_id["bass-1"].hand == Hand.LEFT
    assert by_id["bass-2"].hand == Hand.LEFT
    assert by_id["treble-1"].hand == Hand.RIGHT
    assert by_id["treble-2"].hand == Hand.RIGHT
    assert all(note.hand_confidence >= 0.9 for note in result.notes)
    assert all(
        any("track" in reason.lower() for reason in note.explanation)
        for note in result.notes
    )


def test_single_track_chord_is_split_by_pitch_and_is_deterministic() -> None:
    timeline = make_timeline(
        "midi",
        [
            make_note("low", 45, 0, track=0),
            make_note("high", 72, 0, track=0),
            make_note("next-low", 47, 1, track=0),
            make_note("next-high", 74, 1, track=0),
        ],
    )

    first = assign_hands(timeline)
    second = assign_hands(timeline)

    assert [(note.id, note.hand) for note in first.notes] == [
        (note.id, note.hand) for note in second.notes
    ]
    assert {note.id: note.hand for note in first.notes} == {
        "low": Hand.LEFT,
        "high": Hand.RIGHT,
        "next-low": Hand.LEFT,
        "next-high": Hand.RIGHT,
    }
    assert all(note.explanation for note in first.notes)
