"""Time-queryable hand trajectory planning for normalized notes."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from itertools import groupby

from piano_hand.models.motion import HandPose, MotionFrame
from piano_hand.models.note import Hand, NoteEvent
from piano_hand.motion.hand_pose import HandPoseFactory
from piano_hand.motion.interpolation import interpolate_hand_pose
from piano_hand.motion.keyboard_geometry import KeyboardGeometry


class MotionPhase(StrEnum):
    PREPARE = "prepare"
    PRESS = "press"
    HOLD = "hold"
    RELEASE = "release"
    TRANSITION = "transition"


@dataclass(frozen=True, slots=True)
class MotionKeyframe:
    time_sec: float
    hand: Hand
    phase: MotionPhase
    pose: HandPose
    pitches: tuple[int, ...] = ()
    fingers: tuple[int, ...] = ()
    measure: int | None = None


@dataclass(frozen=True, slots=True)
class _Chord:
    onset: float
    offset: float
    notes: tuple[NoteEvent, ...]

    @property
    def measure(self) -> int:
        return self.notes[0].measure


class MotionPlanner:
    """Plan readable skeleton motion and query a public MotionFrame by time."""

    def __init__(
        self,
        notes: Iterable[NoteEvent],
        keyboard: KeyboardGeometry | None = None,
        *,
        prepare_time: float = 0.22,
        release_time: float = 0.12,
    ) -> None:
        self.notes = tuple(sorted(notes, key=lambda note: (note.onset_sec, note.pitch, note.id)))
        if prepare_time < 0 or release_time < 0:
            raise ValueError("prepare_time and release_time must be non-negative")
        self.prepare_time = prepare_time
        self.release_time = release_time
        self.keyboard = keyboard or KeyboardGeometry.from_pitches(note.pitch for note in self.notes)
        self.pose_factory = HandPoseFactory(self.keyboard)
        self._chords = {
            Hand.LEFT: self._group_chords(Hand.LEFT),
            Hand.RIGHT: self._group_chords(Hand.RIGHT),
        }
        self._keyframes = {
            Hand.LEFT: self._build_keyframes(Hand.LEFT),
            Hand.RIGHT: self._build_keyframes(Hand.RIGHT),
        }

    @property
    def duration_sec(self) -> float:
        if not self.notes:
            return 0.0
        return max(note.offset_sec for note in self.notes) + self.release_time

    def keyframes_for_hand(self, hand: Hand | str) -> tuple[MotionKeyframe, ...]:
        return self._keyframes[Hand(hand)]

    def frame_at(self, time_sec: float) -> MotionFrame:
        """Return hand poses and exact physical key state at a playback time."""

        if time_sec < 0:
            raise ValueError("time_sec must be non-negative")
        pressed_notes = [
            note for note in self.notes if note.onset_sec <= time_sec < note.offset_sec
        ]
        active_fingers: dict[str, list[int]] = {}
        for hand in (Hand.LEFT, Hand.RIGHT):
            fingers = sorted(
                {
                    note.finger
                    for note in pressed_notes
                    if note.hand == hand and note.finger is not None
                }
            )
            if fingers:
                active_fingers[hand.value] = fingers
        measure = self._measure_at(time_sec)
        return MotionFrame(
            time_sec=time_sec,
            left=self._pose_at(Hand.LEFT, time_sec),
            right=self._pose_at(Hand.RIGHT, time_sec),
            pressed_keys=sorted({note.pitch for note in pressed_notes}),
            active_fingers=active_fingers,
            measure=measure,
        )

    def _group_chords(self, hand: Hand) -> tuple[_Chord, ...]:
        hand_notes = [note for note in self.notes if note.hand == hand]
        result: list[_Chord] = []
        for onset, grouped in groupby(hand_notes, key=lambda note: round(note.onset_sec, 6)):
            chord_notes = tuple(grouped)
            result.append(
                _Chord(
                    onset=float(onset),
                    offset=max(note.offset_sec for note in chord_notes),
                    notes=chord_notes,
                )
            )
        return tuple(result)

    def _build_keyframes(self, hand: Hand) -> tuple[MotionKeyframe, ...]:
        chords = self._chords[hand]
        if not chords:
            return ()
        keyframes: list[MotionKeyframe] = []
        for index, chord in enumerate(chords):
            previous_chord = chords[index - 1] if index > 0 else None
            next_chord = chords[index + 1] if index + 1 < len(chords) else None
            targets = self._targets_for_chord(chord)
            center_x = self.keyboard.cluster_center(note.pitch for note in chord.notes)[0]
            pitches = tuple(sorted(note.pitch for note in chord.notes))
            fingers = tuple(sorted(targets))
            prepare_at = max(0.0, chord.onset - self.prepare_time)
            if previous_chord is not None:
                prepare_at = min(chord.onset, max(prepare_at, previous_chord.offset))
            first_offset = min(note.offset_sec for note in chord.notes)
            hold_at = max(
                chord.onset,
                first_offset - min(0.05, (first_offset - chord.onset) / 2),
            )
            hover_pose = self.pose_factory.pose_for_targets(
                hand, targets, center_x=center_x, pressed=False
            )
            press_pose = self.pose_factory.pose_for_targets(
                hand, targets, center_x=center_x, pressed=True
            )
            keyframes.extend(
                [
                    MotionKeyframe(
                        time_sec=prepare_at,
                        hand=hand,
                        phase=MotionPhase.PREPARE,
                        pose=hover_pose,
                        pitches=pitches,
                        fingers=fingers,
                        measure=chord.measure,
                    ),
                    MotionKeyframe(
                        time_sec=chord.onset,
                        hand=hand,
                        phase=MotionPhase.PRESS,
                        pose=press_pose,
                        pitches=pitches,
                        fingers=fingers,
                        measure=chord.measure,
                    ),
                    MotionKeyframe(
                        time_sec=hold_at,
                        hand=hand,
                        phase=MotionPhase.HOLD,
                        pose=press_pose,
                        pitches=pitches,
                        fingers=fingers,
                        measure=chord.measure,
                    ),
                ]
            )
            offsets = sorted({note.offset_sec for note in chord.notes})
            for offset in offsets:
                released_notes = tuple(
                    note for note in chord.notes if abs(note.offset_sec - offset) <= 1e-9
                )
                released_pitches = tuple(sorted(note.pitch for note in released_notes))
                released_fingers = tuple(
                    sorted(
                        finger
                        for finger, note in self._finger_assignments_for_chord(chord).items()
                        if note in released_notes
                    )
                )
                keyframes.append(
                    MotionKeyframe(
                        time_sec=offset,
                        hand=hand,
                        phase=MotionPhase.RELEASE,
                        pose=self._pose_for_chord_at(
                            hand,
                            chord,
                            targets,
                            center_x=center_x,
                            time_sec=offset,
                        ),
                        pitches=released_pitches,
                        fingers=released_fingers,
                        measure=chord.measure,
                    )
                )
                if self.release_time > 0:
                    release_end = offset + self.release_time
                    if next_chord is None or release_end < next_chord.onset:
                        keyframes.append(
                            MotionKeyframe(
                                time_sec=release_end,
                                hand=hand,
                                phase=MotionPhase.RELEASE,
                                pose=self._pose_for_chord_at(
                                    hand,
                                    chord,
                                    targets,
                                    center_x=center_x,
                                    time_sec=release_end,
                                ),
                                pitches=released_pitches,
                                fingers=released_fingers,
                                measure=chord.measure,
                            )
                        )
            if next_chord is not None:
                next_targets = self._targets_for_chord(next_chord)
                next_prepare_at = max(
                    chord.offset,
                    next_chord.onset - self.prepare_time,
                )
                transition_at = min(
                    chord.offset + self.release_time,
                    next_prepare_at,
                )
                keyframes.append(
                    MotionKeyframe(
                        time_sec=transition_at,
                        hand=hand,
                        phase=MotionPhase.TRANSITION,
                        pose=self._pose_for_chord_at(
                            hand,
                            chord,
                            targets,
                            center_x=center_x,
                            time_sec=transition_at,
                        ),
                        pitches=tuple(sorted(note.pitch for note in next_chord.notes)),
                        fingers=tuple(sorted(next_targets)),
                        measure=next_chord.measure,
                    )
                )
        phase_order = {
            MotionPhase.TRANSITION: 0,
            MotionPhase.PREPARE: 1,
            MotionPhase.HOLD: 2,
            MotionPhase.RELEASE: 3,
            MotionPhase.PRESS: 4,
        }
        return tuple(
            sorted(keyframes, key=lambda keyframe: (keyframe.time_sec, phase_order[keyframe.phase]))
        )

    def _targets_for_chord(self, chord: _Chord) -> dict[int, tuple[float, float]]:
        return {
            finger: self.keyboard.contact_point(note.pitch)
            for finger, note in self._finger_assignments_for_chord(chord).items()
        }

    def _finger_assignments_for_chord(self, chord: _Chord) -> dict[int, NoteEvent]:
        assignments: dict[int, NoteEvent] = {}
        ordered = sorted(chord.notes, key=lambda note: note.pitch)
        used: set[int] = set()
        for index, note in enumerate(ordered):
            finger = note.finger
            if finger is None or finger in used:
                finger = self._fallback_finger(note.hand, index, len(ordered), used)
            used.add(finger)
            assignments[finger] = note
        return assignments

    def _pose_for_chord_at(
        self,
        hand: Hand,
        chord: _Chord,
        targets: dict[int, tuple[float, float]],
        *,
        center_x: float,
        time_sec: float,
    ) -> HandPose:
        pressed_pose = self.pose_factory.pose_for_targets(
            hand,
            targets,
            center_x=center_x,
            pressed=True,
        )
        hover_pose = self.pose_factory.pose_for_targets(
            hand,
            targets,
            center_x=center_x,
            pressed=False,
        )
        fingers = {finger: list(points) for finger, points in pressed_pose.fingers.items()}
        for finger, note in self._finger_assignments_for_chord(chord).items():
            if self.release_time <= 1e-9:
                release_progress = float(time_sec >= note.offset_sec)
            else:
                release_progress = (time_sec - note.offset_sec) / self.release_time
            released_pose = interpolate_hand_pose(
                pressed_pose,
                hover_pose,
                release_progress,
            )
            fingers[finger] = list(released_pose.fingers[finger])
        return HandPose(wrist=pressed_pose.wrist, fingers=fingers)

    @staticmethod
    def _fallback_finger(hand: Hand, index: int, count: int, used: set[int]) -> int:
        if count == 1:
            candidates = [3, 2, 4, 1, 5]
        elif hand == Hand.LEFT:
            candidates = list(reversed(range(1, 6)))
        else:
            candidates = list(range(1, 6))
        available = [finger for finger in candidates if finger not in used]
        return available[min(index, len(available) - 1)]

    def _pose_at(self, hand: Hand, time_sec: float) -> HandPose | None:
        keyframes = self._keyframes[hand]
        if not keyframes:
            return None
        exact = [
            keyframe.pose
            for keyframe in keyframes
            if abs(keyframe.time_sec - time_sec) <= 1e-9
        ]
        if exact:
            return exact[-1]
        if time_sec < keyframes[0].time_sec:
            return keyframes[0].pose
        if time_sec >= keyframes[-1].time_sec:
            return keyframes[-1].pose
        for start, end in zip(keyframes, keyframes[1:], strict=False):
            if start.time_sec <= time_sec <= end.time_sec:
                duration = end.time_sec - start.time_sec
                if duration <= 1e-9:
                    return end.pose
                return interpolate_hand_pose(
                    start.pose,
                    end.pose,
                    (time_sec - start.time_sec) / duration,
                )
        return keyframes[-1].pose

    def _measure_at(self, time_sec: float) -> int | None:
        started = [note for note in self.notes if note.onset_sec <= time_sec]
        if started:
            return max(started, key=lambda note: note.onset_sec).measure
        return self.notes[0].measure if self.notes else None
