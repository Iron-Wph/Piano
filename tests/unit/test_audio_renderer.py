from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import mido
import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import NoteEvent, PedalEvent, ScoreSource, ScoreTimeline
from piano_hand.rendering.audio_renderer import (
    build_fluidsynth_command,
    render_timeline_audio,
    write_timeline_midi,
)


def make_timeline() -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path="score.mid", type="midi", sha256="0" * 64),
        notes=[
            NoteEvent(
                id="n1",
                pitch=60,
                pitch_name="C4",
                onset_beat=0,
                duration_beat=1,
                onset_sec=0.25,
                duration_sec=0.5,
                measure=1,
                velocity=90,
            )
        ],
        pedal_events=[
            PedalEvent(time_sec=0.1, down=True),
            PedalEvent(time_sec=0.7, down=False),
        ],
        duration_sec=1.0,
    )


def test_write_timeline_midi_preserves_final_seconds(tmp_path: Path) -> None:
    midi_path = write_timeline_midi(make_timeline(), tmp_path / "playback.mid")

    midi = mido.MidiFile(midi_path)
    absolute_ticks = 0
    observed: list[tuple[str, int, int | None]] = []
    for message in midi.tracks[0]:
        absolute_ticks += message.time
        if message.type in {"note_on", "note_off", "control_change"}:
            value = message.value if message.type == "control_change" else None
            observed.append((message.type, absolute_ticks, value))

    assert midi.ticks_per_beat == 1_000
    assert observed == [
        ("control_change", 100, 127),
        ("note_on", 250, None),
        ("control_change", 700, 0),
        ("note_off", 750, None),
    ]
    assert sum(message.time for message in midi.tracks[0]) == 1_000


def test_render_timeline_audio_supports_explicit_mute(tmp_path: Path) -> None:
    called = False

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    result = render_timeline_audio(
        make_timeline(),
        tmp_path / "muted.wav",
        enabled=False,
        runner=runner,
    )

    assert result is None
    assert called is False
    assert not (tmp_path / "muted.wav").exists()


def test_build_fluidsynth_command_is_non_interactive() -> None:
    command = build_fluidsynth_command(
        "fluidsynth",
        "piano.sf2",
        "playback.mid",
        "output.wav",
        sample_rate=48_000,
    )

    assert command == [
        "fluidsynth",
        "-ni",
        "-R",
        "0",
        "-C",
        "0",
        "-r",
        "48000",
        "-F",
        "output.wav",
        "piano.sf2",
        "playback.mid",
    ]


def test_render_timeline_audio_runs_fluidsynth_with_argument_array(tmp_path: Path) -> None:
    soundfont = tmp_path / "piano.sf2"
    soundfont.write_bytes(b"soundfont")
    captured: list[str] = []

    def runner(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        captured.extend(args)
        wav_path = Path(args[args.index("-F") + 1])
        wav_path.write_bytes(b"RIFF" + b"\0" * 100)
        return subprocess.CompletedProcess(args, 0, "", "")

    output = render_timeline_audio(
        make_timeline(),
        tmp_path / "audio.wav",
        soundfont_path=soundfont,
        fluidsynth_bin=sys.executable,
        runner=runner,
    )

    assert output == tmp_path / "audio.wav"
    assert captured[0] == sys.executable
    assert "-ni" in captured
    assert captured[-2] == str(soundfont)
    assert captured[-1].endswith("playback.mid")


def test_render_timeline_audio_normalizes_process_failure(tmp_path: Path) -> None:
    soundfont = tmp_path / "piano.sf2"
    soundfont.write_bytes(b"soundfont")

    def runner(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 2, "", "cannot load soundfont")

    with pytest.raises(PianoHandError) as exc_info:
        render_timeline_audio(
            make_timeline(),
            tmp_path / "audio.wav",
            soundfont_path=soundfont,
            fluidsynth_bin=sys.executable,
            runner=runner,
        )

    assert exc_info.value.code == ErrorCode.RENDER_ERROR
    assert "cannot load soundfont" in str(exc_info.value)


def test_render_timeline_audio_requires_soundfont(tmp_path: Path) -> None:
    with pytest.raises(PianoHandError) as exc_info:
        render_timeline_audio(make_timeline(), tmp_path / "audio.wav")

    assert exc_info.value.code == ErrorCode.DEPENDENCY_ERROR
