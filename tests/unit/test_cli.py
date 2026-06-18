from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from piano_hand import cli
from piano_hand.config import load_resolved_project, save_project_config
from piano_hand.io import read_timeline_json, write_fingering_csv, write_timeline_json
from piano_hand.models import (
    AudioConfig,
    FingerSource,
    Hand,
    InputConfig,
    Issue,
    IssueSeverity,
    NoteEvent,
    OutputConfig,
    PlaybackConfig,
    ProjectConfig,
    ScoreSource,
    ScoreTimeline,
    ValidationReport,
)

runner = CliRunner()


def make_timeline(*, finger: int = 2) -> ScoreTimeline:
    return ScoreTimeline(
        source=ScoreSource(path="source.mid", type="midi", sha256="c" * 64),
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
                hand=Hand.RIGHT,
                hand_confidence=1,
                finger=finger,
                finger_source=FingerSource.GENERATED,
                finger_confidence=1,
            )
        ],
        duration_sec=0.5,
    )


def create_project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "source.mid").write_bytes(b"MThd")
    timeline = make_timeline()
    write_timeline_json(timeline, tmp_path / "timeline.json")
    write_fingering_csv(timeline, tmp_path / "fingering.csv")
    save_project_config(
        ProjectConfig(
            input=InputConfig(path="./source.mid", type="midi"),
            audio=AudioConfig(enabled=False),
        ),
        tmp_path,
    )
    return tmp_path


def test_analyze_creates_project_files_without_real_parser(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "song.mid"
    source.write_bytes(b"MThd-test")
    output = tmp_path / "project"
    monkeypatch.setattr(cli, "_parse_input", lambda path: make_timeline())
    monkeypatch.setattr(cli, "_apply_planning", lambda timeline: timeline)

    result = runner.invoke(cli.app, ["analyze", str(source), "--output", str(output)])

    assert result.exit_code == 0, result.output
    assert (output / "project.yaml").is_file()
    assert (output / "timeline.json").is_file()
    assert (output / "fingering.csv").is_file()
    assert (output / "source.mid").read_bytes() == source.read_bytes()
    assert read_timeline_json(output / "timeline.json").source.sha256


def test_analyze_mute_disables_audio(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song.mid"
    source.write_bytes(b"MThd-test")
    output = tmp_path / "project"
    monkeypatch.setattr(cli, "_parse_input", lambda path: make_timeline())
    monkeypatch.setattr(cli, "_apply_planning", lambda timeline: timeline)

    result = runner.invoke(
        cli.app,
        ["analyze", str(source), "--output", str(output), "--mute"],
    )

    assert result.exit_code == 0, result.output
    assert load_resolved_project(output).config.audio.enabled is False


def test_analyze_refuses_silent_overwrite(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "song.mid"
    source.write_bytes(b"MThd-test")
    output = tmp_path / "project"
    output.mkdir()
    (output / "notes.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(cli, "_parse_input", lambda path: make_timeline())

    result = runner.invoke(cli.app, ["analyze", str(source), "--output", str(output)])

    assert result.exit_code == cli.EXIT_ERROR
    assert "not empty" in result.output
    assert (output / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_planning_adapter_uses_parallel_module_contracts() -> None:
    timeline = make_timeline()
    timeline = timeline.model_copy(
        update={
            "notes": [
                timeline.notes[0].model_copy(
                    update={
                        "hand": Hand.UNKNOWN,
                        "hand_confidence": 0.0,
                        "finger": None,
                        "finger_source": FingerSource.UNKNOWN,
                        "finger_confidence": 0.0,
                    }
                )
            ]
        }
    )

    planned = cli._apply_planning(timeline)

    assert planned.notes[0].hand in {Hand.LEFT, Hand.RIGHT}
    assert planned.notes[0].finger in range(1, 6)


def test_validate_strict_returns_two_for_warning(tmp_path: Path, monkeypatch) -> None:
    resolved = load_resolved_project(create_project(tmp_path))
    report = ValidationReport(
        valid=True,
        issues=[
            Issue(
                code="TEST_WARNING",
                severity=IssueSeverity.WARNING,
                message="review",
            )
        ],
    )
    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project: (resolved, report)
    )

    result = runner.invoke(cli.app, ["validate", str(tmp_path), "--strict"])

    assert result.exit_code == cli.EXIT_WARNINGS


def test_render_failure_is_written_to_report(tmp_path: Path, monkeypatch) -> None:
    resolved = load_resolved_project(create_project(tmp_path))
    report = ValidationReport(valid=True)
    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project: (resolved, report)
    )

    def fail_render(**kwargs):
        raise RuntimeError("frame renderer exploded")

    monkeypatch.setattr(cli, "_render_pipeline", fail_render)

    result = runner.invoke(cli.app, ["render", str(tmp_path)])

    assert result.exit_code == cli.EXIT_ERROR
    payload = json.loads(resolved.report_path.read_text(encoding="utf-8"))
    assert payload["valid"] is False
    assert any(issue["code"] == "RENDER_FAILED" for issue in payload["issues"])


def test_render_config_failure_writes_fallback_report(
    tmp_path: Path, monkeypatch
) -> None:
    project = tmp_path / "broken"
    project.mkdir()
    (project / "project.yaml").write_text("not: [valid", encoding="utf-8")

    result = runner.invoke(cli.app, ["render", str(project)])

    assert result.exit_code == cli.EXIT_ERROR
    payload = json.loads(
        (project / "validation-report.json").read_text(encoding="utf-8")
    )
    assert payload["issues"][0]["code"] == "PROJECT_VALIDATION_FAILED"


def test_render_applies_csv_override_before_pipeline(
    tmp_path: Path, monkeypatch
) -> None:
    resolved = load_resolved_project(create_project(tmp_path))
    (tmp_path / "fingering.csv").write_text(
        "note_id,hand,finger\nn1,left,5\n", encoding="utf-8"
    )
    report = ValidationReport(valid=True)
    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project: (resolved, report)
    )
    captured: dict[str, object] = {}

    def fake_render(**kwargs):
        captured["timeline"] = kwargs["timeline"]
        output = kwargs["output_path"]
        output.write_bytes(b"x" * 2048)
        return output

    monkeypatch.setattr(cli, "_render_pipeline", fake_render)
    monkeypatch.setattr(cli, "inspect_rendered_media", lambda *args, **kwargs: [])

    result = runner.invoke(cli.app, ["render", str(tmp_path)])

    assert result.exit_code == 0, result.output
    timeline = captured["timeline"]
    assert isinstance(timeline, ScoreTimeline)
    assert timeline.notes[0].hand == Hand.LEFT
    assert timeline.notes[0].finger == 5
    assert timeline.notes[0].finger_source == FingerSource.MANUAL


@pytest.mark.parametrize(
    ("target_name", "expected_label"),
    [
        ("source.mid", "input.path"),
        ("timeline.json", "timeline.json"),
        ("fingering.csv", "fingering.csv"),
        ("project.yaml", "project.yaml"),
        ("validation-report.json", "validation-report.json"),
    ],
)
def test_render_output_refuses_protected_project_files_without_modifying_them(
    tmp_path: Path,
    monkeypatch,
    target_name: str,
    expected_label: str,
) -> None:
    project = create_project(tmp_path)
    target = project / target_name
    if target_name == "validation-report.json":
        target.write_text("keep-report", encoding="utf-8")
    original = target.read_bytes()
    render_called = False

    def fake_render(**kwargs):
        nonlocal render_called
        render_called = True
        return kwargs["output_path"]

    monkeypatch.setattr(cli, "_render_pipeline", fake_render)

    result = runner.invoke(
        cli.app,
        ["render", str(project), "--output", str(target)],
    )

    assert result.exit_code == cli.EXIT_ERROR
    assert "OUTPUT_ERROR" in result.output
    assert expected_label in result.output
    assert target.read_bytes() == original
    assert render_called is False


def test_render_configured_output_refuses_source_without_modifying_it(
    tmp_path: Path, monkeypatch
) -> None:
    project = create_project(tmp_path)
    resolved = load_resolved_project(project)
    config = resolved.config.model_copy(
        update={"output": OutputConfig(video_path="./source.mid")}
    )
    save_project_config(config, resolved.project_file)
    original = resolved.input_path.read_bytes()
    render_called = False

    def fake_render(**kwargs):
        nonlocal render_called
        render_called = True
        return kwargs["output_path"]

    monkeypatch.setattr(cli, "_render_pipeline", fake_render)

    result = runner.invoke(cli.app, ["render", str(project)])

    assert result.exit_code == cli.EXIT_ERROR
    assert "OUTPUT_ERROR" in result.output
    assert "input.path" in result.output
    assert resolved.input_path.read_bytes() == original
    assert render_called is False


def test_render_output_checks_override_parent_writability(
    tmp_path: Path, monkeypatch
) -> None:
    project = create_project(tmp_path / "project")
    resolved = load_resolved_project(project)
    report = ValidationReport(valid=True)
    external_dir = tmp_path / "read-only"
    external_dir.mkdir()
    output = external_dir / "video.mp4"
    real_access = os.access

    def fake_access(path, mode):
        if Path(path) == external_dir and mode == os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(cli.os, "access", fake_access)
    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project_path: (resolved, report)
    )

    result = runner.invoke(
        cli.app,
        ["render", str(project), "--output", str(output)],
    )

    assert result.exit_code == cli.EXIT_ERROR
    assert "OUTPUT_ERROR" in result.output
    assert "parent directory is not writable" in result.output
    assert not output.exists()


def test_render_output_refuses_directory_target(tmp_path: Path) -> None:
    project = create_project(tmp_path / "project")
    target = tmp_path / "video-directory"
    target.mkdir()
    resolved = load_resolved_project(project)
    config = resolved.config.model_copy(
        update={"output": OutputConfig(video_path=str(target))}
    )
    save_project_config(config, resolved.project_file)

    result = runner.invoke(cli.app, ["render", str(project)])

    assert result.exit_code == cli.EXIT_ERROR
    assert "OUTPUT_ERROR" in result.output
    assert "not a regular file" in result.output


def test_render_output_allows_writable_external_file(
    tmp_path: Path, monkeypatch
) -> None:
    project = create_project(tmp_path / "project")
    resolved = load_resolved_project(project)
    report = ValidationReport(valid=True)
    output = tmp_path / "exports" / "lesson.mp4"
    output.parent.mkdir()

    def fake_render(**kwargs):
        target = kwargs["output_path"]
        target.write_bytes(b"x" * 2048)
        return target

    monkeypatch.setattr(
        cli, "_validate_and_write", lambda project_path: (resolved, report)
    )
    monkeypatch.setattr(cli, "_render_pipeline", fake_render)
    monkeypatch.setattr(cli, "inspect_rendered_media", lambda *args, **kwargs: [])

    result = runner.invoke(
        cli.app,
        ["render", str(project), "--output", str(output)],
    )

    assert result.exit_code == 0, result.output
    assert output.read_bytes() == b"x" * 2048


def test_render_pipeline_uses_motion_frame_and_encoder_contracts(
    tmp_path: Path, monkeypatch
) -> None:
    resolved = load_resolved_project(create_project(tmp_path))
    captured: dict[str, object] = {}

    def fake_encode(frames, output_path, **kwargs):
        first = next(iter(frames))
        captured["shape"] = first.shape
        output = Path(output_path)
        output.write_bytes(b"x" * 2048)
        return output

    monkeypatch.setattr(
        "piano_hand.rendering.video_encoder.encode_rgb_frames", fake_encode
    )

    output = cli._render_pipeline(
        timeline=make_timeline(),
        project=resolved,
        output_path=tmp_path / "pipeline.mp4",
        temp_dir=tmp_path,
    )

    assert output.is_file()
    assert captured["shape"] == (720, 1280, 3)


def test_doctor_has_stable_failure_exit(monkeypatch, tmp_path: Path) -> None:
    issue = Issue(
        code="MISSING_FFMPEG",
        severity=IssueSeverity.ERROR,
        message="missing",
    )
    monkeypatch.setattr(
        cli,
        "check_dependencies",
        lambda **kwargs: ([issue], {"ffmpeg": None, "ffprobe": "ok", "fluidsynth": "ok"}),
    )

    result = runner.invoke(cli.app, ["doctor", "--output-dir", str(tmp_path)])

    assert result.exit_code == cli.EXIT_ERROR
    assert "MISSING_FFMPEG" in result.output


def test_doctor_mute_accepts_missing_audio_dependencies(tmp_path: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["doctor", "--mute", "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "fluidsynth: OPTIONAL" in result.output
    assert "SoundFont: OPTIONAL" in result.output


def test_playback_timeline_applies_crop_speed_and_count_in() -> None:
    timeline = make_timeline()
    timeline = timeline.model_copy(
        update={
            "notes": [
                timeline.notes[0].model_copy(
                    update={"onset_sec": 2.0, "duration_sec": 1.0, "measure": 2}
                )
            ],
            "duration_sec": 3.0,
        }
    )
    config = ProjectConfig(
        input=InputConfig(path="./source.mid", type="midi"),
        playback=PlaybackConfig(
            tempo_mode="multiplier",
            tempo_value=2.0,
            start_measure=2,
            count_in_beats=2,
        ),
        audio=AudioConfig(enabled=False),
    )

    playback = cli._prepare_playback_timeline(timeline, config)

    assert playback.notes[0].onset_sec == 0.5
    assert playback.notes[0].duration_sec == 0.5
    assert playback.duration_sec == 1.0
