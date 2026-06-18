"""External media dependency helpers and ffprobe-based validation."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from piano_hand.errors import ErrorCode, PianoHandError

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Relevant media metadata returned by ffprobe."""

    path: Path
    size_bytes: int
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    fps: float | None
    duration_sec: float | None
    video_duration_sec: float | None
    audio_duration_sec: float | None
    video_codec: str | None
    audio_codec: str | None
    video_pixel_format: str | None
    audio_sample_rate: int | None
    audio_channels: int | None


def resolve_executable(name: str, explicit_path: str | Path | None = None) -> str:
    """Resolve an external executable or raise a stable dependency error."""

    candidate = str(explicit_path) if explicit_path is not None else name
    resolved = shutil.which(candidate)
    if resolved is None and explicit_path is not None:
        path = Path(explicit_path).expanduser()
        if path.is_file():
            resolved = str(path.resolve())
    if resolved is None:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            f"Required executable '{candidate}' is unavailable.",
            f"Install {name} or provide its executable path.",
        )
    return resolved


def run_external_command(
    args: Sequence[str | Path],
    *,
    failure_code: ErrorCode,
    action: str,
    runner: CommandRunner = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    """Run a command without a shell and normalize process failures."""

    command = [str(arg) for arg in args]
    try:
        result = runner(command, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            f"{action} could not start because '{command[0]}' was not found.",
            "Install the dependency or configure its executable path.",
        ) from exc
    except OSError as exc:
        raise PianoHandError(
            failure_code,
            f"{action} could not start: {exc}",
            "Check executable permissions and output paths.",
        ) from exc

    if result.returncode != 0:
        detail = _stderr_detail(result.stderr)
        raise PianoHandError(
            failure_code,
            f"{action} failed with exit code {result.returncode}.{detail}",
            "Inspect the input media and external tool installation.",
        )
    return result


def probe_media(
    media_path: str | Path,
    *,
    ffprobe_bin: str | Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> MediaInfo:
    """Inspect a media file with ffprobe."""

    path = Path(media_path)
    if not path.is_file():
        raise PianoHandError(
            ErrorCode.INPUT_ERROR,
            f"Media file does not exist: {path}",
            "Render or select an existing media file.",
        )

    executable = resolve_executable("ffprobe", ffprobe_bin)
    result = run_external_command(
        [
            executable,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ],
        failure_code=ErrorCode.ENCODE_ERROR,
        action=f"ffprobe inspection of '{path}'",
        runner=runner,
    )
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PianoHandError(
            ErrorCode.ENCODE_ERROR,
            f"ffprobe returned invalid JSON for '{path}'.",
            "Verify that ffprobe matches the installed FFmpeg version.",
        ) from exc

    streams = payload.get("streams", [])
    if not isinstance(streams, list):
        streams = []
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    format_data = payload.get("format", {})
    if not isinstance(format_data, dict):
        format_data = {}

    format_duration = _positive_float(format_data.get("duration"))
    video_duration = _stream_duration(video) if video else None
    audio_duration = _stream_duration(audio) if audio else None
    duration = format_duration or _maximum_optional(video_duration, audio_duration)

    return MediaInfo(
        path=path,
        size_bytes=path.stat().st_size,
        has_video=video is not None,
        has_audio=audio is not None,
        width=_positive_int(video.get("width")) if video else None,
        height=_positive_int(video.get("height")) if video else None,
        fps=_frame_rate(video) if video else None,
        duration_sec=duration,
        video_duration_sec=video_duration or format_duration if video else None,
        audio_duration_sec=audio_duration or format_duration if audio else None,
        video_codec=_optional_string(video.get("codec_name")) if video else None,
        audio_codec=_optional_string(audio.get("codec_name")) if audio else None,
        video_pixel_format=_optional_string(video.get("pix_fmt")) if video else None,
        audio_sample_rate=_positive_int(audio.get("sample_rate")) if audio else None,
        audio_channels=_positive_int(audio.get("channels")) if audio else None,
    )


def validate_media(
    media_path: str | Path,
    *,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: float | None = None,
    require_audio: bool = False,
    min_size_bytes: int = 1,
    fps_tolerance: float = 0.01,
    max_av_delta_sec: float = 0.1,
    ffprobe_bin: str | Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> MediaInfo:
    """Probe and enforce the post-render media quality rules."""

    info = probe_media(media_path, ffprobe_bin=ffprobe_bin, runner=runner)
    problems: list[str] = []
    if info.size_bytes < min_size_bytes:
        problems.append(f"file size {info.size_bytes} is below {min_size_bytes} bytes")
    if not info.has_video:
        problems.append("video stream is missing")
    if require_audio and not info.has_audio:
        problems.append("audio stream is missing")
    if expected_width is not None and info.width != expected_width:
        problems.append(f"width is {info.width}, expected {expected_width}")
    if expected_height is not None and info.height != expected_height:
        problems.append(f"height is {info.height}, expected {expected_height}")
    if expected_fps is not None and (
        info.fps is None or abs(info.fps - expected_fps) > fps_tolerance
    ):
        problems.append(f"fps is {info.fps}, expected {expected_fps}")
    if info.duration_sec is None or info.duration_sec <= 0:
        problems.append("media duration is unavailable or zero")
    if (
        info.has_video
        and info.has_audio
        and info.video_duration_sec is not None
        and info.audio_duration_sec is not None
        and abs(info.video_duration_sec - info.audio_duration_sec) > max_av_delta_sec
    ):
        delta = abs(info.video_duration_sec - info.audio_duration_sec)
        problems.append(
            f"audio/video duration delta is {delta:.3f}s, limit is {max_av_delta_sec:.3f}s"
        )

    if problems:
        raise PianoHandError(
            ErrorCode.ENCODE_ERROR,
            f"Media validation failed for '{info.path}': {'; '.join(problems)}.",
            "Review render settings and FFmpeg output.",
        )
    return info


def _first_stream(streams: list[Any], codec_type: str) -> dict[str, Any] | None:
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == codec_type:
            return stream
    return None


def _stream_duration(stream: dict[str, Any]) -> float | None:
    duration = _positive_float(stream.get("duration"))
    if duration is not None:
        return duration
    duration_ts = _positive_float(stream.get("duration_ts"))
    time_base = _rational(stream.get("time_base"))
    if duration_ts is not None and time_base is not None:
        return duration_ts * time_base
    return None


def _frame_rate(stream: dict[str, Any]) -> float | None:
    return _rational(stream.get("avg_frame_rate")) or _rational(stream.get("r_frame_rate"))


def _rational(value: object) -> float | None:
    if not isinstance(value, str) or not value or value == "0/0":
        return None
    try:
        numerator, denominator = value.split("/", maxsplit=1)
        denominator_value = float(denominator)
        if denominator_value == 0:
            return None
        result = float(numerator) / denominator_value
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _positive_float(value: object) -> float | None:
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _positive_int(value: object) -> int | None:
    try:
        result = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _maximum_optional(*values: float | None) -> float | None:
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _stderr_detail(stderr: str | bytes | None) -> str:
    if not stderr:
        return ""
    if isinstance(stderr, bytes):
        text = stderr.decode(errors="replace")
    else:
        text = stderr
    compact = " ".join(text.strip().split())
    if not compact:
        return ""
    return f" stderr: {compact[-2000:]}"
