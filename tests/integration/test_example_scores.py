from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from piano_hand import cli
from piano_hand.config import load_resolved_project, save_project_config
from piano_hand.models import PlaybackConfig, RenderConfig
from piano_hand.parsers import parse_score
from piano_hand.planning import assign_hands, plan_fingering
from piano_hand.rendering.media_probe import validate_media

EXAMPLE_STEM = Path("examples/twinkle_twinkle_beginner")
EXAMPLE_PATHS = (
    EXAMPLE_STEM.with_suffix(".musicxml"),
    EXAMPLE_STEM.with_suffix(".mid"),
)


def _event_signature(path: Path) -> list[tuple[int, float, float, int]]:
    timeline = parse_score(path)
    return sorted(
        (
            note.pitch,
            note.onset_beat,
            note.duration_beat,
            note.measure,
        )
        for note in timeline.notes
    )


def test_beginner_musicxml_and_midi_examples_are_equivalent() -> None:
    musicxml_events = _event_signature(EXAMPLE_PATHS[0])
    midi_events = _event_signature(EXAMPLE_PATHS[1])

    assert musicxml_events == midi_events
    assert len(musicxml_events) == 54
    assert max(event[1] + event[2] for event in musicxml_events) == 48.0


@pytest.mark.parametrize("source", EXAMPLE_PATHS)
def test_beginner_examples_support_hand_and_fingering_planning(source: Path) -> None:
    planned = plan_fingering(assign_hands(parse_score(source)))

    assert len(planned.notes) == 54
    assert sum(note.hand.value == "left" for note in planned.notes) == 12
    assert sum(note.hand.value == "right" for note in planned.notes) == 42
    assert all(note.finger in range(1, 6) for note in planned.notes)


@pytest.mark.parametrize("source", EXAMPLE_PATHS)
def test_beginner_examples_render_as_full_keyboard_silent_video(
    source: Path,
    tmp_path: Path,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe is not installed")

    project_dir = tmp_path / source.suffix.removeprefix(".")
    runner = CliRunner()
    analyze_result = runner.invoke(
        cli.app,
        [
            "analyze",
            str(source),
            "--output",
            str(project_dir),
            "--mute",
        ],
    )
    assert analyze_result.exit_code == 0, analyze_result.output

    resolved = load_resolved_project(project_dir)
    config = resolved.config.model_copy(
        update={
            "render": RenderConfig(
                width=320,
                height=240,
                fps=8,
                keyboard_mode="full",
            ),
            "playback": PlaybackConfig(
                tempo_mode="multiplier",
                tempo_value=8.0,
                count_in_beats=0,
            ),
        }
    )
    save_project_config(config, resolved.project_file)

    render_result = runner.invoke(cli.app, ["render", str(project_dir)])
    assert render_result.exit_code == 0, render_result.output

    media = validate_media(
        resolved.video_path,
        expected_width=320,
        expected_height=240,
        expected_fps=8,
        require_audio=False,
        min_size_bytes=1024,
        ffprobe_bin=ffprobe,
    )
    assert media.has_video is True
    assert media.has_audio is False
