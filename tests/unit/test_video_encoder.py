from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.rendering.media_probe import probe_media, validate_media
from piano_hand.rendering.video_encoder import build_ffmpeg_command, encode_rgb_frames


def test_build_ffmpeg_command_configures_h264_and_aac() -> None:
    command = build_ffmpeg_command(
        "ffmpeg",
        "output.mp4",
        width=1280,
        height=720,
        fps=30,
        audio_path="audio.wav",
    )

    assert command[:4] == ["ffmpeg", "-y", "-hide_banner", "-loglevel"]
    assert ["-f", "rawvideo"] == command[command.index("-f") : command.index("-f") + 2]
    assert command[command.index("-c:v") + 1] == "libx264"
    assert command[command.index("-pix_fmt", command.index("-c:v")) + 1] == "yuv420p"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-ar") + 1] == "48000"
    assert "-shortest" in command
    assert command[-1] == "output.mp4"


def test_build_ffmpeg_command_rejects_odd_yuv420p_dimensions() -> None:
    with pytest.raises(PianoHandError) as exc_info:
        build_ffmpeg_command("ffmpeg", "output.mp4", width=63, height=48, fps=30)

    assert exc_info.value.code == ErrorCode.CONFIG_ERROR


def test_encode_rgb_frames_rejects_empty_stream(tmp_path: Path) -> None:
    with pytest.raises(PianoHandError) as exc_info:
        encode_rgb_frames([], tmp_path / "empty.mp4", width=64, height=48, fps=30)

    assert exc_info.value.code == ErrorCode.RENDER_ERROR


def test_encode_rgb_frames_rejects_wrong_frame_size(tmp_path: Path) -> None:
    process = FakeProcess(return_code=0)

    with pytest.raises(PianoHandError) as exc_info:
        encode_rgb_frames(
            [b"\0" * 10],
            tmp_path / "bad.mp4",
            width=64,
            height=48,
            fps=30,
            ffmpeg_bin=sys.executable,
            process_factory=lambda *args, **kwargs: process,
        )

    assert exc_info.value.code == ErrorCode.RENDER_ERROR


def test_encode_rgb_frames_normalizes_ffmpeg_failure(tmp_path: Path) -> None:
    process = FakeProcess(return_code=1, stderr=b"encoder unavailable")

    with pytest.raises(PianoHandError) as exc_info:
        encode_rgb_frames(
            [b"\0" * (64 * 48 * 3)],
            tmp_path / "failed.mp4",
            width=64,
            height=48,
            fps=30,
            ffmpeg_bin=sys.executable,
            process_factory=lambda *args, **kwargs: process,
        )

    assert exc_info.value.code == ErrorCode.ENCODE_ERROR
    assert "encoder unavailable" in str(exc_info.value)


def test_probe_media_parses_stream_metadata(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"media")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "duration": "2.000",
            },
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "duration": "1.950",
            },
        ],
        "format": {"duration": "2.000"},
    }

    def runner(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    info = probe_media(media, ffprobe_bin=sys.executable, runner=runner)

    assert info.has_video is True
    assert info.has_audio is True
    assert (info.width, info.height, info.fps) == (1280, 720, 30.0)
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.audio_sample_rate == 48_000


def test_validate_media_rejects_av_delta_over_100ms(tmp_path: Path) -> None:
    media = tmp_path / "video.mp4"
    media.write_bytes(b"media")
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 64,
                "height": 48,
                "avg_frame_rate": "12/1",
                "duration": "1.000",
            },
            {
                "codec_type": "audio",
                "duration": "0.850",
            },
        ],
        "format": {"duration": "1.000"},
    }

    def runner(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 0, json.dumps(payload), "")

    with pytest.raises(PianoHandError) as exc_info:
        validate_media(
            media,
            expected_width=64,
            expected_height=48,
            expected_fps=12,
            require_audio=True,
            ffprobe_bin=sys.executable,
            runner=runner,
        )

    assert exc_info.value.code == ErrorCode.ENCODE_ERROR
    assert "0.150s" in str(exc_info.value)


class FakeProcess:
    def __init__(self, *, return_code: int, stderr: bytes = b"") -> None:
        self.stdin = io.BytesIO()
        self.stderr = io.BytesIO(stderr)
        self.return_code = return_code

    def wait(self) -> int:
        return self.return_code
