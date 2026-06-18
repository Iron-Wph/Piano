"""Semantic checks for normalized score timelines."""

from __future__ import annotations

from collections import defaultdict

from piano_hand.models import Hand, Issue, IssueSeverity, NoteEvent, ScoreTimeline

DEFAULT_MAX_CHORD_SPAN = 12
DEFAULT_MAX_LEAP = 12
DEFAULT_MIN_PREPARATION_SEC = 0.2
DEFAULT_LOW_HAND_CONFIDENCE = 0.5
ONSET_TOLERANCE = 1e-6


def check_score(
    timeline: ScoreTimeline,
    *,
    max_chord_span: int = DEFAULT_MAX_CHORD_SPAN,
    max_leap: int = DEFAULT_MAX_LEAP,
    min_preparation_sec: float = DEFAULT_MIN_PREPARATION_SEC,
    low_hand_confidence: float = DEFAULT_LOW_HAND_CONFIDENCE,
) -> list[Issue]:
    """Validate render-blocking score state and report risky movement heuristics."""

    issues: list[Issue] = []
    if not timeline.notes:
        return [
            Issue(
                code="EMPTY_SCORE",
                severity=IssueSeverity.ERROR,
                message="Timeline contains no notes.",
                location="timeline.notes",
                suggestion="Use a score containing at least one playable note.",
            )
        ]

    notes = timeline.sorted_notes()
    parser_warnings_seen: set[tuple[str, str]] = set()
    for note in notes:
        for explanation in note.explanation:
            if not explanation.startswith("warning:"):
                continue
            warning = explanation.removeprefix("warning:").strip()
            warning_key = (note.id, warning)
            if warning_key in parser_warnings_seen:
                continue
            parser_warnings_seen.add(warning_key)
            issues.append(
                Issue(
                    code="PARSER_WARNING",
                    severity=IssueSeverity.WARNING,
                    message=f"Note {note.id}: {warning or 'parser warning'}",
                    location=f"note:{note.id}",
                    suggestion="Review the source score and parser warning.",
                )
            )
        if note.hand == Hand.UNKNOWN:
            issues.append(
                Issue(
                    code="UNASSIGNED_HAND",
                    severity=IssueSeverity.ERROR,
                    message=f"Note {note.id} has no left/right hand assignment.",
                    location=f"note:{note.id}.hand",
                    suggestion="Set hand to left or right in fingering.csv.",
                )
            )
        if note.finger is None:
            issues.append(
                Issue(
                    code="MISSING_FINGER",
                    severity=IssueSeverity.ERROR,
                    message=f"Note {note.id} has no finger assignment.",
                    location=f"note:{note.id}.finger",
                    suggestion="Set finger to an integer from 1 through 5.",
                )
            )
        if note.hand != Hand.UNKNOWN and note.hand_confidence < low_hand_confidence:
            issues.append(
                Issue(
                    code="LOW_HAND_CONFIDENCE",
                    severity=IssueSeverity.WARNING,
                    message=(
                        f"Note {note.id} hand confidence {note.hand_confidence:.2f} "
                        f"is below {low_hand_confidence:.2f}."
                    ),
                    location=f"note:{note.id}.hand_confidence",
                    suggestion="Review the hand assignment in fingering.csv.",
                )
            )

    issues.extend(_check_overlapping_finger_assignments(notes))

    onset_groups: dict[int, list[NoteEvent]] = defaultdict(list)
    for note in notes:
        onset_groups[round(note.onset_sec / ONSET_TOLERANCE)].append(note)

    for simultaneous in onset_groups.values():
        issues.extend(_check_simultaneous_notes(simultaneous, max_chord_span))

    by_hand: dict[Hand, list[NoteEvent]] = defaultdict(list)
    for note in notes:
        if note.hand in {Hand.LEFT, Hand.RIGHT}:
            by_hand[note.hand].append(note)
    for hand, hand_notes in by_hand.items():
        previous = None
        for note in hand_notes:
            if previous is not None:
                interval = abs(note.pitch - previous.pitch)
                preparation = note.onset_sec - previous.offset_sec
                if interval > max_leap and preparation < min_preparation_sec:
                    issues.append(
                        Issue(
                            code="LARGE_FAST_LEAP",
                            severity=IssueSeverity.WARNING,
                            message=(
                                f"{hand.value} hand jumps {interval} semitones from "
                                f"{previous.id} to {note.id} with only "
                                f"{max(0.0, preparation):.3f}s preparation."
                            ),
                            location=f"note:{note.id}",
                            suggestion="Review the hand assignment or allow more preparation time.",
                        )
                    )
            previous = note
    return issues


def _check_simultaneous_notes(
    notes: list[NoteEvent], max_chord_span: int
) -> list[Issue]:
    issues: list[Issue] = []
    by_hand: dict[Hand, list[NoteEvent]] = defaultdict(list)
    for note in notes:
        if note.hand in {Hand.LEFT, Hand.RIGHT}:
            by_hand[note.hand].append(note)

    for hand, hand_notes in by_hand.items():
        if len(hand_notes) > 1:
            span = max(note.pitch for note in hand_notes) - min(
                note.pitch for note in hand_notes
            )
            if span > max_chord_span:
                issues.append(
                    Issue(
                        code="CHORD_SPAN_EXCEEDED",
                        severity=IssueSeverity.WARNING,
                        message=(
                            f"{hand.value} hand chord spans {span} semitones, "
                            f"above the configured limit {max_chord_span}."
                        ),
                        location=f"measure:{hand_notes[0].measure}",
                        suggestion="Review hand allocation or simplify the chord.",
                    )
                )

    left = by_hand.get(Hand.LEFT, [])
    right = by_hand.get(Hand.RIGHT, [])
    if left and right and max(note.pitch for note in left) > min(note.pitch for note in right):
        issues.append(
            Issue(
                code="HANDS_CROSS",
                severity=IssueSeverity.WARNING,
                message="Left- and right-hand pitches cross at the same onset.",
                location=f"measure:{notes[0].measure}",
                suggestion="Confirm that the crossing is intentional.",
            )
        )
    return issues


def _check_overlapping_finger_assignments(notes: list[NoteEvent]) -> list[Issue]:
    issues: list[Issue] = []
    by_assignment: dict[tuple[Hand, int], list[NoteEvent]] = defaultdict(list)
    for note in notes:
        if note.hand in {Hand.LEFT, Hand.RIGHT} and note.finger is not None:
            by_assignment[(note.hand, note.finger)].append(note)

    for (hand, finger), assigned_notes in by_assignment.items():
        active: list[NoteEvent] = []
        for note in sorted(
            assigned_notes,
            key=lambda item: (item.onset_sec, item.offset_sec, item.pitch, item.id),
        ):
            active = [other for other in active if other.offset_sec > note.onset_sec]
            for other in active:
                if other.pitch == note.pitch:
                    continue
                issues.append(
                    Issue(
                        code="SIMULTANEOUS_FINGER_CONFLICT",
                        severity=IssueSeverity.ERROR,
                        message=(
                            f"{hand.value} finger {finger} is assigned to different "
                            f"overlapping pitches {other.pitch} and {note.pitch}."
                        ),
                        location=f"note:{other.id},note:{note.id}",
                        suggestion="Assign distinct fingers to overlapping keys.",
                    )
                )
            active.append(note)
    return issues
