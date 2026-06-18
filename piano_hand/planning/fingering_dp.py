"""Hard-constraint plus dynamic-programming fingering planner."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import copysign

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import FingerSource, Hand, NoteEvent, ScoreTimeline
from piano_hand.planning.fingering_rules import raise_for_blocking_fingering_violations

_BLACK_KEY_CLASSES = {1, 3, 6, 8, 10}


@dataclass(frozen=True, slots=True)
class FingeringConfig:
    """All fingering limits and cost weights in one auditable object."""

    max_chord_span: int = 12
    onset_tolerance: float = 1e-6
    top_n_state_paths: int = 3
    semitones_per_finger: float = 1.8
    distance_mismatch_weight: float = 0.45
    hand_position_weight: float = 0.25
    same_finger_different_pitch_weight: float = 3.5
    repeated_note_finger_change_weight: float = 0.4
    thumb_crossing_weight: float = 2.4
    non_thumb_crossing_weight: float = 4.0
    black_key_thumb_weight: float = 1.2
    weak_finger_large_leap_weight: float = 1.4
    chord_stretch_weight: float = 0.2
    score_fingering_conflict_weight: float = 5.0


@dataclass(frozen=True, slots=True)
class FingeringCandidate:
    """One ranked complete fingering path for a single hand."""

    fingers: dict[str, int]
    total_cost: float
    explanations: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class _Path:
    cost: float
    assignments: tuple[tuple[int, ...], ...]
    reasons: tuple[tuple[str, ...], ...]


def plan_fingering(
    timeline: ScoreTimeline,
    config: FingeringConfig | None = None,
) -> ScoreTimeline:
    """Generate legal 1-5 fingerings for both hands.

    Existing manual fingerings are hard constraints. Score-provided fingerings
    are strong preferences, while generated fingerings may be recomputed.
    """

    cfg = config or FingeringConfig()
    unknown = [note.id for note in timeline.notes if note.hand == Hand.UNKNOWN]
    if unknown:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"Cannot plan fingering before hand assignment. Unknown hand: {', '.join(unknown)}.",
            "Run hand assignment or provide manual hand overrides first.",
        )

    updated: dict[str, NoteEvent] = {note.id: note for note in timeline.notes}
    for hand in (Hand.LEFT, Hand.RIGHT):
        hand_notes = [note for note in timeline.notes if note.hand == hand]
        if not hand_notes:
            continue
        candidates = rank_fingering_candidates(
            hand_notes,
            hand=hand,
            config=cfg,
            top_n=2,
        )
        best = candidates[0]
        margin = (
            max(0.0, candidates[1].total_cost - best.total_cost)
            if len(candidates) > 1
            else 4.0
        )
        confidence = min(0.98, 0.62 + min(0.32, margin * 0.06))

        for note in hand_notes:
            if note.finger_source == FingerSource.MANUAL:
                continue
            finger = best.fingers[note.id]
            reasons = list(best.explanations.get(note.id, ()))
            summary = (
                f"Generated fingering {finger} by dynamic programming; "
                f"path cost={best.total_cost:.2f}, alternative margin={margin:.2f}."
            )
            updated[note.id] = note.model_copy(
                update={
                    "finger": finger,
                    "finger_source": FingerSource.GENERATED,
                    "finger_confidence": confidence,
                    "explanation": [*note.explanation, summary, *reasons],
                }
            )

    result = timeline.model_copy(update={"notes": [updated[note.id] for note in timeline.notes]})
    raise_for_blocking_fingering_violations(
        result.notes,
        max_chord_span=cfg.max_chord_span,
    )
    return result


def rank_fingering_candidates(
    notes: list[NoteEvent],
    *,
    hand: Hand | None = None,
    config: FingeringConfig | None = None,
    top_n: int = 3,
) -> list[FingeringCandidate]:
    """Return the top-N deterministic fingering paths for one hand."""

    if top_n < 1:
        raise ValueError("top_n must be at least 1")
    if not notes:
        return []
    cfg = config or FingeringConfig()
    selected_hand = hand or notes[0].hand
    if selected_hand == Hand.UNKNOWN or any(note.hand != selected_hand for note in notes):
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            "Candidate ranking requires notes assigned to exactly one known hand.",
            "Separate notes by hand before calling the fingering planner.",
        )

    groups = _group_by_onset(
        sorted(notes, key=lambda note: (note.onset_beat, note.pitch, note.id)),
        cfg.onset_tolerance,
    )
    group_candidates = [
        _candidate_assignments(group, selected_hand, cfg)
        for group in groups
    ]
    state_limit = max(top_n, cfg.top_n_state_paths)
    paths_by_state: dict[tuple[int, ...], list[_Path]] = {}

    for assignment in group_candidates[0]:
        unary_cost, unary_reasons = _unary_cost(groups[0], assignment, cfg)
        paths_by_state[assignment] = [
            _Path(
                cost=unary_cost,
                assignments=(assignment,),
                reasons=(unary_reasons,),
            )
        ]

    for group_index in range(1, len(groups)):
        current_paths: dict[tuple[int, ...], list[_Path]] = {}
        for assignment in group_candidates[group_index]:
            unary_cost, unary_reasons = _unary_cost(
                groups[group_index],
                assignment,
                cfg,
            )
            expansions: list[_Path] = []
            for previous_assignment, previous_paths in paths_by_state.items():
                transition_cost, transition_reasons = _transition_cost(
                    groups[group_index - 1],
                    previous_assignment,
                    groups[group_index],
                    assignment,
                    selected_hand,
                    cfg,
                )
                for previous_path in previous_paths:
                    expansions.append(
                        _Path(
                            cost=previous_path.cost + unary_cost + transition_cost,
                            assignments=(*previous_path.assignments, assignment),
                            reasons=(
                                *previous_path.reasons,
                                (*unary_reasons, *transition_reasons),
                            ),
                        )
                    )
            expansions.sort(key=_path_sort_key)
            current_paths[assignment] = expansions[:state_limit]
        paths_by_state = current_paths

    complete = [path for paths in paths_by_state.values() for path in paths]
    complete.sort(key=_path_sort_key)
    return [
        _to_public_candidate(path, groups)
        for path in complete[:top_n]
    ]


def _candidate_assignments(
    group: list[NoteEvent],
    hand: Hand,
    config: FingeringConfig,
) -> list[tuple[int, ...]]:
    if len(group) > 5:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"A single hand cannot assign distinct fingers to {len(group)} simultaneous notes.",
            "Correct hand assignment or simplify the chord.",
        )
    span = max(note.pitch for note in group) - min(note.pitch for note in group)
    if span > config.max_chord_span:
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"Chord span {span} exceeds maximum {config.max_chord_span} semitones.",
            "Split the chord between hands or provide a reachable manual arrangement.",
        )

    candidates: list[tuple[int, ...]] = []
    for selected in combinations(range(1, 6), len(group)):
        assignment = selected if hand == Hand.RIGHT else tuple(reversed(selected))
        if _matches_manual_constraints(group, assignment):
            candidates.append(assignment)

    if not candidates:
        note_ids = ", ".join(note.id for note in group)
        raise PianoHandError(
            ErrorCode.FINGERING_ERROR,
            f"Manual fingering constraints are impossible for simultaneous notes: {note_ids}.",
            "Use distinct, hand-ordered fingers for the chord.",
        )
    return candidates


def _matches_manual_constraints(
    group: list[NoteEvent],
    assignment: tuple[int, ...],
) -> bool:
    for note, finger in zip(group, assignment, strict=True):
        if note.finger_source == FingerSource.MANUAL and note.finger != finger:
            return False
    return True


def _unary_cost(
    group: list[NoteEvent],
    assignment: tuple[int, ...],
    config: FingeringConfig,
) -> tuple[float, tuple[str, ...]]:
    cost = 0.0
    reasons: list[str] = []
    for note, finger in zip(group, assignment, strict=True):
        if finger == 1 and note.pitch % 12 in _BLACK_KEY_CLASSES:
            cost += config.black_key_thumb_weight
            reasons.append(f"Thumb-on-black-key penalty applied to note {note.id}.")
        if (
            note.finger_source == FingerSource.SCORE
            and note.finger is not None
            and note.finger != finger
        ):
            cost += config.score_fingering_conflict_weight
            reasons.append(
                f"Generated finger differs from score finger {note.finger} on note {note.id}."
            )

    if len(group) > 1:
        pitch_span = group[-1].pitch - group[0].pitch
        natural_span = _natural_index(assignment[-1], group[-1].hand) - _natural_index(
            assignment[0],
            group[0].hand,
        )
        stretch = abs(pitch_span - abs(natural_span) * config.semitones_per_finger)
        cost += stretch * config.chord_stretch_weight
        if stretch >= 3:
            reasons.append(f"Chord stretch mismatch contributed {stretch:.1f} semitones.")
    return cost, tuple(dict.fromkeys(reasons))


def _transition_cost(
    previous_group: list[NoteEvent],
    previous_assignment: tuple[int, ...],
    current_group: list[NoteEvent],
    current_assignment: tuple[int, ...],
    hand: Hand,
    config: FingeringConfig,
) -> tuple[float, tuple[str, ...]]:
    cost = 0.0
    reasons: list[str] = []
    previous_pairs = list(zip(previous_group, previous_assignment, strict=True))

    for note, finger in zip(current_group, current_assignment, strict=True):
        previous_note, previous_finger = min(
            previous_pairs,
            key=lambda pair: (abs(note.pitch - pair[0].pitch), pair[0].id),
        )
        pitch_delta = note.pitch - previous_note.pitch
        natural_delta = _natural_index(finger, hand) - _natural_index(previous_finger, hand)

        if pitch_delta == 0:
            if finger != previous_finger:
                cost += config.repeated_note_finger_change_weight
                reasons.append("Repeated note preferred the same finger.")
        else:
            if finger == previous_finger:
                cost += config.same_finger_different_pitch_weight
                reasons.append("Same finger moved between different pitches.")
            expected = abs(natural_delta) * config.semitones_per_finger
            mismatch = abs(abs(pitch_delta) - expected)
            cost += mismatch * config.distance_mismatch_weight
            if mismatch >= 3:
                reasons.append("Pitch distance and finger distance were mismatched.")

            if natural_delta and _sign(pitch_delta) != _sign(natural_delta):
                if 1 in {finger, previous_finger}:
                    cost += config.thumb_crossing_weight
                    reasons.append("Thumb crossing was used and penalized.")
                else:
                    cost += config.non_thumb_crossing_weight
                    reasons.append("Non-thumb finger crossing was penalized.")

            if abs(pitch_delta) >= 6 and finger in {4, 5}:
                cost += config.weak_finger_large_leap_weight
                reasons.append("A weak finger carried a large leap.")

    previous_center = _hand_position(previous_group, previous_assignment, hand)
    current_center = _hand_position(current_group, current_assignment, hand)
    movement = abs(current_center - previous_center)
    cost += movement * config.hand_position_weight
    if movement >= 5:
        reasons.append(f"Large hand-position movement of {movement:.1f} semitones.")
    return cost, tuple(dict.fromkeys(reasons))


def _hand_position(
    group: list[NoteEvent],
    assignment: tuple[int, ...],
    hand: Hand,
) -> float:
    offsets = []
    for note, finger in zip(group, assignment, strict=True):
        natural_index = _natural_index(finger, hand)
        offsets.append(note.pitch - (natural_index - 1) * 1.8)
    return sum(offsets) / len(offsets)


def _natural_index(finger: int, hand: Hand) -> int:
    return finger if hand == Hand.RIGHT else 6 - finger


def _sign(value: int) -> int:
    return int(copysign(1, value))


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


def _path_sort_key(path: _Path) -> tuple[float, tuple[tuple[int, ...], ...]]:
    return path.cost, path.assignments


def _to_public_candidate(
    path: _Path,
    groups: list[list[NoteEvent]],
) -> FingeringCandidate:
    fingers: dict[str, int] = {}
    explanations: dict[str, tuple[str, ...]] = {}
    for group, assignment, reasons in zip(
        groups,
        path.assignments,
        path.reasons,
        strict=True,
    ):
        default_reason = reasons or ("Natural ordered fingering with minimal movement.",)
        for note, finger in zip(group, assignment, strict=True):
            fingers[note.id] = finger
            explanations[note.id] = default_reason
    return FingeringCandidate(
        fingers=fingers,
        total_cost=path.cost,
        explanations=explanations,
    )
