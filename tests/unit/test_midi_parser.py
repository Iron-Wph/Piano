from __future__ import annotations

from types import SimpleNamespace

import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.parsers import midi_parser


def message(message_type: str, time: int = 0, **values: object) -> SimpleNamespace:
    return SimpleNamespace(type=message_type, time=time, **values)


class FakeMidiFile:
    def __init__(self, tracks: list[list[SimpleNamespace]], ticks_per_beat: int = 480) -> None:
        self.tracks = tracks
        self.ticks_per_beat = ticks_per_beat


def install_fake_mido(monkeypatch: pytest.MonkeyPatch, midi_file: FakeMidiFile) -> None:
    fake_module = SimpleNamespace(MidiFile=lambda _: midi_file)
    monkeypatch.setattr(midi_parser, "_require_mido", lambda: fake_module)


def test_midi_parses_tempo_notes_pedal_velocity_zero_and_tracks(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "example.mid"
    source.write_bytes(b"MThd-fake")
    midi_file = FakeMidiFile(
        tracks=[
            [
                message("time_signature", numerator=3, denominator=4),
                message("set_tempo", tempo=500_000),
                message("set_tempo", time=480, tempo=1_000_000),
            ],
            [
                message("note_on", note=60, velocity=90, channel=0),
                message("control_change", time=240, control=64, value=127, channel=0),
                message("note_on", time=240, note=60, velocity=0, channel=0),
                message("control_change", time=480, control=64, value=0, channel=0),
                message("note_on", note=64, velocity=70, channel=0),
                message("note_off", time=480, note=64, velocity=0, channel=0),
            ],
            [
                message("note_on", note=48, velocity=55, channel=1),
                message("note_off", time=480, note=48, velocity=0, channel=1),
            ],
        ]
    )
    install_fake_mido(monkeypatch, midi_file)

    timeline = midi_parser.parse_midi(source)
    c4 = next(note for note in timeline.notes if note.pitch == 60)
    e4 = next(note for note in timeline.notes if note.pitch == 64)
    c3 = next(note for note in timeline.notes if note.pitch == 48)

    assert [(change.beat, change.bpm) for change in timeline.tempo_map] == [
        (0.0, 120.0),
        (1.0, 60.0),
    ]
    assert c4.duration_beat == pytest.approx(2.0)
    assert c4.duration_sec == pytest.approx(1.5)
    assert c4.pedal_down is True
    assert "physical_duration_beat=1" in c4.explanation
    assert e4.onset_beat == pytest.approx(2.0)
    assert e4.duration_sec == pytest.approx(1.0)
    assert c3.track == 2
    assert c3.voice == "2"
    assert timeline.time_signatures[0].numerator == 3
    assert [(event.time_sec, event.down) for event in timeline.pedal_events] == [
        (pytest.approx(0.25), True),
        (pytest.approx(1.5), False),
    ]


def test_midi_auto_closes_unclosed_notes_with_warning(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "unclosed.mid"
    source.write_bytes(b"MThd-fake")
    midi_file = FakeMidiFile(
        tracks=[
            [
                message("note_on", note=67, velocity=80, channel=0),
                message("end_of_track", time=480),
            ]
        ]
    )
    install_fake_mido(monkeypatch, midi_file)

    timeline = midi_parser.parse_midi(source)

    assert timeline.notes[0].duration_beat == pytest.approx(1.0)
    assert any("unclosed MIDI note" in item for item in timeline.notes[0].explanation)


def test_midi_sustain_applies_across_tracks_on_the_same_channel(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "cross-track-pedal.mid"
    source.write_bytes(b"MThd-fake")
    midi_file = FakeMidiFile(
        tracks=[
            [
                message("control_change", time=240, control=64, value=127, channel=0),
                message("control_change", time=720, control=64, value=0, channel=0),
            ],
            [
                message("note_on", note=60, velocity=90, channel=0),
                message("note_on", time=480, note=60, velocity=0, channel=0),
            ],
            [
                message("note_on", note=48, velocity=70, channel=1),
                message("note_off", time=480, note=48, velocity=0, channel=1),
            ],
        ]
    )
    install_fake_mido(monkeypatch, midi_file)

    timeline = midi_parser.parse_midi(source)
    same_channel = next(note for note in timeline.notes if note.pitch == 60)
    other_channel = next(note for note in timeline.notes if note.pitch == 48)

    assert same_channel.track == 1
    assert same_channel.duration_beat == pytest.approx(2.0)
    assert same_channel.pedal_down is True
    assert "physical_duration_beat=1" in same_channel.explanation
    assert other_channel.track == 2
    assert other_channel.duration_beat == pytest.approx(1.0)
    assert other_channel.pedal_down is False


def test_midi_overlapping_same_pitch_notes_keep_fifo_velocity_and_track(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "overlap.mid"
    source.write_bytes(b"MThd-fake")
    midi_file = FakeMidiFile(
        tracks=[
            [],
            [
                message("note_on", note=60, velocity=90, channel=0),
                message("note_on", time=120, note=60, velocity=70, channel=0),
                message("note_off", time=120, note=60, velocity=0, channel=0),
                message("note_on", time=120, note=60, velocity=0, channel=0),
            ],
        ]
    )
    install_fake_mido(monkeypatch, midi_file)

    timeline = midi_parser.parse_midi(source)

    assert [note.velocity for note in timeline.notes] == [90, 70]
    assert [note.track for note in timeline.notes] == [1, 1]
    assert [note.duration_beat for note in timeline.notes] == pytest.approx([0.5, 0.5])


def test_midi_dependency_is_loaded_lazily_and_reports_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_: str) -> None:
        raise ModuleNotFoundError("mido")

    monkeypatch.setattr(midi_parser.importlib, "import_module", missing)

    with pytest.raises(PianoHandError) as error:
        midi_parser._require_mido()

    assert error.value.code == ErrorCode.DEPENDENCY_ERROR


def test_midi_wraps_library_parse_errors(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "broken.mid"
    source.write_bytes(b"not-midi")

    def fail(_: str) -> None:
        raise ValueError("bad header")

    monkeypatch.setattr(
        midi_parser,
        "_require_mido",
        lambda: SimpleNamespace(MidiFile=fail),
    )

    with pytest.raises(PianoHandError) as error:
        midi_parser.parse_midi(source)

    assert error.value.code == ErrorCode.PARSE_ERROR
    assert str(source) in error.value.message
