from __future__ import annotations

import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource, Hand, NoteEvent, ScoreSource, ScoreTimeline
from piano_hand.planning.fingering_dp import plan_fingering, rank_fingering_candidates
from piano_hand.planning.fingering_rules import (
    FingeringViolationCode,
    check_fingering_constraints,
)


def make_note(note_id: str, pitch: int, onset: float, **overrides: object) -> NoteEvent:
    values: dict[str, object] = {
        "id": note_id,
        "pitch": pitch,
        "pitch_name": f"midi-{pitch}",
        "onset_beat": onset,
        "duration_beat": 1.0,
        "onset_sec": onset * 0.5,
        "duration_sec": 0.5,
        "measure": 1,
        "hand": Hand.RIGHT,
        "hand_confidence": 0.9,
    }
    values.update(overrides)
    return NoteEvent.model_validate(values)


def make_timeline(notes: list[NoteEvent]) -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path="song.mid", type="midi", sha256="0" * 64),
        notes=notes,
        duration_sec=max(note.offset_sec for note in notes),
    )


def test_planner_generates_legal_explained_fingering_for_scale() -> None:
    timeline = make_timeline(
        [
            make_note("n1", 60, 0),
            make_note("n2", 62, 1),
            make_note("n3", 64, 2),
            make_note("n4", 65, 3),
        ]
    )

    result = plan_fingering(timeline)

    assert all(note.finger in range(1, 6) for note in result.notes)
    assert all(note.finger_source == FingerSource.GENERATED for note in result.notes)
    assert all(note.explanation for note in result.notes)
    assert [note.finger for note in result.notes] == sorted(
        note.finger for note in result.notes
    )


def test_chord_uses_distinct_hand_ordered_fingers() -> None:
    right = make_timeline(
        [
            make_note("r1", 60, 0),
            make_note("r2", 64, 0),
            make_note("r3", 67, 0),
        ]
    )
    left = make_timeline(
        [
            make_note("l1", 48, 0, hand=Hand.LEFT),
            make_note("l2", 52, 0, hand=Hand.LEFT),
            make_note("l3", 55, 0, hand=Hand.LEFT),
        ]
    )

    right_result = plan_fingering(right)
    left_result = plan_fingering(left)
    right_fingers = [note.finger for note in right_result.sorted_notes()]
    left_fingers = [note.finger for note in left_result.sorted_notes()]

    assert len(set(right_fingers)) == 3
    assert right_fingers == sorted(right_fingers)
    assert len(set(left_fingers)) == 3
    assert left_fingers == sorted(left_fingers, reverse=True)


def test_repeated_note_prefers_same_finger() -> None:
    timeline = make_timeline(
        [
            make_note("n1", 60, 0),
            make_note("n2", 60, 1),
            make_note("n3", 60, 2),
        ]
    )

    result = plan_fingering(timeline)

    assert len({note.finger for note in result.notes}) == 1


def test_score_fingering_is_a_strong_preference_but_output_is_generated() -> None:
    timeline = make_timeline(
        [
            make_note(
                "n1",
                60,
                0,
                finger=4,
                finger_source=FingerSource.SCORE,
                finger_confidence=1,
            )
        ]
    )

    result = plan_fingering(timeline)

    assert result.notes[0].finger == 4
    assert result.notes[0].finger_source == FingerSource.GENERATED


def test_manual_fingering_is_preserved_by_planner() -> None:
    timeline = make_timeline(
        [
            make_note(
                "manual",
                60,
                0,
                finger=3,
                finger_source=FingerSource.MANUAL,
                finger_confidence=1,
            ),
            make_note("generated", 62, 1),
        ]
    )

    result = plan_fingering(timeline)

    assert result.notes[0].finger == 3
    assert result.notes[0].finger_source == FingerSource.MANUAL
    assert result.notes[1].finger_source == FingerSource.GENERATED


def test_forced_thumb_crossing_is_supported_and_explained() -> None:
    notes = [
        make_note(
            "before",
            60,
            0,
            finger=2,
            finger_source=FingerSource.MANUAL,
        ),
        make_note(
            "after",
            62,
            1,
            finger=1,
            finger_source=FingerSource.MANUAL,
        ),
    ]

    candidate = rank_fingering_candidates(notes, top_n=1)[0]

    assert candidate.fingers == {"before": 2, "after": 1}
    assert any("Thumb crossing" in reason for reason in candidate.explanations["after"])


def test_ranked_candidates_are_deterministic_and_sorted() -> None:
    notes = [
        make_note("n1", 60, 0),
        make_note("n2", 64, 1),
    ]

    first = rank_fingering_candidates(notes, top_n=3)
    second = rank_fingering_candidates(notes, top_n=3)

    assert first == second
    assert [candidate.total_cost for candidate in first] == sorted(
        candidate.total_cost for candidate in first
    )


def test_unreachable_chord_raises_stable_error() -> None:
    timeline = make_timeline(
        [
            make_note("low", 48, 0),
            make_note("high", 67, 0),
        ]
    )

    with pytest.raises(PianoHandError) as error:
        plan_fingering(timeline)

    assert error.value.code == ErrorCode.FINGERING_ERROR
    assert "span" in error.value.message.lower()


def test_constraint_checker_reports_conflict_and_nonblocking_span() -> None:
    notes = [
        make_note(
            "a",
            48,
            0,
            duration_beat=2,
            duration_sec=1,
            finger=1,
            finger_source=FingerSource.MANUAL,
        ),
        make_note(
            "b",
            62,
            0,
            finger=1,
            finger_source=FingerSource.MANUAL,
        ),
    ]

    violations = check_fingering_constraints(notes, max_chord_span=12)

    assert {violation.code for violation in violations} == {
        FingeringViolationCode.SAME_FINGER_CONFLICT,
        FingeringViolationCode.CHORD_SPAN_EXCEEDED,
    }
    assert any(violation.blocking for violation in violations)
    assert any(not violation.blocking for violation in violations)
