from __future__ import annotations

import json
import subprocess
from pathlib import Path

from piano_hand.config import load_resolved_project, save_project_config
from piano_hand.io import write_fingering_csv, write_timeline_json
from piano_hand.models import (
    AudioConfig,
    FingerSource,
    Hand,
    InputConfig,
    NoteEvent,
    PlaybackConfig,
    ProjectConfig,
    RenderConfig,
    ScoreSource,
    ScoreTimeline,
)
from piano_hand.validation.media_checks import check_dependencies, inspect_rendered_media
from piano_hand.validation.report_builder import validate_resolved_project
from piano_hand.validation.score_checks import check_score


def make_note(note_id: str, **updates: object) -> NoteEvent:
    values: dict[str, object] = {
        "id": note_id,
        "pitch": 60,
        "pitch_name": "C4",
        "onset_beat": 0.0,
        "duration_beat": 1.0,
        "onset_sec": 0.0,
        "duration_sec": 0.5,
        "measure": 1,
        "hand": Hand.RIGHT,
        "hand_confidence": 1.0,
        "finger": 1,
        "finger_source": FingerSource.GENERATED,
        "finger_confidence": 1.0,
    }
    values.update(updates)
    return NoteEvent.model_validate(values)


def make_timeline(notes: list[NoteEvent]) -> ScoreTimeline:
    duration = max((note.offset_sec for note in notes), default=0.0)
    return ScoreTimeline(
        source=ScoreSource(path="source.mid", type="midi", sha256="b" * 64),
        notes=notes,
        duration_sec=duration,
    )


def create_project(
    tmp_path: Path,
    timeline: ScoreTimeline,
    *,
    config: ProjectConfig | None = None,
) -> Path:
    (tmp_path / "source.mid").write_bytes(b"MThd")
    write_timeline_json(timeline, tmp_path / "timeline.json")
    write_fingering_csv(timeline, tmp_path / "fingering.csv")
    save_project_config(
        config
        or ProjectConfig(
            input=InputConfig(path="./source.mid", type="midi"),
            audio=AudioConfig(enabled=False),
        ),
        tmp_path,
    )
    return tmp_path


def test_empty_timeline_is_blocking() -> None:
    issues = check_score(make_timeline([]))

    assert [(issue.code, issue.severity.value) for issue in issues] == [
        ("EMPTY_SCORE", "error")
    ]


def test_score_checks_assignments_conflict_span_and_confidence() -> None:
    timeline = make_timeline(
        [
            make_note("n1", pitch=48, finger=1, hand_confidence=0.1),
            make_note("n2", pitch=72, finger=1),
            make_note(
                "n3",
                pitch=60,
                hand=Hand.UNKNOWN,
                finger=None,
                finger_source=FingerSource.UNKNOWN,
            ),
        ]
    )

    codes = {issue.code for issue in check_score(timeline)}

    assert {
        "LOW_HAND_CONFIDENCE",
        "SIMULTANEOUS_FINGER_CONFLICT",
        "CHORD_SPAN_EXCEEDED",
        "UNASSIGNED_HAND",
        "MISSING_FINGER",
    } <= codes


def test_project_validation_checks_configured_paths(tmp_path: Path) -> None:
    project_dir = create_project(tmp_path, make_timeline([make_note("n1")]))
    (project_dir / "source.mid").unlink()
    resolved = load_resolved_project(project_dir)

    report = validate_resolved_project(
        resolved, check_external_dependencies=False
    )

    assert not report.valid
    assert any(issue.location == "input.path" for issue in report.errors)


def test_project_validation_applies_csv_before_conflict_checks(tmp_path: Path) -> None:
    timeline = make_timeline(
        [
            make_note("n1", pitch=60, finger=1),
            make_note("n2", pitch=64, finger=2),
        ]
    )
    project_dir = create_project(tmp_path, timeline)
    (project_dir / "fingering.csv").write_text(
        "note_id,hand,finger\nn1,right,1\nn2,right,1\n",
        encoding="utf-8",
    )
    resolved = load_resolved_project(project_dir)

    report = validate_resolved_project(
        resolved, check_external_dependencies=False
    )

    assert any(
        issue.code == "SIMULTANEOUS_FINGER_CONFLICT" for issue in report.errors
    )


def test_project_validation_blocks_odd_yuv420p_dimensions(tmp_path: Path) -> None:
    config = ProjectConfig(
        input=InputConfig(path="./source.mid", type="midi"),
        render=RenderConfig(width=1279, height=719),
        audio=AudioConfig(enabled=False),
    )
    project_dir = create_project(
        tmp_path,
        make_timeline([make_note("n1")]),
        config=config,
    )

    report = validate_resolved_project(
        load_resolved_project(project_dir),
        check_external_dependencies=False,
    )

    assert not report.valid
    assert {
        issue.location
        for issue in report.errors
        if issue.code == "RENDER_DIMENSION_NOT_EVEN"
    } == {"render.width", "render.height"}


def test_project_validation_blocks_measure_range_without_notes(tmp_path: Path) -> None:
    config = ProjectConfig(
        input=InputConfig(path="./source.mid", type="midi"),
        playback=PlaybackConfig(start_measure=2, end_measure=3),
        audio=AudioConfig(enabled=False),
    )
    project_dir = create_project(
        tmp_path,
        make_timeline([make_note("n1", measure=1)]),
        config=config,
    )

    report = validate_resolved_project(
        load_resolved_project(project_dir),
        check_external_dependencies=False,
    )

    assert not report.valid
    assert any(issue.code == "EMPTY_PLAYBACK_RANGE" for issue in report.errors)


def test_parser_warnings_are_deduplicated_and_counted_in_report(
    tmp_path: Path,
) -> None:
    warning = "warning: unclosed MIDI note auto-closed at end of track"
    timeline = make_timeline(
        [make_note("warned-note", explanation=[warning, warning, "planning detail"])]
    )
    project_dir = create_project(tmp_path, timeline)

    report = validate_resolved_project(
        load_resolved_project(project_dir),
        check_external_dependencies=False,
    )

    assert report.valid
    assert len(report.warnings) == 1
    assert report.warnings[0].code == "PARSER_WARNING"
    assert "warned-note" in report.warnings[0].message
    assert report.metrics["warning_count"] == 1


def test_same_finger_different_pitch_overlap_is_blocking() -> None:
    timeline = make_timeline(
        [
            make_note("held", pitch=60, onset_sec=0.0, duration_sec=1.0),
            make_note("next", pitch=64, onset_sec=0.5, duration_sec=0.5),
        ]
    )

    errors = [
        issue
        for issue in check_score(timeline)
        if issue.code == "SIMULTANEOUS_FINGER_CONFLICT"
    ]

    assert len(errors) == 1
    assert errors[0].location == "note:held,note:next"


def test_same_finger_boundary_and_same_pitch_overlap_are_allowed() -> None:
    timeline = make_timeline(
        [
            make_note("first", pitch=60, onset_sec=0.0, duration_sec=0.5),
            make_note("boundary", pitch=64, onset_sec=0.5, duration_sec=0.5),
            make_note("same-pitch", pitch=64, onset_sec=0.75, duration_sec=0.5),
        ]
    )

    conflicts = [
        issue
        for issue in check_score(timeline)
        if issue.code == "SIMULTANEOUS_FINGER_CONFLICT"
    ]

    assert conflicts == []


def test_dependency_check_isolated_from_real_commands(
    tmp_path: Path, monkeypatch
) -> None:
    soundfont = tmp_path / "piano.sf2"
    soundfont.write_bytes(b"sf2")
    monkeypatch.setattr(
        "piano_hand.validation.media_checks.shutil.which",
        lambda command: f"/tools/{command}",
    )

    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=f"{args[0]} version 1\n", stderr="")

    issues, environment = check_dependencies(soundfont=soundfont, runner=runner)

    assert issues == []
    assert environment["ffmpeg"] == "/tools/ffmpeg version 1"
    assert environment["soundfont"] == str(soundfont)


def test_media_validation_uses_ffprobe_json(tmp_path: Path, monkeypatch) -> None:
    project_dir = create_project(tmp_path, make_timeline([make_note("n1")]))
    resolved = load_resolved_project(project_dir)
    media = tmp_path / "output.mp4"
    media.write_bytes(b"x" * 2048)
    monkeypatch.setattr(
        "piano_hand.validation.media_checks.shutil.which",
        lambda command: f"/tools/{command}",
    )
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1280,
                "height": 720,
                "avg_frame_rate": "30/1",
                "duration": "1.0",
            }
        ],
        "format": {"duration": "1.0"},
    }

    def runner(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")

    assert inspect_rendered_media(media, resolved, runner=runner) == []
