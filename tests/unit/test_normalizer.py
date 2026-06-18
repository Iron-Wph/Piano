from __future__ import annotations

import hashlib

import pytest

from piano_hand.models import FingerSource
from piano_hand.parsers.normalizer import RawNote, TempoMap, build_timeline, pitch_name, sha256_file


def test_tempo_map_converts_across_multiple_tempo_regions() -> None:
    tempo = TempoMap([(0.0, 120.0), (1.0, 60.0), (3.0, 240.0)])

    assert tempo.seconds_at(0.5) == pytest.approx(0.25)
    assert tempo.seconds_at(2.0) == pytest.approx(1.5)
    assert tempo.duration_seconds(0.5, 3.0) == pytest.approx(2.375)


def test_build_timeline_assigns_stable_ids_seconds_and_defaults(tmp_path) -> None:
    source = tmp_path / "score.xml"
    source.write_text("<score/>", encoding="utf-8")
    notes = [
        RawNote(pitch=64, onset_beat=1.0, duration_beat=1.0, measure=1, voice="1"),
        RawNote(
            pitch=60,
            onset_beat=0.0,
            duration_beat=1.0,
            measure=1,
            voice="1",
            finger=1,
            finger_source=FingerSource.SCORE,
            finger_confidence=1.0,
        ),
    ]

    timeline = build_timeline(
        path=source,
        source_type="musicxml",
        raw_notes=reversed(notes),
        tempo_changes=[],
        time_signatures=[],
    )

    assert [note.pitch for note in timeline.notes] == [60, 64]
    assert [note.id for note in timeline.notes] == [
        "m0001-t00-v1-n0001",
        "m0001-t00-v1-n0002",
    ]
    assert timeline.notes[1].onset_sec == pytest.approx(0.5)
    assert timeline.tempo_map[0].bpm == pytest.approx(120)
    assert timeline.time_signatures[0].numerator == 4


def test_sha256_and_pitch_names_are_deterministic(tmp_path) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"piano-hand")

    assert sha256_file(source) == hashlib.sha256(b"piano-hand").hexdigest()
    assert pitch_name(21) == "A0"
    assert pitch_name(60) == "C4"
    assert pitch_name(108) == "C8"


def test_pitch_name_rejects_out_of_range_pitch() -> None:
    with pytest.raises(ValueError):
        pitch_name(128)
