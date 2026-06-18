from __future__ import annotations

import pytest

from piano_hand.motion.keyboard_geometry import (
    PIANO_HIGH_PITCH,
    PIANO_LOW_PITCH,
    KeyboardGeometry,
    is_black_pitch,
    pitch_name,
)


def test_pitch_class_and_name_mapping() -> None:
    assert pitch_name(60) == "C4"
    assert pitch_name(61) == "C#4"
    assert not is_black_pitch(60)
    assert is_black_pitch(61)


def test_geometry_is_deterministic_and_black_key_is_between_whites() -> None:
    first = KeyboardGeometry.from_pitches([60, 64, 67], width=800, top=300)
    second = KeyboardGeometry.from_pitches([67, 60, 64], width=800, top=300)

    assert first.visible_pitches == second.visible_pitches
    assert first.key_for_pitch(60) == second.key_for_pitch(60)
    c4 = first.key_for_pitch(60)
    c_sharp4 = first.key_for_pitch(61)
    d4 = first.key_for_pitch(62)
    assert c4.x < c_sharp4.center[0] < d4.right
    assert c_sharp4.height < c4.height
    assert c_sharp4.contact_point[1] < c4.contact_point[1]


def test_score_range_adds_context_white_keys() -> None:
    geometry = KeyboardGeometry.from_pitches([60, 72], context_white_keys=2)

    assert geometry.mode == "local"
    assert geometry.low_white_pitch == 57
    assert geometry.high_white_pitch == 76
    assert 60 in geometry.visible_pitches
    assert 72 in geometry.visible_pitches
    assert len(geometry.white_keys) == 12


def test_out_of_view_pitch_has_actionable_error() -> None:
    geometry = KeyboardGeometry.from_pitches([60], context_white_keys=0)

    with pytest.raises(KeyError, match="outside keyboard viewport"):
        geometry.key_for_pitch(72)


def test_full_mode_contains_exact_standard_88_key_range() -> None:
    geometry = KeyboardGeometry.from_pitches([60], width=1280, mode="full")

    assert geometry.mode == "full"
    assert geometry.visible_pitches == tuple(range(PIANO_LOW_PITCH, PIANO_HIGH_PITCH + 1))
    assert len(geometry.visible_pitches) == 88
    assert len(geometry.white_keys) == 52
    assert geometry.low_white_pitch == PIANO_LOW_PITCH
    assert geometry.high_white_pitch == PIANO_HIGH_PITCH


def test_full_mode_rejects_notes_outside_standard_piano_range() -> None:
    with pytest.raises(ValueError, match=r"21\.\.108"):
        KeyboardGeometry.from_pitches([20, 60], mode="full")


def test_white_key_width_is_exposed_for_hand_scaling() -> None:
    geometry = KeyboardGeometry.from_pitches([60, 64, 67], width=800)

    assert geometry.white_key_width == pytest.approx(geometry.white_keys[0].width)
    assert geometry.bottom == pytest.approx(
        geometry.top + geometry.white_key_height
    )
