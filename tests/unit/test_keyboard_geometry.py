from __future__ import annotations

import pytest

from piano_hand.motion.keyboard_geometry import KeyboardGeometry, is_black_pitch, pitch_name


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

    assert geometry.low_white_pitch == 57
    assert geometry.high_white_pitch == 76
    assert 60 in geometry.visible_pitches
    assert 72 in geometry.visible_pitches
    assert len(geometry.white_keys) == 12


def test_out_of_view_pitch_has_actionable_error() -> None:
    geometry = KeyboardGeometry.from_pitches([60], context_white_keys=0)

    with pytest.raises(KeyError, match="outside keyboard viewport"):
        geometry.key_for_pitch(72)
