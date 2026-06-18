from __future__ import annotations

import pytest

from piano_hand.models.note import FingerSource, Hand, NoteEvent
from piano_hand.motion.hand_pose import HandPoseFactory
from piano_hand.motion.interpolation import cubic_ease, hermite_point
from piano_hand.motion.keyboard_geometry import KeyboardGeometry
from piano_hand.motion.trajectory import MotionPhase, MotionPlanner


def note(
    note_id: str,
    pitch: int,
    onset: float,
    duration: float,
    hand: Hand,
    finger: int,
    measure: int = 1,
) -> NoteEvent:
    return NoteEvent(
        id=note_id,
        pitch=pitch,
        pitch_name="note",
        onset_beat=onset,
        duration_beat=duration,
        onset_sec=onset,
        duration_sec=duration,
        measure=measure,
        hand=hand,
        hand_confidence=1,
        finger=finger,
        finger_source=FingerSource.GENERATED,
        finger_confidence=1,
    )


def test_cubic_and_hermite_interpolation_boundaries() -> None:
    assert cubic_ease(-1) == 0
    assert cubic_ease(0) == 0
    assert cubic_ease(1) == 1
    assert cubic_ease(2) == 1
    assert hermite_point((1, 2), (9, 10), 0) == pytest.approx((1, 2))
    assert hermite_point((1, 2), (9, 10), 1) == pytest.approx((9, 10))


def test_key_times_touch_and_release_exactly() -> None:
    event = note("n1", 60, 1.0, 0.5, Hand.RIGHT, 1)
    keyboard = KeyboardGeometry.from_pitches([60], width=640, top=300, white_key_height=200)
    planner = MotionPlanner([event], keyboard)

    before = planner.frame_at(0.999)
    pressed = planner.frame_at(1.0)
    holding = planner.frame_at(1.499)
    released = planner.frame_at(1.5)

    assert before.pressed_keys == []
    assert pressed.pressed_keys == [60]
    assert holding.pressed_keys == [60]
    assert released.pressed_keys == []
    assert pressed.right is not None
    assert pressed.right.fingers[1][-1] == pytest.approx(keyboard.contact_point(60))
    assert pressed.active_fingers == {"right": [1]}


def test_note_at_zero_starts_in_press_pose_not_prepare_pose() -> None:
    event = note("n0", 60, 0.0, 0.5, Hand.RIGHT, 1)
    keyboard = KeyboardGeometry.from_pitches([60], width=640, top=300, white_key_height=200)
    frame = MotionPlanner([event], keyboard).frame_at(0.0)

    assert frame.right is not None
    assert frame.pressed_keys == [60]
    assert frame.right.fingers[1][-1] == pytest.approx(keyboard.contact_point(60))


def test_chord_targets_multiple_fingertips_and_has_all_motion_concepts() -> None:
    notes = [
        note("c", 60, 1.0, 1.0, Hand.RIGHT, 1),
        note("e", 64, 1.0, 1.0, Hand.RIGHT, 3),
        note("g", 67, 1.0, 1.0, Hand.RIGHT, 5),
        note("a", 69, 2.5, 0.5, Hand.RIGHT, 5, measure=2),
    ]
    keyboard = KeyboardGeometry.from_pitches([60, 64, 67, 69], width=800, top=350)
    planner = MotionPlanner(notes, keyboard)

    frame = planner.frame_at(1.0)
    assert frame.right is not None
    assert frame.pressed_keys == [60, 64, 67]
    for finger, pitch in ((1, 60), (3, 64), (5, 67)):
        assert frame.right.fingers[finger][-1] == pytest.approx(keyboard.contact_point(pitch))

    phases = {keyframe.phase for keyframe in planner.keyframes_for_hand(Hand.RIGHT)}
    assert {
        MotionPhase.PREPARE,
        MotionPhase.PRESS,
        MotionPhase.HOLD,
        MotionPhase.RELEASE,
        MotionPhase.TRANSITION,
    } <= phases


def test_chord_fingers_release_at_their_own_note_offsets() -> None:
    notes = [
        note("short", 60, 1.0, 0.5, Hand.RIGHT, 1),
        note("long", 67, 1.0, 1.0, Hand.RIGHT, 5),
        note("next", 72, 2.1, 0.4, Hand.RIGHT, 5),
    ]
    keyboard = KeyboardGeometry.from_pitches([60, 67, 72], width=800, top=350)
    planner = MotionPlanner(notes, keyboard)

    short_contact = keyboard.contact_point(60)
    long_contact = keyboard.contact_point(67)
    after_short_release = planner.frame_at(1.5 + planner.release_time)
    before_long_release = planner.frame_at(1.99)
    after_next_press = planner.frame_at(2.1 + 0.01)

    assert after_short_release.pressed_keys == [67]
    assert after_short_release.active_fingers == {"right": [5]}
    assert after_short_release.right is not None
    assert after_short_release.right.fingers[1][-1][1] > short_contact[1] + 10
    assert after_short_release.right.fingers[5][-1] == pytest.approx(long_contact)
    assert before_long_release.pressed_keys == [67]
    assert before_long_release.right is not None
    assert before_long_release.right.fingers[1][-1][1] > short_contact[1] + 10
    assert before_long_release.right.fingers[5][-1] == pytest.approx(long_contact)
    assert after_next_press.pressed_keys == [72]
    assert after_next_press.active_fingers == {"right": [5]}
    assert after_next_press.right is not None
    assert after_next_press.right.fingers[5][-1] == pytest.approx(keyboard.contact_point(72))

    release_starts = {
        keyframe.time_sec
        for keyframe in planner.keyframes_for_hand(Hand.RIGHT)
        if keyframe.phase == MotionPhase.RELEASE
    }
    assert {1.5, 2.0} <= release_starts


def test_wrist_moves_smoothly_toward_next_key_cluster() -> None:
    notes = [
        note("n1", 48, 0.5, 0.25, Hand.LEFT, 5),
        note("n2", 60, 1.5, 0.25, Hand.LEFT, 1),
    ]
    keyboard = KeyboardGeometry.from_pitches([48, 60], width=900, top=350)
    planner = MotionPlanner(notes, keyboard)
    start_x = planner.frame_at(0.5).left.wrist[0]  # type: ignore[union-attr]
    middle_x = planner.frame_at(1.0).left.wrist[0]  # type: ignore[union-attr]
    end_x = planner.frame_at(1.5).left.wrist[0]  # type: ignore[union-attr]

    assert start_x < middle_x < end_x


@pytest.mark.parametrize("hand", [Hand.LEFT, Hand.RIGHT])
def test_hand_points_from_lower_wrist_toward_keyboard(hand: Hand) -> None:
    keyboard = KeyboardGeometry.from_pitches(
        [60, 64, 67],
        width=800,
        top=300,
        white_key_height=240,
    )
    factory = HandPoseFactory(keyboard)
    pose = factory.neutral_pose(hand, center_x=400)

    assert all(points[-1][1] < pose.wrist[1] for points in pose.fingers.values())
    assert all(points[0][1] < pose.wrist[1] for points in pose.fingers.values())


def test_five_fingers_expand_from_rendered_white_key_width() -> None:
    keyboard = KeyboardGeometry.from_pitches(
        [60, 64, 67],
        width=800,
        top=300,
        white_key_height=240,
    )
    pose = HandPoseFactory(keyboard).neutral_pose(Hand.RIGHT, center_x=400)
    tips = [pose.fingers[finger][-1][0] for finger in range(1, 6)]

    expected_spacing = keyboard.white_key_width * 0.9
    assert tips == sorted(tips)
    for left, right in zip(tips, tips[1:], strict=False):
        assert right - left == pytest.approx(expected_spacing)


def test_single_finger_target_offsets_wrist_toward_hand_center() -> None:
    keyboard = KeyboardGeometry.from_pitches(
        [60],
        width=800,
        top=300,
        white_key_height=240,
    )
    target = keyboard.contact_point(60)
    factory = HandPoseFactory(keyboard)
    thumb_pose = factory.pose_for_targets(
        Hand.RIGHT,
        {1: target},
        pressed=True,
    )
    little_finger_pose = factory.pose_for_targets(
        Hand.RIGHT,
        {5: target},
        pressed=True,
    )

    assert thumb_pose.wrist[0] > target[0]
    assert little_finger_pose.wrist[0] < target[0]
