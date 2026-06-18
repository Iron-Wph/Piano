from __future__ import annotations

import pytest
from pydantic import ValidationError

from piano_hand.models import (
    FingerSource,
    Hand,
    NoteEvent,
    ScoreSource,
    ScoreTimeline,
)


def make_note(**overrides: object) -> NoteEvent:
    values: dict[str, object] = {
        "id": "n1",
        "pitch": 60,
        "pitch_name": "C4",
        "onset_beat": 0.0,
        "duration_beat": 1.0,
        "onset_sec": 0.0,
        "duration_sec": 0.5,
        "measure": 1,
    }
    values.update(overrides)
    return NoteEvent.model_validate(values)


def test_note_rejects_illegal_finger() -> None:
    with pytest.raises(ValidationError):
        make_note(finger=6)


def test_note_requires_unknown_source_without_finger() -> None:
    with pytest.raises(ValidationError):
        make_note(finger_source=FingerSource.GENERATED)


def test_score_rejects_duration_shorter_than_last_note() -> None:
    source = ScoreSource(path="song.mid", type="midi", sha256="0" * 64)
    with pytest.raises(ValidationError):
        ScoreTimeline(
            source=source,
            notes=[make_note(hand=Hand.RIGHT)],
            duration_sec=0.1,
        )


def test_score_sorts_notes_deterministically() -> None:
    source = ScoreSource(path="song.mid", type="midi", sha256="0" * 64)
    score = ScoreTimeline(
        source=source,
        notes=[
            make_note(id="b", pitch=64, onset_beat=1, onset_sec=0.5),
            make_note(id="a", pitch=60),
        ],
        duration_sec=1.0,
    )
    assert [note.id for note in score.sorted_notes()] == ["a", "b"]
