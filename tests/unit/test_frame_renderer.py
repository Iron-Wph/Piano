from __future__ import annotations

import numpy as np

from piano_hand.models.motion import MotionFrame
from piano_hand.models.note import FingerSource, Hand, NoteEvent
from piano_hand.motion.keyboard_geometry import KeyboardGeometry
from piano_hand.motion.trajectory import MotionPlanner
from piano_hand.rendering.frame_renderer import FrameRenderer


def test_render_rgb_has_requested_shape_and_dtype() -> None:
    keyboard = KeyboardGeometry.from_pitches(
        [60, 64, 67], width=640, top=240, white_key_height=220
    )
    renderer = FrameRenderer(keyboard, width=640, height=480, show_measure=False)
    frame = MotionFrame(time_sec=0, pressed_keys=[])

    image = renderer.render_rgb(frame)

    assert image.shape == (480, 640, 3)
    assert image.dtype == np.uint8


def test_pressed_key_and_hand_change_pixels() -> None:
    event = NoteEvent(
        id="n1",
        pitch=60,
        pitch_name="C4",
        onset_beat=0.5,
        duration_beat=1.0,
        onset_sec=0.5,
        duration_sec=1.0,
        measure=1,
        hand=Hand.RIGHT,
        hand_confidence=1,
        finger=1,
        finger_source=FingerSource.GENERATED,
        finger_confidence=1,
    )
    keyboard = KeyboardGeometry.from_pitches(
        [60], width=640, top=240, white_key_height=220
    )
    planner = MotionPlanner([event], keyboard)
    renderer = FrameRenderer(keyboard, width=640, height=480, show_measure=False)

    blank = renderer.render_rgb(MotionFrame(time_sec=0, pressed_keys=[]))
    active = renderer.render_rgb(planner.frame_at(0.5))

    assert np.count_nonzero(blank != active) > 500
    contact_x, contact_y = keyboard.contact_point(60)
    assert not np.array_equal(
        blank[int(contact_y), int(contact_x)],
        active[int(contact_y), int(contact_x)],
    )


def test_dark_and_light_themes_have_distinct_backgrounds() -> None:
    keyboard = KeyboardGeometry.from_pitches(
        [60], width=640, top=240, white_key_height=220
    )
    frame = MotionFrame(time_sec=0, pressed_keys=[])
    dark = FrameRenderer(
        keyboard, width=640, height=480, theme="dark", show_measure=False
    ).render_rgb(frame)
    light = FrameRenderer(
        keyboard, width=640, height=480, theme="light", show_measure=False
    ).render_rgb(frame)

    assert not np.array_equal(dark[0, 0], light[0, 0])
