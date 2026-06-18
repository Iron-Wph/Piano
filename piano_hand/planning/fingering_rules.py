"""Reusable fingering constraint checks for planning and validation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import Hand, NoteEvent


class FingeringViolationCode(StrEnum):
    UNKNOWN_HAND = "UNKNOWN_HAND"
    MISSING_FINGER = "MISSING_FINGER"
    SAME_FINGER_CONFLICT = "SAME_FINGER_CONFLICT"
    CHORD_SPAN_EXCEEDED = "CHORD_SPAN_EXCEEDED"


@dataclass(frozen=True, slots=True)
class FingeringViolation:
    code: FingeringViolationCode
    message: str
    note_ids: tuple[str, ...]
    blocking: bool


def check_fingering_constraints(
    notes: list[NoteEvent],
    *,
    max_chord_span: int = 12,
    onset_tolerance: float = 1e-6,
) -> list[FingeringViolation]:
    """Return deterministic constraint violations without mutating notes."""

    violations: list[FingeringViolation] = []
    ordered = sorted(notes, key=lambda note: (note.onset_beat, note.pitch, note.id))

    for note in ordered:
        if note.hand == Hand.UNKNOWN:
            violations.append(
                FingeringViolation(
                    code=FingeringViolationCode.UNKNOWN_HAND,
                    message=f"Note {note.id} has no left/right hand assignment.",
                    note_ids=(note.id,),
                    blocking=True,
                )
            )
        if note.finger is None:
            violations.append(
                FingeringViolation(
                    code=FingeringViolationCode.MISSING_FINGER,
                    message=f"Note {note.id} has no fingering.",
                    note_ids=(note.id,),
                    blocking=True,
                )
            )

    for index, first in enumerate(ordered):
        if first.hand == Hand.UNKNOWN or first.finger is None:
            continue
        for second in ordered[index + 1 :]:
            if second.onset_beat >= first.offset_beat - onset_tolerance:
                break
            if (
                second.hand == first.hand
                and second.finger == first.finger
                and second.pitch != first.pitch
                and second.offset_beat > first.onset_beat + onset_tolerance
            ):
                violations.append(
                    FingeringViolation(
                        code=FingeringViolationCode.SAME_FINGER_CONFLICT,
                        message=(
                            f"{first.hand.value} finger {first.finger} cannot hold pitches "
                            f"{first.pitch} and {second.pitch} at the same time."
                        ),
                        note_ids=(first.id, second.id),
                        blocking=True,
                    )
                )

    for group in _group_by_onset(ordered, onset_tolerance):
        for hand in (Hand.LEFT, Hand.RIGHT):
            hand_notes = [note for note in group if note.hand == hand]
            if len(hand_notes) < 2:
                continue
            span = max(note.pitch for note in hand_notes) - min(note.pitch for note in hand_notes)
            if span > max_chord_span:
                violations.append(
                    FingeringViolation(
                        code=FingeringViolationCode.CHORD_SPAN_EXCEEDED,
                        message=(
                            f"{hand.value}-hand chord span {span} exceeds configured "
                            f"maximum {max_chord_span} semitones."
                        ),
                        note_ids=tuple(note.id for note in hand_notes),
                        blocking=False,
                    )
                )

    return violations


def raise_for_blocking_fingering_violations(
    notes: list[NoteEvent],
    *,
    max_chord_span: int = 12,
) -> None:
    """Raise a stable application error for the first blocking violation."""

    violations = check_fingering_constraints(notes, max_chord_span=max_chord_span)
    blocking = [violation for violation in violations if violation.blocking]
    if not blocking:
        return
    first = blocking[0]
    affected = ", ".join(first.note_ids)
    raise PianoHandError(
        ErrorCode.FINGERING_ERROR,
        f"{first.message} Affected note IDs: {affected}.",
        "Correct the hand/finger assignment or update the manual override CSV.",
    )


def _group_by_onset(
    notes: list[NoteEvent],
    tolerance: float,
) -> list[list[NoteEvent]]:
    groups: list[list[NoteEvent]] = []
    for note in notes:
        if not groups or abs(note.onset_beat - groups[-1][0].onset_beat) > tolerance:
            groups.append([note])
        else:
            groups[-1].append(note)
    return groups
