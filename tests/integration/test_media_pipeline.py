from __future__ import annotations

import os
import shutil
import wave
from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pytest

from piano_hand.models import NoteEvent, ScoreSource, ScoreTimeline
from piano_hand.rendering.audio_renderer import render_timeline_audio
from piano_hand.rendering.media_probe import validate_media
from piano_hand.rendering.video_encoder import encode_rgb_frames


def test_real_ffmpeg_encodes_muted_h264_video(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe is not installed")

    output = encode_rgb_frames(
        color_frames(width=64, height=48, count=12),
        tmp_path / "muted.mp4",
        width=64,
        height=48,
        fps=12,
        ffmpeg_bin=ffmpeg,
    )
    info = validate_media(
        output,
        expected_width=64,
        expected_height=48,
        expected_fps=12,
        require_audio=False,
        min_size_bytes=500,
        ffprobe_bin=ffprobe,
    )

    assert info.video_codec == "h264"
    assert info.video_pixel_format == "yuv420p"
    assert info.has_audio is False
    assert info.duration_sec == pytest.approx(1.0, abs=0.05)


def test_real_ffmpeg_muxes_aac_audio_within_100ms(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe is not installed")

    wav_path = tmp_path / "silence.wav"
    write_silent_wav(wav_path, duration_sec=1.0)
    output = encode_rgb_frames(
        color_frames(width=64, height=48, count=12),
        tmp_path / "with-audio.mp4",
        width=64,
        height=48,
        fps=12,
        audio_path=wav_path,
        ffmpeg_bin=ffmpeg,
    )
    info = validate_media(
        output,
        expected_width=64,
        expected_height=48,
        expected_fps=12,
        require_audio=True,
        min_size_bytes=500,
        ffprobe_bin=ffprobe,
    )

    assert info.audio_codec == "aac"
    assert info.audio_sample_rate == 48_000
    assert info.audio_channels == 2


def test_real_fluidsynth_renders_wav_when_dependency_is_available(tmp_path: Path) -> None:
    fluidsynth = shutil.which("fluidsynth")
    if fluidsynth is None:
        pytest.skip("FluidSynth is not installed")
    soundfont_value = os.environ.get("PIANO_HAND_TEST_SOUNDFONT")
    if not soundfont_value:
        pytest.skip("PIANO_HAND_TEST_SOUNDFONT is not configured")
    soundfont = Path(soundfont_value)
    if not soundfont.is_file():
        pytest.skip("PIANO_HAND_TEST_SOUNDFONT does not point to a readable file")

    timeline = ScoreTimeline(
        source=ScoreSource(path="score.mid", type="midi", sha256="0" * 64),
        notes=[
            NoteEvent(
                id="n1",
                pitch=60,
                pitch_name="C4",
                onset_beat=0,
                duration_beat=1,
                onset_sec=0,
                duration_sec=0.5,
                measure=1,
                velocity=80,
            )
        ],
        duration_sec=0.5,
    )
    output = render_timeline_audio(
        timeline,
        tmp_path / "piano.wav",
        soundfont_path=soundfont,
        fluidsynth_bin=fluidsynth,
    )

    assert output is not None
    assert output.stat().st_size > 44


def color_frames(*, width: int, height: int, count: int) -> Iterator[np.ndarray]:
    for index in range(count):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:, :, 0] = index * 17
        frame[:, :, 1] = 255 - index * 11
        frame[:, :, 2] = 80
        yield frame


def write_silent_wav(path: Path, *, duration_sec: float) -> None:
    sample_rate = 48_000
    frame_count = round(sample_rate * duration_sec)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"\0" * frame_count * 2 * 2)
