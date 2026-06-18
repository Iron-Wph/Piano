"""Validation report assembly and persistence."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path

from piano_hand import __version__
from piano_hand.config import ResolvedProject, load_resolved_project
from piano_hand.io import (
    apply_fingering_overrides,
    read_fingering_csv,
    read_timeline_json,
    write_json,
)
from piano_hand.models import Issue, IssueSeverity, ScoreTimeline, ValidationReport
from piano_hand.validation.fingering_checks import check_fingering_overrides
from piano_hand.validation.media_checks import CommandRunner, check_dependencies
from piano_hand.validation.score_checks import check_score


def validate_project(
    path: str | Path,
    *,
    check_external_dependencies: bool = True,
    runner: CommandRunner | None = None,
) -> tuple[ResolvedProject, ValidationReport]:
    project = load_resolved_project(path)
    report = validate_resolved_project(
        project,
        check_external_dependencies=check_external_dependencies,
        runner=runner,
    )
    return project, report


def validate_resolved_project(
    project: ResolvedProject,
    *,
    check_external_dependencies: bool = True,
    runner: CommandRunner | None = None,
) -> ValidationReport:
    started = time.perf_counter()
    issues = [*_check_render_dimensions(project), *_check_project_paths(project)]
    timeline = None
    if project.timeline_path.is_file():
        try:
            timeline = read_timeline_json(project.timeline_path)
        except Exception as exc:
            issues.append(
                Issue(
                    code="INVALID_TIMELINE",
                    severity=IssueSeverity.ERROR,
                    message=str(exc),
                    location=str(project.timeline_path),
                    suggestion="Regenerate or correct timeline.json.",
                )
            )
    if timeline is not None and project.fingering_path.is_file():
        try:
            overrides = read_fingering_csv(project.fingering_path)
            issues.extend(check_fingering_overrides(timeline, overrides))
            timeline = apply_fingering_overrides(timeline, overrides)
        except Exception as exc:
            issues.append(
                Issue(
                    code="INVALID_FINGERING_CSV",
                    severity=IssueSeverity.ERROR,
                    message=str(exc),
                    location=str(project.fingering_path),
                    suggestion="Correct the reported fingering CSV row.",
                )
            )
    if timeline is not None:
        issues.extend(_check_playback_measure_range(project, timeline))
        issues.extend(check_score(timeline))

    environment = _base_environment()
    if check_external_dependencies:
        if runner is None:
            dependency_issues, dependency_environment = check_dependencies(project)
        else:
            dependency_issues, dependency_environment = check_dependencies(
                project, runner=runner
            )
        issues.extend(dependency_issues)
        environment.update(dependency_environment)

    errors = [issue for issue in issues if issue.severity == IssueSeverity.ERROR]
    return ValidationReport(
        valid=not errors,
        issues=issues,
        input_summary={
            "path": str(project.input_path),
            "type": project.config.input.type,
            "source_sha256": timeline.source.sha256 if timeline is not None else None,
        },
        metrics={
            "note_count": len(timeline.notes) if timeline is not None else 0,
            "duration_sec": timeline.duration_sec if timeline is not None else 0.0,
            "warning_count": sum(
                issue.severity == IssueSeverity.WARNING for issue in issues
            ),
            "error_count": len(errors),
            "render_width": project.config.render.width,
            "render_height": project.config.render.height,
            "render_fps": project.config.render.fps,
            "random_seed": project.config.render.random_seed,
            "tempo_mode": project.config.playback.tempo_mode,
            "tempo_value": project.config.playback.tempo_value,
            "start_measure": project.config.playback.start_measure,
            "end_measure": project.config.playback.end_measure,
            "count_in_beats": project.config.playback.count_in_beats,
            "audio_enabled": project.config.audio.enabled,
        },
        stage_timings_sec={"validation": time.perf_counter() - started},
        environment=environment,
    )


def write_validation_report(report: ValidationReport, path: str | Path) -> Path:
    return write_json(report, path)


def _check_project_paths(project: ResolvedProject) -> list[Issue]:
    issues: list[Issue] = []
    required_files = {
        "input.path": project.input_path,
        "timeline.path": project.timeline_path,
        "timeline.fingering_overrides": project.fingering_path,
    }
    for location, path in required_files.items():
        if not path.is_file():
            issues.append(
                Issue(
                    code="CONFIG_PATH_MISSING",
                    severity=IssueSeverity.ERROR,
                    message=f"Configured file does not exist: {path}",
                    location=location,
                    suggestion="Correct the relative path in project.yaml.",
                )
            )
        elif not os.access(path, os.R_OK):
            issues.append(
                Issue(
                    code="CONFIG_PATH_UNREADABLE",
                    severity=IssueSeverity.ERROR,
                    message=f"Configured file is not readable: {path}",
                    location=location,
                    suggestion="Grant read permission or choose another file.",
                )
            )
    if not _destination_is_writable(project.video_path):
        issues.append(
            Issue(
                code="OUTPUT_PATH_UNWRITABLE",
                severity=IssueSeverity.ERROR,
                message=f"Video output location is not writable: {project.video_path}",
                location="output.video_path",
                suggestion="Choose a writable output path.",
            )
        )
    if not _destination_is_writable(project.report_path):
        issues.append(
            Issue(
                code="OUTPUT_PATH_UNWRITABLE",
                severity=IssueSeverity.ERROR,
                message=f"Report output location is not writable: {project.report_path}",
                location="output.report_path",
                suggestion="Choose a writable report path.",
            )
        )
    return issues


def _check_render_dimensions(project: ResolvedProject) -> list[Issue]:
    issues: list[Issue] = []
    dimensions = {
        "render.width": project.config.render.width,
        "render.height": project.config.render.height,
    }
    for location, value in dimensions.items():
        if value % 2:
            issues.append(
                Issue(
                    code="RENDER_DIMENSION_NOT_EVEN",
                    severity=IssueSeverity.ERROR,
                    message=f"{location} must be even for yuv420p output; got {value}.",
                    location=location,
                    suggestion="Choose an even render width and height.",
                )
            )
    return issues


def _check_playback_measure_range(
    project: ResolvedProject, timeline: ScoreTimeline
) -> list[Issue]:
    start_measure = project.config.playback.start_measure
    end_measure = project.config.playback.end_measure
    if any(
        note.measure >= start_measure
        and (end_measure is None or note.measure <= end_measure)
        for note in timeline.notes
    ):
        return []
    return [
        Issue(
            code="EMPTY_PLAYBACK_RANGE",
            severity=IssueSeverity.ERROR,
            message=(
                f"Playback measure range {start_measure}.."
                f"{end_measure if end_measure is not None else 'end'} contains no notes."
            ),
            location="playback.start_measure",
            suggestion="Choose a measure range present in the score.",
        )
    ]


def _destination_is_writable(path: Path) -> bool:
    if path.exists():
        return path.is_file() and os.access(path, os.W_OK)
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return parent.is_dir() and os.access(parent, os.W_OK)


def _base_environment() -> dict[str, str | None]:
    return {
        "piano_hand": __version__,
        "git_commit": _git_commit(),
        "python": platform.python_version(),
        "python_executable": sys.executable,
        "platform": platform.platform(),
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None
