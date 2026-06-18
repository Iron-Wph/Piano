from __future__ import annotations

import csv
import shutil
from pathlib import Path

import mido  # type: ignore[import-untyped]
import pytest
from typer.testing import CliRunner

from piano_hand import cli
from piano_hand.config import load_resolved_project, save_project_config
from piano_hand.io import read_fingering_csv
from piano_hand.models import RenderConfig, ValidationReport
from piano_hand.rendering.media_probe import validate_media


def test_midi_to_muted_teaching_video_end_to_end(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe is not installed")

    source = tmp_path / "scale.mid"
    _write_scale_midi(source)
    project_dir = tmp_path / "project"
    runner = CliRunner()

    analyze_result = runner.invoke(
        cli.app,
        ["analyze", str(source), "--output", str(project_dir), "--mute"],
    )
    assert analyze_result.exit_code == 0, analyze_result.output

    resolved = load_resolved_project(project_dir)
    compact_config = resolved.config.model_copy(
        update={
            "render": RenderConfig(width=320, height=240, fps=12),
            "playback": resolved.config.playback.model_copy(
                update={"count_in_beats": 0}
            ),
        }
    )
    save_project_config(compact_config, resolved.project_file)

    render_result = runner.invoke(cli.app, ["render", str(project_dir)])
    assert render_result.exit_code == 0, render_result.output

    video = project_dir / "output.mp4"
    info = validate_media(
        video,
        expected_width=320,
        expected_height=240,
        expected_fps=12,
        require_audio=False,
        min_size_bytes=1024,
        ffprobe_bin=ffprobe,
    )
    assert info.has_video is True
    assert info.has_audio is False
    assert (project_dir / "validation-report.json").is_file()


def test_render_override_to_external_directory_uses_resolved_output_path(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "scale.mid"
    _write_scale_midi(source)
    project_dir = tmp_path / "project"
    runner = CliRunner()
    analyze_result = runner.invoke(
        cli.app,
        ["analyze", str(source), "--output", str(project_dir), "--mute"],
    )
    assert analyze_result.exit_code == 0, analyze_result.output

    resolved = load_resolved_project(project_dir)
    report = ValidationReport(valid=True)
    external_output = tmp_path / "exports" / "lesson.mp4"
    external_output.parent.mkdir()

    def fake_render(**kwargs):
        output = kwargs["output_path"]
        output.write_bytes(b"x" * 2048)
        return output

    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project: (resolved, report)
    )
    monkeypatch.setattr(cli, "_render_pipeline", fake_render)
    monkeypatch.setattr(cli, "inspect_rendered_media", lambda *args, **kwargs: [])

    render_result = runner.invoke(
        cli.app,
        ["render", str(project_dir), "--output", str(external_output)],
    )

    assert render_result.exit_code == 0, render_result.output
    assert external_output.read_bytes() == b"x" * 2048
    assert resolved.video_path.exists() is False


def test_musicxml_build_and_csv_override_rerender_end_to_end(tmp_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("FFmpeg/ffprobe is not installed")

    source = Path("examples/two_hand_scale.musicxml").resolve()
    project_dir = tmp_path / "musicxml-project"
    runner = CliRunner()

    build_result = runner.invoke(
        cli.app,
        ["build", str(source), "--output", str(project_dir), "--mute"],
    )
    assert build_result.exit_code == 0, build_result.output

    resolved = load_resolved_project(project_dir)
    rows = read_fingering_csv(resolved.fingering_path)
    first_note_id = next(iter(rows))
    replacement_hand = rows[first_note_id].hand.value
    replacement_finger = 5 if rows[first_note_id].finger != 5 else 1
    with resolved.fingering_path.open("r", encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
        fieldnames = list(source_rows[0])
    for row in source_rows:
        if row["note_id"] == first_note_id:
            row["hand"] = replacement_hand
            row["finger"] = str(replacement_finger)
    with resolved.fingering_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(source_rows)

    compact_config = resolved.config.model_copy(
        update={
            "render": RenderConfig(width=320, height=240, fps=12),
            "playback": resolved.config.playback.model_copy(
                update={"count_in_beats": 0}
            ),
        }
    )
    save_project_config(compact_config, resolved.project_file)
    rerender_result = runner.invoke(cli.app, ["render", str(project_dir)])
    assert rerender_result.exit_code == 0, rerender_result.output

    updated = read_fingering_csv(resolved.fingering_path)[first_note_id]
    assert updated.hand.value == replacement_hand
    assert updated.finger == replacement_finger
    info = validate_media(
        resolved.video_path,
        expected_width=320,
        expected_height=240,
        expected_fps=12,
        require_audio=False,
        min_size_bytes=1024,
        ffprobe_bin=ffprobe,
    )
    assert info.has_video is True


def _write_scale_midi(path: Path) -> None:
    midi = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    for pitch in (60, 62, 64, 65):
        track.append(mido.Message("note_on", note=pitch, velocity=80, time=0))
        track.append(mido.Message("note_off", note=pitch, velocity=0, time=240))
    midi.save(path)
