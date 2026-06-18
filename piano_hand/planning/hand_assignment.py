"""Deterministic left/right hand assignment for normalized scores."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import inf
from statistics import median

from piano_hand.models import Hand, NoteEvent, ScoreTimeline


@dataclass(frozen=True, slots=True)
class HandAssignmentConfig:
    """Centralized weights and limits for deterministic hand assignment."""

    default_midpoint: float = 60.0
    onset_tolerance: float = 1e-6
    clear_track_separation: float = 5.0
    max_simultaneous_span: int = 12
    max_enumerated_chord_size: int = 12
    pitch_side_weight: float = 1.0
    continuity_weight: float = 0.35
    track_mismatch_weight: float = 5.0
    crossing_weight: float = 4.0
    span_excess_weight: float = 8.0


def assign_hands(
    timeline: ScoreTimeline,
    config: HandAssignmentConfig | None = None,
) -> ScoreTimeline:
    """Return a copy of ``timeline`` with deterministic hand assignments.

    Existing non-unknown assignments always win. MusicXML then prefers staff
    information, while MIDI can use a clearly separated multi-track layout.
    Remaining notes are assigned by pitch side, continuity, crossing, and span
    costs.
    """

    cfg = config or HandAssignmentConfig()
    source_type = timeline.source.type.lower()
    if source_type == "musicxml":
        assigned = _assign_musicxml(timeline.notes, cfg)
    else:
        assigned = _assign_heuristically(
            timeline.notes,
            cfg,
            use_track_priors=source_type == "midi",
            explanation_prefix="MIDI heuristic" if source_type == "midi" else "Pitch heuristic",
        )
    by_id = {note.id: note for note in assigned}
    return timeline.model_copy(update={"notes": [by_id[note.id] for note in timeline.notes]})


def _assign_musicxml(
    notes: list[NoteEvent],
    config: HandAssignmentConfig,
) -> list[NoteEvent]:
    prepared: list[NoteEvent] = []
    for note in notes:
        if note.hand != Hand.UNKNOWN:
            prepared.append(
                _with_hand(
                    note,
                    note.hand,
                    max(note.hand_confidence, 0.98),
                    "MusicXML: preserved explicit/original hand assignment.",
                )
            )
        elif note.staff == 1:
            prepared.append(
                _with_hand(note, Hand.RIGHT, 0.98, "MusicXML: staff 1 assigned to right hand.")
            )
        elif note.staff == 2:
            prepared.append(
                _with_hand(note, Hand.LEFT, 0.98, "MusicXML: staff 2 assigned to left hand.")
            )
        else:
            prepared.append(note)

    return _assign_heuristically(
        prepared,
        config,
        use_track_priors=False,
        explanation_prefix="MusicXML cross-staff fallback",
    )


def _assign_heuristically(
    notes: list[NoteEvent],
    config: HandAssignmentConfig,
    *,
    use_track_priors: bool,
    explanation_prefix: str,
) -> list[NoteEvent]:
    ordered = sorted(notes, key=lambda note: (note.onset_beat, note.pitch, note.id))
    track_priors = _build_track_priors(ordered, config) if use_track_priors else {}
    result: dict[str, NoteEvent] = {}
    previous_pitch: dict[Hand, float | None] = {Hand.LEFT: None, Hand.RIGHT: None}
    global_midpoint = _global_midpoint(ordered, config)

    for group in _group_by_onset(ordered, config.onset_tolerance):
        midpoint = _dynamic_midpoint(previous_pitch, global_midpoint)
        fixed: dict[str, Hand] = {
            note.id: note.hand for note in group if note.hand != Hand.UNKNOWN
        }
        unknown = [note for note in group if note.hand == Hand.UNKNOWN]

        if unknown:
            ranked = _rank_group_assignments(
                group,
                unknown,
                fixed,
                track_priors,
                previous_pitch,
                midpoint,
                config,
            )
            best_cost, best_assignment = ranked[0]
            second_cost = ranked[1][0] if len(ranked) > 1 else best_cost + 4.0
            margin = max(0.0, second_cost - best_cost)
        else:
            best_assignment = fixed
            best_cost = 0.0
            margin = 4.0

        assigned_group: list[NoteEvent] = []
        for note in group:
            if note.hand != Hand.UNKNOWN:
                assigned = _with_hand(
                    note,
                    note.hand,
                    max(note.hand_confidence, 0.98),
                    f"{explanation_prefix}: preserved higher-priority hand assignment.",
                )
            else:
                hand = best_assignment[note.id]
                track_prior = track_priors.get(note.track)
                confidence = _assignment_confidence(margin, track_prior == hand)
                reasons = [
                    (
                        f"{explanation_prefix}: assigned {hand.value}; "
                        f"dynamic midpoint={midpoint:.1f}, group cost={best_cost:.2f}, "
                        f"alternative margin={margin:.2f}."
                    )
                ]
                if track_prior is not None:
                    reasons.append(
                        f"Clear track layout suggested {track_prior.value} hand "
                        f"for track {note.track}."
                    )
                if previous_pitch[hand] is not None:
                    reasons.append(
                        f"Continuity compared with previous {hand.value}-hand pitch "
                        f"{previous_pitch[hand]:.1f}."
                    )
                assigned = note.model_copy(
                    update={
                        "hand": hand,
                        "hand_confidence": confidence,
                        "explanation": [*note.explanation, *reasons],
                    }
                )
            result[note.id] = assigned
            assigned_group.append(assigned)

        for hand in (Hand.LEFT, Hand.RIGHT):
            hand_pitches = [note.pitch for note in assigned_group if note.hand == hand]
            if hand_pitches:
                previous_pitch[hand] = sum(hand_pitches) / len(hand_pitches)

    return [result[note.id] for note in notes]


def _build_track_priors(
    notes: list[NoteEvent],
    config: HandAssignmentConfig,
) -> dict[int | None, Hand]:
    pitches_by_track: dict[int, list[int]] = {}
    for note in notes:
        if note.track is not None:
            pitches_by_track.setdefault(note.track, []).append(note.pitch)
    if len(pitches_by_track) < 2:
        return {}

    ranked = sorted(
        ((float(median(pitches)), track) for track, pitches in pitches_by_track.items()),
        key=lambda item: (item[0], item[1]),
    )
    if ranked[-1][0] - ranked[0][0] < config.clear_track_separation:
        return {}

    split = len(ranked) // 2
    priors: dict[int | None, Hand] = {}
    for index, (_, track) in enumerate(ranked):
        priors[track] = Hand.LEFT if index < split else Hand.RIGHT
    return priors


def _rank_group_assignments(
    group: list[NoteEvent],
    unknown: list[NoteEvent],
    fixed: dict[str, Hand],
    track_priors: dict[int | None, Hand],
    previous_pitch: dict[Hand, float | None],
    midpoint: float,
    config: HandAssignmentConfig,
) -> list[tuple[float, dict[str, Hand]]]:
    if len(unknown) > config.max_enumerated_chord_size:
        greedy = {
            note.id: track_priors.get(
                note.track,
                Hand.LEFT if note.pitch < midpoint else Hand.RIGHT,
            )
            for note in unknown
        }
        assignment = {**fixed, **greedy}
        return [
            (
                _assignment_cost(
                    group,
                    assignment,
                    track_priors,
                    previous_pitch,
                    midpoint,
                    config,
                    hard_span=False,
                ),
                assignment,
            )
        ]

    candidates: list[tuple[float, dict[str, Hand]]] = []
    for choices in product((Hand.LEFT, Hand.RIGHT), repeat=len(unknown)):
        assignment = {**fixed, **dict(zip((note.id for note in unknown), choices, strict=True))}
        cost = _assignment_cost(
            group,
            assignment,
            track_priors,
            previous_pitch,
            midpoint,
            config,
            hard_span=True,
        )
        if cost < inf:
            candidates.append((cost, assignment))

    if not candidates:
        for choices in product((Hand.LEFT, Hand.RIGHT), repeat=len(unknown)):
            assignment = {
                **fixed,
                **dict(zip((note.id for note in unknown), choices, strict=True)),
            }
            candidates.append(
                (
                    _assignment_cost(
                        group,
                        assignment,
                        track_priors,
                        previous_pitch,
                        midpoint,
                        config,
                        hard_span=False,
                    ),
                    assignment,
                )
            )

    candidates.sort(
        key=lambda item: (
            item[0],
            tuple(item[1][note.id].value for note in sorted(group, key=lambda value: value.id)),
        )
    )
    return candidates


def _assignment_cost(
    group: list[NoteEvent],
    assignment: dict[str, Hand],
    track_priors: dict[int | None, Hand],
    previous_pitch: dict[Hand, float | None],
    midpoint: float,
    config: HandAssignmentConfig,
    *,
    hard_span: bool,
) -> float:
    cost = 0.0
    pitches: dict[Hand, list[int]] = {Hand.LEFT: [], Hand.RIGHT: []}

    for note in group:
        hand = assignment[note.id]
        pitches[hand].append(note.pitch)
        side_distance = (
            max(0.0, note.pitch - midpoint)
            if hand == Hand.LEFT
            else max(0.0, midpoint - note.pitch)
        )
        cost += side_distance * config.pitch_side_weight

        previous = previous_pitch[hand]
        if previous is not None:
            cost += abs(note.pitch - previous) * config.continuity_weight

        track_prior = track_priors.get(note.track)
        if track_prior is not None and track_prior != hand:
            cost += config.track_mismatch_weight

    for hand in (Hand.LEFT, Hand.RIGHT):
        if len(pitches[hand]) > 1:
            span = max(pitches[hand]) - min(pitches[hand])
            if hard_span and span > config.max_simultaneous_span:
                return inf
            cost += max(0, span - config.max_simultaneous_span) * config.span_excess_weight

    if pitches[Hand.LEFT] and pitches[Hand.RIGHT]:
        crossing = max(pitches[Hand.LEFT]) - min(pitches[Hand.RIGHT])
        if crossing > 0:
            cost += crossing * config.crossing_weight
    return cost


def _group_by_onset(notes: list[NoteEvent], tolerance: float) -> list[list[NoteEvent]]:
    groups: list[list[NoteEvent]] = []
    for note in notes:
        if not groups or abs(note.onset_beat - groups[-1][0].onset_beat) > tolerance:
            groups.append([note])
        else:
            groups[-1].append(note)
    return groups


def _global_midpoint(notes: list[NoteEvent], config: HandAssignmentConfig) -> float:
    if not notes:
        return config.default_midpoint
    observed = float(median(note.pitch for note in notes))
    return (observed + config.default_midpoint) / 2.0


def _dynamic_midpoint(
    previous_pitch: dict[Hand, float | None],
    fallback: float,
) -> float:
    left = previous_pitch[Hand.LEFT]
    right = previous_pitch[Hand.RIGHT]
    if left is not None and right is not None:
        return (left + right) / 2.0
    if left is not None:
        return (left + 5.0 + fallback) / 2.0
    if right is not None:
        return (right - 5.0 + fallback) / 2.0
    return fallback


def _assignment_confidence(margin: float, track_match: bool) -> float:
    confidence = 0.55 + min(0.35, margin * 0.07)
    if track_match:
        confidence = max(confidence, 0.9)
    return min(0.98, confidence)


def _with_hand(
    note: NoteEvent,
    hand: Hand,
    confidence: float,
    reason: str,
) -> NoteEvent:
    if reason in note.explanation:
        explanations = note.explanation
    else:
        explanations = [*note.explanation, reason]
    return note.model_copy(
        update={
            "hand": hand,
            "hand_confidence": confidence,
            "explanation": explanations,
        }
    )
