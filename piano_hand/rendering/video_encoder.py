"""Streaming RGB frame encoding through FFmpeg."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable, Iterator
from itertools import chain
from pathlib import Path
from typing import Any

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.rendering.media_probe import resolve_executable

Frame = bytes | bytearray | memoryview | Any
ProcessFactory = Callable[..., subprocess.Popen[bytes]]


def build_ffmpeg_command(
    ffmpeg_executable: str | Path,
    output_path: str | Path,
    *,
    width: int,
    height: int,
    fps: float,
    audio_path: str | Path | None = None,
    audio_sample_rate: int = 48_000,
) -> list[str]:
    """Build an FFmpeg raw RGB24 to H.264 MP4 command."""

    _validate_video_settings(width, height, fps)
    command = [
        str(ffmpeg_executable),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s:v",
        f"{width}x{height}",
        "-r",
        _format_rate(fps),
        "-i",
        "pipe:0",
    ]
    if audio_path is not None:
        command.extend(
            [
                "-i",
                str(audio_path),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
            ]
        )
    command.extend(
        [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            _format_rate(fps),
            "-movflags",
            "+faststart",
        ]
    )
    if audio_path is None:
        command.append("-an")
    else:
        command.extend(
            [
                "-c:a",
                "aac",
                "-ar",
                str(audio_sample_rate),
                "-ac",
                "2",
                "-shortest",
            ]
        )
    command.append(str(output_path))
    return command


def encode_rgb_frames(
    frames: Iterable[Frame],
    output_path: str | Path,
    *,
    width: int,
    height: int,
    fps: float,
    audio_path: str | Path | None = None,
    audio_sample_rate: int = 48_000,
    ffmpeg_bin: str | Path | None = None,
    process_factory: ProcessFactory = subprocess.Popen,
) -> Path:
    """Stream RGB24 frames into FFmpeg and return the encoded MP4 path."""

    _validate_video_settings(width, height, fps)
    if not 8_000 <= audio_sample_rate <= 192_000:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Audio sample rate {audio_sample_rate} is outside 8000..192000 Hz.",
            "Use the configured sample-rate range.",
        )

    frame_iterator = iter(frames)
    try:
        first_frame = next(frame_iterator)
    except StopIteration as exc:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            "Cannot encode a video from an empty frame stream.",
            "Render at least one RGB frame.",
        ) from exc

    output = Path(output_path)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Could not create video output directory '{output.parent}': {exc}",
            "Choose a writable output directory.",
        ) from exc

    audio: Path | None = None
    if audio_path is not None:
        audio = Path(audio_path)
        if not audio.is_file():
            raise PianoHandError(
                ErrorCode.INPUT_ERROR,
                f"Audio file does not exist: {audio}",
                "Render the WAV file first or encode in explicit mute mode.",
            )

    executable = resolve_executable("ffmpeg", ffmpeg_bin)
    command = build_ffmpeg_command(
        executable,
        output,
        width=width,
        height=height,
        fps=fps,
        audio_path=audio,
        audio_sample_rate=audio_sample_rate,
    )
    try:
        process = process_factory(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            f"FFmpeg executable was not found: {executable}",
            "Install FFmpeg or provide its executable path.",
        ) from exc
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.ENCODE_ERROR,
            f"FFmpeg could not start: {exc}",
            "Check executable permissions and output paths.",
        ) from exc

    stderr = b""
    write_error: OSError | None = None
    try:
        if process.stdin is None:
            raise OSError("FFmpeg stdin pipe was not created")
        for frame in _with_first(first_frame, frame_iterator):
            process.stdin.write(_frame_bytes(frame, width=width, height=height))
    except (BrokenPipeError, OSError) as exc:
        write_error = exc
    finally:
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.stderr is not None:
            stderr = process.stderr.read()
        return_code = process.wait()

    if write_error is not None or return_code != 0:
        detail = _stderr_detail(stderr)
        reason = f"exit code {return_code}"
        if write_error is not None:
            reason += f", frame pipe error: {write_error}"
        raise PianoHandError(
            ErrorCode.ENCODE_ERROR,
            f"FFmpeg encoding of '{output}' failed ({reason}).{detail}",
            "Check frame dimensions, audio input, and FFmpeg codec support.",
        )
    if not output.is_file() or output.stat().st_size == 0:
        raise PianoHandError(
            ErrorCode.ENCODE_ERROR,
            f"FFmpeg reported success but produced no video: {output}",
            "Check the output path and FFmpeg installation.",
        )
    return output


def _with_first(first: Frame, remaining: Iterator[Frame]) -> Iterable[Frame]:
    return chain((first,), remaining)


def _frame_bytes(frame: Frame, *, width: int, height: int) -> bytes:
    expected_size = width * height * 3
    if isinstance(frame, bytes):
        data = frame
    elif isinstance(frame, (bytearray, memoryview)):
        data = bytes(frame)
    else:
        data = _array_or_image_bytes(frame, width=width, height=height)
    if len(data) != expected_size:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            f"RGB frame has {len(data)} bytes, expected {expected_size} "
            f"for {width}x{height}.",
            "Provide tightly packed RGB24 frames matching the render settings.",
        )
    return data


def _array_or_image_bytes(frame: Any, *, width: int, height: int) -> bytes:
    try:
        from PIL import Image

        if isinstance(frame, Image.Image):
            if frame.size != (width, height):
                raise PianoHandError(
                    ErrorCode.RENDER_ERROR,
                    f"Pillow frame size is {frame.size}, expected {(width, height)}.",
                    "Render frames at the configured output dimensions.",
                )
            return frame.convert("RGB").tobytes()
    except ImportError:
        pass

    try:
        import numpy as np
    except ImportError as exc:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            "NumPy is required for array-based RGB frames.",
            "Install the piano-hand project dependencies or provide RGB bytes.",
        ) from exc

    array = np.asarray(frame)
    expected_shape = (height, width, 3)
    if array.shape != expected_shape or array.dtype != np.uint8:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            f"Array frame is shape={array.shape}, dtype={array.dtype}; "
            f"expected shape={expected_shape}, dtype=uint8.",
            "Convert renderer output to an RGB uint8 array.",
        )
    return np.ascontiguousarray(array).tobytes()


def _validate_video_settings(width: int, height: int, fps: float) -> None:
    if width <= 0 or height <= 0:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Video dimensions must be positive, got {width}x{height}.",
            "Use positive render dimensions.",
        )
    if width % 2 or height % 2:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"yuv420p requires even video dimensions, got {width}x{height}.",
            "Use even width and height values.",
        )
    if fps <= 0:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Video fps must be positive, got {fps}.",
            "Use a positive frame rate.",
        )


def _format_rate(fps: float) -> str:
    return str(int(fps)) if float(fps).is_integer() else f"{fps:.6f}".rstrip("0")


def _stderr_detail(stderr: bytes | str | None) -> str:
    if not stderr:
        return ""
    text = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
    compact = " ".join(text.strip().split())
    return f" stderr: {compact[-2000:]}" if compact else ""
