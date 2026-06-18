"""MIDI serialization and FluidSynth-based WAV rendering."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from piano_hand.errors import ErrorCode, PianoHandError
from piano_hand.models import ScoreTimeline
from piano_hand.rendering.media_probe import (
    CommandRunner,
    resolve_executable,
    run_external_command,
)

DEFAULT_SAMPLE_RATE = 48_000
DEFAULT_TICKS_PER_BEAT = 1_000
_FIXED_TEMPO_US_PER_BEAT = 1_000_000


def write_timeline_midi(
    timeline: ScoreTimeline,
    output_path: str | Path,
    *,
    ticks_per_beat: int = DEFAULT_TICKS_PER_BEAT,
) -> Path:
    """Write the final second-based playback timeline as a standard MIDI file.

    A fixed 60 BPM tempo makes one beat equal one second. This preserves final
    playback seconds exactly and avoids reapplying the source tempo map.
    """

    if ticks_per_beat <= 0:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"ticks_per_beat must be positive, got {ticks_per_beat}.",
            "Use a positive MIDI tick resolution.",
        )

    mido = _load_mido()
    output = Path(output_path)
    _ensure_parent(output)
    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="Piano Hand playback", time=0))
    track.append(
        mido.MetaMessage("set_tempo", tempo=_FIXED_TEMPO_US_PER_BEAT, time=0)
    )

    events: list[tuple[int, int, Any]] = []
    for note in timeline.notes:
        onset_tick = _seconds_to_ticks(note.onset_sec, ticks_per_beat)
        offset_tick = _seconds_to_ticks(note.offset_sec, ticks_per_beat)
        velocity = max(1, note.velocity)
        events.append(
            (
                onset_tick,
                2,
                mido.Message(
                    "note_on",
                    note=note.pitch,
                    velocity=velocity,
                    channel=0,
                    time=0,
                ),
            )
        )
        events.append(
            (
                max(onset_tick + 1, offset_tick),
                0,
                mido.Message(
                    "note_off",
                    note=note.pitch,
                    velocity=0,
                    channel=0,
                    time=0,
                ),
            )
        )

    for pedal in timeline.pedal_events:
        events.append(
            (
                _seconds_to_ticks(pedal.time_sec, ticks_per_beat),
                1,
                mido.Message(
                    "control_change",
                    control=64,
                    value=127 if pedal.down else 0,
                    channel=0,
                    time=0,
                ),
            )
        )

    current_tick = 0
    for absolute_tick, _, message in sorted(events, key=lambda item: (item[0], item[1])):
        message.time = max(0, absolute_tick - current_tick)
        track.append(message)
        current_tick = absolute_tick

    end_tick = max(current_tick, _seconds_to_ticks(timeline.duration_sec, ticks_per_beat))
    track.append(mido.MetaMessage("end_of_track", time=end_tick - current_tick))
    try:
        midi.save(output)
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Could not write temporary MIDI '{output}': {exc}",
            "Check the output directory permissions and free space.",
        ) from exc
    return output


def build_fluidsynth_command(
    fluidsynth_executable: str | Path,
    soundfont_path: str | Path,
    midi_path: str | Path,
    wav_path: str | Path,
    *,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> list[str]:
    """Build a non-interactive FluidSynth file-render command."""

    return [
        str(fluidsynth_executable),
        "-ni",
        "-R",
        "0",
        "-C",
        "0",
        "-r",
        str(sample_rate),
        "-F",
        str(wav_path),
        str(soundfont_path),
        str(midi_path),
    ]


def render_timeline_audio(
    timeline: ScoreTimeline,
    output_path: str | Path,
    *,
    soundfont_path: str | Path | None = None,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    enabled: bool = True,
    fluidsynth_bin: str | Path | None = None,
    runner: CommandRunner = subprocess.run,
) -> Path | None:
    """Render a timeline to WAV, or return ``None`` for explicit mute mode."""

    if not enabled:
        return None
    if not 8_000 <= sample_rate <= 192_000:
        raise PianoHandError(
            ErrorCode.CONFIG_ERROR,
            f"Audio sample rate {sample_rate} is outside 8000..192000 Hz.",
            "Use the configured sample-rate range.",
        )
    if soundfont_path is None:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            "Audio is enabled but no SoundFont path was provided.",
            "Configure a readable .sf2 SoundFont or disable audio.",
        )

    soundfont = Path(soundfont_path).expanduser()
    if not soundfont.is_file():
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            f"SoundFont is unavailable: {soundfont}",
            "Configure an existing, readable .sf2 SoundFont or disable audio.",
        )

    output = Path(output_path)
    _ensure_parent(output)
    executable = resolve_executable("fluidsynth", fluidsynth_bin)
    try:
        with tempfile.TemporaryDirectory(prefix="piano-hand-audio-") as temp_dir:
            midi_path = write_timeline_midi(timeline, Path(temp_dir) / "playback.mid")
            run_external_command(
                build_fluidsynth_command(
                    executable,
                    soundfont,
                    midi_path,
                    output,
                    sample_rate=sample_rate,
                ),
                failure_code=ErrorCode.RENDER_ERROR,
                action=f"FluidSynth rendering of '{output}'",
                runner=runner,
            )
    except PianoHandError:
        raise
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Could not create audio render workspace: {exc}",
            "Check temporary and output directory permissions.",
        ) from exc

    if not output.is_file() or output.stat().st_size <= 44:
        raise PianoHandError(
            ErrorCode.RENDER_ERROR,
            f"FluidSynth did not create a valid WAV file: {output}",
            "Check the SoundFont and FluidSynth stderr output.",
        )
    return output


def _seconds_to_ticks(seconds: float, ticks_per_beat: int) -> int:
    return max(0, round(seconds * ticks_per_beat))


def _ensure_parent(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PianoHandError(
            ErrorCode.OUTPUT_ERROR,
            f"Could not create output directory '{path.parent}': {exc}",
            "Choose a writable output directory.",
        ) from exc


def _load_mido() -> Any:
    try:
        import mido  # type: ignore[import-untyped]
    except ImportError as exc:
        raise PianoHandError(
            ErrorCode.DEPENDENCY_ERROR,
            "The required Python package 'mido' is unavailable.",
            "Install the piano-hand project dependencies.",
        ) from exc
    return mido
