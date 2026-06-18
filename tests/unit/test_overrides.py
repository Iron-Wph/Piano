from __future__ import annotations

from pathlib import Path

import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource, Hand, NoteEvent, ScoreSource, ScoreTimeline
from piano_hand.planning.overrides import (
    FingeringOverride,
    apply_overrides,
    load_overrides_csv,
)


def make_note(note_id: str, pitch: int, onset: float, finger: int) -> NoteEvent:
    return NoteEvent(
        id=note_id,
        pitch=pitch,
        pitch_name=f"midi-{pitch}",
        onset_beat=onset,
        duration_beat=1,
        onset_sec=onset * 0.5,
        duration_sec=0.5,
        measure=1,
        hand=Hand.RIGHT,
        hand_confidence=0.8,
        finger=finger,
        finger_source=FingerSource.GENERATED,
        finger_confidence=0.8,
    )


def make_timeline(notes: list[NoteEvent]) -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path="song.mid", type="midi", sha256="0" * 64),
        notes=notes,
        duration_sec=max(note.offset_sec for note in notes),
    )


def write_csv(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_load_overrides_csv_validates_and_parses_rows(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "fingering.csv",
        "note_id,hand,finger\nn1,left,5\nn2,right,2\n",
    )

    overrides = load_overrides_csv(path)

    assert overrides == [
        FingeringOverride("n1", Hand.LEFT, 5),
        FingeringOverride("n2", Hand.RIGHT, 2),
    ]


@pytest.mark.parametrize(
    "row",
    [
        "n1,middle,2",
        "n1,left,0",
        "n1,right,6",
        "n1,right,abc",
    ],
)
def test_load_overrides_csv_rejects_invalid_hand_or_finger(
    tmp_path: Path,
    row: str,
) -> None:
    path = write_csv(tmp_path / "bad.csv", f"note_id,hand,finger\n{row}\n")

    with pytest.raises(PianoHandError) as error:
        load_overrides_csv(path)

    assert error.value.code == ErrorCode.FINGERING_ERROR
    assert "row 2" in error.value.message


def test_load_overrides_csv_rejects_duplicate_note_id(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "duplicate.csv",
        "note_id,hand,finger\nn1,left,5\nn1,right,1\n",
    )

    with pytest.raises(PianoHandError, match="duplicate note_id"):
        load_overrides_csv(path)


def test_load_overrides_csv_requires_all_columns(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path / "missing-column.csv",
        "note_id,hand\nn1,left\n",
    )

    with pytest.raises(PianoHandError) as error:
        load_overrides_csv(path)

    assert error.value.code == ErrorCode.FINGERING_ERROR
    assert "missing required columns" in error.value.message


def test_apply_overrides_has_manual_priority_and_preserves_order() -> None:
    timeline = make_timeline(
        [
            make_note("n1", 60, 0, 1),
            make_note("n2", 64, 1, 3),
        ]
    )

    result = apply_overrides(
        timeline,
        [FingeringOverride("n2", Hand.LEFT, 5)],
    )

    assert [note.id for note in result.notes] == ["n1", "n2"]
    assert result.notes[0].finger_source == FingerSource.GENERATED
    assert result.notes[1].hand == Hand.LEFT
    assert result.notes[1].finger == 5
    assert result.notes[1].finger_source == FingerSource.MANUAL
    assert result.notes[1].finger_confidence == 1
    assert any("Manual CSV override" in reason for reason in result.notes[1].explanation)


def test_apply_overrides_rejects_unknown_note_id() -> None:
    timeline = make_timeline([make_note("n1", 60, 0, 1)])

    with pytest.raises(PianoHandError) as error:
        apply_overrides(
            timeline,
            [FingeringOverride("missing", Hand.RIGHT, 2)],
        )

    assert error.value.code == ErrorCode.FINGERING_ERROR
    assert "unknown note ID" in error.value.message


def test_apply_overrides_rechecks_same_finger_conflicts() -> None:
    timeline = make_timeline(
        [
            make_note("n1", 60, 0, 1),
            make_note("n2", 64, 0, 3),
        ]
    )

    with pytest.raises(PianoHandError) as error:
        apply_overrides(
            timeline,
            [FingeringOverride("n2", Hand.RIGHT, 1)],
        )

    assert error.value.code == ErrorCode.FINGERING_ERROR
    assert "cannot hold pitches" in error.value.message
