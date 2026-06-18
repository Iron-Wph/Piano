"""External dependency and rendered-media checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from piano_hand.config import ResolvedProject
from piano_hand.models import Issue, IssueSeverity

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def command_version(
    command: str,
    *,
    args: tuple[str, ...] = ("-version",),
    runner: CommandRunner = subprocess.run,
) -> tuple[bool, str | None]:
    executable = shutil.which(command)
    if not executable:
        return False, None
    try:
        result = runner(
            [executable, *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False, None
    text = (result.stdout or result.stderr or "").strip().splitlines()
    return result.returncode == 0, text[0] if text else None


def check_dependencies(
    project: ResolvedProject | None = None,
    *,
    soundfont: Path | None = None,
    require_audio: bool = True,
    runner: CommandRunner = subprocess.run,
) -> tuple[list[Issue], dict[str, str | None]]:
    """Check required local executables and the active SoundFont."""

    issues: list[Issue] = []
    environment: dict[str, str | None] = {}
    commands = {
        "ffmpeg": ("-version",),
        "ffprobe": ("-version",),
        "fluidsynth": ("--version",),
    }
    for command, args in commands.items():
        ok, version = command_version(command, args=args, runner=runner)
        environment[command] = version
        audio_required = project.config.audio.enabled if project is not None else require_audio
        required = command != "fluidsynth" or audio_required
        if required and not ok:
            issues.append(
                Issue(
                    code=f"MISSING_{command.upper()}",
                    severity=IssueSeverity.ERROR,
                    message=f"Required executable is unavailable: {command}.",
                    location=command,
                    suggestion=f"Install {command} and add it to PATH.",
                )
            )

    active_soundfont = soundfont
    if project is not None:
        active_soundfont = project.soundfont_path
        soundfont_required = project.config.audio.enabled
    else:
        soundfont_required = require_audio
    environment["soundfont"] = str(active_soundfont) if active_soundfont else None
    if soundfont_required:
        if active_soundfont is None:
            issues.append(
                Issue(
                    code="MISSING_SOUNDFONT",
                    severity=IssueSeverity.ERROR,
                    message="Audio is enabled but no SoundFont is configured.",
                    location="audio.soundfont_path",
                    suggestion=(
                        "Set audio.soundfont_path or the PIANO_HAND_SOUNDFONT environment variable."
                    ),
                )
            )
        elif not active_soundfont.is_file():
            issues.append(
                Issue(
                    code="INVALID_SOUNDFONT",
                    severity=IssueSeverity.ERROR,
                    message=f"SoundFont does not exist: {active_soundfont}",
                    location="audio.soundfont_path",
                    suggestion="Configure a readable, legally licensed .sf2 file.",
                )
            )
        elif not os.access(active_soundfont, os.R_OK):
            issues.append(
                Issue(
                    code="INVALID_SOUNDFONT",
                    severity=IssueSeverity.ERROR,
                    message=f"SoundFont is not readable: {active_soundfont}",
                    location="audio.soundfont_path",
                    suggestion="Grant read permission or configure another .sf2 file.",
                )
            )
        elif active_soundfont.suffix.lower() != ".sf2":
            issues.append(
                Issue(
                    code="INVALID_SOUNDFONT",
                    severity=IssueSeverity.ERROR,
                    message=f"SoundFont must use the .sf2 extension: {active_soundfont}",
                    location="audio.soundfont_path",
                    suggestion="Configure a valid .sf2 SoundFont.",
                )
            )
        elif not active_soundfont.exists() or not active_soundfont.stat().st_size:
            issues.append(
                Issue(
                    code="INVALID_SOUNDFONT",
                    severity=IssueSeverity.ERROR,
                    message=f"SoundFont is empty or unreadable: {active_soundfont}",
                    location="audio.soundfont_path",
                    suggestion="Configure a readable, non-empty .sf2 SoundFont.",
                )
            )
    return issues, environment


def inspect_rendered_media(
    output: str | Path,
    project: ResolvedProject,
    *,
    runner: CommandRunner = subprocess.run,
    minimum_size: int = 1024,
) -> list[Issue]:
    """Use ffprobe JSON to verify streams, dimensions, FPS, and A/V duration."""

    media = Path(output)
    issues: list[Issue] = []
    if not media.is_file() or media.stat().st_size < minimum_size:
        return [
            Issue(
                code="OUTPUT_MEDIA_MISSING",
                severity=IssueSeverity.ERROR,
                message=f"Rendered media is missing or smaller than {minimum_size} bytes: {media}",
                location=str(media),
                suggestion="Review rendering and encoder logs.",
            )
        ]

    executable = shutil.which("ffprobe")
    if not executable:
        return [
            Issue(
                code="MISSING_FFPROBE",
                severity=IssueSeverity.ERROR,
                message="Cannot inspect output because ffprobe is unavailable.",
                location="ffprobe",
                suggestion="Install ffprobe and add it to PATH.",
            )
        ]
    try:
        result = runner(
            [
                executable,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(media),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return [_probe_failure(media, str(exc))]
    if result.returncode != 0:
        return [_probe_failure(media, (result.stderr or "").strip())]
    try:
        payload: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return [_probe_failure(media, f"invalid ffprobe JSON: {exc}")]

    streams = payload.get("streams", [])
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not video_streams:
        issues.append(_media_issue("VIDEO_STREAM_MISSING", media, "No video stream found."))
    else:
        video = video_streams[0]
        if video.get("width") != project.config.render.width:
            issues.append(
                _media_issue(
                    "VIDEO_WIDTH_MISMATCH",
                    media,
                    f"Expected width {project.config.render.width}, got {video.get('width')}.",
                )
            )
        if video.get("height") != project.config.render.height:
            issues.append(
                _media_issue(
                    "VIDEO_HEIGHT_MISMATCH",
                    media,
                    f"Expected height {project.config.render.height}, got {video.get('height')}.",
                )
            )
        actual_fps = _parse_rate(video.get("avg_frame_rate"))
        if actual_fps is None or abs(actual_fps - project.config.render.fps) > 0.01:
            issues.append(
                _media_issue(
                    "VIDEO_FPS_MISMATCH",
                    media,
                    f"Expected FPS {project.config.render.fps}, got {actual_fps}.",
                )
            )
    if project.config.audio.enabled and not audio_streams:
        issues.append(_media_issue("AUDIO_STREAM_MISSING", media, "No audio stream found."))

    if video_streams and audio_streams:
        video_duration = _duration(video_streams[0], payload)
        audio_duration = _duration(audio_streams[0], payload)
        if (
            video_duration is not None
            and audio_duration is not None
            and abs(video_duration - audio_duration) > 0.1
        ):
            issues.append(
                _media_issue(
                    "AV_DURATION_MISMATCH",
                    media,
                    (
                        f"Audio/video duration differs by "
                        f"{abs(video_duration - audio_duration):.3f}s."
                    ),
                )
            )
    return issues


def _parse_rate(value: object) -> float | None:
    try:
        numerator, denominator = str(value).split("/", maxsplit=1)
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _duration(stream: dict[str, Any], payload: dict[str, Any]) -> float | None:
    value = stream.get("duration", payload.get("format", {}).get("duration"))
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_failure(path: Path, detail: str) -> Issue:
    return _media_issue("FFPROBE_FAILED", path, f"ffprobe failed: {detail or 'unknown error'}")


def _media_issue(code: str, path: Path, message: str) -> Issue:
    return Issue(
        code=code,
        severity=IssueSeverity.ERROR,
        message=message,
        location=str(path),
        suggestion="Review encoder settings and external command logs.",
    )
